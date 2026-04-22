#!/usr/bin/env python3
"""
VecturaFlow — preflight OpenAI credential check.

Fails fast if OPENAI_API_KEY is missing or still looks like a placeholder.
Uses the project config and performs a tiny embedding request to validate the
key is real and usable.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from api.config import settings
from openai import OpenAI


def _fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def _validate_key(key: str) -> None:
    placeholders = {"", "sk-...", "sk-test", "YOUR_OPENAI_API_KEY", "changeme"}
    if key in placeholders or key.startswith("sk-..."):
        _fail("OPENAI_API_KEY is missing or still a placeholder. Update your .env file.")


def main() -> int:
    _validate_key(settings.openai_api_key)

    print("OpenAI preflight check")
    print(f"  model : {settings.embedding_model}")

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.embeddings.create(
        model=settings.embedding_model,
        input=["VecturaFlow preflight check"],
    )
    dimension = len(response.data[0].embedding)
    print(f"  status: OK (embedding dimension={dimension})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())