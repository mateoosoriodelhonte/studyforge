"""Concepts: the things worth learning, extracted from source material."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studyforge.models.base import Base, TimestampMixin
from studyforge.models.enums import ExtractionMethod

if TYPE_CHECKING:
    from studyforge.models.course import Course


class Concept(Base, TimestampMixin):
    """A term or idea a learner is expected to know.

    ``normalized_name`` is the deduplication key: lowercased, singularised and
    whitespace-collapsed, so "AVL Trees", "avl tree" and "AVL  Tree" converge on
    one concept per course rather than three.

    ``score`` is extraction confidence, not mastery. It says how much textual
    evidence supported pulling this out as a concept -- nothing about whether
    the learner knows it. Mastery lives in the weak-concept analysis, computed
    from actual review and quiz outcomes.
    """

    __tablename__ = "concepts"
    __table_args__ = (
        UniqueConstraint("course_id", "normalized_name", name="uq_concepts_course_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    definition: Mapped[str | None] = mapped_column(Text, default=None)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        Enum(ExtractionMethod, native_enum=False, length=20),
        default=ExtractionMethod.MANUAL,
    )
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    # Provenance. ``SET NULL`` rather than cascade: deleting the document a
    # concept came from should not silently delete the learner's concept and,
    # with it, all their performance history against it.
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), default=None, index=True
    )
    source_chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="SET NULL"), default=None
    )

    course: Mapped[Course] = relationship(back_populates="concepts")

    @property
    def has_definition(self) -> bool:
        return bool(self.definition and self.definition.strip())
