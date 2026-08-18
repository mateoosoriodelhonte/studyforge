"""Quiz lifecycle: generation, taking, grading.

Short-answer grading deserves a note. Comparing free text to an expected answer
is genuinely hard, and pretending otherwise would be dishonest. StudyForge
normalises aggressively (case, punctuation, articles, whitespace) and then
checks for containment in either direction -- which catches "a rooted binary
tree" against "rooted binary tree" but will still mark a correct paraphrase
wrong.

Rather than hide that, the UI offers an explicit **"I was actually right"**
override, and every self-graded answer is flagged in the database so it can be
reported separately. Self-marking is a legitimate study technique; silently
folding it into an accuracy figure is not.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from studyforge.domain.generation.quizzes import QuestionCandidate, generate_questions
from studyforge.domain.generation.quizzes import QuestionKind as DomainQuestionKind
from studyforge.logging_config import log_event
from studyforge.models import (
    AnswerAttempt,
    GenerationMethod,
    Question,
    QuestionKind,
    Quiz,
    QuizAttempt,
)
from studyforge.services.concepts import concept_sources
from studyforge.services.courses import get_course
from studyforge.services.exceptions import ConflictError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

DEFAULT_QUESTION_COUNT = 10

_ARTICLES = frozenset({"a", "an", "the"})
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class QuizGenerationResult:
    """A generated quiz, and an honest account of what could not be generated."""

    quiz: Quiz | None
    requested: int
    generated: int
    reason_if_short: str | None = None

    @property
    def produced_nothing(self) -> bool:
        return self.quiz is None


def generate_quiz(
    session: Session,
    *,
    course_id: int,
    title: str | None = None,
    question_count: int = DEFAULT_QUESTION_COUNT,
) -> QuizGenerationResult:
    """Build a quiz from the course's concepts.

    Returns fewer questions than asked for -- or none at all -- rather than
    padding with weak ones. ``reason_if_short`` explains that to the learner in
    plain language instead of leaving them wondering.
    """
    course = get_course(session, course_id)
    sources = concept_sources(session, course_id)
    candidates = generate_questions(sources, max_questions=question_count)

    if not candidates:
        return QuizGenerationResult(
            quiz=None,
            requested=question_count,
            generated=0,
            reason_if_short=(
                "There are no concepts with definitions in this course yet. "
                "Add some notes, or write a definition on a concept, and try again."
            ),
        )

    quiz = Quiz(
        course_id=course.id,
        title=(title or "").strip() or f"{course.name} quiz",
        generation_method=GenerationMethod.DETERMINISTIC,
    )
    quiz.questions = [_to_model(candidate, ordinal) for ordinal, candidate in enumerate(candidates)]
    session.add(quiz)
    session.flush()

    short_reason = None
    if len(candidates) < question_count:
        short_reason = (
            f"Generated {len(candidates)} of the {question_count} questions requested. "
            "StudyForge only writes a question when the course has enough material to "
            "make it a fair one."
        )

    log_event(
        logger,
        "quiz_generated",
        quiz_id=quiz.id,
        course_id=course_id,
        questions=len(candidates),
        requested=question_count,
    )
    return QuizGenerationResult(
        quiz=quiz,
        requested=question_count,
        generated=len(candidates),
        reason_if_short=short_reason,
    )


def _to_model(candidate: QuestionCandidate, ordinal: int) -> Question:
    return Question(
        ordinal=ordinal,
        kind=(
            QuestionKind.MULTIPLE_CHOICE
            if candidate.kind is DomainQuestionKind.MULTIPLE_CHOICE
            else QuestionKind.SHORT_ANSWER
        ),
        prompt=candidate.prompt,
        expected_answer=candidate.expected_answer,
        explanation=candidate.explanation,
        choices=list(candidate.choices) or None,
        correct_choice_index=candidate.correct_choice_index,
        concept_id=candidate.concept_id,
        source_document_id=candidate.source_document_id,
        source_chunk_id=candidate.source_chunk_id,
    )


def get_quiz(session: Session, quiz_id: int) -> Quiz:
    quiz = session.get(Quiz, quiz_id)
    if quiz is None:
        raise NotFoundError("That quiz does not exist. It may have been deleted.")
    return quiz


def list_quizzes(session: Session, *, course_id: int) -> list[Quiz]:
    return list(
        session.scalars(
            select(Quiz).where(Quiz.course_id == course_id).order_by(Quiz.created_at.desc())
        )
    )


def delete_quiz(session: Session, quiz_id: int) -> None:
    session.delete(get_quiz(session, quiz_id))
    session.flush()


def start_attempt(session: Session, *, quiz_id: int) -> QuizAttempt:
    quiz = get_quiz(session, quiz_id)
    if not quiz.questions:
        raise ConflictError("That quiz has no questions in it.")
    attempt = QuizAttempt(quiz_id=quiz.id, question_count=len(quiz.questions))
    session.add(attempt)
    session.flush()
    log_event(logger, "quiz_attempt_started", attempt_id=attempt.id, quiz_id=quiz.id)
    return attempt


def get_attempt(session: Session, attempt_id: int) -> QuizAttempt:
    attempt = session.get(QuizAttempt, attempt_id)
    if attempt is None:
        raise NotFoundError("That quiz attempt does not exist.")
    return attempt


def answer_question(
    session: Session,
    *,
    attempt_id: int,
    question_id: int,
    response: str,
    self_graded: bool = False,
) -> AnswerAttempt:
    """Record and grade one answer.

    Answering the same question twice within an attempt updates the existing
    record rather than creating a second one, so a browser retry cannot inflate
    a score. The only exception is ``self_graded``, which is *meant* to revise
    an earlier verdict.
    """
    attempt = get_attempt(session, attempt_id)
    question = session.get(Question, question_id)
    if question is None or question.quiz_id != attempt.quiz_id:
        raise NotFoundError("That question is not part of this quiz.")
    if attempt.completed_at is not None:
        raise ConflictError("This attempt has already been submitted.")

    correct = True if self_graded else _grade(question, response)

    existing = session.scalars(
        select(AnswerAttempt).where(
            AnswerAttempt.quiz_attempt_id == attempt.id,
            AnswerAttempt.question_id == question.id,
        )
    ).first()

    if existing is not None:
        existing.response = response
        existing.is_correct = correct
        existing.self_graded = self_graded
        existing.answered_at = datetime.now(UTC)
        answer = existing
    else:
        answer = AnswerAttempt(
            quiz_attempt_id=attempt.id,
            question_id=question.id,
            concept_id=question.concept_id,
            response=response,
            is_correct=correct,
            self_graded=self_graded,
        )
        session.add(answer)

    session.flush()
    attempt.correct_count = _count_correct(session, attempt.id)
    session.flush()
    return answer


def complete_attempt(session: Session, attempt_id: int) -> QuizAttempt:
    attempt = get_attempt(session, attempt_id)
    if attempt.completed_at is None:
        attempt.completed_at = datetime.now(UTC)
        attempt.correct_count = _count_correct(session, attempt.id)
        session.flush()
        log_event(
            logger,
            "quiz_completed",
            attempt_id=attempt.id,
            quiz_id=attempt.quiz_id,
            correct=attempt.correct_count,
            total=attempt.question_count,
        )
    return attempt


def _count_correct(session: Session, attempt_id: int) -> int:
    """Count correct answers with a query rather than off the relationship.

    ``attempt.answers`` is a loaded collection; rows added through
    ``session.add`` in this same transaction do not appear in it, which silently
    under-counted the score. Asking the database is both correct and cheap.
    """
    return (
        session.scalar(
            select(func.count())
            .select_from(AnswerAttempt)
            .where(
                AnswerAttempt.quiz_attempt_id == attempt_id,
                AnswerAttempt.is_correct.is_(True),
            )
        )
        or 0
    )


def _grade(question: Question, response: str) -> bool:
    if question.kind is QuestionKind.MULTIPLE_CHOICE:
        return _grade_multiple_choice(question, response)
    return _grade_short_answer(question.expected_answer, response)


def _grade_multiple_choice(question: Question, response: str) -> bool:
    """Grade by choice index. A non-numeric or out-of-range answer is wrong.

    Deliberately no exception here: a malformed index is untrusted input from a
    form, not a programming error, and the right response is to mark it wrong.
    """
    try:
        chosen = int(response.strip())
    except (TypeError, ValueError):
        return False
    return chosen == question.correct_choice_index


def _grade_short_answer(expected: str, response: str) -> bool:
    """Normalised comparison, tolerant of the differences that do not matter."""
    normalised_expected = normalize_answer(expected)
    normalised_response = normalize_answer(response)
    if not normalised_response or not normalised_expected:
        return False
    if normalised_response == normalised_expected:
        return True
    # Containment in either direction: a learner who writes the core of the
    # definition, or who writes it out more fully, is not wrong.
    return normalised_response in normalised_expected or normalised_expected in normalised_response


def normalize_answer(text: str) -> str:
    """Reduce free text to its comparable core.

    Folds case and accents, strips punctuation, drops articles, and collapses
    whitespace, so "The AVL Tree." and "avl tree" compare equal.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _PUNCTUATION.sub(" ", folded.lower())
    words = [w for w in _WHITESPACE.split(folded) if w and w not in _ARTICLES]
    return " ".join(words)


def require_response(response: str) -> str:
    text = (response or "").strip()
    if not text:
        raise ValidationError(
            "Please answer the question before moving on.",
            {"response": "Write an answer, or skip this question."},
        )
    return text
