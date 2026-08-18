"""Enumerations persisted as short strings.

Stored as ``VARCHAR`` rather than native database enums: SQLite has no enum
type, and a string column keeps migrations trivial when a member is added.
Validity is enforced by SQLAlchemy's ``Enum(..., native_enum=False)``, which
emits a CHECK constraint.
"""

from __future__ import annotations

import enum


class DocumentSource(enum.StrEnum):
    """How the material arrived."""

    PASTE = "paste"
    UPLOAD = "upload"


class DocumentStatus(enum.StrEnum):
    """Where a document is in the ingestion pipeline.

    ``NO_TEXT`` is deliberately distinct from ``FAILED``: a scanned PDF is not a
    broken file, and the user needs to be told specifically that OCR is not
    enabled rather than shown a generic error.
    """

    PENDING = "pending"
    EXTRACTED = "extracted"
    NO_TEXT = "no_text"
    FAILED = "failed"


class ExtractionMethod(enum.StrEnum):
    """The evidence a concept was derived from. Recorded for provenance."""

    MANUAL = "manual"
    DEFINITION = "definition"
    GLOSSARY = "glossary"
    HEADING = "heading"
    FREQUENCY = "frequency"
    AI = "ai"


class GenerationMethod(enum.StrEnum):
    """How a flashcard or question came to exist."""

    MANUAL = "manual"
    DETERMINISTIC = "deterministic"
    AI = "ai"


class QuestionKind(enum.StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
