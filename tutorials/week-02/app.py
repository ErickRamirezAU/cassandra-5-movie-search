"""Week 2: a small FastAPI search endpoint over the movies table.

Run from the repository root, with the virtual environment from
docs/setup.md active (requirements.txt already installs fastapi and
uvicorn):

    uvicorn app:app --reload --app-dir tutorials/week-02

Connects with the same code as tools/loader.py (tools/connection.py), so
it reads the same .env file in the project root: ASTRA_DB_TOKEN and the
secure connect bundle for Astra DB, or CASSANDRA_HOSTS for a plain
Apache Cassandra cluster. Requires the movies table from week 1's
schema.cql and the two indexes in this week's schema.cql to already
exist.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR / "tools"))

from connection import build_config, connect  # noqa: E402

# The same rule tools/loader.py uses to fill title_words and actor_words, so
# a search term splits into exactly the tokens the loader stored.
TOKEN_STRIP_RE = re.compile(r"[:,.\-–—]")


def tokenise(text: str) -> list[str]:
    stripped = TOKEN_STRIP_RE.sub("", text)
    return [t.lower() for t in stripped.split() if t]


cluster, session = connect(build_config())
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
