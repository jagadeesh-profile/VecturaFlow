"""
VecturaFlow — Lightweight structured logger for ingestion Lambdas.

Why a separate module from ``api.logger``?
  Lambda zips must stay small. ``ingestion.*`` should not depend on the
  FastAPI/pydantic-settings stack. This module gives us the same
  structured-JSON shape the API uses — in pure stdlib.

Usage:
    from ingestion.logging_util import get_logger
    logger = get_logger(__name__)
    logger.info("ingestion.queued", doc_id=doc_id, file_type="pdf")

In Lambda / production (``API_DEBUG!=true``) the output is single-line JSON
suitable for CloudWatch Logs Insights. In local development it's
human-readable ``key=value`` pairs.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

_DEV_MODE = os.environ.get("API_DEBUG", "false").lower() in {"1", "true", "yes"}


class _StructuredAdapter(logging.LoggerAdapter):
    """Accepts arbitrary keyword context and renders it structurally."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:  # type: ignore[override]
        extra = kwargs.pop("extra", {}) or {}
        # structlog-style: every kwarg that isn't a std logging kwarg becomes context
        reserved = {"exc_info", "stack_info", "stacklevel"}
        context: dict[str, Any] = {}
        for key in list(kwargs.keys()):
            if key in reserved:
                continue
            context[key] = kwargs.pop(key)
        extra["context"] = context
        kwargs["extra"] = extra
        return msg, kwargs


class _JsonFormatter(logging.Formatter):
    """One JSON object per log record, flat-keyed, UTF-8 safe."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            "logger": record.name,
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class _DevFormatter(logging.Formatter):
    """Human-readable: level  event  key=value key2=value2"""

    def format(self, record: logging.LogRecord) -> str:
        context = getattr(record, "context", None) or {}
        parts = [f"{k}={v}" for k, v in context.items()]
        ctx_str = " ".join(parts)
        base = f"{record.levelname:<7} {record.name} — {record.getMessage()}"
        if ctx_str:
            base = f"{base}  {ctx_str}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


_configured = False


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_DevFormatter() if _DEV_MODE else _JsonFormatter())

    root = logging.getLogger()
    # Don't duplicate handlers in Lambda warm starts
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))
    _configured = True


def get_logger(name: str) -> _StructuredAdapter:
    _configure_once()
    return _StructuredAdapter(logging.getLogger(name), extra={})


__all__ = ["get_logger"]
