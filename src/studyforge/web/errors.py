"""Error handling for the HTML interface.

Two rules:

1. **No stack trace ever reaches a user.** In production the detail is logged
   and the page says something plain and actionable.
2. **Service errors carry their own message**, written for a person. Routes
   translate them to a status code and render them; they do not rewrite them.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from studyforge.config import Environment, Settings
from studyforge.documents.validation import UploadError
from studyforge.services.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceError,
    ValidationError,
)
from studyforge.web.templating import render

logger = logging.getLogger(__name__)

_HEADINGS = {
    400: "That request did not make sense",
    404: "Not found",
    409: "That cannot be done right now",
    413: "That file is too large",
    422: "Something in that form was not valid",
    500: "Something went wrong",
}

_DEFAULT_DETAIL = {
    404: "The page or item you were looking for does not exist. It may have been deleted.",
    500: (
        "StudyForge hit an unexpected problem. The details have been logged. "
        "Your study data is unaffected."
    ),
}


def wants_json(request: Request) -> bool:
    """JSON for API routes, HTML for everything else."""
    return request.url.path.startswith("/api/")


def install_error_handlers(app: FastAPI, templates: Jinja2Templates) -> None:
    settings: Settings = app.state.settings

    def error_page(request: Request, status_code: int, detail: str) -> Response:
        if wants_json(request):
            return JSONResponse({"detail": detail}, status_code=status_code)
        return render(
            templates,
            request,
            "error.html",
            {
                "status_code": status_code,
                "heading": _HEADINGS.get(status_code, "Something went wrong"),
                "detail": detail,
            },
            status_code=status_code,
        )

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> Response:
        return error_page(request, status.HTTP_404_NOT_FOUND, exc.message)

    @app.exception_handler(ValidationError)
    async def _validation(request: Request, exc: ValidationError) -> Response:
        return error_page(request, status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message)

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError) -> Response:
        return error_page(request, status.HTTP_409_CONFLICT, exc.message)

    @app.exception_handler(UploadError)
    async def _upload(request: Request, exc: UploadError) -> Response:
        return error_page(request, status.HTTP_400_BAD_REQUEST, exc.message)

    @app.exception_handler(ServiceError)
    async def _service(request: Request, exc: ServiceError) -> Response:
        return error_page(request, status.HTTP_400_BAD_REQUEST, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> Response:
        detail = _DEFAULT_DETAIL.get(exc.status_code) or str(exc.detail)
        return error_page(request, exc.status_code, detail)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> Response:
        if wants_json(request):
            return JSONResponse(
                {"detail": exc.errors()}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
            )
        return error_page(
            request,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Some of the values in that form were not valid. Please go back and check them.",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Response:
        # exc_info goes to the log, never to the response body.
        logger.exception("unhandled_error", extra={"studyforge_fields": {"path": request.url.path}})
        if settings.environment is Environment.DEVELOPMENT:
            # In development, surfacing the exception type saves a trip to the
            # terminal. Still no traceback, and never in production.
            detail = f"{type(exc).__name__}: {exc}"
        else:
            detail = _DEFAULT_DETAIL[500]
        return error_page(request, status.HTTP_500_INTERNAL_SERVER_ERROR, detail)
