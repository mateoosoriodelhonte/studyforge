"""Home, dashboard, progress, settings and search."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from studyforge import __version__
from studyforge.ai import build_provider
from studyforge.dependencies import SessionDep, SettingsDep
from studyforge.documents.validation import ALLOWED_EXTENSIONS
from studyforge.domain.study.fsrs import PARAMETER_COUNT, SchedulerConfig
from studyforge.domain.study.weakness import (
    MIN_OBSERVATIONS,
    STATUS_DEFINITIONS,
    ConceptStatus,
    assess_concepts,
    weakest_concepts,
)
from studyforge.models import Course, StudySession
from studyforge.services import courses as course_service
from studyforge.services import progress as progress_service
from studyforge.services import search as search_service
from studyforge.services import study as study_service
from studyforge.web.templating import build_templates, render

router = APIRouter()
templates = build_templates()


@router.get("/", name="home")
async def home(request: Request) -> Any:
    return render(templates, request, "home.html", {"active_nav": "home"})


@router.get("/dashboard")
async def dashboard(request: Request, session: SessionDep) -> Any:
    now = datetime.now(UTC)
    courses = course_service.list_courses(session)
    entries = [
        {"course": course, "stats": course_service.course_stats(session, course.id, now=now)}
        for course in courses
    ]

    assessments = assess_concepts(
        study_service.gather_observations(session, course_id=None, now=now), now=now
    )
    weak = weakest_concepts(assessments, limit=6)
    names = progress_service.concept_names(session, [a.concept_id for a in weak])

    archived = session.scalars(select(Course).where(Course.archived_at.is_not(None))).all()
    recent = session.scalars(
        select(StudySession).order_by(StudySession.started_at.desc()).limit(6)
    ).all()

    return render(
        templates,
        request,
        "dashboard.html",
        {
            "active_nav": "dashboard",
            "courses": entries,
            "totals": progress_service.course_totals(session),
            "archived_count": len(archived),
            "weak_concepts": [
                {"name": names.get(a.concept_id, f"Concept {a.concept_id}"), "assessment": a}
                for a in weak
            ],
            "recent_sessions": list(recent),
            "min_observations": MIN_OBSERVATIONS,
            "greeting": _greeting(entries),
        },
    )


def _greeting(entries: list[dict[str, Any]]) -> str:
    """A one-line summary. Says what is true, without cheerleading."""
    if not entries:
        return "Nothing here yet. Create a course and add some material to begin."
    due = sum(entry["stats"].due_now for entry in entries)
    if due == 0:
        return "Nothing is due right now. Everything you have studied is scheduled for later."
    return f"{due} card{'s' if due != 1 else ''} ready for review."


@router.get("/progress")
async def progress_page(
    request: Request,
    session: SessionDep,
    course_id: int | None = Query(default=None),
) -> Any:
    now = datetime.now(UTC)
    course = course_service.get_course(session, course_id) if course_id else None
    report = progress_service.build_report(session, course_id=course_id, now=now)
    names = progress_service.concept_names(session, [a.concept_id for a in report.weak_concepts])
    busiest = max((day.total for day in report.activity), default=0)

    return render(
        templates,
        request,
        "progress.html",
        {
            "active_nav": "progress",
            "report": report,
            "course": course,
            "all_courses": course_service.list_courses(session),
            "weak_rows": [
                {"name": names.get(a.concept_id, f"Concept {a.concept_id}"), "assessment": a}
                for a in report.weak_concepts
            ],
            "busiest": busiest,
            "status_definitions": [
                (status.value, definition) for status, definition in STATUS_DEFINITIONS.items()
            ],
            "status_enum": ConceptStatus,
            "min_observations": MIN_OBSERVATIONS,
            "recent_window_days": progress_service.RECENT_WINDOW_DAYS,
        },
    )


@router.get("/settings")
async def settings_page(request: Request, settings: SettingsDep) -> Any:
    provider = build_provider(settings)
    status = await provider.status()
    config = SchedulerConfig()

    return render(
        templates,
        request,
        "settings.html",
        {
            "active_nav": "settings",
            "version": __version__,
            "environment": settings.environment.value,
            "ai": {
                "provider": status.name,
                "model": status.model,
                "endpoint": status.endpoint,
                "reachable": status.is_ready,
                "status_detail": status.detail,
            },
            "storage": {
                "database": settings.database_url,
                "uploads": str(settings.uploads_dir),
                "max_upload_mb": settings.max_upload_mb,
                "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
            },
            "scheduler": {
                "parameter_count": PARAMETER_COUNT,
                "desired_retention": config.desired_retention,
                "learning_steps": [_step(s) for s in config.learning_steps],
                "relearning_steps": [_step(s) for s in config.relearning_steps],
            },
        },
    )


def _step(delta: Any) -> str:
    minutes = delta.total_seconds() / 60
    return f"{minutes:.0f}m" if minutes < 60 else f"{minutes / 60:.0f}h"


@router.get("/search")
async def search_page(request: Request, session: SessionDep, q: str = Query(default="")) -> Any:
    return render(
        templates,
        request,
        "search.html",
        {
            "active_nav": "search",
            "query": q,
            "groups": search_service.search(session, q) if q else [],
        },
    )


@router.get("/search/results")
async def search_results(request: Request, session: SessionDep, q: str = Query(default="")) -> Any:
    """HTMX fragment: just the results list, swapped as the user types."""
    return render(
        templates,
        request,
        "partials/search_results.html",
        {"query": q, "groups": search_service.search(session, q) if q else []},
    )
