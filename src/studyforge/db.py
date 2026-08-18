"""Database engine, session lifecycle and SQLite pragmas.

SQLite defaults are wrong for an application database in two ways that matter,
and both are corrected here at connect time:

``foreign_keys=ON``
    SQLite ships with foreign-key enforcement **off**. Without this pragma the
    ``ondelete`` rules declared on the models are inert decoration, and deleting
    a course would silently orphan every document, card and review under it.

``journal_mode=WAL``
    The default rollback journal takes a database-wide write lock. WAL lets the
    study queue keep reading while a review is being written, which is exactly
    the access pattern here.

Everything is written against the synchronous SQLAlchemy API. StudyForge is a
single-user local application whose queries are indexed lookups over a local
file; async drivers would add real complexity to save microseconds that no user
will ever perceive. FastAPI runs sync dependencies in a threadpool, so the event
loop is not blocked.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from studyforge.config import Settings

logger = logging.getLogger(__name__)


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """Apply per-connection pragmas. Registered for SQLite engines only."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        # Wait rather than immediately raising "database is locked" if another
        # connection holds the write lock.
        cursor.execute("PRAGMA busy_timeout=5000")
        # FULL is slower than needed for a local app; NORMAL is safe under WAL.
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_db_engine(settings: Settings, *, echo: bool = False) -> Engine:
    """Build an engine configured for the target database."""
    connect_args: dict[str, Any] = {}
    if settings.is_sqlite:
        _ensure_sqlite_parent_dir(settings.database_url)
        # FastAPI may hand a session to a threadpool worker; SQLite's default
        # same-thread check would reject that. Session scoping still guarantees
        # one connection is never used concurrently.
        connect_args["check_same_thread"] = False

    engine = create_engine(
        settings.database_url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if settings.is_sqlite:
        event.listen(engine, "connect", _configure_sqlite)
    return engine


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create the directory holding the SQLite file, if it is a real path."""
    _, _, location = database_url.partition("///")
    if not location or location == ":memory:" or location.startswith(":"):
        return
    parent = Path(location).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        # Objects stay usable after commit; templates read attributes off
        # entities the route already committed, and expiring them would trigger
        # a lazy reload against a closed session.
        expire_on_commit=False,
        class_=Session,
    )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """A transactional scope: commit on success, roll back on any exception."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def foreign_keys_enforced(session: Session) -> bool:
    """Whether the current connection actually enforces foreign keys.

    Exists because this is easy to get silently wrong on SQLite, and a test
    asserts it rather than trusting that the pragma was applied.
    """
    return bool(session.execute(text("PRAGMA foreign_keys")).scalar())
