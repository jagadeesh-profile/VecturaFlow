"""
VecturaFlow — RAGAgent (LangGraph).

A 4-node StateGraph that orchestrates query decomposition, retrieval,
answer generation, and confidence validation.

Graph topology:
  decompose_query → retrieve_context → generate_answer → validate_answer → END

Nodes:
  decompose_query  — split complex multi-part queries into sub-queries using GPT-4o mini
  retrieve_context — call RetrieverAgent for each sub-query, merge + deduplicate
  generate_answer  — build grounded prompt, call GPT-4o mini for final answer
  validate_answer  — check answer is grounded; downgrade confidence if not

All state is carried in AgentState (TypedDict) between nodes.
The compiled graph is exposed as `rag_agent` for use by api/main.py.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from api.config import settings
from api.logger import logger
from api.retriever import retrieve
from api.schemas import Confidence, RetrievedChunk, SourceCitation

# ─────────────────────────────────────────────────────────────────────────────
# LLM — lazy singleton so imports don't require real OpenAI creds
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.generation_model,
        temperature=0,
        api_key=settings.openai_api_key,
        request_timeout=25,
        max_retries=1,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Graph state
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    query: str
    sub_queries: list[str]
    chunks: list[dict]          # serialised RetrievedChunk dicts
    answer: str
    sources: list[dict]         # serialised SourceCitation dicts
    confidence: str
    filters: dict[str, Any] | None


# ─────────────────────────────────────────────────────────────────────────────
# Node helpers
# ─────────────────────────────────────────────────────────────────────────────

_DECOMPOSE_SYSTEM = """You are a query decomposition assistant.
Your job: decide if a question needs to be split into simpler sub-questions.

Rules:
- If the question is self-contained and asks ONE thing → return it unchanged on a single line.
- If it asks MULTIPLE distinct things (connected by "and", "also", contains multiple "?") →
  split it into 2-4 focused sub-questions, one per line.
- Return ONLY the question(s), no preamble, no numbering, no explanation.

Examples:
  Input:  "What is the refund policy?"
  Output: "What is the refund policy?"

  Input:  "What is the refund policy and how long does shipping take?"
  Output: "What is the refund policy?
How long does shipping take?"
"""

_ANSWER_SYSTEM = """You are a precise question-answering assistant.
Answer the user's question using ONLY the provided context.
- Be concise and factual.
- If the context does not contain enough information, say so clearly.
- Do NOT fabricate information not present in the context.
- Cite sources naturally within your answer when relevant.
"""

_VALIDATE_SYSTEM = """You are a grounding validator.
Given an answer and the context it was generated from, determine if the answer
is grounded in the context.

Reply with exactly one word:
  GROUNDED   — the answer is supported by the context
  UNGROUNDED — the answer contains claims not in the context
"""


# ─────────────────────────────────────────────────────────────────────────────
# Nodes
# ─────────────────────────────────────────────────────────────────────────────

def decompose_query(state: AgentState) -> AgentState:
    """
    Use GPT-4o mini to decide if the query should be split into sub-queries.
    Falls back to passing the original query through if the LLM call fails.
    """
    query = state["query"]
    try:
        response = _llm().invoke([
            SystemMessage(content=_DECOMPOSE_SYSTEM),
            HumanMessage(content=query),
        ])
        lines = [line.strip() for line in response.content.strip().splitlines() if line.strip()]
        sub_queries = lines if lines else [query]
    except Exception as exc:
        logger.warning("agent.decompose_failed", error=str(exc))
        sub_queries = [query]

    logger.info("agent.decomposed", original=query, sub_queries=sub_queries)
    state["sub_queries"] = sub_queries
    return state


def retrieve_context(state: AgentState) -> AgentState:
    """
    Call RetrieverAgent for each sub-query in sequence.
    Deduplicates chunks by chunk_id, keeps top 5 by score.
    """
    seen_ids: set[str] = set()
    all_chunks: list[RetrievedChunk] = []
    filters = state.get("filters")

    for sq in state["sub_queries"]:
        try:
            chunks = retrieve(sq, top_k=settings.retrieval_top_k, filters=filters)
            for chunk in chunks:
                if chunk.chunk_id not in seen_ids:
                    all_chunks.append(chunk)
                    seen_ids.add(chunk.chunk_id)
        except Exception as exc:
            logger.warning("agent.retrieve_failed", sub_query=sq, error=str(exc))

    # Sort by score descending, keep top 5
    all_chunks.sort(key=lambda c: c.score, reverse=True)
    top_chunks = all_chunks[:settings.retrieval_top_k]

    logger.info(
        "agent.retrieved",
        total_chunks=len(all_chunks),
        kept=len(top_chunks),
        low_confidence=any(c.low_confidence for c in top_chunks),
    )

    state["chunks"] = [c.model_dump() for c in top_chunks]
    return state


def generate_answer(state: AgentState) -> AgentState:
    """
    Build a grounded prompt from retrieved chunks and call GPT-4o mini.
    If no context is available, returns a honest no-context response.
    """
    chunks = state["chunks"]

    if not chunks:
        state["answer"] = (
            "I don't have enough information in the knowledge base to answer this question."
        )
        state["confidence"] = Confidence.no_context.value
        state["sources"] = []
        return state

    # Build context block
    context_lines = []
    for c in chunks:
        source_label = c.get("source", "unknown")
        page = c.get("page")
        section = c.get("section")
        location = []
        if page is not None:
            location.append(f"page {page}")
        if section:
            location.append(f"section {section}")
        if location:
            source_label = f"{source_label} ({', '.join(location)})"
        context_lines.append(f"[{source_label}]\n{c['text']}")
    context = "\n\n---\n\n".join(context_lines)

    user_prompt = (
        f"Context:\n{context}\n\n"
        f"Question: {state['query']}\n\n"
        "Answer:"
    )

    try:
        response = _llm().invoke([
            SystemMessage(content=_ANSWER_SYSTEM),
            HumanMessage(content=user_prompt),
        ])
        answer = response.content.strip()
    except Exception as exc:
        logger.error("agent.generate_failed", error=str(exc), exc_info=True)
        state["answer"] = "I encountered an error generating the answer. Please try again."
        state["confidence"] = Confidence.no_context.value
        state["sources"] = []
        return state

    # Build source citations
    sources = []
    seen_sources: set[str] = set()
    for c in chunks:
        key = f"{c.get('doc_id', '')}_{c.get('chunk_index', 0)}"
        if key not in seen_sources:
            sources.append(
                SourceCitation(
                    doc_id=c.get("doc_id", ""),
                    source=c.get("source", ""),
                    score=c.get("score", 0.0),
                    chunk_index=c.get("chunk_index", 0),
                    page=c.get("page"),
                    section=c.get("section"),
                ).model_dump()
            )
            seen_sources.add(key)

    has_low_confidence = any(c.get("low_confidence", False) for c in chunks)
    state["answer"] = answer
    state["sources"] = sources
    state["confidence"] = (
        Confidence.low.value if has_low_confidence else Confidence.high.value
    )
    return state


def validate_answer(state: AgentState) -> AgentState:
    """
    Ask GPT-4o mini to verify the answer is grounded in the retrieved context.
    Downgrades confidence to 'low' if the answer contains ungrounded claims.
    Skips validation if there is no context (already marked no_context).
    """
    if state["confidence"] == Confidence.no_context.value or not state["chunks"]:
        return state

    context_snippet = " ".join(c["text"] for c in state["chunks"])[:2000]
    validation_prompt = (
        f"Context:\n{context_snippet}\n\n"
        f"Answer:\n{state['answer']}"
    )

    try:
        response = _llm().invoke([
            SystemMessage(content=_VALIDATE_SYSTEM),
            HumanMessage(content=validation_prompt),
        ])
        verdict = response.content.strip().upper()
        if "UNGROUNDED" in verdict:
            logger.info("agent.validation_ungrounded", answer_snippet=state["answer"][:100])
            state["confidence"] = Confidence.low.value
    except Exception as exc:
        # Validation failure is non-fatal — keep existing confidence
        logger.warning("agent.validate_failed", error=str(exc))

    return state


# ─────────────────────────────────────────────────────────────────────────────
# Graph assembly
# ─────────────────────────────────────────────────────────────────────────────

_graph = StateGraph(AgentState)

_graph.add_node("decompose", decompose_query)
_graph.add_node("retrieve", retrieve_context)
_graph.add_node("generate", generate_answer)
_graph.add_node("validate", validate_answer)

_graph.set_entry_point("decompose")
_graph.add_edge("decompose", "retrieve")
_graph.add_edge("retrieve", "generate")
_graph.add_edge("generate", "validate")
_graph.add_edge("validate", END)

rag_agent = _graph.compile()


# ─────────────────────────────────────────────────────────────────────────────
# Convenience wrapper — used by api/main.py
# ─────────────────────────────────────────────────────────────────────────────

def run_rag(
    query: str,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run the RAG agent and return a plain dict with answer, sources, confidence.

    Args:
        query:   The user's question.
        filters: Optional Pinecone metadata filters.

    Returns:
        {
            "answer":     str,
            "sources":    list[dict],   # SourceCitation dicts
            "confidence": str           # "high" | "low" | "no_context"
        }
    """
    initial_state: AgentState = {
        "query": query,
        "sub_queries": [],
        "chunks": [],
        "answer": "",
        "sources": [],
        "confidence": Confidence.no_context.value,
        "filters": filters,
    }

    result = rag_agent.invoke(initial_state)

    return {
        "answer": result["answer"],
        "sources": result["sources"],
        "confidence": result["confidence"],
    }
