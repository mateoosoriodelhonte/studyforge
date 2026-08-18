"""Document ingestion: the pipeline from raw input to study-ready material.

    input (paste | text file | PDF)
        -> validate           documents.validation
        -> store              documents.storage        (uploads only)
        -> extract text       documents.extraction
        -> normalise          domain.text.normalize
        -> chunk              domain.text.chunking
        -> extract concepts   domain.concepts.extract
        -> persist

Each stage is independently testable and the pipeline is deterministic end to
end: the same document always produces the same chunks and the same concepts.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from studyforge.documents.extraction import ExtractionOutcome, extract_text
from studyforge.documents.storage import DocumentStorage
from studyforge.documents.validation import (
    ValidatedUpload,
    validate_upload,
)
from studyforge.domain.concepts.extract import (
    ConceptCandidate,
    ExtractionConfig,
    extract_concepts,
    normalize_concept_name,
)
from studyforge.domain.text.chunking import Chunk, ChunkingConfig, chunk_text
from studyforge.domain.text.normalize import is_probably_meaningful, normalize_text
from studyforge.logging_config import log_event
from studyforge.models import (
    Concept,
    Document,
    DocumentChunk,
    DocumentSource,
    DocumentStatus,
    ExtractionMethod,
)
from studyforge.services.courses import get_course
from studyforge.services.exceptions import NotFoundError, ValidationError

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 300
MAX_PASTED_CHARS = 2_000_000

_METHOD_MAP = {
    "definition": ExtractionMethod.DEFINITION,
    "glossary": ExtractionMethod.GLOSSARY,
    "heading": ExtractionMethod.HEADING,
    "frequency": ExtractionMethod.FREQUENCY,
}


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """What ingesting one document produced."""

    document: Document
    chunk_count: int
    concepts_created: int
    concepts_matched: int

    @property
    def succeeded(self) -> bool:
        return self.document.status is DocumentStatus.EXTRACTED

    @property
    def needs_attention(self) -> bool:
        return self.document.status in (DocumentStatus.NO_TEXT, DocumentStatus.FAILED)


def ingest_pasted_text(
    session: Session, *, course_id: int, title: str, body: str
) -> IngestionResult:
    """Ingest text pasted directly into the app -- the simplest input path."""
    get_course(session, course_id)
    clean_title = _require_title(title)

    if not body or not body.strip():
        raise ValidationError(
            "There is nothing to save.", {"body": "Please paste in some notes first."}
        )
    if len(body) > MAX_PASTED_CHARS:
        raise ValidationError(
            "That is too much text to paste at once.",
            {"body": f"Please keep pasted notes under {MAX_PASTED_CHARS:,} characters."},
        )

    text = normalize_text(body)
    document = Document(
        course_id=course_id,
        title=clean_title,
        source_type=DocumentSource.PASTE,
        size_bytes=len(body.encode("utf-8")),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    session.add(document)
    session.flush()

    if not is_probably_meaningful(text):
        return _finish_without_text(
            session,
            document,
            text=text,
            error="These notes are too short to generate study material from.",
        )
    return _process(session, document, text=text, page_count=None)


def ingest_upload(
    session: Session,
    storage: DocumentStorage,
    *,
    course_id: int,
    filename: str | None,
    content: bytes,
    declared_content_type: str | None,
    max_bytes: int,
    title: str | None = None,
) -> IngestionResult:
    """Ingest an uploaded file.

    Validation runs *before* anything is written to disk, so a rejected upload
    leaves no trace on the filesystem.
    """
    get_course(session, course_id)

    upload: ValidatedUpload = validate_upload(
        filename=filename,
        content=content,
        declared_content_type=declared_content_type,
        max_bytes=max_bytes,
    )
    stored = storage.store(upload)

    document = Document(
        course_id=course_id,
        title=_require_title(title or upload.display_filename),
        source_type=DocumentSource.UPLOAD,
        original_filename=upload.display_filename,
        stored_filename=stored.stored_filename,
        content_type=declared_content_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )
    session.add(document)
    session.flush()
    log_event(
        logger,
        "document_uploaded",
        document_id=document.id,
        course_id=course_id,
        size_bytes=stored.size_bytes,
        extension=upload.kind.extension,
    )

    extracted = extract_text(upload)
    if extracted.outcome is not ExtractionOutcome.SUCCESS:
        document.page_count = extracted.page_count
        status = (
            DocumentStatus.NO_TEXT
            if extracted.outcome is ExtractionOutcome.NO_TEXT
            else DocumentStatus.FAILED
        )
        return _finish_without_text(
            session, document, text=extracted.text, error=extracted.error, status=status
        )

    return _process(session, document, text=extracted.text, page_count=extracted.page_count)


def find_duplicate(session: Session, *, course_id: int, sha256: str) -> Document | None:
    """An identical document already in this course, if there is one.

    Used to warn rather than to block: re-uploading a corrected version of the
    same notes is a legitimate thing to do.
    """
    return session.scalars(
        select(Document).where(Document.course_id == course_id, Document.sha256 == sha256).limit(1)
    ).first()


def get_document(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise NotFoundError("That document does not exist. It may have been deleted.")
    return document


def delete_document(session: Session, storage: DocumentStorage, document_id: int) -> None:
    """Delete a document and its stored file.

    Cards generated from it survive, with their provenance nulled: losing the
    source must not silently destroy the learner's review history.
    """
    document = get_document(session, document_id)
    stored_filename = document.stored_filename
    session.delete(document)
    session.flush()
    if stored_filename:
        storage.delete(stored_filename)
    log_event(logger, "document_deleted", document_id=document_id)


def _process(
    session: Session, document: Document, *, text: str, page_count: int | None
) -> IngestionResult:
    """Chunk the text, extract concepts and persist both."""
    document.extracted_text = text
    document.char_count = len(text)
    document.page_count = page_count
    document.status = DocumentStatus.EXTRACTED
    document.extraction_error = None

    chunks = chunk_text(text, ChunkingConfig())
    for chunk in chunks:
        session.add(
            DocumentChunk(
                document_id=document.id,
                ordinal=chunk.ordinal,
                text=chunk.text,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                heading=chunk.heading,
            )
        )
    session.flush()

    created, matched = _persist_concepts(session, document, chunks_text=text, chunks=chunks)

    log_event(
        logger,
        "extraction_completed",
        document_id=document.id,
        course_id=document.course_id,
        char_count=document.char_count,
        chunk_count=len(chunks),
        concepts_created=created,
    )
    return IngestionResult(
        document=document,
        chunk_count=len(chunks),
        concepts_created=created,
        concepts_matched=matched,
    )


def _persist_concepts(
    session: Session,
    document: Document,
    *,
    chunks_text: str,
    chunks: list[Chunk],
) -> tuple[int, int]:
    """Store extracted concepts, merging with any the course already has.

    Concepts are unique per course by normalised name. A second document
    mentioning the same term enriches the existing concept -- adding a
    definition it lacked -- rather than creating a duplicate.
    """
    headings = [chunk.heading for chunk in chunks if chunk.heading]
    candidates: list[ConceptCandidate] = extract_concepts(
        chunks_text, config=ExtractionConfig(), headings=headings
    )
    if not candidates:
        return 0, 0

    existing = {
        concept.normalized_name: concept
        for concept in session.scalars(
            select(Concept).where(Concept.course_id == document.course_id)
        )
    }
    chunk_by_ordinal = {
        chunk.ordinal: chunk
        for chunk in session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
    }

    created = matched = 0
    for candidate in candidates:
        key = normalize_concept_name(candidate.name)
        if not key:
            continue

        source_chunk = chunk_by_ordinal.get(candidate.chunk_ordinal or -1)
        if (current := existing.get(key)) is not None:
            matched += 1
            # Only ever add information; never overwrite a better definition or
            # downgrade a concept the learner may have edited by hand.
            if candidate.has_definition and not current.has_definition:
                current.definition = candidate.definition
                current.extraction_method = _METHOD_MAP.get(
                    candidate.method, ExtractionMethod.MANUAL
                )
                current.source_document_id = document.id
                current.source_chunk_id = source_chunk.id if source_chunk else None
            current.score = max(current.score, candidate.score)
            continue

        concept = Concept(
            course_id=document.course_id,
            name=candidate.name,
            normalized_name=key,
            definition=candidate.definition,
            extraction_method=_METHOD_MAP.get(candidate.method, ExtractionMethod.MANUAL),
            score=candidate.score,
            source_document_id=document.id,
            source_chunk_id=source_chunk.id if source_chunk else None,
        )
        session.add(concept)
        existing[key] = concept
        created += 1

    session.flush()
    return created, matched


def _finish_without_text(
    session: Session,
    document: Document,
    *,
    text: str,
    error: str | None,
    status: DocumentStatus = DocumentStatus.NO_TEXT,
) -> IngestionResult:
    """Record an honest failure rather than fabricating study material."""
    document.extracted_text = text or None
    document.char_count = len(text)
    document.status = status
    document.extraction_error = error
    session.flush()
    log_event(
        logger,
        "extraction_failed" if status is DocumentStatus.FAILED else "extraction_no_text",
        level=logging.WARNING,
        document_id=document.id,
        course_id=document.course_id,
        status=status.value,
    )
    return IngestionResult(document=document, chunk_count=0, concepts_created=0, concepts_matched=0)


def _require_title(value: str | None) -> str:
    title = (value or "").strip()
    if not title:
        raise ValidationError(
            "This document needs a title.", {"title": "Please give this document a title."}
        )
    if len(title) > MAX_TITLE_CHARS:
        return title[:MAX_TITLE_CHARS]
    return title


__all__ = [
    "IngestionResult",
    "delete_document",
    "find_duplicate",
    "get_document",
    "ingest_pasted_text",
    "ingest_upload",
]
