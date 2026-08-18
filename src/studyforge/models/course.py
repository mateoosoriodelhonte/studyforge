"""Courses: the organising unit for all study material."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studyforge.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from studyforge.models.concept import Concept
    from studyforge.models.document import Document
    from studyforge.models.flashcard import Flashcard
    from studyforge.models.quiz import Quiz


class Course(Base, TimestampMixin):
    """A subject a learner is studying, e.g. *CS 2420 - Data Structures*.

    Archiving is a nullable timestamp rather than a boolean: it records *when*
    a course was set aside, and keeps the row so its review history stays
    intact. Nothing in StudyForge hard-deletes study history implicitly.
    """

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    code: Mapped[str | None] = mapped_column(String(50), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    archived_at: Mapped[datetime | None] = mapped_column(default=None, index=True)

    documents: Mapped[list[Document]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )
    concepts: Mapped[list[Concept]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )
    flashcards: Mapped[list[Flashcard]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )
    quizzes: Mapped[list[Quiz]] = relationship(
        back_populates="course", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def display_name(self) -> str:
        return f"{self.code} - {self.name}" if self.code else self.name
