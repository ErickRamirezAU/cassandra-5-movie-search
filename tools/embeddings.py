"""
Gemini embedding calls, shared by tools/loader.py and tools/embed_query.py.

Uses Google's `gemini-embedding-2` model at its native 3,072 dimensions,
through the batchEmbedContents endpoint, with the standard library only.
The API key goes in a request header, so it never appears in a URL or an
error message.

Google's free tier counts every text in a batch against its limits, not
just the request, so batching saves round trips but doesn't stretch your
allowance. When Google answers HTTP 429, embed_texts() waits for the delay it
asks for and tries again. If the answer says the daily limit is used up, it
raises DailyLimitReached, so the caller can stop and keep what it has.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-embedding-2"
DIMENSIONS = 3072

# Plots per request.
BATCH_SIZE = 100

MAX_RETRIES = 6
DEFAULT_RETRY_SECONDS = 15


class DailyLimitReached(RuntimeError):
    """Google says today's free tier allowance is used up."""


class EmbeddingError(RuntimeError):
    """Any other failure from the Gemini API."""


def _retry_seconds(error_body: dict) -> float | None:
    for detail in error_body.get("error", {}).get("details", []):
        delay = detail.get("retryDelay")
        if delay:
            return float(delay.rstrip("s"))
    match = re.search(r"retry in ([\d.]+)s", error_body.get("error", {}).get("message", ""))
    return float(match.group(1)) if match else None


def _is_daily_limit(error_body: dict) -> bool:
    for detail in error_body.get("error", {}).get("details", []):
        for violation in detail.get("violations", []):
            if "perday" in violation.get("quotaId", "").lower():
                return True
    return False


def embed_texts(api_key: str, texts: list[str]) -> list[list[float]]:
    """Embed texts in one batch request and return one vector per text."""
    body = json.dumps(
        {
            "requests": [
                {
                    "model": f"models/{MODEL}",
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": DIMENSIONS,
                }
                for text in texts
            ]
        }
    ).encode("utf-8")

    for attempt in range(1, MAX_RETRIES + 1):
        request = urllib.request.Request(
            f"{API_BASE}/models/{MODEL}:batchEmbedContents",
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                error_body = json.loads(raw)
            except json.JSONDecodeError:
                error_body = {}
            if e.code == 429:
                if _is_daily_limit(error_body):
                    raise DailyLimitReached(error_body["error"]["message"].splitlines()[0]) from e
                wait = (_retry_seconds(error_body) or DEFAULT_RETRY_SECONDS) + 1
                print(f"  [429] Gemini asked us to wait, retrying in {wait:.0f}s (attempt {attempt})", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            if e.code in (500, 502, 503, 504):
                wait = 5 * attempt
                print(f"  [{e.code}] Gemini transient error, retrying in {wait}s (attempt {attempt})", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            message = error_body.get("error", {}).get("message", raw[:300])
            raise EmbeddingError(f"Gemini returned HTTP {e.code}: {message}") from e
        except (urllib.error.URLError, OSError) as e:
            wait = 5 * attempt
            print(f"  [network] {type(e).__name__}: {e}, retrying in {wait}s (attempt {attempt})", file=sys.stderr, flush=True)
            time.sleep(wait)
            continue

        vectors = [item["values"] for item in data.get("embeddings", [])]
        if len(vectors) != len(texts) or any(len(v) != DIMENSIONS for v in vectors):
            raise EmbeddingError(f"Expected {len(texts)} vectors of {DIMENSIONS} numbers, got {[len(v) for v in vectors][:5]}...")
        return vectors

    raise EmbeddingError(f"Gave up after {MAX_RETRIES} attempts")


def vector_literal(vector: list[float]) -> str:
    """A CQL vector literal, ready to paste into a query."""
    return "[" + ", ".join(f"{x:.6g}" for x in vector) + "]"
