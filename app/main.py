"""The cMovie API, which grows each build week.

Run from the repository root, with the virtual environment from
docs/setup.md active (requirements.txt already installs fastapi and
uvicorn):

    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000/docs to try the endpoints.

Endpoints:

- GET /movies/search    Week 2: filter and keyword search with SAI.
- GET /movies/semantic  Week 4: search by what a plot means, with ANN OF.
- GET /movies/assist    Week 6: a movie assistant that answers in plain
                        English, using filtered ANN OF results and Gemini.

Connects with the same code as tools/loader.py (tools/connection.py), so it
reads the same .env file in the project root: ASTRA_DB_TOKEN and the secure
connect bundle for Astra DB, or CASSANDRA_HOSTS for a plain Apache Cassandra
cluster. The semantic and assist endpoints also need GEMINI_API_KEY. Requires the
movies table with its indexes from weeks 1 to 3, and every movie's
plot_embedding filled in by the loader.
"""

from __future__ import annotations

import math
import re
import sys
from typing import Optional

from cassandra import ConsistencyLevel
from cassandra.query import SimpleStatement
from fastapi import FastAPI, HTTPException, Query

from tools.connection import DEFAULT_ENV_PATH, build_config, connect, load_env
from tools.embeddings import DailyLimitReached, EmbeddingError, RateLimited, embed_texts
from tools.llm import LLMError, generate_text

# The same rule tools/loader.py uses to fill title_words and actor_words, so
# a search term splits into exactly the tokens the loader stored.
TOKEN_STRIP_RE = re.compile(r"[:,.\-–—]")


def tokenise(text: str) -> list[str]:
    stripped = TOKEN_STRIP_RE.sub("", text)
    return [t.lower() for t in stripped.split() if t]


cluster, session = connect(build_config())

GEMINI_API_KEY = load_env(DEFAULT_ENV_PATH).get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    sys.exit(f"GEMINI_API_KEY isn't set in {DEFAULT_ENV_PATH}. See section 3 of docs/setup.md.")

# Prepared once, with the query vector and the LIMIT as bind markers. Vector
# queries can only be read at consistency level ONE or LOCAL_ONE, and the
# connection's default is LOCAL_QUORUM, so this statement sets its own.
ANN_QUERY = session.prepare(
    "SELECT title, release_year FROM movies ORDER BY plot_embedding ANN OF ? LIMIT ?"
)
ANN_QUERY.consistency_level = ConsistencyLevel.ONE

app = FastAPI()


@app.get("/movies/search")
def search_movies(
    title: Optional[str] = None,
    actor: Optional[str] = None,
    genre: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    rating_min: Optional[float] = None,
):
    clauses, params = [], []
    for word in tokenise(title or ""):
        clauses.append("title_words CONTAINS %s")
        params.append(word)
    for word in tokenise(actor or ""):
        clauses.append("actor_words CONTAINS %s")
        params.append(word)
    if genre:
        clauses.append("genres CONTAINS %s")
        params.append(genre.lower())
    if year_from is not None:
        clauses.append("release_year >= %s")
        params.append(year_from)
    if year_to is not None:
        clauses.append("release_year <= %s")
        params.append(year_to)
    if rating_min is not None:
        clauses.append("cmovie_rating >= %s")
        params.append(rating_min)

    if not clauses:
        raise HTTPException(status_code=400, detail="at least one filter is required")

    query = (
        "SELECT title, release_year, cmovie_rating FROM movies WHERE "
        + " AND ".join(clauses)
        + " LIMIT 10"
    )
    rows = session.execute(query, params)
    # cmovie_rating is a 32-bit float, so round it back to the loader's one
    # decimal place rather than returning 4.900000095367432 for 4.9.
    return {
        "results": [
            {**row._asdict(), "cmovie_rating": round(row.cmovie_rating, 1)}
            for row in rows
        ]
    }


@app.get("/movies/semantic")
def semantic_search(
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(5, ge=1, le=1000),
):
    # Embed the phrase with the same model the loader used for the plots.
    # retry=False because a request shouldn't sit waiting on Google.
    try:
        (vector,) = embed_texts(GEMINI_API_KEY, [q], retry=False)
    except DailyLimitReached as e:
        raise HTTPException(status_code=429, detail=f"Gemini's free tier limit for today is used up: {e}")
    except RateLimited as e:
        raise HTTPException(
            status_code=429,
            detail="Gemini is rate limiting this key, try again shortly",
            headers={"Retry-After": str(math.ceil(e.retry_after))},
        )
    except EmbeddingError as e:
        raise HTTPException(status_code=502, detail=str(e))

    rows = session.execute(ANN_QUERY, (vector, limit))
    return {"results": [row._asdict() for row in rows]}


# The rules the model works under. Everything it may recommend comes from the
# movies the user message lists, so it can't suggest a film that isn't in the
# database.
SYSTEM_PROMPT = (
    "You are cMovie Assistant, a friendly movie concierge. "
    "Recommend movies ONLY from the list in the user's message. "
    "For each pick, say why it fits the request, using its plot, and mention its "
    "release year and cMovie rating. End each pick with its Wikipedia link. "
    "If none of the movies fit the request, say so politely and don't suggest "
    "anything else. Never invent titles, plot details or cast members."
)


def movie_context(rows) -> str:
    # One block per movie. The plot is cut to 300 characters to keep the prompt short.
    return "\n\n".join(
        f"{row.title} ({row.release_year})\n"
        f"Genres: {', '.join(sorted(row.genres))}\n"
        f"cMovie rating: {row.cmovie_rating:.1f} out of 10\n"
        f"Plot: {row.plot[:300]}\n"
        f"Wikipedia: {row.wikipedia_url}"
        for row in rows
    )


def gemini_http_error(e: Exception) -> HTTPException:
    if isinstance(e, DailyLimitReached):
        return HTTPException(status_code=429, detail=f"Gemini's free tier limit for today is used up: {e}")
    if isinstance(e, RateLimited):
        return HTTPException(
            status_code=429,
            detail="Gemini is rate limiting this key, try again shortly",
            headers={"Retry-After": str(math.ceil(e.retry_after))},
        )
    return HTTPException(status_code=502, detail=str(e))


@app.get("/movies/assist")
def assist(
    prompt: str = Query(min_length=1, max_length=500),
    genre: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    rating_min: Optional[float] = None,
):
    # Step 1: embed the request and find the 3 closest movies that pass the filters.
    try:
        (vector,) = embed_texts(GEMINI_API_KEY, [prompt], retry=False)
    except (DailyLimitReached, RateLimited, EmbeddingError) as e:
        raise gemini_http_error(e)

    # Genre labels are case sensitive ("Christmas", "science fiction"), so the
    # label goes into the query exactly as the caller typed it.
    clauses, params = [], []
    if genre:
        clauses.append("genres CONTAINS %s")
        params.append(genre)
    if year_from is not None:
        clauses.append("release_year >= %s")
        params.append(year_from)
    if year_to is not None:
        clauses.append("release_year <= %s")
        params.append(year_to)
    if rating_min is not None:
        clauses.append("cmovie_rating >= %s")
        params.append(rating_min)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""

    # The WHERE clause changes with the filters, so this statement can't be
    # prepared once. It still needs consistency level ONE for the ANN OF.
    query = SimpleStatement(
        "SELECT title, release_year, genres, cmovie_rating, plot, wikipedia_url "
        f"FROM movies {where}ORDER BY plot_embedding ANN OF %s LIMIT 3",
        consistency_level=ConsistencyLevel.ONE,
    )
    rows = list(session.execute(query, params + [vector]))

    # Nothing passed the filters, so there's nothing to ground an answer in.
    if not rows:
        return {
            "recommendation": "I couldn't find any movies that match those filters.",
            "candidates": [],
        }

    # Steps 2 and 3: put the movies in the prompt and ask Gemini to answer.
    try:
        recommendation = generate_text(
            GEMINI_API_KEY, SYSTEM_PROMPT, f"{prompt}\n\nMovies:\n\n{movie_context(rows)}"
        )
    except (DailyLimitReached, RateLimited, LLMError) as e:
        raise gemini_http_error(e)

    return {
        "recommendation": recommendation,
        "candidates": [
            {
                "title": row.title,
                "release_year": row.release_year,
                "genres": sorted(row.genres),
                "cmovie_rating": round(row.cmovie_rating, 1),
                "wikipedia_url": row.wikipedia_url,
            }
            for row in rows
        ],
    }
