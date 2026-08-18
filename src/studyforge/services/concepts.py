"""Concept queries and the projection the generators consume."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from studyforge.domain.concepts.extract import normalize_concept_name
from studyforge.domain.generation.source import ConceptSource
from studyforge.logging_config import log_event
from studyforge.models import Concept, ExtractionMethod
from studyforge.services.courses import get_course
from studyforge.services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 200
MAX_DEFINITION_CHARS = 2_000


def concept_sources(session: Session, course_id: int) -> list[ConceptSource]:
    """Project a course's concepts into the generators' input shape.

    The generators take plain value objects rather than ORM rows, which is what
    keeps generation testable with literals and free of SQLAlchemy.
    """
    return [
        ConceptSource(
            concept_id=concept.id,
            name=concept.name,
            normalized_name=concept.normalized_name,
            definition=concept.definition,
            source_document_id=concept.source_document_id,
            source_chunk_id=concept.source_chunk_id,
            score=concept.score,
        )
        for concept in list_concepts(session, course_id=course_id)
    ]


def list_concepts(session: Session, *, course_id: int) -> list[Concept]:
    return list(
        session.scalars(
            select(Concept)
            .where(Concept.course_id == course_id)
            .order_by(Concept.score.desc(), Concept.normalized_name)
        )
    )


def get_concept(session: Session, concept_id: int) -> Concept:
    concept = session.get(Concept, concept_id)
    if concept is None:
        raise NotFoundError("That concept does not exist. It may have been deleted.")
    return concept


def create_concept(
    session: Session, *, course_id: int, name: str, definition: str | None = None
) -> Concept:
    """Add a concept by hand.

    Extraction produces *candidates*; this is how a learner corrects or
    supplements them. Manually created concepts are marked as such so the UI
    never presents the learner's own work as machine-extracted.
    """
    get_course(session, course_id)
    clean_name = _require_name(name)
    key = normalize_concept_name(clean_name)

    existing = session.scalars(
        select(Concept).where(Concept.course_id == course_id, Concept.normalized_name == key)
    ).first()
    if existing is not None:
        raise ValidationError(
            f"This course already has a concept called {existing.name!r}.",
            {"name": "A concept with this name already exists in this course."},
        )

    concept = Concept(
        course_id=course_id,
        name=clean_name,
        normalized_name=key,
        definition=_optional_definition(definition),
        extraction_method=ExtractionMethod.MANUAL,
        score=1.0,
    )
    session.add(concept)
    session.flush()
    log_event(logger, "concept_created", concept_id=concept.id, course_id=course_id)
    return concept


def update_concept(
    session: Session, concept_id: int, *, name: str, definition: str | None
) -> Concept:
    concept = get_concept(session, concept_id)
    clean_name = _require_name(name)
    key = normalize_concept_name(clean_name)

    clash = session.scalars(
        select(Concept).where(
            Concept.course_id == concept.course_id,
            Concept.normalized_name == key,
            Concept.id != concept.id,
        )
    ).first()
    if clash is not None:
        raise ValidationError(
            f"This course already has a concept called {clash.name!r}.",
            {"name": "A concept with this name already exists in this course."},
        )

    concept.name = clean_name
    concept.normalized_name = key
    concept.definition = _optional_definition(definition)
    # A concept the learner has edited is theirs, not an extraction artefact.
    concept.extraction_method = ExtractionMethod.MANUAL
    session.flush()
    return concept


def delete_concept(session: Session, concept_id: int) -> None:
    session.delete(get_concept(session, concept_id))
    session.flush()
    log_event(logger, "concept_deleted", concept_id=concept_id)


def _require_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise ValidationError(
            "A concept needs a name.", {"name": "Please give this concept a name."}
        )
    if len(name) > MAX_NAME_CHARS:
        raise ValidationError(
            "That concept name is too long.",
            {"name": f"Please keep it under {MAX_NAME_CHARS} characters."},
        )
    return name


def _optional_definition(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > MAX_DEFINITION_CHARS:
        raise ValidationError(
            "That definition is too long.",
            {"definition": f"Please keep it under {MAX_DEFINITION_CHARS:,} characters."},
        )
    return text
