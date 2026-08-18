"""Migrations must build a working schema on a clean database, by themselves.

A model without an executable migration is a model that does not exist for
anyone who clones the repository, so these tests run the real Alembic pipeline
rather than ``create_all``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect

from studyforge.config import Settings
from studyforge.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def alembic_config(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Config:
    """Alembic pointed at a throwaway database.

    ``env.py`` reads the URL from ``Settings``, so the environment is what
    redirects it; that coupling is deliberate and is what stops a migration
    being applied to the wrong database.
    """
    settings.ensure_directories()
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("DATA_DIR", str(settings.data_dir))
    from studyforge.config import get_settings

    get_settings.cache_clear()

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    yield config
    get_settings.cache_clear()


def _engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url)


class TestUpgrade:
    def test_builds_the_full_schema_from_empty(
        self, alembic_config: Config, settings: Settings
    ) -> None:
        command.upgrade(alembic_config, "head")

        tables = set(inspect(_engine(settings)).get_table_names())
        expected = set(Base.metadata.tables)
        assert expected <= tables, f"migrations missed: {sorted(expected - tables)}"

    def test_produces_the_same_schema_the_models_describe(
        self, alembic_config: Config, settings: Settings
    ) -> None:
        """Guards against a hand-edited migration drifting from the models."""
        command.upgrade(alembic_config, "head")
        inspector = inspect(_engine(settings))

        for name, table in Base.metadata.tables.items():
            migrated = {c["name"] for c in inspector.get_columns(name)}
            declared = {c.name for c in table.columns}
            assert declared == migrated, f"column mismatch on {name}"

    def test_is_idempotent(self, alembic_config: Config) -> None:
        command.upgrade(alembic_config, "head")
        command.upgrade(alembic_config, "head")  # must be a no-op, not an error


class TestDowngrade:
    def test_round_trips_to_base_and_back(self, alembic_config: Config, settings: Settings) -> None:
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "base")

        remaining = set(inspect(_engine(settings)).get_table_names())
        assert not (remaining & set(Base.metadata.tables))

        command.upgrade(alembic_config, "head")
        assert set(Base.metadata.tables) <= set(inspect(_engine(settings)).get_table_names())


class TestRevisionHygiene:
    def test_there_is_exactly_one_head(self) -> None:
        """Two heads means someone branched migrations and did not merge them."""
        script = ScriptDirectory(str(REPO_ROOT / "migrations"))
        assert len(script.get_heads()) == 1, "multiple migration heads"

    def test_every_revision_can_be_walked(self) -> None:
        script = ScriptDirectory(str(REPO_ROOT / "migrations"))
        revisions = list(script.walk_revisions())
        assert revisions
        assert all(r.module.upgrade for r in revisions)
