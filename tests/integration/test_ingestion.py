"""The ingestion pipeline end to end: paste, text file and PDF."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from studyforge.config import Settings
from studyforge.documents.storage import DocumentStorage
from studyforge.models import Concept, Course, Document, DocumentChunk, DocumentStatus
from studyforge.services import documents as service
from studyforge.services.exceptions import NotFoundError, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_fixtures import build_pdf, build_pdf_without_text_layer

LECTURE = """
Binary Search Trees

A binary search tree is a rooted binary tree in which every node stores a key
greater than all keys in its left subtree and less than all keys in its right
subtree.

Balance factor: the height of a node's right subtree minus the height of its
left subtree.

An AVL tree is a self-balancing binary search tree that keeps every balance
factor within the set negative one, zero and one.
"""


@pytest.fixture
def course(db_session: Session) -> Course:
    db_session.add(course := Course(name="Data Structures", code="CS 2420"))
    db_session.commit()
    return course


@pytest.fixture
def storage(settings: Settings) -> DocumentStorage:
    settings.ensure_directories()
    return DocumentStorage(settings.uploads_dir)


class TestPastedText:
    def test_produces_chunks_and_concepts(self, db_session: Session, course: Course) -> None:
        result = service.ingest_pasted_text(
            db_session, course_id=course.id, title="Lecture 1", body=LECTURE
        )
        assert result.succeeded
        assert result.chunk_count > 0
        assert result.concepts_created > 0

        document = result.document
        assert document.status is DocumentStatus.EXTRACTED
        assert document.char_count > 0
        assert document.stored_filename is None, "pasted text has no file on disk"

    def test_concepts_carry_provenance_back_to_the_document(
        self, db_session: Session, course: Course
    ) -> None:
        result = service.ingest_pasted_text(
            db_session, course_id=course.id, title="Lecture 1", body=LECTURE
        )
        concepts = list(db_session.scalars(select(Concept).where(Concept.course_id == course.id)))
        assert concepts
        assert all(c.source_document_id == result.document.id for c in concepts)

    def test_chunk_offsets_locate_the_passage_in_the_document(
        self, db_session: Session, course: Course
    ) -> None:
        """Provenance is only honest if the offsets are real."""
        result = service.ingest_pasted_text(
            db_session, course_id=course.id, title="Lecture 1", body=LECTURE
        )
        text = result.document.extracted_text or ""
        chunks = list(
            db_session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == result.document.id)
            )
        )
        assert chunks
        for chunk in chunks:
            assert 0 <= chunk.char_start < chunk.char_end <= len(text)

    def test_a_second_document_enriches_rather_than_duplicates_concepts(
        self, db_session: Session, course: Course
    ) -> None:
        service.ingest_pasted_text(
            db_session, course_id=course.id, title="One", body="Heapsort. Heapsort. Heapsort."
        )
        service.ingest_pasted_text(
            db_session,
            course_id=course.id,
            title="Two",
            body=(
                "Heapsort is a comparison sort that builds a binary heap and then "
                "repeatedly extracts the maximum element from it."
            ),
        )
        heapsort = db_session.scalars(
            select(Concept).where(
                Concept.course_id == course.id, Concept.normalized_name == "heapsort"
            )
        ).all()
        assert len(heapsort) == 1, "the same concept must not be duplicated"
        assert heapsort[0].has_definition, "the second document should supply the definition"

    def test_notes_too_short_to_use_are_reported_honestly(
        self, db_session: Session, course: Course
    ) -> None:
        result = service.ingest_pasted_text(
            db_session, course_id=course.id, title="Scrap", body="hi"
        )
        assert not result.succeeded
        assert result.document.status is DocumentStatus.NO_TEXT
        assert result.chunk_count == 0
        assert result.document.extraction_error

    @pytest.mark.parametrize("body", ["", "   ", "\n\n"])
    def test_empty_input_is_rejected_with_a_field_error(
        self, db_session: Session, course: Course, body: str
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            service.ingest_pasted_text(db_session, course_id=course.id, title="Empty", body=body)
        assert "body" in caught.value.field_errors

    def test_a_missing_title_is_rejected(self, db_session: Session, course: Course) -> None:
        with pytest.raises(ValidationError) as caught:
            service.ingest_pasted_text(db_session, course_id=course.id, title="  ", body=LECTURE)
        assert "title" in caught.value.field_errors

    def test_ingesting_into_a_missing_course_is_a_not_found(self, db_session: Session) -> None:
        with pytest.raises(NotFoundError):
            service.ingest_pasted_text(db_session, course_id=9999, title="t", body=LECTURE)


class TestTextFileUpload:
    def test_ingests_and_stores_the_file(
        self, db_session: Session, course: Course, storage: DocumentStorage
    ) -> None:
        result = service.ingest_upload(
            db_session,
            storage,
            course_id=course.id,
            filename="lecture1.txt",
            content=LECTURE.encode(),
            declared_content_type="text/plain",
            max_bytes=1_000_000,
        )
        assert result.succeeded
        document = result.document
        assert document.original_filename == "lecture1.txt"
        assert document.stored_filename
        assert storage.resolve(document.stored_filename).exists()
        assert document.sha256

    def test_the_title_defaults_to_the_filename(
        self, db_session: Session, course: Course, storage: DocumentStorage
    ) -> None:
        result = service.ingest_upload(
            db_session,
            storage,
            course_id=course.id,
            filename="week-3-notes.md",
            content=LECTURE.encode(),
            declared_content_type="text/markdown",
            max_bytes=1_000_000,
        )
        assert result.document.title == "week-3-notes.md"

    def test_a_rejected_upload_writes_nothing_to_disk(
        self, db_session: Session, course: Course, storage: DocumentStorage
    ) -> None:
        """Validation runs before storage, so a refusal leaves no trace."""
        storage.root.mkdir(parents=True, exist_ok=True)
        before = set(storage.root.iterdir())
        with pytest.raises(Exception):  # noqa: B017 - any rejection is acceptable here
            service.ingest_upload(
                db_session,
                storage,
                course_id=course.id,
                filename="evil.exe",
                content=b"MZ\x90\x00",
                declared_content_type="application/octet-stream",
                max_bytes=1_000_000,
            )
        assert set(storage.root.iterdir()) == before
        assert db_session.scalar(select(func.count()).select_from(Document)) == 0


class TestPdfUpload:
    def test_extracts_text_from_a_real_pdf(
        self, db_session: Session, course: Course, storage: DocumentStorage
    ) -> None:
        pdf = build_pdf(
            [
                "A binary search tree is a rooted binary tree with keys in sorted order.",
                "An AVL tree is a self-balancing binary search tree with bounded height.",
            ]
        )
        result = service.ingest_upload(
            db_session,
            storage,
            course_id=course.id,
            filename="lecture.pdf",
            content=pdf,
            declared_content_type="application/pdf",
            max_bytes=1_000_000,
        )
        assert result.succeeded
        assert result.document.page_count == 2
        assert "binary search tree" in (result.document.extracted_text or "").lower()
        assert result.chunk_count > 0

    def test_a_scanned_pdf_is_reported_as_scanned_not_as_success(
        self, db_session: Session, course: Course, storage: DocumentStorage
    ) -> None:
        """The case that must never silently produce junk study material."""
        result = service.ingest_upload(
            db_session,
            storage,
            course_id=course.id,
            filename="scan.pdf",
            content=build_pdf_without_text_layer(3),
            declared_content_type="application/pdf",
            max_bytes=1_000_000,
        )
        assert not result.succeeded
        assert result.document.status is DocumentStatus.NO_TEXT
        assert result.document.page_count == 3
        assert result.chunk_count == 0
        error = result.document.extraction_error or ""
        assert "OCR" in error and "scan" in error.lower()

    def test_a_corrupt_pdf_fails_gracefully(
        self, db_session: Session, course: Course, storage: DocumentStorage
    ) -> None:
        result = service.ingest_upload(
            db_session,
            storage,
            course_id=course.id,
            filename="broken.pdf",
            content=b"%PDF-1.4\nthis is not really a pdf at all",
            declared_content_type="application/pdf",
            max_bytes=1_000_000,
        )
        assert result.document.status is DocumentStatus.FAILED
        error = result.document.extraction_error or ""
        assert "corrupted" in error
        assert "Traceback" not in error and "pypdf" not in error


class TestDeletion:
    def test_deleting_removes_the_row_the_chunks_and_the_file(
        self, db_session: Session, course: Course, storage: DocumentStorage
    ) -> None:
        result = service.ingest_upload(
            db_session,
            storage,
            course_id=course.id,
            filename="lecture.txt",
            content=LECTURE.encode(),
            declared_content_type="text/plain",
            max_bytes=1_000_000,
        )
        db_session.commit()
        stored_filename = result.document.stored_filename
        assert stored_filename
        path = storage.resolve(stored_filename)

        service.delete_document(db_session, storage, result.document.id)
        db_session.commit()

        assert not path.exists()
        assert db_session.scalar(select(func.count()).select_from(Document)) == 0
        assert db_session.scalar(select(func.count()).select_from(DocumentChunk)) == 0

    def test_deleting_a_missing_document_is_a_not_found(
        self, db_session: Session, storage: DocumentStorage
    ) -> None:
        with pytest.raises(NotFoundError):
            service.delete_document(db_session, storage, 9999)


class TestDuplicateDetection:
    def test_identical_content_is_detected(self, db_session: Session, course: Course) -> None:
        first = service.ingest_pasted_text(
            db_session, course_id=course.id, title="One", body=LECTURE
        )
        db_session.commit()
        duplicate = service.find_duplicate(
            db_session, course_id=course.id, sha256=first.document.sha256 or ""
        )
        assert duplicate is not None
        assert duplicate.id == first.document.id

    def test_different_content_is_not_flagged(self, db_session: Session, course: Course) -> None:
        service.ingest_pasted_text(db_session, course_id=course.id, title="One", body=LECTURE)
        db_session.commit()
        assert service.find_duplicate(db_session, course_id=course.id, sha256="0" * 64) is None


class TestDeterminism:
    def test_the_same_document_always_produces_the_same_chunks(
        self, db_session: Session, course: Course
    ) -> None:
        first = service.ingest_pasted_text(db_session, course_id=course.id, title="A", body=LECTURE)
        second = service.ingest_pasted_text(
            db_session, course_id=course.id, title="B", body=LECTURE
        )
        db_session.commit()

        def texts(document_id: int) -> list[str]:
            return [
                c.text
                for c in db_session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .order_by(DocumentChunk.ordinal)
                )
            ]

        assert texts(first.document.id) == texts(second.document.id)
