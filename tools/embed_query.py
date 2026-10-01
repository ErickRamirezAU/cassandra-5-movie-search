#!/usr/bin/env python3
"""
Turn a search phrase into a ready-to-paste cMovie vector search query.

The Astra CQL console can't call Gemini, so this helper embeds your phrase
with the same model the loader used for the plots (`gemini-embedding-2`, 3,072
dimensions) and prints a complete SELECT statement with the vector filled in.
Copy the whole statement into the CQL console and run it.

Reads GEMINI_API_KEY from .env in the repo's root directory. Run from the
repo's root directory, in your virtual environment:

    ./.venv/bin/python tools/embed_query.py "a retired hitman is pulled back in"

Each run sends one phrase to Gemini, which counts as one text against your
free tier allowance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

from connection import DEFAULT_ENV_PATH, load_env  # noqa: E402
from embeddings import DailyLimitReached, EmbeddingError, embed_texts, vector_literal  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("phrase", help="What the movie is about, in your own words")
    parser.add_argument("--limit", type=int, default=5, help="How many movies to ask for (default: 5)")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    args = parser.parse_args()

    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")

    api_key = load_env(Path(args.env_file)).get("GEMINI_API_KEY")
    if not api_key:
        sys.exit(f"GEMINI_API_KEY isn't set in {args.env_file}. See section 3 of docs/setup.md.")

    try:
        (vector,) = embed_texts(api_key, [args.phrase])
    except DailyLimitReached as e:
        sys.exit(f"Google's free tier limit for today is used up: {e}")
    except EmbeddingError as e:
        sys.exit(str(e))

    print(
        "SELECT title, release_year FROM movies "
        f"ORDER BY plot_embedding ANN OF {vector_literal(vector)} LIMIT {args.limit};"
    )


if __name__ == "__main__":
    main()
