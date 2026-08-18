"""The input shape shared by the card and question generators.

A plain value object rather than the ORM ``Concept``, so generation stays
testable with literals and free of SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConceptSource:
    """A concept, with whatever the pipeline managed to learn about it."""

    concept_id: int
    name: str
    normalized_name: str
    definition: str | None = None
    source_document_id: int | None = None
    source_chunk_id: int | None = None
    score: float = 0.0

    @property
    def has_definition(self) -> bool:
        return bool(self.definition and self.definition.strip())
