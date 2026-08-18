"""Structured logging: format, field handling and secret hygiene."""

from __future__ import annotations

import json
import logging

import pytest

from studyforge.logging_config import (
    ConsoleFormatter,
    JSONFormatter,
    configure_logging,
    log_event,
)


def _record(msg: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestJSONFormatter:
    def test_emits_one_parseable_line(self) -> None:
        payload = json.loads(JSONFormatter().format(_record()))
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert "timestamp" in payload

    def test_includes_structured_fields(self) -> None:
        payload = json.loads(JSONFormatter().format(_record(course_id=7, event="x")))
        assert payload["course_id"] == 7
        assert payload["event"] == "x"

    def test_never_spans_multiple_lines(self) -> None:
        assert "\n" not in JSONFormatter().format(_record("a\nb"))


class TestConsoleFormatter:
    def test_renders_message_and_fields(self) -> None:
        out = ConsoleFormatter().format(_record(document_id=3))
        assert "hello" in out
        assert "document_id=3" in out

    def test_works_without_extra_fields(self) -> None:
        assert "hello" in ConsoleFormatter().format(_record())


class TestLogEvent:
    def test_records_the_event_name(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("sf.test")
        with caplog.at_level(logging.INFO, logger="sf.test"):
            log_event(logger, "review_completed", card_id=1, rating=3)
        record = caplog.records[0]
        assert record.message == "review_completed"
        assert record.event == "review_completed"  # type: ignore[attr-defined]
        assert record.card_id == 1  # type: ignore[attr-defined]

    def test_truncates_long_values_so_notes_do_not_land_in_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A user's private study material must not be copied into a log file."""
        logger = logging.getLogger("sf.test")
        with caplog.at_level(logging.INFO, logger="sf.test"):
            log_event(logger, "extraction_completed", text="x" * 5000)
        text: str = caplog.records[0].text  # type: ignore[attr-defined]
        assert len(text) < 300
        assert text.endswith("chars)")

    def test_short_values_pass_through_untouched(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("sf.test")
        with caplog.at_level(logging.INFO, logger="sf.test"):
            log_event(logger, "e", title="Data Structures")
        assert caplog.records[0].title == "Data Structures"  # type: ignore[attr-defined]

    def test_respects_an_explicit_level(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("sf.test")
        with caplog.at_level(logging.DEBUG, logger="sf.test"):
            log_event(logger, "ai_request_failed", level=logging.ERROR, provider="ollama")
        assert caplog.records[0].levelno == logging.ERROR


class TestConfigureLogging:
    @pytest.mark.parametrize("fmt", ["console", "json"])
    def test_installs_exactly_one_handler(self, fmt: str) -> None:
        configure_logging(level="INFO", fmt=fmt)
        configure_logging(level="INFO", fmt=fmt)  # idempotent
        assert len(logging.getLogger().handlers) == 1

    def test_sets_the_level(self) -> None:
        configure_logging(level="ERROR")
        assert logging.getLogger().level == logging.ERROR
        configure_logging(level="INFO")
