"""structlog configuration with redaction wired into the processor chain.

Every log event passes through a redaction processor BEFORE it is rendered, so no secret value
can reach a log line (FR-007, logging half). Call configure_logging() once at startup.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from app.core.redaction import redact_mapping


def _redaction_processor(
    logger: Any, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """structlog processor that masks secret-bearing fields in every event.

    Runs redact_mapping over the whole event dict so both the rendered message and any
    structured key/values are scrubbed before the renderer serializes them.
    """
    return redact_mapping(event_dict)


def configure_logging(*, env: str = "local") -> None:
    """Configure structlog process-wide with the redaction processor in the chain.

    JSON output outside local dev, human-readable console output locally. The redaction
    processor sits immediately before the renderer so nothing secret is ever serialized.
    Safe to call once at app startup.
    """
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer(colors=False)
        if env == "local"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redaction_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
