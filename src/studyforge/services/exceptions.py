"""Service-layer errors.

Each carries a message intended for the person using the application. Routes
translate these into an HTTP status and a rendered page; nothing here formats
HTML or knows a status code.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base class. ``message`` is safe to show a user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ServiceError):
    """The requested resource does not exist, or has been deleted."""


class ValidationError(ServiceError):
    """User input was rejected.

    ``field_errors`` maps a form field name to its message so the UI can render
    the error beside the input it belongs to, which is both better UX and an
    accessibility requirement.
    """

    def __init__(self, message: str, field_errors: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.field_errors = field_errors or {}


class ConflictError(ServiceError):
    """The action cannot be applied to the resource in its current state."""
