"""
Gemini text generation, used by the week 6 movie assistant in app/main.py.

Calls Google's `generateContent` endpoint with the standard library only, the
same way tools/embeddings.py calls the embedding endpoint. The API key goes in
a request header, so it never appears in a URL or an error message.

This helper is for a web request, which can't sit waiting for Google, so
there's no retry loop. When Google answers HTTP 429, generate_text() raises
DailyLimitReached or RateLimited straight away, the two exceptions that
tools/embeddings.py defines, and any other failure raises LLMError.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from tools.embeddings import (
    API_BASE,
    DEFAULT_RETRY_SECONDS,
    DailyLimitReached,
    RateLimited,
    _is_daily_limit,
    _retry_seconds,
)

# Google retires model names from time to time. If this one starts returning
# HTTP 404, check the Gemini models page for the current Flash-Lite name.
MODEL = "gemini-3.5-flash-lite"


class LLMError(RuntimeError):
    """Any failure from the Gemini API other than a 429."""


def generate_text(api_key: str, system_prompt: str, user_prompt: str) -> str:
    """Ask Gemini to answer user_prompt under the rules in system_prompt."""
    body = json.dumps(
        {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": 512},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/models/{MODEL}:generateContent",
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
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
            raise RateLimited(_retry_seconds(error_body) or DEFAULT_RETRY_SECONDS) from e
        message = error_body.get("error", {}).get("message", raw[:300])
        raise LLMError(f"Gemini returned HTTP {e.code}: {message}") from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError(f"Couldn't reach Gemini: {e}") from e

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise LLMError("Gemini's answer didn't contain any text") from e
