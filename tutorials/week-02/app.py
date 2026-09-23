"""Week 2: a small FastAPI search endpoint over the movies table.

Run from the repository root, with the virtual environment from
docs/setup.md active and fastapi/uvicorn installed:

    pip install fastapi uvicorn
    uvicorn tutorials.week-02.app:app --reload --app-dir .

If your shell or editor won't import a module with a hyphen in its
path, run it from inside this directory instead:

    cd tutorials/week-02
    uvicorn app:app --reload

Reads the same .env file and secure connect bundle as tools/loader.py,
in the project root: ASTRA_DB_TOKEN, optionally ASTRA_DB_KEYSPACE
(defaults to Astra's own default_keyspace), and
secure-connect-cmovies.zip. Requires the movies table from week 1's
schema.cql and the two indexes in this week's schema.cql to already
exist.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from cassandra.auth import PlainTextAuthProvider
from cassandra.cluster import Cluster
from fastapi import FastAPI

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCB_PATH = PROJECT_DIR / "secure-connect-cmovies.zip"
DEFAULT_ENV_PATH = PROJECT_DIR / ".env"


def load_env(path: Path) -> dict:
    values = dict(os.environ)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def connect() -> "Session":  # noqa: F821 - cassandra.cluster.Session
    env = load_env(DEFAULT_ENV_PATH)
    token = env.get("ASTRA_DB_TOKEN")
    if not token:
        sys.exit(
            f"ASTRA_DB_TOKEN not set. Checked env file {DEFAULT_ENV_PATH} and "
            "the current environment, same as tools/loader.py."
        )
    if not DEFAULT_SCB_PATH.exists():
        sys.exit(
            f"Secure Connect Bundle not found at {DEFAULT_SCB_PATH}. Download "
            "it from your Astra DB dashboard and save it in the project root."
        )
    cluster = Cluster(
        cloud={"secure_connect_bundle": str(DEFAULT_SCB_PATH)},
        auth_provider=PlainTextAuthProvider("token", token),
    )
    keyspace = env.get("ASTRA_DB_KEYSPACE") or "default_keyspace"
    return cluster.connect(keyspace)


session = connect()
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
    if title:
        clauses.append("title_words CONTAINS %s")
        params.append(title.lower())
    if actor:
        clauses.append("actor_words CONTAINS %s")
        params.append(actor.lower())
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
        return {"error": "at least one filter is required"}

    query = (
        "SELECT title, release_year, cmovie_rating FROM movies WHERE "
        + " AND ".join(clauses)
        + " LIMIT 10"
    )
    rows = session.execute(query, params)
    return {"results": [row._asdict() for row in rows]}
