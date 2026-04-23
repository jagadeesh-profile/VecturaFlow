"""
VecturaFlow — Pydantic v2 schemas.
OpenAI-compatible request/response models + internal types.
"""
from __future__ import annotations

from enum import StrEnum
import time
from typing import Any, Literal
import uuid

from pydantic import BaseModel, Field, field_validator

# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class Role(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"


class Confidence(StrEnum):
    high = "high"
    low = "low"
    no_context = "no_context"


# ─────────────────────────────────────────────────────────────────────────────
# Request models
# ─────────────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: Role
    content: str = Field(..., min_length=1, max_length=32_000)


class ChatRequest(BaseModel):
    """OpenAI-compatible /v1/chat/completions request body."""
    model: str = Field(default="vecturaflow", description="Model identifier (ignored, always uses VecturaFlow RAG)")
    messages: list[ChatMessage] = Field(..., min_length=1)
    stream: bool = Field(default=False, description="Streaming not yet supported — always False")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)
    # Optional VecturaFlow-specific metadata
    filters: dict[str, Any] | None = Field(default=None, description="Pinecone metadata filters e.g. {source: 'report.pdf'}")

    @field_validator("messages")
    @classmethod
    def must_have_user_message(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        if not any(m.role == Role.user for m in v):
            raise ValueError("At least one message with role='user' is required")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Response models
# ─────────────────────────────────────────────────────────────────────────────

class ResponseMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class Choice(BaseModel):
    index: int = 0
    message: ResponseMessage
    finish_reason: Literal["stop", "length", "error"] = "stop"


class SourceCitation(BaseModel):
    doc_id: str
    source: str
    score: float = Field(ge=0.0, le=1.0)
    chunk_index: int = 0
    page: int | None = None
    section: str | None = None


class UsageMetadata(BaseModel):
    """Extended usage — includes VecturaFlow-specific fields alongside token counts."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    sources: list[SourceCitation] = Field(default_factory=list)
    confidence: Confidence = Confidence.no_context
    latency_ms: int = 0


class ChatResponse(BaseModel):
    """OpenAI-compatible response with VecturaFlow extensions in `usage`."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "vecturaflow"
    choices: list[Choice]
    usage: UsageMetadata = Field(default_factory=UsageMetadata)


# ─────────────────────────────────────────────────────────────────────────────
# Error models
# ─────────────────────────────────────────────────────────────────────────────

class ErrorDetail(BaseModel):
    message: str
    type: str
    code: str | None = None


class ErrorResponse(BaseModel):
    """Matches OpenAI error response shape for client compatibility."""
    error: ErrorDetail


# ─────────────────────────────────────────────────────────────────────────────
# Internal RAG state (used by RAGAgent / LangGraph)
# ─────────────────────────────────────────────────────────────────────────────

class RetrievedChunk(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    source: str
    score: float
    chunk_index: int = 0
    page: int | None = None
    section: str | None = None
    low_confidence: bool = False


class RAGState(BaseModel):
    """Typed state passed through LangGraph nodes."""
    query: str
    sub_queries: list[str] = Field(default_factory=list)
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    answer: str = ""
    sources: list[SourceCitation] = Field(default_factory=list)
    confidence: Confidence = Confidence.no_context
    filters: dict[str, Any] | None = None
