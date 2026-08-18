"""FastAPI application factory.

Composition root: this module wires configuration, logging, middleware, error
handling and routers together. It holds no business logic -- everything it
mounts lives in :mod:`studyforge.web`, :mod:`studyforge.api` and
:mod:`studyforge.services`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from studyforge import __version__
from studyforge.config import Environment, Settings, get_settings
from studyforge.logging_config import configure_logging, log_event

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.ensure_directories()
    log_event(
        logger,
        "application_started",
        version=__version__,
        environment=settings.environment.value,
        ai_provider=settings.ai_provider.value,
        database="sqlite" if settings.is_sqlite else "other",
    )
    if settings.environment is Environment.PRODUCTION and settings.secret_key_is_insecure_default:
        logger.warning(
            "SECRET_KEY is the built-in development default; set your own before hosting",
            extra={"event": "insecure_secret_key"},
        )
    yield
    log_event(logger, "application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Accepting an explicit ``settings`` object is what lets the test suite spin up
    a fully isolated app against a temporary database without touching process
    environment variables.
    """
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format.value)

    app = FastAPI(
        title="StudyForge",
        version=__version__,
        summary="A local-first intelligent study system.",
        description=(
            "StudyForge ingests your notes, extracts concepts, builds flashcards "
            "and quizzes, and schedules them with the FSRS-6 spaced-repetition "
            "algorithm. It is fully functional with no AI provider configured."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings

    # Signed cookie, used only for one-shot flash messages. There is no login:
    # StudyForge V1 is a single-user local application by design.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="studyforge_session",
        same_site="lax",
        https_only=settings.environment is Environment.PRODUCTION,
    )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
