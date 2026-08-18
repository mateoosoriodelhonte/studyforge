"""Structured logging.

StudyForge emits named domain events (``document_uploaded``, ``review_completed``,
...) with structured fields rather than free-form strings, so that logs stay
greppable and could be shipped to a log aggregator without reparsing prose.

Nothing here ever logs secrets or full document bodies; call sites pass
identifiers and counts, and :func:`log_event` truncates stray long values.

All custom fields travel inside a single ``LogRecord`` attribute
(:data:`FIELDS_ATTRIBUTE`) rather than being splatted onto the record. Python's
logging module raises ``KeyError`` if an ``extra`` key collides with a built-in
record attribute, and several natural field names -- ``created``, ``name``,
``module``, ``message`` -- do exactly that. Namespacing makes the collision
impossible instead of leaving a landmine on a rarely-executed code path.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_MAX_FIELD_CHARS = 200

#: The single record attribute holding every structured field.
FIELDS_ATTRIBUTE = "studyforge_fields"


class JSONFormatter(logging.Formatter):
    """Render each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(event_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line: ``LEVEL logger: message  key=value``."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"{record.levelname:<8} {record.name}: {record.getMessage()}"
        fields = event_fields(record)
        if fields:
            base += "  " + " ".join(f"{k}={v}" for k, v in fields.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def event_fields(record: logging.LogRecord) -> dict[str, Any]:
    """The structured fields attached to ``record``, if any."""
    fields = getattr(record, FIELDS_ATTRIBUTE, None)
    return dict(fields) if isinstance(fields, dict) else {}


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Install a single stderr handler on the root logger. Idempotent."""
    formatter: logging.Formatter = JSONFormatter() if fmt == "json" else ConsoleFormatter()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own colourised handlers; defer to ours.
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(noisy)
        logger.handlers.clear()
        logger.propagate = True


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[:_MAX_FIELD_CHARS] + f"...(+{len(value) - _MAX_FIELD_CHARS} chars)"
    return value


def log_event(
    logger: logging.Logger,
    event: str,
    /,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a named domain event with structured fields.

    Any field name is safe here, including ones that clash with ``LogRecord``
    attributes. Long string values are truncated: study material is the user's
    private data and has no business being copied wholesale into a log file.
    """
    payload = {"event": event, **{k: _truncate(v) for k, v in fields.items()}}
    logger.log(level, event, extra={FIELDS_ATTRIBUTE: payload})
