"""Persistence behaviour: round-trips, cascades, constraints and UTC handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from studyforge.db import create_session_factory, foreign_keys_enforced
from studyforge.domain.study.fsrs import CardState, Rating, Scheduler, SchedulingCard
from studyforge.models import (
    AnswerAttempt,
    Concept,
    Course,
    Document,
    DocumentChunk,
    DocumentSource,
    DocumentStatus,
    ExtractionMethod,
    Flashcard,
    GenerationMethod,
    Question,
    QuestionKind,
    Quiz,
    QuizAttempt,
    Review,
    StudySession,
)

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def course(db_session: Session) -> Course:
    course = Course(name="Data Structures", code="CS 2420")
    db_session.add(course)
    db_session.commit()
    return course


class TestSQLiteConfiguration:
    def test_foreign_keys_are_actually_enforced(self, db_session: Session) -> None:
        """SQLite ships with FK enforcement off; the pragma must be applied."""
        assert foreign_keys_enforced(db_session)

    def test_an_orphan_row_is_rejected(self, db_session: Session) -> None:
        db_session.add(Document(course_id=9999, title="x", source_type=DocumentSource.PASTE))
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestCourse:
    def test_round_trips(self, db_session: Session, course: Course) -> None:
        loaded = db_session.get(Course, course.id)
        assert loaded is not None
        assert loaded.name == "Data Structures"
        assert loaded.display_name == "CS 2420 - Data Structures"
        assert not loaded.is_archived

    def test_display_name_without_a_code(self, db_session: Session) -> None:
        db_session.add(course := Course(name="German A1"))
        db_session.commit()
        assert course.display_name == "German A1"

    def test_archiving_is_a_timestamp_not_a_deletion(
        self, db_session: Session, course: Course
    ) -> None:
        course.archived_at = NOW
        db_session.commit()
        assert course.is_archived
        assert db_session.get(Course, course.id) is not None

    def test_timestamps_are_populated_and_aware(self, db_session: Session, course: Course) -> None:
        assert course.created_at.tzinfo is not None
        assert course.updated_at.tzinfo is not None


class TestUTCHandling:
    def test_aware_datetimes_survive_the_round_trip(
        self, db_session: Session, course: Course, engine: Engine
    ) -> None:
        """A naive value coming back would silently corrupt every interval."""
        card = Flashcard(course_id=course.id, front="q", back="a", due_at=NOW)
        db_session.add(card)
        db_session.commit()

        # Read through a fresh session so the value really comes from SQLite.
        with create_session_factory(engine)() as fresh:
            loaded = fresh.get(Flashcard, card.id)
            assert loaded is not None
            assert loaded.due_at.tzinfo is not None
            assert loaded.due_at == NOW

    def test_naive_input_is_interpreted_as_utc(
        self, db_session: Session, course: Course, engine: Engine
    ) -> None:
        naive = datetime(2026, 3, 1, 9, 0)
        card = Flashcard(course_id=course.id, front="q", back="a", due_at=naive)
        db_session.add(card)
        db_session.commit()

        with create_session_factory(engine)() as fresh:
            loaded = fresh.get(Flashcard, card.id)
            assert loaded is not None
            assert loaded.due_at == NOW


class TestDocument:
    def test_round_trips_with_chunks(self, db_session: Session, course: Course) -> None:
        doc = Document(
            course_id=course.id,
            title="Lecture 1",
            source_type=DocumentSource.PASTE,
            status=DocumentStatus.EXTRACTED,
            extracted_text="A tree is an acyclic graph.",
            char_count=27,
        )
        doc.chunks = [
            DocumentChunk(ordinal=0, text="A tree is", char_start=0, char_end=9),
            DocumentChunk(ordinal=1, text="an acyclic graph.", char_start=10, char_end=27),
        ]
        db_session.add(doc)
        db_session.commit()

        loaded = db_session.get(Document, doc.id)
        assert loaded is not None
        assert loaded.is_ready
        assert [c.ordinal for c in loaded.chunks] == [0, 1]
        assert loaded.chunks[1].char_count == len("an acyclic graph.")

    def test_a_scanned_pdf_is_not_ready(self, db_session: Session, course: Course) -> None:
        doc = Document(
            course_id=course.id,
            title="scan.pdf",
            source_type=DocumentSource.UPLOAD,
            status=DocumentStatus.NO_TEXT,
        )
        db_session.add(doc)
        db_session.commit()
        assert not doc.is_ready

    def test_chunk_ordinals_are_unique_per_document(
        self, db_session: Session, course: Course
    ) -> None:
        doc = Document(course_id=course.id, title="d", source_type=DocumentSource.PASTE)
        doc.chunks = [
            DocumentChunk(ordinal=0, text="a", char_start=0, char_end=1),
            DocumentChunk(ordinal=0, text="b", char_start=1, char_end=2),
        ]
        db_session.add(doc)
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestConcept:
    def test_is_unique_per_course_by_normalised_name(
        self, db_session: Session, course: Course
    ) -> None:
        db_session.add(Concept(course_id=course.id, name="AVL Tree", normalized_name="avl tree"))
        db_session.commit()
        db_session.add(Concept(course_id=course.id, name="avl trees", normalized_name="avl tree"))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_the_same_concept_may_exist_in_two_courses(self, db_session: Session) -> None:
        a = Course(name="A")
        b = Course(name="B")
        db_session.add_all([a, b])
        db_session.commit()
        db_session.add_all(
            [
                Concept(course_id=a.id, name="Graph", normalized_name="graph"),
                Concept(course_id=b.id, name="Graph", normalized_name="graph"),
            ]
        )
        db_session.commit()  # must not raise

    def test_records_its_extraction_evidence(self, db_session: Session, course: Course) -> None:
        concept = Concept(
            course_id=course.id,
            name="Big-O",
            normalized_name="big-o",
            definition="An upper bound on growth rate.",
            extraction_method=ExtractionMethod.DEFINITION,
            score=0.8,
        )
        db_session.add(concept)
        db_session.commit()
        assert concept.has_definition
        assert concept.extraction_method is ExtractionMethod.DEFINITION


class TestFlashcardScheduling:
    def test_maps_onto_the_pure_scheduler_and_back(
        self, db_session: Session, course: Course
    ) -> None:
        """The ORM <-> domain seam must preserve every field of the memory state."""
        card = Flashcard(course_id=course.id, front="What is O(1)?", back="Constant", due_at=NOW)
        db_session.add(card)
        db_session.commit()

        scheduler = Scheduler()
        snapshot = scheduler.review(card.to_scheduling_card(), Rating.GOOD, reviewed_at=NOW)
        card.apply_scheduling(snapshot.card_after)
        db_session.commit()

        assert card.stability == snapshot.card_after.stability
        assert card.difficulty == snapshot.card_after.difficulty
        assert card.state is snapshot.card_after.state
        assert card.due_at == snapshot.card_after.due_at
        assert card.reps == 1
        assert not card.is_new

    def test_a_fresh_card_projects_as_new(self, db_session: Session, course: Course) -> None:
        card = Flashcard(course_id=course.id, front="q", back="a", due_at=NOW)
        db_session.add(card)
        db_session.commit()
        projected: SchedulingCard = card.to_scheduling_card()
        assert projected.is_new
        assert projected.state is CardState.LEARNING
        assert card.is_new

    def test_review_history_persists_before_and_after_state(
        self, db_session: Session, course: Course
    ) -> None:
        card = Flashcard(course_id=course.id, front="q", back="a", due_at=NOW)
        db_session.add(card)
        db_session.commit()

        before = card.to_scheduling_card()
        snapshot = Scheduler().review(before, Rating.HARD, reviewed_at=NOW)
        card.apply_scheduling(snapshot.card_after)
        db_session.add(
            Review(
                flashcard_id=card.id,
                rating=int(snapshot.rating),
                reviewed_at=snapshot.reviewed_at,
                scheduled_days=snapshot.scheduled_interval.days,
                state_before=before.state,
                state_after=snapshot.card_after.state,
                stability_before=before.stability,
                stability_after=snapshot.card_after.stability,
                difficulty_before=before.difficulty,
                difficulty_after=snapshot.card_after.difficulty,
            )
        )
        db_session.commit()

        [review] = card.reviews
        assert review.rating == 2
        assert review.was_recalled, "Hard is a successful recall, not a lapse"
        assert review.stability_before is None
        assert review.stability_after is not None

    def test_suspension_is_a_timestamp(self, db_session: Session, course: Course) -> None:
        card = Flashcard(course_id=course.id, front="q", back="a", due_at=NOW)
        db_session.add(card)
        db_session.commit()
        assert not card.is_suspended
        card.suspended_at = NOW
        db_session.commit()
        assert card.is_suspended

    def test_generated_cards_keep_their_provenance(
        self, db_session: Session, course: Course
    ) -> None:
        doc = Document(course_id=course.id, title="d", source_type=DocumentSource.PASTE)
        doc.chunks = [DocumentChunk(ordinal=0, text="t", char_start=0, char_end=1)]
        db_session.add(doc)
        db_session.commit()

        card = Flashcard(
            course_id=course.id,
            front="q",
            back="a",
            due_at=NOW,
            generation_method=GenerationMethod.DETERMINISTIC,
            source_document_id=doc.id,
            source_chunk_id=doc.chunks[0].id,
            generated_at=NOW,
        )
        db_session.add(card)
        db_session.commit()
        assert card.is_generated
        assert card.source_chunk_id == doc.chunks[0].id


class TestQuiz:
    def test_full_attempt_round_trip(self, db_session: Session, course: Course) -> None:
        quiz = Quiz(course_id=course.id, title="Week 1")
        quiz.questions = [
            Question(
                ordinal=0,
                kind=QuestionKind.MULTIPLE_CHOICE,
                prompt="Complexity of binary search?",
                expected_answer="O(log n)",
                choices=["O(1)", "O(log n)", "O(n)", "O(n log n)"],
                correct_choice_index=1,
            ),
            Question(
                ordinal=1,
                kind=QuestionKind.SHORT_ANSWER,
                prompt="Define a leaf node.",
                expected_answer="A node with no children.",
            ),
        ]
        db_session.add(quiz)
        db_session.commit()

        attempt = QuizAttempt(quiz_id=quiz.id, question_count=2)
        attempt.answers = [
            AnswerAttempt(question_id=quiz.questions[0].id, response="1", is_correct=True),
            AnswerAttempt(question_id=quiz.questions[1].id, response="dunno", is_correct=False),
        ]
        db_session.add(attempt)
        db_session.commit()

        assert quiz.question_count == 2
        assert quiz.questions[0].is_multiple_choice
        assert quiz.questions[0].choices == ["O(1)", "O(log n)", "O(n)", "O(n log n)"]
        assert attempt.accuracy == 0.5
        assert not attempt.is_complete

    def test_an_unanswered_attempt_reports_no_accuracy_rather_than_zero(
        self, db_session: Session, course: Course
    ) -> None:
        """'No data' and 'scored zero' are different facts."""
        quiz = Quiz(course_id=course.id, title="q")
        db_session.add(quiz)
        db_session.commit()
        attempt = QuizAttempt(quiz_id=quiz.id)
        db_session.add(attempt)
        db_session.commit()
        assert attempt.accuracy is None

    def test_a_question_cannot_be_answered_twice_in_one_attempt(
        self, db_session: Session, course: Course
    ) -> None:
        quiz = Quiz(course_id=course.id, title="q")
        quiz.questions = [
            Question(ordinal=0, kind=QuestionKind.SHORT_ANSWER, prompt="p", expected_answer="a")
        ]
        db_session.add(quiz)
        db_session.commit()
        attempt = QuizAttempt(quiz_id=quiz.id)
        attempt.answers = [
            AnswerAttempt(question_id=quiz.questions[0].id, response="x"),
            AnswerAttempt(question_id=quiz.questions[0].id, response="y"),
        ]
        db_session.add(attempt)
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestStudySession:
    def test_tracks_recall_rate(self, db_session: Session) -> None:
        session = StudySession(started_at=NOW, cards_reviewed=10, again_count=3)
        db_session.add(session)
        db_session.commit()
        assert session.is_active
        assert session.recall_rate == pytest.approx(0.7)

    def test_an_empty_session_reports_no_recall_rate(self, db_session: Session) -> None:
        session = StudySession(started_at=NOW)
        db_session.add(session)
        db_session.commit()
        assert session.recall_rate is None

    def test_can_span_every_course(self, db_session: Session) -> None:
        session = StudySession(started_at=NOW, course_id=None)
        db_session.add(session)
        db_session.commit()
        assert session.course_id is None


class TestCascades:
    def test_deleting_a_course_removes_everything_under_it(
        self, db_session: Session, course: Course
    ) -> None:
        doc = Document(course_id=course.id, title="d", source_type=DocumentSource.PASTE)
        doc.chunks = [DocumentChunk(ordinal=0, text="t", char_start=0, char_end=1)]
        db_session.add(doc)
        card = Flashcard(course_id=course.id, front="q", back="a", due_at=NOW)
        db_session.add(card)
        db_session.commit()
        db_session.add(
            Review(
                flashcard_id=card.id,
                rating=3,
                reviewed_at=NOW,
                state_before=CardState.LEARNING,
                state_after=CardState.REVIEW,
            )
        )
        db_session.commit()

        db_session.delete(course)
        db_session.commit()

        for model in (Document, DocumentChunk, Flashcard, Review):
            count = db_session.scalar(select(func.count()).select_from(model))
            assert count == 0, f"{model.__name__} rows orphaned"

    def test_deleting_a_source_document_keeps_the_card_and_its_history(
        self, db_session: Session, course: Course
    ) -> None:
        """Losing the source must not silently destroy the learner's progress."""
        doc = Document(course_id=course.id, title="d", source_type=DocumentSource.PASTE)
        db_session.add(doc)
        db_session.commit()
        card = Flashcard(
            course_id=course.id,
            front="q",
            back="a",
            due_at=NOW,
            source_document_id=doc.id,
            generation_method=GenerationMethod.DETERMINISTIC,
        )
        db_session.add(card)
        db_session.commit()

        db_session.delete(doc)
        db_session.commit()
        db_session.refresh(card)

        assert db_session.get(Flashcard, card.id) is not None
        assert card.source_document_id is None

    def test_deleting_a_card_removes_its_reviews(self, db_session: Session, course: Course) -> None:
        card = Flashcard(course_id=course.id, front="q", back="a", due_at=NOW)
        db_session.add(card)
        db_session.commit()
        db_session.add(
            Review(
                flashcard_id=card.id,
                rating=1,
                reviewed_at=NOW + timedelta(days=1),
                state_before=CardState.LEARNING,
                state_after=CardState.LEARNING,
            )
        )
        db_session.commit()

        db_session.delete(card)
        db_session.commit()
        assert db_session.scalar(select(func.count()).select_from(Review)) == 0
