"""
VecturaFlow — Structured JSON logging via structlog.
Import `logger` from this module everywhere. Never use print() in production code.
"""
import logging
import sys

import structlog

from api.config import settings


def _configure_logging() -> None:
    log_level = logging.DEBUG if settings.api_debug else logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer() if settings.api_debug else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


_configure_logging()

logger = structlog.get_logger("vecturaflow")
