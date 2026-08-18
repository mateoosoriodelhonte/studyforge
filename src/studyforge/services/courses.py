"""Course lifecycle and the aggregated view a course page needs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from studyforge.logging_config import log_event
from studyforge.models import Concept, Course, Document, Flashcard, Quiz
from studyforge.services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 200
MAX_CODE_CHARS = 50
MAX_DESCRIPTION_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class CourseStats:
    """Counts shown on a course card and course page."""

    documents: int = 0
    concepts: int = 0
    flashcards: int = 0
    quizzes: int = 0
    due_now: int = 0
    new_cards: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.documents or self.concepts or self.flashcards)


def create_course(
    session: Session, *, name: str, code: str | None = None, description: str | None = None
) -> Course:
    """Create a course after validating its fields."""
    course = Course(
        name=_require_name(name),
        code=_optional_text(code, "code", MAX_CODE_CHARS),
        description=_optional_text(description, "description", MAX_DESCRIPTION_CHARS),
    )
    session.add(course)
    session.flush()
    log_event(logger, "course_created", course_id=course.id)
    return course


def update_course(
    session: Session,
    course_id: int,
    *,
    name: str,
    code: str | None = None,
    description: str | None = None,
) -> Course:
    course = get_course(session, course_id)
    course.name = _require_name(name)
    course.code = _optional_text(code, "code", MAX_CODE_CHARS)
    course.description = _optional_text(description, "description", MAX_DESCRIPTION_CHARS)
    session.flush()
    log_event(logger, "course_updated", course_id=course.id)
    return course


def set_archived(session: Session, course_id: int, *, archived: bool) -> Course:
    """Archive or restore a course.

    Archiving hides a course from the dashboard but keeps every document, card
    and review. Nothing in StudyForge discards study history as a side effect.
    """
    course = get_course(session, course_id)
    course.archived_at = datetime.now(UTC) if archived else None
    session.flush()
    log_event(logger, "course_archived" if archived else "course_restored", course_id=course.id)
    return course


def delete_course(session: Session, course_id: int) -> None:
    """Permanently delete a course and everything under it."""
    course = get_course(session, course_id)
    session.delete(course)
    session.flush()
    log_event(logger, "course_deleted", course_id=course_id)


def get_course(session: Session, course_id: int) -> Course:
    course = session.get(Course, course_id)
    if course is None:
        raise NotFoundError("That course does not exist. It may have been deleted.")
    return course


def list_courses(session: Session, *, include_archived: bool = False) -> list[Course]:
    query = select(Course).order_by(Course.archived_at.is_(None).desc(), Course.name)
    if not include_archived:
        query = query.where(Course.archived_at.is_(None))
    return list(session.scalars(query))


def course_stats(session: Session, course_id: int, *, now: datetime) -> CourseStats:
    """Counts for one course, gathered in a single round trip per entity.

    ``now`` is passed in rather than read here so that dashboard figures are
    consistent across every course in one render, and so tests can pin time.
    """

    def count(model: type[Course] | type[Document] | type[Concept] | type[Quiz]) -> int:
        return (
            session.scalar(
                select(func.count()).select_from(model).where(model.course_id == course_id)  # type: ignore[union-attr]
            )
            or 0
        )

    due = (
        session.scalar(
            select(func.count())
            .select_from(Flashcard)
            .where(
                Flashcard.course_id == course_id,
                Flashcard.suspended_at.is_(None),
                Flashcard.due_at <= now,
            )
        )
        or 0
    )
    new = (
        session.scalar(
            select(func.count())
            .select_from(Flashcard)
            .where(
                Flashcard.course_id == course_id,
                Flashcard.suspended_at.is_(None),
                Flashcard.stability.is_(None),
            )
        )
        or 0
    )
    total_cards = (
        session.scalar(
            select(func.count()).select_from(Flashcard).where(Flashcard.course_id == course_id)
        )
        or 0
    )
    return CourseStats(
        documents=count(Document),
        concepts=count(Concept),
        flashcards=total_cards,
        quizzes=count(Quiz),
        due_now=due,
        new_cards=new,
    )


def _require_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise ValidationError("A course needs a name.", {"name": "Please give this course a name."})
    if len(name) > MAX_NAME_CHARS:
        raise ValidationError(
            "That course name is too long.",
            {"name": f"Please keep the name under {MAX_NAME_CHARS} characters."},
        )
    return name


def _optional_text(value: str | None, field: str, limit: int) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValidationError(
            f"That {field} is too long.",
            {field: f"Please keep the {field} under {limit} characters."},
        )
    return text
