"""JSON API routes."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Response, status

from studyforge.api.schemas import (
    ConceptOut,
    ConceptStatusOut,
    CourseDetailOut,
    CourseIn,
    CourseOut,
    CourseStatsOut,
    DocumentOut,
    FlashcardIn,
    FlashcardOut,
    GenerationOut,
    PasteIn,
    ProgressOut,
    QueueEntryOut,
    QueueOut,
    QuizOut,
    RateOut,
    ReviewIn,
    ReviewOut,
)
from studyforge.dependencies import SessionDep
from studyforge.domain.study.fsrs import Rating
from studyforge.services import concepts as concept_service
from studyforge.services import courses as course_service
from studyforge.services import documents as document_service
from studyforge.services import flashcards as card_service
from studyforge.services import progress as progress_service
from studyforge.services import quizzes as quiz_service
from studyforge.services import study as study_service

api_router = APIRouter(prefix="/api")

courses = APIRouter(prefix="/courses", tags=["courses"])
documents = APIRouter(prefix="/documents", tags=["documents"])
flashcards = APIRouter(prefix="/flashcards", tags=["flashcards"])
reviews = APIRouter(prefix="/reviews", tags=["reviews"])
quizzes = APIRouter(prefix="/quizzes", tags=["quizzes"])
progress = APIRouter(prefix="/progress", tags=["progress"])


# ---------------------------------------------------------------- courses


@courses.get("", summary="List courses")
def list_courses(
    session: SessionDep, include_archived: bool = Query(default=False)
) -> list[CourseOut]:
    return [
        CourseOut.model_validate(course)
        for course in course_service.list_courses(session, include_archived=include_archived)
    ]


@courses.post("", status_code=status.HTTP_201_CREATED, summary="Create a course")
def create_course(session: SessionDep, payload: CourseIn) -> CourseOut:
    course = course_service.create_course(
        session, name=payload.name, code=payload.code, description=payload.description
    )
    return CourseOut.model_validate(course)


@courses.get("/{course_id}", summary="Get one course with its counts")
def get_course(session: SessionDep, course_id: int) -> CourseDetailOut:
    course = course_service.get_course(session, course_id)
    stats = course_service.course_stats(session, course_id, now=datetime.now(UTC))
    return CourseDetailOut(
        **CourseOut.model_validate(course).model_dump(),
        stats=CourseStatsOut(**asdict(stats)),
    )


@courses.put("/{course_id}", summary="Update a course")
def update_course(session: SessionDep, course_id: int, payload: CourseIn) -> CourseOut:
    course = course_service.update_course(
        session,
        course_id,
        name=payload.name,
        code=payload.code,
        description=payload.description,
    )
    return CourseOut.model_validate(course)


@courses.post("/{course_id}/archive", summary="Archive a course without deleting it")
def archive_course(session: SessionDep, course_id: int) -> CourseOut:
    return CourseOut.model_validate(course_service.set_archived(session, course_id, archived=True))


@courses.get("/{course_id}/concepts", summary="Concepts extracted for a course")
def list_concepts(session: SessionDep, course_id: int) -> list[ConceptOut]:
    course_service.get_course(session, course_id)
    return [
        ConceptOut.model_validate(concept)
        for concept in concept_service.list_concepts(session, course_id=course_id)
    ]


@courses.get("/{course_id}/documents", summary="Documents in a course")
def list_documents(session: SessionDep, course_id: int) -> list[DocumentOut]:
    course = course_service.get_course(session, course_id)
    return [DocumentOut.model_validate(document) for document in course.documents]


@courses.post(
    "/{course_id}/documents/paste",
    status_code=status.HTTP_201_CREATED,
    summary="Add a document by pasting text",
    description=(
        "File uploads are handled by the web interface only. Multipart uploads "
        "need streaming size enforcement that does not belong in a JSON API."
    ),
)
def paste_document(session: SessionDep, course_id: int, payload: PasteIn) -> DocumentOut:
    result = document_service.ingest_pasted_text(
        session, course_id=course_id, title=payload.title, body=payload.body
    )
    return DocumentOut.model_validate(result.document)


@courses.get("/{course_id}/flashcards", summary="Flashcards in a course")
def list_flashcards(
    session: SessionDep, course_id: int, include_suspended: bool = Query(default=True)
) -> list[FlashcardOut]:
    course_service.get_course(session, course_id)
    return [
        FlashcardOut.model_validate(card)
        for card in card_service.list_cards(
            session, course_id=course_id, include_suspended=include_suspended
        )
    ]


@courses.post(
    "/{course_id}/flashcards",
    status_code=status.HTTP_201_CREATED,
    summary="Write a flashcard",
)
def create_flashcard(session: SessionDep, course_id: int, payload: FlashcardIn) -> FlashcardOut:
    card = card_service.create_card(
        session,
        course_id=course_id,
        front=payload.front,
        back=payload.back,
        concept_id=payload.concept_id,
    )
    return FlashcardOut.model_validate(card)


@courses.post(
    "/{course_id}/flashcards/generate",
    summary="Generate flashcards from this course's concepts",
    description=(
        "Deterministic generation. Produces nothing for concepts without a "
        "definition, and skips cards that already exist."
    ),
)
def generate_flashcards(session: SessionDep, course_id: int) -> GenerationOut:
    summary = card_service.generate_and_accept(session, course_id=course_id)
    return GenerationOut(
        created=summary.created_count,
        skipped_duplicates=summary.skipped_duplicates,
        note=(
            "No concept in this course has a definition to answer with."
            if summary.produced_nothing
            else None
        ),
    )


@courses.post("/{course_id}/quizzes/generate", summary="Generate a quiz")
def generate_quiz(
    session: SessionDep, course_id: int, question_count: int = Query(default=10, ge=1, le=50)
) -> QuizOut | GenerationOut:
    result = quiz_service.generate_quiz(session, course_id=course_id, question_count=question_count)
    if result.quiz is None:
        return GenerationOut(created=0, skipped_duplicates=0, note=result.reason_if_short)
    return QuizOut.model_validate(result.quiz)


# -------------------------------------------------------------- documents


@documents.get("/{document_id}", summary="Get a document")
def get_document(session: SessionDep, document_id: int) -> DocumentOut:
    return DocumentOut.model_validate(document_service.get_document(session, document_id))


# ------------------------------------------------------------- flashcards


@flashcards.get("/{card_id}", summary="Get a flashcard")
def get_flashcard(session: SessionDep, card_id: int) -> FlashcardOut:
    return FlashcardOut.model_validate(card_service.get_card(session, card_id))


@flashcards.put("/{card_id}", summary="Update a flashcard")
def update_flashcard(session: SessionDep, card_id: int, payload: FlashcardIn) -> FlashcardOut:
    return FlashcardOut.model_validate(
        card_service.update_card(session, card_id, front=payload.front, back=payload.back)
    )


@flashcards.delete(
    "/{card_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a flashcard"
)
def delete_flashcard(session: SessionDep, card_id: int) -> Response:
    card_service.delete_card(session, card_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@flashcards.post("/{card_id}/suspend", summary="Withhold a card from the queue")
def suspend_flashcard(session: SessionDep, card_id: int) -> FlashcardOut:
    return FlashcardOut.model_validate(card_service.set_suspended(session, card_id, suspended=True))


@flashcards.post("/{card_id}/unsuspend", summary="Return a card to the queue")
def unsuspend_flashcard(session: SessionDep, card_id: int) -> FlashcardOut:
    return FlashcardOut.model_validate(
        card_service.set_suspended(session, card_id, suspended=False)
    )


# ----------------------------------------------------------------- reviews


@reviews.get(
    "/queue",
    summary="What to study now",
    description=(
        "Ordered: overdue, then due, then cards for concepts you are getting "
        "wrong, then new cards under a separate daily cap."
    ),
)
def review_queue(
    session: SessionDep,
    course_id: int | None = Query(default=None),
    limit: int = Query(default=60, ge=1, le=500),
) -> QueueOut:
    plan = study_service.build_study_queue(
        session, course_id=course_id, now=datetime.now(UTC), session_limit=limit
    )
    return QueueOut(
        entries=[
            QueueEntryOut(card_id=e.card_id, reason=e.reason.value, position=e.position)
            for e in plan.entries
        ],
        overdue_count=plan.overdue_count,
        due_count=plan.due_count,
        new_available=plan.new_available,
    )


@reviews.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Rate a card",
    description=(
        "Applies FSRS-6 and returns the new schedule. Submitting the same rating "
        "twice at the same instant is ignored rather than double-counted; check "
        "`was_duplicate`."
    ),
)
def submit_review(session: SessionDep, payload: ReviewIn) -> ReviewOut:
    outcome = study_service.record_review(
        session, card_id=payload.card_id, rating=Rating(payload.rating)
    )
    return ReviewOut(
        card_id=outcome.card.id,
        rating=int(outcome.rating),
        interval_days=outcome.interval_days,
        next_due_at=outcome.next_due_at,
        state=outcome.card.state.value,
        stability=outcome.card.stability,
        difficulty=outcome.card.difficulty,
        was_duplicate=outcome.was_duplicate,
    )


# ----------------------------------------------------------------- quizzes


@quizzes.get("/{quiz_id}", summary="Get a quiz")
def get_quiz(session: SessionDep, quiz_id: int) -> QuizOut:
    return QuizOut.model_validate(quiz_service.get_quiz(session, quiz_id))


# ---------------------------------------------------------------- progress


@progress.get(
    "",
    summary="Learning progress",
    description=(
        "Rates carry their sample size and are null when there is no data. "
        "Do not render a null rate as 0%."
    ),
)
def get_progress(session: SessionDep, course_id: int | None = Query(default=None)) -> ProgressOut:
    report = progress_service.build_report(session, course_id=course_id, now=datetime.now(UTC))
    return ProgressOut(
        total_cards=report.total_cards,
        new_cards=report.new_cards,
        suspended_cards=report.suspended_cards,
        due_now=report.due_now,
        overdue=report.overdue,
        reviews_total=report.reviews_total,
        review_recall=RateOut(
            value=report.review_recall.value,
            numerator=report.review_recall.numerator,
            denominator=report.review_recall.denominator,
        ),
        quiz_accuracy=RateOut(
            value=report.quiz_accuracy.value,
            numerator=report.quiz_accuracy.numerator,
            denominator=report.quiz_accuracy.denominator,
        ),
        concepts_total=report.concepts_total,
        weak_concepts=[
            ConceptStatusOut(
                concept_id=a.concept_id,
                status=a.status.value,
                status_definition=a.status_definition,
                accuracy=a.accuracy,
                observation_count=a.observation_count,
            )
            for a in report.weak_concepts
        ],
    )


for sub in (courses, documents, flashcards, reviews, quizzes, progress):
    api_router.include_router(sub)


__all__ = ["api_router"]
