"""Structured logging.

StudyForge emits named domain events (``document_uploaded``, ``review_completed``,
...) with structured fields rather than free-form strings, so that logs stay
greppable and could be shipped to a log aggregator without reparsing prose.

Nothing here ever logs secrets or full document bodies; call sites pass
identifiers and counts, and :func:`log_event` truncates stray long values.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_MAX_FIELD_CHARS = 200
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    """Render each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line: ``LEVEL logger: message  key=value``."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"{record.levelname:<8} {record.name}: {record.getMessage()}"
        extras = _extra_fields(record)
        if extras:
            base += "  " + " ".join(f"{k}={v}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")
    }


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

    Long string fields are truncated: study material is the user's private data
    and has no business being copied wholesale into a log file.
    """
    logger.log(
        level,
        event,
        extra={"event": event, **{k: _truncate(v) for k, v in fields.items()}},
    )
