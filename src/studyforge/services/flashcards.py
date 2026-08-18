"""Flashcard lifecycle and deterministic generation into the database."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from studyforge.domain.generation.flashcards import (
    CardCandidate,
    CardStrategy,
    generate_cards,
)
from studyforge.logging_config import log_event
from studyforge.models import Flashcard, GenerationMethod
from studyforge.services.concepts import concept_sources
from studyforge.services.courses import get_course
from studyforge.services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

MAX_SIDE_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class GenerationSummary:
    """The outcome of a generation run, including what it declined to do."""

    created: list[Flashcard]
    skipped_duplicates: int
    concepts_without_definitions: int

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def produced_nothing(self) -> bool:
        return not self.created


def create_card(
    session: Session,
    *,
    course_id: int,
    front: str,
    back: str,
    concept_id: int | None = None,
) -> Flashcard:
    get_course(session, course_id)
    card = Flashcard(
        course_id=course_id,
        concept_id=concept_id,
        front=_require_side(front, "front"),
        back=_require_side(back, "back"),
        generation_method=GenerationMethod.MANUAL,
        due_at=datetime.now(UTC),
    )
    session.add(card)
    session.flush()
    log_event(logger, "flashcard_created", card_id=card.id, course_id=course_id)
    return card


def update_card(session: Session, card_id: int, *, front: str, back: str) -> Flashcard:
    card = get_card(session, card_id)
    card.front = _require_side(front, "front")
    card.back = _require_side(back, "back")
    session.flush()
    return card


def delete_card(session: Session, card_id: int) -> None:
    card = get_card(session, card_id)
    session.delete(card)
    session.flush()
    log_event(logger, "flashcard_deleted", card_id=card_id)


def set_suspended(session: Session, card_id: int, *, suspended: bool) -> Flashcard:
    """Suspend or restore a card.

    A suspended card keeps its memory state and review history; it is simply
    withheld from the queue. Deleting would throw away everything the learner
    has invested in it.
    """
    card = get_card(session, card_id)
    card.suspended_at = datetime.now(UTC) if suspended else None
    session.flush()
    log_event(
        logger,
        "flashcard_suspended" if suspended else "flashcard_unsuspended",
        card_id=card_id,
    )
    return card


def get_card(session: Session, card_id: int) -> Flashcard:
    card = session.get(Flashcard, card_id)
    if card is None:
        raise NotFoundError("That flashcard does not exist. It may have been deleted.")
    return card


def list_cards(
    session: Session, *, course_id: int, include_suspended: bool = True
) -> list[Flashcard]:
    query = select(Flashcard).where(Flashcard.course_id == course_id)
    if not include_suspended:
        query = query.where(Flashcard.suspended_at.is_(None))
    return list(session.scalars(query.order_by(Flashcard.created_at.desc(), Flashcard.id.desc())))


def propose_cards(
    session: Session,
    *,
    course_id: int,
    strategies: tuple[CardStrategy, ...] = (
        CardStrategy.TERM_TO_DEFINITION,
        CardStrategy.DEFINITION_TO_TERM,
    ),
    max_cards: int = 40,
) -> list[CardCandidate]:
    """Propose cards without saving them.

    Generation is a *proposal* step. Candidates are shown to the learner and
    accepted explicitly, because silently inserting generated material into
    someone's study queue is how a study tool loses their trust.
    """
    get_course(session, course_id)
    sources = concept_sources(session, course_id)
    existing = _existing_keys(session, course_id)
    proposed = generate_cards(sources, strategies=strategies, max_cards=None)
    fresh = [c for c in proposed if c.dedupe_key not in existing]
    return fresh[:max_cards]


def accept_cards(
    session: Session, *, course_id: int, candidates: list[CardCandidate]
) -> GenerationSummary:
    """Persist accepted candidates, skipping any that already exist."""
    get_course(session, course_id)
    existing = _existing_keys(session, course_id)
    now = datetime.now(UTC)

    created: list[Flashcard] = []
    skipped = 0
    for candidate in candidates:
        if candidate.dedupe_key in existing:
            skipped += 1
            continue
        existing.add(candidate.dedupe_key)
        card = Flashcard(
            course_id=course_id,
            concept_id=candidate.concept_id,
            front=candidate.front,
            back=candidate.back,
            generation_method=GenerationMethod.DETERMINISTIC,
            source_document_id=candidate.source_document_id,
            source_chunk_id=candidate.source_chunk_id,
            generated_at=now,
            due_at=now,
        )
        session.add(card)
        created.append(card)

    session.flush()
    without_definitions = sum(
        1 for source in concept_sources(session, course_id) if not source.has_definition
    )
    log_event(
        logger,
        "flashcards_generated",
        course_id=course_id,
        created=len(created),
        skipped=skipped,
    )
    return GenerationSummary(
        created=created,
        skipped_duplicates=skipped,
        concepts_without_definitions=without_definitions,
    )


def generate_and_accept(
    session: Session, *, course_id: int, max_cards: int = 40
) -> GenerationSummary:
    """Convenience path: propose and accept in one step."""
    return accept_cards(
        session,
        course_id=course_id,
        candidates=propose_cards(session, course_id=course_id, max_cards=max_cards),
    )


def _existing_keys(session: Session, course_id: int) -> set[tuple[int, str]]:
    """Identify generated cards already present, so re-running is idempotent.

    A card's identity for this purpose is (concept, question shape). Comparing
    text would let a small wording change silently duplicate every card.
    """
    keys: set[tuple[int, str]] = set()
    for card in session.scalars(
        select(Flashcard).where(
            Flashcard.course_id == course_id,
            Flashcard.generation_method == GenerationMethod.DETERMINISTIC,
            Flashcard.concept_id.is_not(None),
        )
    ):
        assert card.concept_id is not None
        keys.add((card.concept_id, _infer_strategy(card.front)))
    return keys


def _infer_strategy(front: str) -> str:
    if front.startswith("Define: "):
        return CardStrategy.TERM_TO_DEFINITION.value
    if front.startswith("Which term is described here?"):
        return CardStrategy.DEFINITION_TO_TERM.value
    return CardStrategy.CLOZE.value


def _require_side(value: str, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationError(
            f"The {field} of a card cannot be empty.",
            {field: f"Please write something for the {field} of this card."},
        )
    if len(text) > MAX_SIDE_CHARS:
        raise ValidationError(
            f"The {field} of this card is too long.",
            {field: f"Please keep the {field} under {MAX_SIDE_CHARS:,} characters."},
        )
    return text
