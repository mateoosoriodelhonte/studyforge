"""The study and quiz lifecycles against a real database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from studyforge.domain.study.fsrs import Rating
from studyforge.domain.study.queue import QueueReason
from studyforge.models import Concept, Course, Flashcard, GenerationMethod, Review
from studyforge.services import concepts as concept_service
from studyforge.services import documents as document_service
from studyforge.services import flashcards as card_service
from studyforge.services import progress as progress_service
from studyforge.services import quizzes as quiz_service
from studyforge.services import study as study_service
from studyforge.services.exceptions import ConflictError, NotFoundError, ValidationError

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

NOTES = """
Binary Search Trees

A binary search tree is a rooted binary tree in which every node stores a key
greater than all keys in its left subtree.

An AVL tree is a self-balancing binary search tree that keeps every balance
factor within the set negative one, zero and one.

Balance factor: the height of a node's right subtree minus the height of its
left subtree.

Rotation - a local restructuring operation that restores the tree invariant
after an insertion or a deletion.

A heap is a complete binary tree that satisfies the heap ordering property at
every one of its nodes.

Quicksort is a divide and conquer sorting algorithm that partitions an array
around a chosen pivot element.
"""


def correct_response(question: object) -> str:
    """The response a learner would submit to get this question right.

    Multiple choice is answered by index (that is what a radio button posts);
    short answer by text.
    """
    if question.is_multiple_choice:  # type: ignore[attr-defined]
        return str(question.correct_choice_index)  # type: ignore[attr-defined]
    return str(question.expected_answer)  # type: ignore[attr-defined]


@pytest.fixture
def course(db_session: Session) -> Course:
    db_session.add(course := Course(name="Data Structures", code="CS 2420"))
    db_session.commit()
    return course


@pytest.fixture
def stocked_course(db_session: Session, course: Course) -> Course:
    document_service.ingest_pasted_text(
        db_session, course_id=course.id, title="Lecture 1", body=NOTES
    )
    db_session.commit()
    return course


class TestCardGeneration:
    def test_generates_cards_from_extracted_concepts(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        summary = card_service.generate_and_accept(db_session, course_id=stocked_course.id)
        assert summary.created_count > 0
        for card in summary.created:
            assert card.generation_method is GenerationMethod.DETERMINISTIC
            assert card.concept_id is not None
            assert card.generated_at is not None

    def test_generated_cards_keep_provenance_to_a_chunk(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        summary = card_service.generate_and_accept(db_session, course_id=stocked_course.id)
        assert any(card.source_chunk_id is not None for card in summary.created)

    def test_running_generation_twice_creates_no_duplicates(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        first = card_service.generate_and_accept(db_session, course_id=stocked_course.id)
        db_session.commit()
        second = card_service.generate_and_accept(db_session, course_id=stocked_course.id)
        db_session.commit()
        assert first.created_count > 0
        assert second.created_count == 0
        assert db_session.scalar(select(func.count()).select_from(Flashcard)) == first.created_count

    def test_proposing_does_not_save_anything(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        """Generation is a proposal; nothing enters the queue unaccepted."""
        proposed = card_service.propose_cards(db_session, course_id=stocked_course.id)
        assert proposed
        assert db_session.scalar(select(func.count()).select_from(Flashcard)) == 0

    def test_an_empty_course_generates_nothing(self, db_session: Session, course: Course) -> None:
        summary = card_service.generate_and_accept(db_session, course_id=course.id)
        assert summary.produced_nothing


class TestCardLifecycle:
    def test_manual_create_edit_and_delete(self, db_session: Session, course: Course) -> None:
        card = card_service.create_card(
            db_session, course_id=course.id, front="What is O(1)?", back="Constant time."
        )
        db_session.commit()
        assert card.generation_method is GenerationMethod.MANUAL

        card_service.update_card(db_session, card.id, front="What is O(1)?", back="Constant.")
        db_session.commit()
        assert card.back == "Constant."

        card_service.delete_card(db_session, card.id)
        db_session.commit()
        assert db_session.scalar(select(func.count()).select_from(Flashcard)) == 0

    @pytest.mark.parametrize("side", ["front", "back"])
    def test_an_empty_side_is_rejected_with_a_field_error(
        self, db_session: Session, course: Course, side: str
    ) -> None:
        kwargs = {"front": "q", "back": "a", side: "   "}
        with pytest.raises(ValidationError) as caught:
            card_service.create_card(db_session, course_id=course.id, **kwargs)  # type: ignore[arg-type]
        assert side in caught.value.field_errors

    def test_suspension_keeps_the_card_and_its_history(
        self, db_session: Session, course: Course
    ) -> None:
        card = card_service.create_card(db_session, course_id=course.id, front="q", back="a")
        db_session.commit()
        study_service.record_review(db_session, card_id=card.id, rating=Rating.GOOD, now=NOW)
        db_session.commit()

        card_service.set_suspended(db_session, card.id, suspended=True)
        db_session.commit()
        assert card.is_suspended
        assert card.reps == 1
        assert len(card.reviews) == 1


class TestReviewing:
    def test_a_review_updates_the_schedule_and_writes_history(
        self, db_session: Session, course: Course
    ) -> None:
        card = card_service.create_card(db_session, course_id=course.id, front="q", back="a")
        db_session.commit()

        outcome = study_service.record_review(
            db_session, card_id=card.id, rating=Rating.GOOD, now=NOW
        )
        db_session.commit()

        assert not outcome.was_duplicate
        assert card.reps == 1
        assert card.stability is not None
        assert card.due_at > NOW
        [review] = card.reviews
        assert review.rating == 3
        assert review.state_before is not None
        assert review.stability_after == card.stability

    def test_a_double_submission_does_not_review_twice(
        self, db_session: Session, course: Course
    ) -> None:
        """A retried request must not corrupt the interval."""
        card = card_service.create_card(db_session, course_id=course.id, front="q", back="a")
        db_session.commit()

        first = study_service.record_review(
            db_session, card_id=card.id, rating=Rating.GOOD, now=NOW
        )
        second = study_service.record_review(
            db_session, card_id=card.id, rating=Rating.GOOD, now=NOW
        )
        db_session.commit()

        assert not first.was_duplicate
        assert second.was_duplicate
        assert card.reps == 1
        assert db_session.scalar(select(func.count()).select_from(Review)) == 1

    def test_a_suspended_card_cannot_be_reviewed(self, db_session: Session, course: Course) -> None:
        card = card_service.create_card(db_session, course_id=course.id, front="q", back="a")
        card_service.set_suspended(db_session, card.id, suspended=True)
        db_session.commit()
        with pytest.raises(ConflictError):
            study_service.record_review(db_session, card_id=card.id, rating=Rating.GOOD, now=NOW)

    def test_reviewing_a_missing_card_is_a_not_found(self, db_session: Session) -> None:
        with pytest.raises(NotFoundError):
            study_service.record_review(db_session, card_id=9999, rating=Rating.GOOD, now=NOW)

    def test_the_session_tracks_its_own_totals(self, db_session: Session, course: Course) -> None:
        cards = [
            card_service.create_card(db_session, course_id=course.id, front=f"q{i}", back="a")
            for i in range(3)
        ]
        db_session.commit()
        session_row = study_service.start_session(db_session, course_id=course.id, now=NOW)
        db_session.commit()

        for card, rating in zip(cards, [Rating.GOOD, Rating.AGAIN, Rating.EASY], strict=True):
            study_service.record_review(
                db_session,
                card_id=card.id,
                rating=rating,
                study_session_id=session_row.id,
                now=NOW,
            )
        study_service.end_session(db_session, session_row.id, now=NOW + timedelta(minutes=5))
        db_session.commit()

        assert session_row.cards_reviewed == 3
        assert session_row.again_count == 1
        assert session_row.recall_rate == pytest.approx(2 / 3)
        assert not session_row.is_active

    def test_interval_previews_are_labelled_for_the_buttons(
        self, db_session: Session, course: Course
    ) -> None:
        """The learner should see the schedule, not be asked to trust it."""
        card = card_service.create_card(db_session, course_id=course.id, front="q", back="a")
        db_session.commit()
        previews = study_service.preview_intervals(card, now=NOW)
        assert set(previews) == set(Rating)
        assert all(label and label[-1] in "mhdoy" for label in previews.values())
        assert card.reps == 0, "previewing must not touch the card"


class TestQueue:
    def test_an_overdue_card_outranks_a_new_one(self, db_session: Session, course: Course) -> None:
        new_card = card_service.create_card(db_session, course_id=course.id, front="new", back="a")
        old_card = card_service.create_card(db_session, course_id=course.id, front="old", back="a")
        db_session.commit()
        study_service.record_review(
            db_session, card_id=old_card.id, rating=Rating.EASY, now=NOW - timedelta(days=90)
        )
        db_session.commit()

        plan = study_service.build_study_queue(db_session, course_id=course.id, now=NOW)
        assert plan.entries[0].card_id == old_card.id
        assert plan.entries[0].reason is QueueReason.OVERDUE
        assert new_card.id in {e.card_id for e in plan.entries}

    def test_suspended_cards_stay_out_of_the_queue(
        self, db_session: Session, course: Course
    ) -> None:
        card = card_service.create_card(db_session, course_id=course.id, front="q", back="a")
        card_service.set_suspended(db_session, card.id, suspended=True)
        db_session.commit()
        assert study_service.build_study_queue(db_session, course_id=course.id, now=NOW).is_empty

    def test_the_queue_loads_cards_in_order(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        card_service.generate_and_accept(db_session, course_id=stocked_course.id)
        db_session.commit()
        plan = study_service.build_study_queue(db_session, course_id=stocked_course.id, now=NOW)
        loaded = study_service.queue_cards(db_session, plan)
        assert [card.id for card, _ in loaded] == [e.card_id for e in plan.entries]

    def test_a_queue_across_all_courses(self, db_session: Session) -> None:
        for name in ("A", "B"):
            db_session.add(course := Course(name=name))
            db_session.flush()
            card_service.create_card(db_session, course_id=course.id, front="q", back="a")
        db_session.commit()
        assert study_service.build_study_queue(db_session, now=NOW).size == 2


class TestQuizLifecycle:
    def test_generates_grades_and_completes(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        result = quiz_service.generate_quiz(
            db_session, course_id=stocked_course.id, question_count=5
        )
        db_session.commit()
        assert result.quiz is not None
        quiz = result.quiz
        assert quiz.questions

        attempt = quiz_service.start_attempt(db_session, quiz_id=quiz.id)
        db_session.commit()

        for question in quiz.questions:
            quiz_service.answer_question(
                db_session,
                attempt_id=attempt.id,
                question_id=question.id,
                response=correct_response(question),
            )
        quiz_service.complete_attempt(db_session, attempt.id)
        db_session.commit()

        assert attempt.is_complete
        assert attempt.correct_count == len(quiz.questions)
        assert attempt.accuracy == pytest.approx(1.0)

    def test_wrong_answers_are_graded_wrong(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        quiz = quiz_service.generate_quiz(db_session, course_id=stocked_course.id).quiz
        assert quiz is not None
        attempt = quiz_service.start_attempt(db_session, quiz_id=quiz.id)
        db_session.commit()

        answer = quiz_service.answer_question(
            db_session,
            attempt_id=attempt.id,
            question_id=quiz.questions[0].id,
            response="completely unrelated nonsense",
        )
        db_session.commit()
        assert not answer.is_correct

    def test_answering_twice_updates_rather_than_duplicating(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        quiz = quiz_service.generate_quiz(db_session, course_id=stocked_course.id).quiz
        assert quiz is not None
        attempt = quiz_service.start_attempt(db_session, quiz_id=quiz.id)
        question = quiz.questions[0]
        db_session.commit()

        quiz_service.answer_question(
            db_session, attempt_id=attempt.id, question_id=question.id, response="wrong"
        )
        quiz_service.answer_question(
            db_session,
            attempt_id=attempt.id,
            question_id=question.id,
            response=correct_response(question),
        )
        db_session.commit()
        assert len(attempt.answers) == 1
        assert attempt.answers[0].is_correct

    def test_self_grading_is_recorded_separately(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        """Self-marking is legitimate; folding it silently into accuracy is not."""
        quiz = quiz_service.generate_quiz(
            db_session, course_id=stocked_course.id, question_count=20
        ).quiz
        assert quiz is not None
        attempt = quiz_service.start_attempt(db_session, quiz_id=quiz.id)
        db_session.commit()

        answer = quiz_service.answer_question(
            db_session,
            attempt_id=attempt.id,
            question_id=quiz.questions[0].id,
            response="my own paraphrase",
            self_graded=True,
        )
        db_session.commit()
        assert answer.is_correct
        assert answer.self_graded

    def test_an_empty_course_produces_no_quiz_and_says_why(
        self, db_session: Session, course: Course
    ) -> None:
        result = quiz_service.generate_quiz(db_session, course_id=course.id)
        assert result.produced_nothing
        assert result.reason_if_short
        assert "definition" in result.reason_if_short

    def test_asking_for_more_questions_than_the_material_supports_is_explained(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        result = quiz_service.generate_quiz(
            db_session, course_id=stocked_course.id, question_count=100
        )
        assert result.generated < 100
        assert result.reason_if_short
        assert "fair one" in result.reason_if_short

    def test_a_completed_attempt_rejects_further_answers(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        quiz = quiz_service.generate_quiz(db_session, course_id=stocked_course.id).quiz
        assert quiz is not None
        attempt = quiz_service.start_attempt(db_session, quiz_id=quiz.id)
        quiz_service.complete_attempt(db_session, attempt.id)
        db_session.commit()
        with pytest.raises(ConflictError):
            quiz_service.answer_question(
                db_session,
                attempt_id=attempt.id,
                question_id=quiz.questions[0].id,
                response="x",
            )

    def test_a_question_from_another_quiz_is_rejected(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        first = quiz_service.generate_quiz(db_session, course_id=stocked_course.id).quiz
        second = quiz_service.generate_quiz(db_session, course_id=stocked_course.id).quiz
        assert first is not None and second is not None
        attempt = quiz_service.start_attempt(db_session, quiz_id=first.id)
        db_session.commit()
        with pytest.raises(NotFoundError):
            quiz_service.answer_question(
                db_session,
                attempt_id=attempt.id,
                question_id=second.questions[0].id,
                response="x",
            )


class TestShortAnswerGrading:
    @pytest.mark.parametrize(
        ("expected", "response"),
        [
            ("A rooted binary tree", "rooted binary tree"),
            ("A rooted binary tree", "  THE ROOTED BINARY TREE  "),
            ("A rooted binary tree", "a rooted, binary tree."),
            ("Depth-first search", "depth first search"),
            ("Naïve method", "naive method"),
        ],
    )
    def test_accepts_answers_that_differ_only_cosmetically(
        self, expected: str, response: str
    ) -> None:
        assert quiz_service._grade_short_answer(expected, response)

    @pytest.mark.parametrize(
        ("expected", "response"),
        [("A rooted binary tree", "a linked list"), ("Quicksort", "mergesort"), ("Heap", "")],
    )
    def test_rejects_genuinely_different_answers(self, expected: str, response: str) -> None:
        assert not quiz_service._grade_short_answer(expected, response)

    def test_a_malformed_choice_index_is_marked_wrong_not_raised(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        """Untrusted form input is graded, not treated as a bug."""
        quiz = quiz_service.generate_quiz(
            db_session, course_id=stocked_course.id, question_count=20
        ).quiz
        assert quiz is not None
        mcq = next((q for q in quiz.questions if q.is_multiple_choice), None)
        if mcq is None:
            pytest.skip("this course did not yield a multiple-choice question")
        attempt = quiz_service.start_attempt(db_session, quiz_id=quiz.id)
        db_session.commit()
        for bogus in ("banana", "-1", "999", ""):
            answer = quiz_service.answer_question(
                db_session, attempt_id=attempt.id, question_id=mcq.id, response=bogus
            )
            assert not answer.is_correct


class TestProgressReporting:
    def test_a_fresh_install_reports_no_data_rather_than_zero(self, db_session: Session) -> None:
        report = progress_service.build_report(db_session, now=NOW)
        assert report.is_empty
        assert report.review_recall.value is None
        assert report.quiz_accuracy.value is None
        assert report.review_recall.percent is None

    def test_reviews_and_answers_move_the_numbers(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        card_service.generate_and_accept(db_session, course_id=stocked_course.id)
        db_session.commit()

        plan = study_service.build_study_queue(db_session, course_id=stocked_course.id, now=NOW)
        for index, entry in enumerate(plan.entries[:6]):
            study_service.record_review(
                db_session,
                card_id=entry.card_id,
                rating=Rating.GOOD if index % 2 == 0 else Rating.AGAIN,
                now=NOW + timedelta(seconds=index),
            )
        db_session.commit()

        report = progress_service.build_report(db_session, course_id=stocked_course.id, now=NOW)
        assert report.reviews_total == 6
        assert report.review_recall.denominator == 6
        assert report.review_recall.percent == 50
        assert report.has_any_activity

    def test_a_rate_reports_its_sample_size(self, db_session: Session, course: Course) -> None:
        """100% from three answers must be visibly 100% from three answers."""
        card = card_service.create_card(db_session, course_id=course.id, front="q", back="a")
        db_session.commit()
        study_service.record_review(db_session, card_id=card.id, rating=Rating.GOOD, now=NOW)
        db_session.commit()
        report = progress_service.build_report(db_session, now=NOW)
        assert report.review_recall.numerator == 1
        assert report.review_recall.denominator == 1

    def test_the_activity_chart_includes_days_with_nothing(
        self, db_session: Session, course: Course
    ) -> None:
        """Omitting zero-days would make a lapsed streak look continuous."""
        report = progress_service.build_report(db_session, now=NOW)
        assert len(report.activity) == progress_service.ACTIVITY_WINDOW_DAYS
        assert all(day.total == 0 for day in report.activity)

    def test_concepts_without_evidence_count_as_not_enough_data(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        from studyforge.domain.study.weakness import ConceptStatus

        report = progress_service.build_report(db_session, course_id=stocked_course.id, now=NOW)
        total = db_session.scalar(
            select(func.count()).select_from(Concept).where(Concept.course_id == stocked_course.id)
        )
        assert report.status_counts[ConceptStatus.NOT_ENOUGH_DATA] == total

    def test_repeated_failures_surface_as_a_weak_concept(
        self, db_session: Session, stocked_course: Course
    ) -> None:
        concept = concept_service.list_concepts(db_session, course_id=stocked_course.id)[0]
        card = card_service.create_card(
            db_session,
            course_id=stocked_course.id,
            front="q",
            back="a",
            concept_id=concept.id,
        )
        db_session.commit()

        for index in range(6):
            study_service.record_review(
                db_session,
                card_id=card.id,
                rating=Rating.AGAIN,
                now=NOW + timedelta(minutes=index),
            )
        db_session.commit()

        report = progress_service.build_report(
            db_session, course_id=stocked_course.id, now=NOW + timedelta(hours=1)
        )
        assert concept.id in {a.concept_id for a in report.weak_concepts}
