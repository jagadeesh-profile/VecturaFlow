"""
VecturaFlow — Ingestion pipeline data models.
Shared across parser, chunker, and Lambda handlers.
Using dataclasses (not Pydantic) to keep Lambda package size small.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TextBlock:
    """
    A single extracted text unit from a parsed document.
    Preserves source position metadata — page, section, row.
    """
    text: str
    doc_id: str
    source: str                          # S3 key e.g. "docs/report.pdf"
    file_type: str                       # pdf | docx | csv | txt | json
    page: int | None = None              # PDF page number (1-indexed)
    section: str | None = None          # DOCX heading / section name
    row: int | None = None              # CSV row index
    element_type: str | None = None     # e.g. "NarrativeText", "Title", "Table"

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Chunk:
    """
    A text chunk ready for embedding.
    Inherits position metadata from the source TextBlock.
    """
    chunk_id: str                        # "{doc_id}_chunk_{global_index}"
    doc_id: str
    text: str
    source: str
    chunk_index: int                     # global index across all chunks in doc
    total_chunks: int
    file_type: str
    page: int | None = None
    section: str | None = None
    row: int | None = None
    element_type: str | None = None

    def to_sqs_message(self) -> dict[str, Any]:
        """Serialisable dict for SQS MessageBody."""
        return {k: v for k, v in self.__dict__.items() if v is not None}
