"""Courses, documents and flashcards."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from studyforge.dependencies import SessionDep, SettingsDep
from studyforge.documents.storage import DocumentStorage
from studyforge.documents.validation import ALLOWED_EXTENSIONS, UploadError
from studyforge.domain.study.weakness import assess_concepts
from studyforge.models import Concept, Document, DocumentChunk
from studyforge.services import concepts as concept_service
from studyforge.services import courses as course_service
from studyforge.services import documents as document_service
from studyforge.services import flashcards as card_service
from studyforge.services import quizzes as quiz_service
from studyforge.services import study as study_service
from studyforge.services.exceptions import ValidationError
from studyforge.web.templating import build_templates, flash, render

router = APIRouter()
templates = build_templates()

FormStr = Annotated[str, Form()]
OptionalFormStr = Annotated[str, Form()]


def _redirect(url: str) -> RedirectResponse:
    """Post/Redirect/Get, so a refresh never resubmits a form."""
    return RedirectResponse(url, status_code=303)


# --------------------------------------------------------------------- courses


@router.get("/courses/new")
async def new_course_form(request: Request) -> Any:
    return render(
        templates, request, "course_form.html", {"course": None, "values": {}, "errors": {}}
    )


@router.post("/courses/new")
async def create_course(
    request: Request,
    session: SessionDep,
    name: FormStr = "",
    code: OptionalFormStr = "",
    description: OptionalFormStr = "",
) -> Any:
    try:
        course = course_service.create_course(
            session, name=name, code=code, description=description
        )
    except ValidationError as error:
        return render(
            templates,
            request,
            "course_form.html",
            {
                "course": None,
                "values": {"name": name, "code": code, "description": description},
                "errors": error.field_errors,
            },
            status_code=422,
        )
    flash(request, f"Created {course.name}. Add some material to get started.", "success")
    return _redirect(f"/courses/{course.id}")


@router.get("/courses/{course_id}")
async def course_page(request: Request, session: SessionDep, course_id: int) -> Any:
    now = datetime.now(UTC)
    course = course_service.get_course(session, course_id)

    documents = session.scalars(
        select(Document).where(Document.course_id == course_id).order_by(Document.created_at.desc())
    ).all()
    chunk_counts = {document.id: len(document.chunks) for document in documents}

    concepts = concept_service.list_concepts(session, course_id=course_id)
    assessments = assess_concepts(
        study_service.gather_observations(session, course_id=course_id, now=now), now=now
    )

    return render(
        templates,
        request,
        "course.html",
        {
            "active_nav": "dashboard",
            "course": course,
            "stats": course_service.course_stats(session, course_id, now=now),
            "documents": [
                {"document": document, "chunk_count": chunk_counts[document.id]}
                for document in documents
            ],
            "concepts": [
                {"concept": concept, "assessment": assessments.get(concept.id)}
                for concept in concepts
            ],
            "cards": card_service.list_cards(session, course_id=course_id),
            "quizzes": quiz_service.list_quizzes(session, course_id=course_id),
        },
    )


@router.get("/courses/{course_id}/edit")
async def edit_course_form(request: Request, session: SessionDep, course_id: int) -> Any:
    course = course_service.get_course(session, course_id)
    return render(
        templates,
        request,
        "course_form.html",
        {
            "course": course,
            "values": {
                "name": course.name,
                "code": course.code,
                "description": course.description,
            },
            "errors": {},
        },
    )


@router.post("/courses/{course_id}/edit")
async def update_course(
    request: Request,
    session: SessionDep,
    course_id: int,
    name: FormStr = "",
    code: OptionalFormStr = "",
    description: OptionalFormStr = "",
) -> Any:
    try:
        course_service.update_course(
            session, course_id, name=name, code=code, description=description
        )
    except ValidationError as error:
        return render(
            templates,
            request,
            "course_form.html",
            {
                "course": course_service.get_course(session, course_id),
                "values": {"name": name, "code": code, "description": description},
                "errors": error.field_errors,
            },
            status_code=422,
        )
    flash(request, "Course updated.", "success")
    return _redirect(f"/courses/{course_id}")


@router.post("/courses/{course_id}/archive")
async def archive_course(request: Request, session: SessionDep, course_id: int) -> Any:
    course = course_service.set_archived(session, course_id, archived=True)
    flash(request, f"Archived {course.name}. Nothing was deleted.", "info")
    return _redirect("/dashboard")


@router.post("/courses/{course_id}/restore")
async def restore_course(request: Request, session: SessionDep, course_id: int) -> Any:
    course = course_service.set_archived(session, course_id, archived=False)
    flash(request, f"Restored {course.name}.", "success")
    return _redirect(f"/courses/{course_id}")


# ------------------------------------------------------------------- documents


@router.get("/courses/{course_id}/documents/new")
async def new_document_form(
    request: Request, session: SessionDep, settings: SettingsDep, course_id: int
) -> Any:
    return render(
        templates,
        request,
        "document_form.html",
        {
            "course": course_service.get_course(session, course_id),
            "values": {},
            "errors": {},
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
            "max_upload_mb": settings.max_upload_mb,
        },
    )


@router.post("/courses/{course_id}/documents/paste")
async def paste_document(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    course_id: int,
    title: FormStr = "",
    body: FormStr = "",
) -> Any:
    try:
        result = document_service.ingest_pasted_text(
            session, course_id=course_id, title=title, body=body
        )
    except ValidationError as error:
        return render(
            templates,
            request,
            "document_form.html",
            {
                "course": course_service.get_course(session, course_id),
                "values": {"title": title, "body": body},
                "errors": error.field_errors,
                "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
                "max_upload_mb": settings.max_upload_mb,
            },
            status_code=422,
        )
    _flash_ingestion(request, result)
    return _redirect(f"/documents/{result.document.id}")


@router.post("/courses/{course_id}/documents/upload")
async def upload_document(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    course_id: int,
    file: UploadFile,
    title: OptionalFormStr = "",
) -> Any:
    content = await _read_bounded(file, settings.max_upload_bytes)
    storage = DocumentStorage(settings.uploads_dir)
    try:
        result = document_service.ingest_upload(
            session,
            storage,
            course_id=course_id,
            filename=file.filename,
            content=content,
            declared_content_type=file.content_type,
            max_bytes=settings.max_upload_bytes,
            title=title or None,
        )
    except UploadError as error:
        return render(
            templates,
            request,
            "document_form.html",
            {
                "course": course_service.get_course(session, course_id),
                "values": {"title": title},
                "errors": {"file": error.message},
                "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
                "max_upload_mb": settings.max_upload_mb,
            },
            status_code=400,
        )
    _flash_ingestion(request, result)
    return _redirect(f"/documents/{result.document.id}")


async def _read_bounded(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload, stopping as soon as it exceeds the limit.

    Reading in chunks matters: ``await file.read()`` on a multi-gigabyte upload
    would buffer the whole thing in memory before any size check could run.
    One byte past the limit is enough to reject it.
    """
    buffer = bytearray()
    while chunk := await file.read(64 * 1024):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            # Stop reading; validate_upload turns this into the user-facing error.
            return bytes(buffer[: max_bytes + 1])
    return bytes(buffer)


def _flash_ingestion(request: Request, result: document_service.IngestionResult) -> None:
    if result.succeeded:
        flash(
            request,
            f"Extracted {result.document.char_count:,} characters into "
            f"{result.chunk_count} chunk{'s' if result.chunk_count != 1 else ''}, "
            f"and found {result.concepts_created} new "
            f"concept{'s' if result.concepts_created != 1 else ''}.",
            "success",
        )
    else:
        flash(request, result.document.extraction_error or "Nothing could be extracted.", "error")


@router.get("/documents/{document_id}")
async def document_page(request: Request, session: SessionDep, document_id: int) -> Any:
    document = document_service.get_document(session, document_id)
    concepts = session.scalars(
        select(Concept)
        .where(Concept.source_document_id == document_id)
        .order_by(Concept.score.desc())
    ).all()
    chunks = session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.ordinal)
    ).all()
    return render(
        templates,
        request,
        "document.html",
        {
            "active_nav": "dashboard",
            "document": document,
            "course": course_service.get_course(session, document.course_id),
            "concepts": list(concepts),
            "chunks": list(chunks),
        },
    )


@router.post("/documents/{document_id}/delete")
async def delete_document(
    request: Request, session: SessionDep, settings: SettingsDep, document_id: int
) -> Any:
    document = document_service.get_document(session, document_id)
    course_id = document.course_id
    document_service.delete_document(session, DocumentStorage(settings.uploads_dir), document_id)
    flash(request, "Document deleted. Cards made from it were kept.", "info")
    return _redirect(f"/courses/{course_id}")


# -------------------------------------------------------------------- concepts
#
# Extraction produces *candidates*; these routes are how a learner corrects
# them. The course page tells the user they can edit or delete anything that is
# wrong, so the ability has to exist.


@router.get("/courses/{course_id}/concepts/new")
async def new_concept_form(request: Request, session: SessionDep, course_id: int) -> Any:
    return render(
        templates,
        request,
        "concept_form.html",
        {
            "course": course_service.get_course(session, course_id),
            "concept": None,
            "values": {},
            "errors": {},
        },
    )


@router.post("/courses/{course_id}/concepts/new")
async def create_concept(
    request: Request,
    session: SessionDep,
    course_id: int,
    name: FormStr = "",
    definition: OptionalFormStr = "",
) -> Any:
    try:
        concept_service.create_concept(
            session, course_id=course_id, name=name, definition=definition
        )
    except ValidationError as error:
        return render(
            templates,
            request,
            "concept_form.html",
            {
                "course": course_service.get_course(session, course_id),
                "concept": None,
                "values": {"name": name, "definition": definition},
                "errors": error.field_errors,
            },
            status_code=422,
        )
    flash(request, f"Added the concept {name.strip()!r}.", "success")
    return _redirect(f"/courses/{course_id}")


@router.get("/concepts/{concept_id}/edit")
async def edit_concept_form(request: Request, session: SessionDep, concept_id: int) -> Any:
    concept = concept_service.get_concept(session, concept_id)
    return render(
        templates,
        request,
        "concept_form.html",
        {
            "course": course_service.get_course(session, concept.course_id),
            "concept": concept,
            "values": {"name": concept.name, "definition": concept.definition},
            "errors": {},
        },
    )


@router.post("/concepts/{concept_id}/edit")
async def update_concept(
    request: Request,
    session: SessionDep,
    concept_id: int,
    name: FormStr = "",
    definition: OptionalFormStr = "",
) -> Any:
    concept = concept_service.get_concept(session, concept_id)
    try:
        concept_service.update_concept(session, concept_id, name=name, definition=definition)
    except ValidationError as error:
        return render(
            templates,
            request,
            "concept_form.html",
            {
                "course": course_service.get_course(session, concept.course_id),
                "concept": concept,
                "values": {"name": name, "definition": definition},
                "errors": error.field_errors,
            },
            status_code=422,
        )
    flash(request, "Concept updated.", "success")
    return _redirect(f"/courses/{concept.course_id}")


@router.post("/concepts/{concept_id}/delete")
async def delete_concept(request: Request, session: SessionDep, concept_id: int) -> Any:
    concept = concept_service.get_concept(session, concept_id)
    course_id = concept.course_id
    concept_service.delete_concept(session, concept_id)
    flash(request, "Concept deleted. Cards made from it were kept.", "info")
    return _redirect(f"/courses/{course_id}")


# ------------------------------------------------------------------ flashcards


@router.get("/courses/{course_id}/cards/new")
async def new_card_form(request: Request, session: SessionDep, course_id: int) -> Any:
    return render(
        templates,
        request,
        "card_form.html",
        {
            "course": course_service.get_course(session, course_id),
            "card": None,
            "values": {},
            "errors": {},
        },
    )


@router.post("/courses/{course_id}/cards/new")
async def create_card(
    request: Request,
    session: SessionDep,
    course_id: int,
    front: FormStr = "",
    back: FormStr = "",
) -> Any:
    try:
        card_service.create_card(session, course_id=course_id, front=front, back=back)
    except ValidationError as error:
        return render(
            templates,
            request,
            "card_form.html",
            {
                "course": course_service.get_course(session, course_id),
                "card": None,
                "values": {"front": front, "back": back},
                "errors": error.field_errors,
            },
            status_code=422,
        )
    flash(request, "Card created.", "success")
    return _redirect(f"/courses/{course_id}#cards")


@router.post("/courses/{course_id}/cards/generate")
async def generate_cards(request: Request, session: SessionDep, course_id: int) -> Any:
    summary = card_service.generate_and_accept(session, course_id=course_id)
    if summary.produced_nothing:
        flash(
            request,
            "No new cards could be generated. StudyForge only writes a card when a "
            "concept has a definition to answer with — add more material, or write "
            "definitions on the concepts you have.",
            "info",
        )
    else:
        flash(
            request,
            f"Generated {summary.created_count} "
            f"card{'s' if summary.created_count != 1 else ''}"
            + (
                f", skipping {summary.skipped_duplicates} already present."
                if summary.skipped_duplicates
                else "."
            ),
            "success",
        )
    return _redirect(f"/courses/{course_id}#cards")


@router.post("/cards/{card_id}/suspend")
async def suspend_card(request: Request, session: SessionDep, card_id: int) -> Any:
    card = card_service.set_suspended(session, card_id, suspended=True)
    return render(templates, request, "partials/card_row.html", {"card": card})


@router.post("/cards/{card_id}/unsuspend")
async def unsuspend_card(request: Request, session: SessionDep, card_id: int) -> Any:
    card = card_service.set_suspended(session, card_id, suspended=False)
    return render(templates, request, "partials/card_row.html", {"card": card})


@router.delete("/cards/{card_id}")
async def delete_card(session: SessionDep, card_id: int) -> Response:
    card_service.delete_card(session, card_id)
    # An empty body with 200 removes the row: hx-swap="outerHTML" replaces the
    # element with nothing.
    return Response(content="", media_type="text/html")


@router.get("/cards/{card_id}/edit")
async def edit_card_form(request: Request, session: SessionDep, card_id: int) -> Any:
    card = card_service.get_card(session, card_id)
    return render(
        templates,
        request,
        "card_form.html",
        {
            "course": course_service.get_course(session, card.course_id),
            "card": card,
            "values": {"front": card.front, "back": card.back},
            "errors": {},
        },
    )


@router.post("/cards/{card_id}/edit")
async def update_card(
    request: Request,
    session: SessionDep,
    card_id: int,
    front: FormStr = "",
    back: FormStr = "",
) -> Any:
    card = card_service.get_card(session, card_id)
    try:
        card_service.update_card(session, card_id, front=front, back=back)
    except ValidationError as error:
        return render(
            templates,
            request,
            "card_form.html",
            {
                "course": course_service.get_course(session, card.course_id),
                "card": card,
                "values": {"front": front, "back": back},
                "errors": error.field_errors,
            },
            status_code=422,
        )
    flash(request, "Card updated.", "success")
    return _redirect(f"/courses/{card.course_id}#cards")
