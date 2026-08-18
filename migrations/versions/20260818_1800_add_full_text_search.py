"""add full-text search

Creates the FTS5 virtual tables and the triggers that keep them in sync.

Triggers rather than application code: the invariant is "the index matches the
table", and that must hold for any writer -- a migration, a script, sqlite3 by
hand -- not only for rows that happen to go through the service layer.

The DDL itself lives in ``studyforge.fts`` so that the migration and the test
fixtures share exactly one definition.

Revision ID: 6b1f4a2c9d31
Revises: 1e63639da8e0
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from studyforge.fts import create_indexes, drop_indexes

revision: str = "6b1f4a2c9d31"
down_revision: str | None = "1e63639da8e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    create_indexes(op.get_bind())


def downgrade() -> None:
    drop_indexes(op.get_bind())
