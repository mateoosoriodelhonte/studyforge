"""Alembic environment.

Reads the database URL from StudyForge's own ``Settings`` rather than from
alembic.ini, so ``alembic upgrade head`` and the running application can never
be pointed at different databases.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from studyforge.config import get_settings
from studyforge.db import create_db_engine

# Importing the package registers every mapper, which autogenerate needs.
from studyforge.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Keep FTS5 tables out of autogenerate comparison.

    The search indexes are virtual tables created by raw DDL in a migration, and
    each one spawns four shadow tables (``_data``, ``_idx``, ``_docsize``,
    ``_config``). None of them exist in ``Base.metadata``, so without this filter
    every autogenerate run -- and ``alembic check`` in CI -- would propose
    dropping them.
    """
    del obj, reflected, compare_to
    if type_ == "table" and name is not None:
        return not (name.endswith("_fts") or "_fts_" in name or name == "_fts5_probe")
    return True


target_metadata = Base.metadata
settings = get_settings()


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER most things in place; batch mode rewrites the
        # table instead, which is what makes future column changes possible.
        render_as_batch=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(settings)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            include_object=include_object,
            poolclass=pool.NullPool,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
