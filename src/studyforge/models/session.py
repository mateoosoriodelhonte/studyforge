"""Study sessions: a bounded run of card reviews."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studyforge.models.base import Base, utcnow

if TYPE_CHECKING:
    from studyforge.models.flashcard import Review


class StudySession(Base):
    """One sitting at the review queue.

    ``course_id`` is nullable: a session can span every course, which is the
    default "just let me study" path from the dashboard.
    """

    __tablename__ = "study_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), default=None, index=True
    )
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    cards_reviewed: Mapped[int] = mapped_column(Integer, default=0)
    again_count: Mapped[int] = mapped_column(Integer, default=0)

    reviews: Mapped[list[Review]] = relationship(back_populates="study_session")

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    @property
    def recall_rate(self) -> float | None:
        """Share of reviews that were not *Again*, or ``None`` if none happened."""
        if self.cards_reviewed == 0:
            return None
        return (self.cards_reviewed - self.again_count) / self.cards_reviewed
