"""Declarative base, shared column types and mixins.

Two things here are load-bearing for correctness rather than convenience:

``UTCDateTime``
    SQLite has no timezone-aware datetime type. Left to itself, SQLAlchemy will
    happily store an aware datetime and hand back a naive one, which silently
    corrupts every interval the scheduler computes. This type decorator
    normalises to UTC on the way in and re-attaches UTC on the way out, so the
    rest of the application only ever sees aware datetimes.

``utcnow``
    A single source of "now" for defaults, so tests can reason about it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

# Explicit, deterministic constraint names. Without these SQLite ends up with
# unnamed constraints that Alembic cannot later alter or drop by name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """The current instant, timezone-aware, in UTC."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A datetime column that is always timezone-aware UTC in Python."""

    impl = DateTime
    cache_ok = True

    # `_dialect` is part of SQLAlchemy's TypeDecorator contract. We do not vary
    # behaviour by backend: UTC is UTC everywhere.
    def process_bind_param(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # Interpret naive input as UTC rather than guessing a local zone.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: ClassVar[dict[type, type]] = {datetime: UTCDateTime}

    def __repr__(self) -> str:
        ident = getattr(self, "id", None)
        return f"<{type(self).__name__} id={ident}>"


class TimestampMixin:
    """``created_at`` / ``updated_at``, maintained by the ORM.

    Kept in Python rather than as database triggers so the behaviour is
    identical on SQLite and Postgres.
    """

    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


def json_default(value: Any) -> Any:  # pragma: no cover - serialisation helper
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value)!r}")
