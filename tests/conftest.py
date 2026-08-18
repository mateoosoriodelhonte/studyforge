"""Shared fixtures.

Every test gets its own temporary data directory and its own settings object, so
nothing touches the developer's real ``./data`` and no two tests can see each
other's state.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from studyforge.config import AIProvider, Environment, Settings
from studyforge.db import create_db_engine, create_session_factory
from studyforge.fts import create_indexes
from studyforge.main import create_app
from studyforge.models import Base


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Isolated settings pointing at a temporary data directory."""
    return Settings(
        environment=Environment.TEST,
        data_dir=tmp_path / "data",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'data' / 'test.db'}",
        secret_key="test-only-secret",
        ai_provider=AIProvider.NONE,
        log_level="WARNING",
    )


@pytest.fixture(autouse=True)
def _no_ambient_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Stop a developer's real ``.env`` from leaking into the test run."""
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    """An engine over a temporary SQLite file with the real schema applied.

    Uses ``Base.metadata.create_all`` rather than Alembic for speed; a separate
    integration test proves the migrations themselves produce this same schema.
    """
    settings.ensure_directories()
    eng = create_db_engine(settings)
    Base.metadata.create_all(eng)
    # The FTS indexes are raw DDL, not ORM metadata. Applying them from the same
    # module the migration uses keeps the test schema honest.
    with eng.begin() as connection:
        create_indexes(connection)
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def app(settings: Settings, engine: Engine) -> Iterator[FastAPI]:
    """An application bound to the test database.

    The lifespan would otherwise build its own engine against the same file;
    overriding ``app.state`` after startup keeps every request on the one
    connection pool the test can inspect.
    """
    application = create_app(settings)
    with TestClient(application):
        application.state.engine = engine
        application.state.session_factory = create_session_factory(engine)
        yield application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
