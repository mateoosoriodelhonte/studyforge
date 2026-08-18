"""Flashcards and their review history.

The FSRS memory state lives directly on ``Flashcard`` rather than in a separate
scheduling table. There is exactly one scheduling state per card and it is read
on every queue build, so splitting it out would buy nothing but a join.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studyforge.domain.study.fsrs import CardState, SchedulingCard
from studyforge.models.base import Base, TimestampMixin, utcnow
from studyforge.models.enums import GenerationMethod

if TYPE_CHECKING:
    from studyforge.models.course import Course
    from studyforge.models.session import StudySession


class Flashcard(Base, TimestampMixin):
    """A single question/answer pair, with its scheduling state and provenance."""

    __tablename__ = "flashcards"
    __table_args__ = (
        # The study queue's hot path: "cards in this course due before now".
        Index("ix_flashcards_course_due", "course_id", "due_at"),
        Index("ix_flashcards_due_suspended", "due_at", "suspended_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    concept_id: Mapped[int | None] = mapped_column(
        ForeignKey("concepts.id", ondelete="SET NULL"), default=None, index=True
    )
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)

    # --- provenance -------------------------------------------------------
    generation_method: Mapped[GenerationMethod] = mapped_column(
        Enum(GenerationMethod, native_enum=False, length=20),
        default=GenerationMethod.MANUAL,
    )
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), default=None, index=True
    )
    source_chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL"), default=None
    )
    ai_provider: Mapped[str | None] = mapped_column(String(50), default=None)
    ai_model: Mapped[str | None] = mapped_column(String(100), default=None)
    generated_at: Mapped[datetime | None] = mapped_column(default=None)

    # --- state ------------------------------------------------------------
    suspended_at: Mapped[datetime | None] = mapped_column(default=None, index=True)

    # --- FSRS scheduling state -------------------------------------------
    # stability/difficulty stay NULL until the first review establishes them;
    # `stability IS NULL` is exactly "this card is new".
    state: Mapped[CardState] = mapped_column(
        Enum(CardState, native_enum=False, length=20), default=CardState.LEARNING
    )
    step: Mapped[int | None] = mapped_column(Integer, default=0)
    stability: Mapped[float | None] = mapped_column(Float, default=None)
    difficulty: Mapped[float | None] = mapped_column(Float, default=None)
    due_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(default=None)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)

    course: Mapped[Course] = relationship(back_populates="flashcards")
    reviews: Mapped[list[Review]] = relationship(
        back_populates="flashcard",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Review.reviewed_at",
    )

    # -- mapping to and from the pure scheduler ---------------------------

    def to_scheduling_card(self) -> SchedulingCard:
        """Project the ORM row onto the scheduler's value object.

        This is the seam that keeps the FSRS maths free of SQLAlchemy: the
        engine only ever sees an immutable :class:`SchedulingCard`.
        """
        return SchedulingCard(
            due_at=self.due_at,
            state=self.state,
            step=self.step,
            stability=self.stability,
            difficulty=self.difficulty,
            last_reviewed_at=self.last_reviewed_at,
            reps=self.reps,
            lapses=self.lapses,
        )

    def apply_scheduling(self, card: SchedulingCard) -> None:
        """Write a scheduler result back onto the row."""
        self.due_at = card.due_at
        self.state = card.state
        self.step = card.step
        self.stability = card.stability
        self.difficulty = card.difficulty
        self.last_reviewed_at = card.last_reviewed_at
        self.reps = card.reps
        self.lapses = card.lapses

    @property
    def is_suspended(self) -> bool:
        return self.suspended_at is not None

    @property
    def is_new(self) -> bool:
        return self.stability is None

    @property
    def is_generated(self) -> bool:
        return self.generation_method is not GenerationMethod.MANUAL


class Review(Base):
    """One rating applied to one card.

    Both the before and after memory state are stored. Keeping only the rating
    would make the schedule unexplainable after the fact; with this, any card's
    history can be replayed or shown to the learner.
    """

    __tablename__ = "reviews"
    __table_args__ = (Index("ix_reviews_card_time", "flashcard_id", "reviewed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    flashcard_id: Mapped[int] = mapped_column(
        ForeignKey("flashcards.id", ondelete="CASCADE"), index=True
    )
    study_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("study_sessions.id", ondelete="SET NULL"), default=None, index=True
    )
    rating: Mapped[int] = mapped_column(Integer)
    reviewed_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)

    elapsed_days: Mapped[int | None] = mapped_column(Integer, default=None)
    scheduled_days: Mapped[int] = mapped_column(Integer, default=0)

    state_before: Mapped[CardState] = mapped_column(Enum(CardState, native_enum=False, length=20))
    state_after: Mapped[CardState] = mapped_column(Enum(CardState, native_enum=False, length=20))
    stability_before: Mapped[float | None] = mapped_column(Float, default=None)
    stability_after: Mapped[float | None] = mapped_column(Float, default=None)
    difficulty_before: Mapped[float | None] = mapped_column(Float, default=None)
    difficulty_after: Mapped[float | None] = mapped_column(Float, default=None)
    retrievability_before: Mapped[float | None] = mapped_column(Float, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    flashcard: Mapped[Flashcard] = relationship(back_populates="reviews")
    study_session: Mapped[StudySession | None] = relationship(back_populates="reviews")

    @property
    def was_recalled(self) -> bool:
        """Any rating above *Again* counts as a successful recall."""
        return self.rating > 1
