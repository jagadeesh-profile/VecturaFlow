"""
VecturaFlow — ParserAgent implementation.
Extracts clean TextBlock list from PDF, DOCX, CSV, TXT, JSON.

Design decisions:
  - unstructured.io is primary for PDF/DOCX (handles scanned + digital)
  - PyMuPDF is fallback for PDF when unstructured fails or is unavailable
  - Each parser returns List[TextBlock] — page/section metadata preserved
  - Text cleaning is centralised in _clean_text() — applied uniformly
  - No S3 download here — caller passes file_bytes (easier to test)
"""
from __future__ import annotations

import io
import json
import logging
import re
from typing import Any
import unicodedata

import pandas as pd

from ingestion.models import TextBlock

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Text cleaning
# ─────────────────────────────────────────────────────────────────────────────

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _clean_text(text: str) -> str:
    """
    Normalise and clean extracted text.
    Order matters: decode → strip control chars → normalise whitespace.
    """
    if not text:
        return ""
    # Normalise unicode (e.g. ligatures, fancy quotes → ASCII equivalents)
    text = unicodedata.normalize("NFKC", text)
    # Strip non-printable control chars (keep \t \n)
    text = _CONTROL_CHAR_RE.sub("", text)
    # Collapse runs of spaces/tabs
    text = _MULTI_SPACE_RE.sub(" ", text)
    # Collapse excessive blank lines
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _is_meaningful(text: str, min_chars: int = 10) -> bool:
    """Skip blocks that are just whitespace, page numbers, or very short noise."""
    cleaned = text.strip()
    if len(cleaned) < min_chars:
        return False
    # Skip pure numeric strings (likely page numbers)
    if cleaned.isdigit():
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Format-specific parsers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_pdf(file_bytes: bytes, doc_id: str, source: str) -> list[TextBlock]:
    """
    Primary: unstructured.partition_pdf — handles both digital and scanned PDFs.
    Fallback: PyMuPDF (fitz) — fast, reliable for digital PDFs.
    """
    blocks: list[TextBlock] = []

    # ── Try unstructured first ─────────────────────────────────────────────
    try:
        from unstructured.partition.pdf import partition_pdf
        elements = partition_pdf(file=io.BytesIO(file_bytes))
        for el in elements:
            text = _clean_text(str(el))
            if not _is_meaningful(text):
                continue
            page = getattr(el.metadata, "page_number", None)
            section = getattr(el.metadata, "section", None)
            el_type = type(el).__name__  # NarrativeText, Title, Table, etc.
            blocks.append(TextBlock(
                text=text, doc_id=doc_id, source=source,
                file_type="pdf", page=page, section=section, element_type=el_type,
            ))
        if blocks:
            logger.info("parser.pdf_unstructured doc_id=%s blocks=%d", doc_id, len(blocks))
            return blocks
        logger.warning("parser.pdf_unstructured_empty doc_id=%s — falling back to PyMuPDF", doc_id)
    except Exception as exc:
        logger.warning("parser.pdf_unstructured_failed doc_id=%s error=%s — falling back", doc_id, exc)

    # ── Fallback: PyMuPDF ─────────────────────────────────────────────────
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            raw = page.get_text("text")
            text = _clean_text(raw)
            if not _is_meaningful(text):
                continue
            # Split page text into paragraphs for finer granularity
            for para in text.split("\n\n"):
                para = para.strip()
                if _is_meaningful(para):
                    blocks.append(TextBlock(
                        text=para, doc_id=doc_id, source=source,
                        file_type="pdf", page=page_num + 1, element_type="NarrativeText",
                    ))
        logger.info("parser.pdf_pymupdf doc_id=%s blocks=%d", doc_id, len(blocks))
        return blocks
    except Exception as exc:
        logger.error("parser.pdf_pymupdf_failed doc_id=%s error=%s", doc_id, exc)
        raise


def _parse_docx(file_bytes: bytes, doc_id: str, source: str) -> list[TextBlock]:
    """
    Primary: unstructured.partition_docx — preserves headings, tables, lists.
    Fallback: python-docx paragraph iteration.
    """
    blocks: list[TextBlock] = []

    # ── Try unstructured first ─────────────────────────────────────────────
    try:
        from unstructured.partition.docx import partition_docx
        elements = partition_docx(file=io.BytesIO(file_bytes))
        current_section: str | None = None
        for el in elements:
            el_type = type(el).__name__
            text = _clean_text(str(el))
            if not _is_meaningful(text):
                continue
            # Track section from Title elements
            if el_type == "Title":
                current_section = text
            blocks.append(TextBlock(
                text=text, doc_id=doc_id, source=source,
                file_type="docx", section=current_section, element_type=el_type,
            ))
        if blocks:
            logger.info("parser.docx_unstructured doc_id=%s blocks=%d", doc_id, len(blocks))
            return blocks
        logger.warning("parser.docx_unstructured_empty doc_id=%s — falling back to python-docx", doc_id)
    except Exception as exc:
        logger.warning("parser.docx_unstructured_failed doc_id=%s error=%s — falling back", doc_id, exc)

    # ── Fallback: python-docx ─────────────────────────────────────────────
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        current_section: str | None = None
        for para in doc.paragraphs:
            text = _clean_text(para.text)
            if not _is_meaningful(text):
                continue
            style = para.style.name if para.style else ""
            el_type = "Title" if "Heading" in style else "NarrativeText"
            if el_type == "Title":
                current_section = text
            blocks.append(TextBlock(
                text=text, doc_id=doc_id, source=source,
                file_type="docx", section=current_section, element_type=el_type,
            ))
        logger.info("parser.docx_python_docx doc_id=%s blocks=%d", doc_id, len(blocks))
        return blocks
    except Exception as exc:
        logger.error("parser.docx_fallback_failed doc_id=%s error=%s", doc_id, exc)
        raise


def _parse_csv(file_bytes: bytes, doc_id: str, source: str) -> list[TextBlock]:
    """
    Converts each CSV row to a natural-language text block.
    Handles encoding detection — tries UTF-8, falls back to latin-1.
    Skips rows where all values are null.
    """
    blocks: list[TextBlock] = []

    df = None
    for encoding in ("utf-8", "latin-1"):
        try:
            df = pd.read_csv(
                io.BytesIO(file_bytes),
                encoding=encoding,
                dtype=str,
                on_bad_lines="skip",   # tolerate rows with mismatched column counts
                engine="python",       # python engine supports on_bad_lines callable
            )
            break
        except UnicodeDecodeError:
            if encoding == "latin-1":
                raise
            logger.debug(
                "parser.csv_utf8_failed", doc_id=doc_id, fallback="latin-1",
            )
    if df is None:
        return blocks

    # Drop completely empty rows
    df = df.dropna(how="all")

    columns = df.columns.tolist()
    for row_idx, row in df.iterrows():
        # Build "col: value | col: value" representation
        parts = []
        for col in columns:
            val = str(row[col]).strip() if pd.notna(row[col]) else ""
            if val and val.lower() not in ("nan", "none", ""):
                parts.append(f"{col}: {val}")

        if not parts:
            continue

        text = _clean_text(" | ".join(parts))
        if not _is_meaningful(text):
            continue

        blocks.append(TextBlock(
            text=text, doc_id=doc_id, source=source,
            file_type="csv", row=int(row_idx), element_type="TableRow",
        ))

    logger.info("parser.csv doc_id=%s rows=%d blocks=%d", doc_id, len(df), len(blocks))
    return blocks


def _parse_txt(file_bytes: bytes, doc_id: str, source: str) -> list[TextBlock]:
    """
    Splits plain text into paragraphs (double-newline separated).
    Encoding: UTF-8 with latin-1 fallback.
    """
    blocks: list[TextBlock] = []

    for encoding in ("utf-8", "latin-1"):
        try:
            raw = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            if encoding == "latin-1":
                raw = file_bytes.decode("latin-1", errors="replace")

    paragraphs = re.split(r"\n\s*\n", raw)
    for i, para in enumerate(paragraphs):
        text = _clean_text(para)
        if _is_meaningful(text):
            blocks.append(TextBlock(
                text=text, doc_id=doc_id, source=source,
                file_type="txt", element_type="NarrativeText",
            ))

    logger.info("parser.txt doc_id=%s blocks=%d", doc_id, len(blocks))
    return blocks


def _parse_json(file_bytes: bytes, doc_id: str, source: str) -> list[TextBlock]:
    """
    Flattens JSON into text blocks.
    Supports: single object, array of objects, nested (1 level deep).
    """
    blocks: list[TextBlock] = []

    try:
        data = json.loads(file_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    items: list[Any] = data if isinstance(data, list) else [data]

    for row_idx, item in enumerate(items):
        if not isinstance(item, dict):
            text = _clean_text(str(item))
            if _is_meaningful(text):
                blocks.append(TextBlock(
                    text=text, doc_id=doc_id, source=source,
                    file_type="json", row=row_idx, element_type="NarrativeText",
                ))
            continue

        parts = []
        for k, v in item.items():
            if isinstance(v, (str, int, float, bool)) and v is not None:
                parts.append(f"{k}: {v}")
            elif isinstance(v, dict):
                # One level of nesting
                nested = ", ".join(f"{nk}: {nv}" for nk, nv in v.items()
                                   if isinstance(nv, (str, int, float)))
                if nested:
                    parts.append(f"{k}: {nested}")

        if parts:
            text = _clean_text(" | ".join(parts))
            if _is_meaningful(text):
                blocks.append(TextBlock(
                    text=text, doc_id=doc_id, source=source,
                    file_type="json", row=row_idx, element_type="TableRow",
                ))

    logger.info("parser.json doc_id=%s items=%d blocks=%d", doc_id, len(items), len(blocks))
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

_PARSERS = {
    "pdf":  _parse_pdf,
    "docx": _parse_docx,
    "csv":  _parse_csv,
    "txt":  _parse_txt,
    "json": _parse_json,
}


def parse_file(
    file_bytes: bytes,
    file_type: str,
    doc_id: str,
    source: str,
) -> list[TextBlock]:
    """
    Parse file bytes into a list of TextBlock objects.
    Caller is responsible for downloading bytes from S3.

    Args:
        file_bytes: Raw file content.
        file_type:  Lowercase extension — pdf | docx | csv | txt | json.
        doc_id:     SHA256 document identifier.
        source:     S3 key for metadata attribution.

    Returns:
        List of TextBlock, each with text + position metadata.
        Empty list if file has no extractable content.

    Raises:
        ValueError: Unsupported file_type.
        Exception:  Parse failure (corrupt file, encoding error, etc.).
    """
    parser_fn = _PARSERS.get(file_type)
    if not parser_fn:
        raise ValueError(
            f"Unsupported file type: '{file_type}'. "
            f"Supported: {sorted(_PARSERS.keys())}"
        )

    blocks = parser_fn(file_bytes, doc_id, source)

    if not blocks:
        logger.warning(
            "parser.empty_result doc_id=%s file_type=%s source=%s",
            doc_id, file_type, source,
        )

    return blocks
