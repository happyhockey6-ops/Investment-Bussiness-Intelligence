"""Structured logging foundation.

The platform must eventually be able to answer, for any event: what
happened, when, via which provider/operation/data-version/model, and as part
of which decision. That means every log line should be structured data, not
free text — this module configures the stdlib `logging` package to emit
either JSON (production/observability) or a readable console format (local
development), and provides a small helper for attaching the standard
context fields consistently.

Deliberately built on the standard library rather than an extra dependency
(e.g. structlog) — Phase 0 favors the simplest thing that gives every log
line real structure.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Fields every log line SHOULD carry when known. None of these are enforced
# at the logging layer (that would couple this module to every caller); they
# are documented here as the contract callers should aim for.
STANDARD_CONTEXT_FIELDS = (
    "operation",
    "provider",
    "model",
    "data_version",
    "decision_id",
    "entity_id",
    "correlation_id",
)


class JSONFormatter(logging.Formatter):
    """Renders each record as one JSON object per line (for log aggregation)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in STANDARD_CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single-line format for local development."""

    def __init__(self) -> None:
        super().__init__(fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s")


def configure_logging(*, level: str = "INFO", fmt: str = "console") -> None:
    """Configure the root logger once, at process startup.

    Safe to call more than once (e.g. in tests): it replaces existing
    handlers rather than stacking them.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JSONFormatter() if fmt == "json" else ConsoleFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Thin wrapper kept for a single, greppable import site across the codebase."""
    return logging.getLogger(name)


def log_context(**fields: Any) -> dict[str, Any]:
    """Build the ``extra=`` dict for attaching standard context to a log call.

    Example::

        logger.info("fetched market data", extra=log_context(
            operation="market_data.fetch", provider="null", entity_id=entity.id,
        ))
    """
    unknown = set(fields) - set(STANDARD_CONTEXT_FIELDS)
    if unknown:
        raise ValueError(
            f"log_context() received non-standard field(s): {sorted(unknown)}. "
            f"Add them to STANDARD_CONTEXT_FIELDS if they should be tracked platform-wide."
        )
    return dict(fields)
