"""Shared fixtures.

Every test gets its own temporary data directory and its own settings object, so
nothing touches the developer's real ``./data`` and no two tests can see each
other's state.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from studyforge.config import AIProvider, Environment, Settings


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
