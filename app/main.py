"""The cMovie API, which grows each build week.

Run from the repository root, with the virtual environment from
docs/setup.md active (requirements.txt already installs fastapi and
uvicorn):

    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000/docs to try the endpoints.

Endpoints:

- GET /movies/search    Week 2: filter and keyword search with SAI.
- GET /movies/semantic  Week 4: search by what a plot means, with ANN OF.

Connects with the same code as tools/loader.py (tools/connection.py), so it
reads the same .env file in the project root: ASTRA_DB_TOKEN and the secure
connect bundle for Astra DB, or CASSANDRA_HOSTS for a plain Apache Cassandra
cluster. The semantic endpoint also needs GEMINI_API_KEY. Requires the
movies table with its indexes from weeks 1 to 3, and every movie's
plot_embedding filled in by the loader.
"""

from __future__ import annotations

import math
import re
import sys
from typing import Optional

from cassandra import ConsistencyLevel
from fastapi import FastAPI, HTTPException, Query

from tools.connection import DEFAULT_ENV_PATH, build_config, connect, load_env
from tools.embeddings import DailyLimitReached, EmbeddingError, RateLimited, embed_texts

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
