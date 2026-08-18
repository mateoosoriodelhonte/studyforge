"""Quizzes, their questions, and recorded attempts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from studyforge.models.base import Base, TimestampMixin, utcnow
from studyforge.models.enums import GenerationMethod, QuestionKind

if TYPE_CHECKING:
    from studyforge.models.course import Course


class Quiz(Base, TimestampMixin):
    """A fixed set of questions drawn from a course's material."""

    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    generation_method: Mapped[GenerationMethod] = mapped_column(
        Enum(GenerationMethod, native_enum=False, length=20),
        default=GenerationMethod.DETERMINISTIC,
    )
    ai_provider: Mapped[str | None] = mapped_column(String(50), default=None)
    ai_model: Mapped[str | None] = mapped_column(String(100), default=None)

    course: Mapped[Course] = relationship(back_populates="quizzes")
    questions: Mapped[list[Question]] = relationship(
        back_populates="quiz",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Question.ordinal",
    )
    attempts: Mapped[list[QuizAttempt]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def question_count(self) -> int:
        return len(self.questions)


class Question(Base):
    """One question. Multiple-choice questions carry their choices as JSON.

    Choices are a JSON array rather than a child table: they are always read and
    written as a whole, never queried individually, and never shared between
    questions. A table here would add a join for no benefit.
    """

    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("quiz_id", "ordinal", name="uq_questions_ordinal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[QuestionKind] = mapped_column(Enum(QuestionKind, native_enum=False, length=20))
    prompt: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text, default=None)

    choices: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    correct_choice_index: Mapped[int | None] = mapped_column(Integer, default=None)

    # Provenance: every question is attributable to a concept so that a wrong
    # answer can feed the weak-concept analysis.
    concept_id: Mapped[int | None] = mapped_column(
        ForeignKey("concepts.id", ondelete="SET NULL"), default=None, index=True
    )
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), default=None
    )
    source_chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL"), default=None
    )

    quiz: Mapped[Quiz] = relationship(back_populates="questions")

    @property
    def is_multiple_choice(self) -> bool:
        return self.kind is QuestionKind.MULTIPLE_CHOICE


class QuizAttempt(Base):
    """One sitting of a quiz. Incomplete attempts are kept, not discarded."""

    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)

    quiz: Mapped[Quiz] = relationship(back_populates="attempts")
    answers: Mapped[list[AnswerAttempt]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    @property
    def accuracy(self) -> float | None:
        """Fraction correct, or ``None`` when nothing has been answered.

        Returns ``None`` rather than ``0.0`` for an empty attempt: "no data" and
        "scored zero" are different facts and the UI must not conflate them.
        """
        answered = len(self.answers)
        if answered == 0:
            return None
        return sum(1 for a in self.answers if a.is_correct) / answered


class AnswerAttempt(Base):
    """One answer to one question within an attempt."""

    __tablename__ = "answer_attempts"
    __table_args__ = (
        UniqueConstraint("quiz_attempt_id", "question_id", name="uq_answer_attempts_question"),
        Index("ix_answer_attempts_concept_time", "concept_id", "answered_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_attempts.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), index=True
    )
    # Denormalised from the question so weak-concept queries do not need a join
    # through questions, and so the attribution survives the question's deletion.
    concept_id: Mapped[int | None] = mapped_column(
        ForeignKey("concepts.id", ondelete="SET NULL"), default=None, index=True
    )
    response: Mapped[str] = mapped_column(Text, default="")
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    # True when the learner used the "I was actually right" override on a
    # short-answer question. Tracked separately so self-marking never masquerades
    # as objective grading in the progress figures.
    self_graded: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)

    attempt: Mapped[QuizAttempt] = relationship(back_populates="answers")
