#!/usr/bin/env python3
"""
VecturaFlow — Retrieval verification utility.

Embeds a question with the same model used by ingestion, queries Pinecone,
prints the retrieved chunks, and asks the LLM to answer from the retrieved
context.

Usage:
    python scripts/verify_retrieval.py "What does the document say about vectors?"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from openai import OpenAI
from pinecone import Pinecone

from api.config import settings


SYSTEM_PROMPT = (
    "You are a precise question-answering assistant. "
    "Answer only from the provided context. "
    "If the context is insufficient, say so clearly."
)


def _embed_question(question: str) -> list[float]:
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.embeddings.create(
        model=settings.embedding_model,
        input=[question],
    )
    return response.data[0].embedding


def _query_pinecone(vector: list[float], top_k: int = 5) -> list[Any]:
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index)
    results = index.query(
        vector=vector,
        top_k=top_k,
        include_metadata=True,
        include_values=False,
    )
    return list(results.matches or [])


def _format_context(matches: list[Any]) -> str:
    blocks = []
    for match in matches:
        metadata = match.metadata or {}
        source = metadata.get("source", "unknown")
        page = metadata.get("page")
        chunk_id = getattr(match, "id", "unknown")
        snippet = (metadata.get("text", "") or "")[:300].replace("\n", " ")
        if page is not None:
            source = f"{source} (page {page})"
        blocks.append(
            f"[chunk_id={chunk_id} score={match.score:.4f} source={source}]\n{snippet}"
        )
    return "\n\n---\n\n".join(blocks)


def _answer_question(question: str, context: str) -> str:
    client = OpenAI(api_key=settings.openai_api_key)
    user_prompt = (
        f"Context:\n{context or '[no retrieved context]'}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )
    response = client.chat.completions.create(
        model=settings.generation_model,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", help="The user question to test against the corpus")
    args = parser.parse_args()

    question = args.question.strip()
    if not question:
        print("Question is empty.")
        return 1

    print("Embedding question with project config...")
    print(f"  index          : {settings.pinecone_index}")
    print(f"  embedding model: {settings.embedding_model}")
    print(f"  answer model   : {settings.generation_model}")

    vector = _embed_question(question)
    matches = _query_pinecone(vector, top_k=5)
    threshold = settings.retrieval_score_threshold
    retrieval_happened = bool(matches) and any(match.score >= threshold for match in matches)

    print("\nRetrieval status:")
    print(f"  matches found      : {len(matches)}")
    print(f"  retrieval happened  : {retrieval_happened}")
    print(f"  score threshold    : {threshold}")

    if matches:
        print("\nTop matches:")
        for match in matches:
            metadata = match.metadata or {}
            source = metadata.get("source", "unknown")
            snippet = (metadata.get("text", "") or "")[:180].replace("\n", " ")
            print(f"  id={match.id}")
            print(f"    score  : {match.score:.4f}")
            print(f"    source : {source}")
            print(f"    snippet: {snippet}")

    context = _format_context(matches[: settings.retrieval_top_k])
    print("\nCalling LLM with retrieved context...\n")
    answer = _answer_question(question, context)

    print("Final answer:")
    print(answer)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())