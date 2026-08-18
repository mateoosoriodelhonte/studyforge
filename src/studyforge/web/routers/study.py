"""The review loop and the quiz-taking flow.

The card queue is rebuilt on each request rather than held in a server-side
session. That keeps the whole flow stateless and refresh-safe: closing the tab
mid-session loses nothing, and a reviewed card simply is not due any more, so it
drops out of the next queue on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse

from studyforge.dependencies import SessionDep
from studyforge.domain.study.fsrs import Rating
from studyforge.domain.study.queue import QueueReason
from studyforge.models import Question
from studyforge.services import courses as course_service
from studyforge.services import flashcards as card_service
from studyforge.services import quizzes as quiz_service
from studyforge.services import study as study_service
from studyforge.services.exceptions import NotFoundError
from studyforge.web.templating import build_templates, flash, render

router = APIRouter()
templates = build_templates()

FormInt = Annotated[int, Form()]
FormStr = Annotated[str, Form()]

REASON_LABELS = {
    QueueReason.OVERDUE: "Overdue",
    QueueReason.DUE: "Due today",
    QueueReason.WEAK_CONCEPT: "Brought forward — you have been getting this wrong",
    QueueReason.NEW: "New card",
}


@router.get("/study")
async def study_page(
    request: Request,
    session: SessionDep,
    course_id: int | None = Query(default=None),
    session_id: int | None = Query(default=None),
) -> Any:
    now = datetime.now(UTC)
    plan = study_service.build_study_queue(session, course_id=course_id, now=now)
    cards = study_service.queue_cards(session, plan)

    if not cards:
        return render(templates, request, "study.html", {"active_nav": "study", "card": None})

    if session_id is None:
        session_id = study_service.start_session(session, course_id=course_id, now=now).id

    card, reason = cards[0]
    return render(
        templates,
        request,
        "study.html",
        {
            "active_nav": "study",
            "card": card,
            "reason": reason.value,
            "reason_label": REASON_LABELS[reason],
            "revealed": False,
            "session_id": session_id,
            "position": 1,
            "total": plan.size,
            "intervals": _intervals(card, now),
        },
    )


@router.get("/study/reveal")
async def reveal(
    request: Request,
    session: SessionDep,
    card_id: int,
    session_id: int,
    position: int = 1,
    total: int = 1,
    reason: str = "due",
) -> Any:
    """Swap in the answer.

    A separate round trip on purpose: the answer genuinely is not in the DOM
    until it is asked for, so it cannot be read out of the page source.
    """
    card = card_service.get_card(session, card_id)
    return render(
        templates,
        request,
        "partials/study_card.html",
        {
            "card": card,
            "revealed": True,
            "reason": reason,
            "reason_label": REASON_LABELS.get(QueueReason(reason), "Due"),
            "session_id": session_id,
            "position": position,
            "total": total,
            "intervals": _intervals(card, datetime.now(UTC)),
        },
    )


@router.post("/study/review")
async def submit_review(
    request: Request,
    session: SessionDep,
    card_id: FormInt,
    rating: FormInt,
    session_id: FormInt,
) -> Any:
    now = datetime.now(UTC)
    try:
        parsed = Rating(rating)
    except ValueError as error:
        raise NotFoundError("That rating is not one StudyForge understands.") from error

    study_service.record_review(
        session, card_id=card_id, rating=parsed, study_session_id=session_id, now=now
    )

    study_session = study_service.get_session(session, session_id)
    plan = study_service.build_study_queue(session, course_id=study_session.course_id, now=now)
    remaining = study_service.queue_cards(session, plan)

    if not remaining:
        study_service.end_session(session, session_id, now=now)
        return render(
            templates,
            request,
            "partials/study_done.html",
            {"session": study_session},
        )

    card, reason = remaining[0]
    reviewed = study_session.cards_reviewed
    return render(
        templates,
        request,
        "partials/study_card.html",
        {
            "card": card,
            "revealed": False,
            "reason": reason.value,
            "reason_label": REASON_LABELS[reason],
            "session_id": session_id,
            "position": reviewed + 1,
            "total": reviewed + plan.size,
            "intervals": _intervals(card, now),
        },
    )


@router.get("/study/end")
async def end_study(request: Request, session: SessionDep, session_id: int) -> Any:
    study_session = study_service.end_session(session, session_id)
    return render(
        templates,
        request,
        "study_summary.html",
        {"active_nav": "study", "session": study_session},
    )


def _intervals(card: Any, now: datetime) -> dict[int, str]:
    """Interval labels keyed by the integer rating the template uses."""
    return {
        int(rating): label
        for rating, label in study_service.preview_intervals(card, now=now).items()
    }


# ------------------------------------------------------------------- quizzes


@router.post("/courses/{course_id}/quizzes/generate")
async def generate_quiz(request: Request, session: SessionDep, course_id: int) -> Any:
    result = quiz_service.generate_quiz(session, course_id=course_id)
    if result.produced_nothing:
        flash(request, result.reason_if_short or "No quiz could be generated.", "info")
        return RedirectResponse(f"/courses/{course_id}", status_code=303)
    if result.reason_if_short:
        flash(request, result.reason_if_short, "info")
    assert result.quiz is not None
    return RedirectResponse(f"/quizzes/{result.quiz.id}", status_code=303)


@router.get("/quizzes/{quiz_id}")
async def quiz_page(request: Request, session: SessionDep, quiz_id: int) -> Any:
    quiz = quiz_service.get_quiz(session, quiz_id)
    attempt = quiz_service.start_attempt(session, quiz_id=quiz_id)
    return render(
        templates,
        request,
        "quiz.html",
        {
            "active_nav": "dashboard",
            "quiz": quiz,
            "course": course_service.get_course(session, quiz.course_id),
            "attempt": attempt,
            "question": quiz.questions[0],
            "position": 1,
            "total": len(quiz.questions),
            "feedback": None,
            "next_position": 2 if len(quiz.questions) > 1 else None,
        },
    )


@router.get("/quizzes/attempts/{attempt_id}/question/{position}")
async def quiz_question(
    request: Request, session: SessionDep, attempt_id: int, position: int
) -> Any:
    attempt = quiz_service.get_attempt(session, attempt_id)
    quiz = quiz_service.get_quiz(session, attempt.quiz_id)
    if not 1 <= position <= len(quiz.questions):
        raise NotFoundError("There is no such question in this quiz.")
    return render(
        templates,
        request,
        "partials/quiz_question.html",
        {
            "quiz": quiz,
            "attempt": attempt,
            "question": quiz.questions[position - 1],
            "position": position,
            "total": len(quiz.questions),
            "feedback": None,
            "next_position": position + 1 if position < len(quiz.questions) else None,
        },
    )


@router.post("/quizzes/attempts/{attempt_id}/answer")
async def answer_question(
    request: Request,
    session: SessionDep,
    attempt_id: int,
    question_id: FormInt,
    response: FormStr = "",
) -> Any:
    return _grade_and_render(request, session, attempt_id, question_id, response, self_graded=False)


@router.post("/quizzes/attempts/{attempt_id}/self-grade")
async def self_grade(
    request: Request, session: SessionDep, attempt_id: int, question_id: FormInt
) -> Any:
    """Override the grader's verdict on a short answer.

    Recorded as ``self_graded`` so it can be reported separately: self-marking
    is a legitimate technique, but folding it silently into an accuracy figure
    would make that figure a lie.
    """
    attempt = quiz_service.get_attempt(session, attempt_id)
    existing = next((a for a in attempt.answers if a.question_id == question_id), None)
    return _grade_and_render(
        request,
        session,
        attempt_id,
        question_id,
        existing.response if existing else "",
        self_graded=True,
    )


def _grade_and_render(
    request: Request,
    session: SessionDep,
    attempt_id: int,
    question_id: int,
    response: str,
    *,
    self_graded: bool,
) -> Any:
    answer = quiz_service.answer_question(
        session,
        attempt_id=attempt_id,
        question_id=question_id,
        response=response,
        self_graded=self_graded,
    )
    attempt = quiz_service.get_attempt(session, attempt_id)
    quiz = quiz_service.get_quiz(session, attempt.quiz_id)
    question = session.get(Question, question_id)
    assert question is not None
    position = question.ordinal + 1

    return render(
        templates,
        request,
        "partials/quiz_question.html",
        {
            "quiz": quiz,
            "attempt": attempt,
            "question": question,
            "position": position,
            "total": len(quiz.questions),
            "feedback": answer,
            "next_position": position + 1 if position < len(quiz.questions) else None,
        },
    )


@router.post("/quizzes/attempts/{attempt_id}/complete")
async def complete_quiz(request: Request, session: SessionDep, attempt_id: int) -> Any:
    attempt = quiz_service.complete_attempt(session, attempt_id)
    quiz = quiz_service.get_quiz(session, attempt.quiz_id)
    answers = {answer.question_id: answer for answer in attempt.answers}
    return render(
        templates,
        request,
        "partials/quiz_result.html",
        {
            "quiz": quiz,
            "attempt": attempt,
            "review_rows": [
                {"question": question, "answer": answers.get(question.id)}
                for question in quiz.questions
            ],
            "self_graded": sum(1 for a in attempt.answers if a.self_graded),
        },
    )
