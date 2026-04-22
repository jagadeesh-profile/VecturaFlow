#!/usr/bin/env python3
"""
VecturaFlow — End-to-end RAG verification.

Proves the full ingest → embed → Pinecone retrieval → LLM answer path against
the already-ingested PDF corpus.

Behavior:
  - Loads .env automatically
  - Fails fast if OPENAI_API_KEY is missing or still placeholder-like
  - Uses the project's configured embedding + generation models
  - Queries Pinecone top_k=5 with metadata
  - Prints each match and a final PASS/FAIL verdict per question

Usage:
    python scripts/verify_end_to_end.py
    python scripts/verify_end_to_end.py "What does the PDF say about X?"
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass
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


@dataclass
class QuestionResult:
    question_type: str
    question: str
    in_corpus: bool
    top_score: float
    second_score: float
    passed: bool
    reason: str
    matches: int
    answer: str


@dataclass
class QuestionSpec:
    question_type: str
    question: str
    in_corpus: bool


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value is not None and value != "" else default


def _fail(msg: str) -> None:
    raise SystemExit(f"ERROR: {msg}")


def _require_openai_key() -> str:
    key = _env("OPENAI_API_KEY")
    if not key:
        _fail("OPENAI_API_KEY is missing. Set it in .env or the environment.")
    placeholders = {"sk-...", "sk-test", "YOUR_OPENAI_API_KEY", "changeme"}
    if key in placeholders or key.startswith("sk-..."):
        _fail("OPENAI_API_KEY still looks like an old placeholder value. Replace it in .env.")
    return key


def _pinecone_config() -> tuple[str, str]:
    api_key = _env("PINECONE_API_KEY")
    if not api_key:
        _fail("PINECONE_API_KEY is missing. Set it in .env or the environment.")
    index_name = _env("PINECONE_INDEX", "vecturaflow")
    return api_key, index_name


def _embedding_model() -> str:
    return _env("EMBEDDING_MODEL", "text-embedding-3-small")


def _generation_model() -> str:
    return _env("GENERATION_MODEL", "gpt-4o-mini")


def _retrieve_threshold() -> float:
    raw_threshold = os.getenv("RETRIEVAL_THRESHOLD", "0.40")
    try:
        return float(raw_threshold)
    except ValueError:
        return 0.40


def _floor_to_step(value: float, step: float = 0.05) -> float:
    if step <= 0:
        return value
    return round(math.floor(value / step) * step, 2)


def _embed_question(client: OpenAI, question: str) -> list[float]:
    response = client.embeddings.create(
        model=_embedding_model(),
        input=[question],
    )
    return response.data[0].embedding


def _describe_index_dimension(index: Any) -> Any:
    stats = index.describe_index_stats()
    return getattr(stats, "dimension", None)


def _query_pinecone(index: Any, vector: list[float], top_k: int = 5) -> list[Any]:
    results = index.query(
        vector=vector,
        top_k=top_k,
        include_metadata=True,
        include_values=False,
    )
    return list(results.matches or [])


def _snippet(text: str, limit: int = 200) -> str:
    return (text or "")[:limit].replace("\n", " ").strip()


def _context_from_matches(matches: list[Any]) -> str:
    blocks: list[str] = []
    for match in matches:
        metadata = match.metadata or {}
        source = metadata.get("source", "unknown")
        page = metadata.get("page")
        label = source if page is None else f"{source} (page {page})"
        blocks.append(
            f"[chunk_id={match.id} score={match.score:.4f} source={label}]\n"
            f"{metadata.get('text', '')}"
        )
    return "\n\n---\n\n".join(blocks)


def _answer_question(client: OpenAI, question: str, context: str) -> Any:
    system = (
        "You answer only from the provided documents. "
        "If the context is insufficient, say exactly: Not found in the provided documents."
    )
    user = (
        f"Context:\n{context or '[no retrieved context]'}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )
    return client.chat.completions.create(
        model=_generation_model(),
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )


def _build_prompt(question: str, context: str) -> tuple[str, str]:
    system = (
        "You answer only from the provided documents. "
        "If the context is insufficient, say exactly: Not found in the provided documents."
    )
    user = (
        f"Context:\n{context or '[no retrieved context]'}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )
    return system, user


def _evidence_terms(matches: list[Any]) -> list[str]:
    terms: list[str] = []
    for match in matches:
        text = (match.metadata or {}).get("text", "") or ""
        for term in re.findall(r"\b(?:\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9_-]{4,})\b", text):
            lowered = term.lower()
            if lowered not in {"the", "this", "that", "with", "from", "about", "which", "their", "there", "where"}:
                terms.append(term)
        if len(terms) >= 20:
            break
    # Keep only unique terms, preserve order.
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(term)
    return deduped[:10]


def _answer_contains_specifics(answer: str, matches: list[Any]) -> bool:
    answer_lower = answer.lower()
    for term in _evidence_terms(matches):
        if term.lower() in answer_lower:
            return True
    return False


def _default_questions() -> list[QuestionSpec]:
    return [
        QuestionSpec(
            question_type="in_corpus_deep_learning",
            question="According to the PDF, what is one specific topic or fact discussed in the deep learning notes?",
            in_corpus=True,
        ),
        QuestionSpec(
            question_type="in_corpus_program_guide",
            question="What does the PDF say about deep learning and Pinecone?",
            in_corpus=True,
        ),
        QuestionSpec(
            question_type="out_of_corpus_laptop_battery",
            question="What is the warranty period for a laptop battery?",
            in_corpus=False,
        ),
    ]


def _run_question(
    client: OpenAI,
    index: Any,
    spec: QuestionSpec,
    threshold: float,
    debug: bool = False,
) -> QuestionResult:
    print("\n" + "=" * 86)
    print(f"QUESTION TYPE: {spec.question_type}")
    print(f"QUESTION: {spec.question}")
    print("=" * 86)

    vector = _embed_question(client, spec.question)
    index_dimension = _describe_index_dimension(index)
    matches = _query_pinecone(index, vector, top_k=5)
    top_score = float(matches[0].score) if matches else 0.0
    second_score = float(matches[1].score) if len(matches) > 1 else 0.0

    print("\nRetrieved matches:")
    if not matches:
        print("  (none)")
    for match in matches:
        metadata = match.metadata or {}
        source = metadata.get("source", "unknown")
        page = metadata.get("page", "-")
        snippet = metadata.get("text", "") if debug else _snippet(metadata.get("text", ""), 200)
        print(f"  score={match.score:.4f} source={source} page={page}")
        print(f"    id     : {match.id}")
        print(f"    snippet: {snippet}")

    context = _context_from_matches(matches[:5])
    if debug:
        system_prompt, user_prompt = _build_prompt(spec.question, context)
        print("\nDebug details:")
        print(f"  query embedding dimension: {len(vector)}")
        print(f"  index dimension          : {index_dimension}")
        print("\n  exact system prompt:")
        print(system_prompt)
        print("\n  exact user prompt:")
        print(user_prompt)
    print("\nCalling LLM with retrieved context...\n")
    llm_response = _answer_question(client, spec.question, context)
    answer = (llm_response.choices[0].message.content or "").strip()
    print("Final answer:")
    print(answer)
    if debug:
        print("\nRaw OpenAI response:")
        try:
            print(llm_response.model_dump())
        except Exception:
            print(llm_response)

    high_score = any(match.score >= threshold for match in matches)
    specifics = _answer_contains_specifics(answer, matches)
    refusal = "not found in the provided documents" in answer.lower()

    passed = bool(matches) and high_score and specifics and not refusal
    if passed:
        reason = "matches above threshold and answer contains context specifics"
    else:
        reasons: list[str] = []
        if not matches:
            reasons.append("no matches returned")
        elif not high_score:
            reasons.append(f"no match score above {threshold:.2f}")
        if not specifics:
            reasons.append("answer did not reuse retrieved specifics")
        if refusal:
            reasons.append("model refused due to insufficient context")
        reason = "; ".join(reasons) if reasons else "failed checks"

    print("\nVerdict:")
    print(f"  {'PASS' if passed else 'FAIL'} - {reason}")
    print("=" * 86)

    return QuestionResult(
        question_type=spec.question_type,
        question=spec.question,
        in_corpus=spec.in_corpus,
        top_score=top_score,
        second_score=second_score,
        passed=passed,
        reason=reason,
        matches=len(matches),
        answer=answer,
    )


def _calibration_floor(results: list[QuestionResult]) -> float:
    in_corpus_scores = [result.top_score for result in results if result.in_corpus]
    out_of_corpus_scores = [result.top_score for result in results if not result.in_corpus]
    if not in_corpus_scores or not out_of_corpus_scores:
        return _floor_to_step(_retrieve_threshold())

    midpoint = (min(in_corpus_scores) + max(out_of_corpus_scores)) / 2
    return _floor_to_step(midpoint)


def _print_calibration_summary(results: list[QuestionResult]) -> None:
    print("\nCalibration summary:")
    print("| question_type | top_score | 2nd_score | suggested_threshold_floor |")
    print("|---|---:|---:|---:|")
    for result in results:
        suggested_floor = _floor_to_step(result.top_score)
        print(
            f"| {result.question_type} | {result.top_score:.4f} | {result.second_score:.4f} | {suggested_floor:.2f} |"
        )

    print(f"Suggested RETRIEVAL_THRESHOLD: {_calibration_floor(results):.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end RAG verification against Pinecone and OpenAI")
    parser.add_argument("question", nargs="*", help="Question to ask; if omitted, uses 3 default test questions.")
    parser.add_argument("--debug", action="store_true", help="Print retrieval, prompt, and raw LLM diagnostics")
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Run the default questions with debug output and print a threshold calibration summary",
    )
    args = parser.parse_args()

    openai_key = _require_openai_key()
    pinecone_key, index_name = _pinecone_config()

    print("VecturaFlow RAG end-to-end verification")
    print(f"  embedding model   : {_embedding_model()}")
    print(f"  generation model   : {_generation_model()}")
    print(f"  pinecone index     : {index_name}")
    print(f"  retrieval threshold: {_retrieve_threshold():.2f}")

    openai_client = OpenAI(api_key=openai_key)
    pc = Pinecone(api_key=pinecone_key)
    index = pc.Index(index_name)

    if args.calibrate:
        if args.question:
            print("Calibration mode ignores custom positional questions and uses the 3 defaults.")
        questions = _default_questions()
        debug = True
    else:
        questions = [
            QuestionSpec(question_type="custom_question", question=" ".join(args.question).strip(), in_corpus=True)
        ] if args.question else _default_questions()
        debug = args.debug

    questions = [question for question in questions if question.question]
    if not questions:
        _fail("No question provided and no default questions available.")

    results: list[QuestionResult] = []
    threshold = _retrieve_threshold()
    for question in questions:
        results.append(_run_question(openai_client, index, question, threshold, debug=debug))

    if args.calibrate:
        _print_calibration_summary(results)

    passed = sum(1 for result in results if result.passed)
    total = len(results)
    print("\n" + "#" * 86)
    print(f"RAG pipeline verified: {passed}/{total} questions passed")
    print("#" * 86)

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())