"""Source documents and the chunks extracted from them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studyforge.models.base import Base, TimestampMixin
from studyforge.models.enums import DocumentSource, DocumentStatus

if TYPE_CHECKING:
    from studyforge.models.course import Course


class Document(Base, TimestampMixin):
    """A piece of source material: pasted notes, a text file, or a PDF.

    ``extracted_text`` holds the *normalised* text, not the raw bytes. The
    original upload stays on disk under an internally generated
    ``stored_filename``; ``original_filename`` is display metadata only and is
    never used to build a path.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[DocumentSource] = mapped_column(
        Enum(DocumentSource, native_enum=False, length=20)
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=20),
        default=DocumentStatus.PENDING,
        index=True,
    )

    # Upload metadata. All nullable: a pasted document has no file behind it.
    original_filename: Mapped[str | None] = mapped_column(String(255), default=None)
    stored_filename: Mapped[str | None] = mapped_column(String(255), default=None)
    content_type: Mapped[str | None] = mapped_column(String(100), default=None)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Lets the UI warn about re-uploading material that is already present.
    sha256: Mapped[str | None] = mapped_column(String(64), default=None, index=True)

    # Extraction results.
    extracted_text: Mapped[str | None] = mapped_column(Text, default=None)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    extraction_error: Mapped[str | None] = mapped_column(Text, default=None)

    course: Mapped[Course] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentChunk.ordinal",
    )

    @property
    def is_ready(self) -> bool:
        """True when the document has usable text to generate material from."""
        return self.status is DocumentStatus.EXTRACTED and self.char_count > 0


class DocumentChunk(Base):
    """A semantically bounded slice of a document.

    Chunks are the unit of provenance: a generated flashcard points at the exact
    chunk it came from, and ``char_start``/``char_end`` locate that chunk in the
    normalised source text so the UI can show the passage in context.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_document_chunks_ordinal"),
        Index("ix_document_chunks_document_ordinal", "document_id", "ordinal"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(300), default=None)

    document: Mapped[Document] = relationship(back_populates="chunks")

    @property
    def char_count(self) -> int:
        return len(self.text)
