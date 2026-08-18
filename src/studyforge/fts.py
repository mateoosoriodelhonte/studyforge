"""SQLite FTS5 index definitions.

The DDL lives here rather than inside the migration so that exactly one
definition exists: the migration applies it to a real database, and the test
fixtures apply it to a throwaway one. Two copies would drift, and the copy that
drifts is always the one the tests use.

A note on the ``noqa: S608`` markers below
------------------------------------------
These statements interpolate table and column names into SQL, which the linter
flags as a possible injection vector. SQL *identifiers* cannot be bound as
parameters -- there is no ``CREATE TABLE ?`` -- so string construction is the
only option available. It is safe here because every interpolated value comes
from :data:`INDEXES`, a module-level constant in this file. **No user input
reaches this module.** Search queries, which *are* user input, are handled by
``studyforge.services.search.to_match_query`` and passed as bound parameters.
"""

from __future__ import annotations

from sqlalchemy import Connection

#: ``(fts table, source table, indexed columns)``
INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("courses_fts", "courses", ("name", "code", "description")),
    ("documents_fts", "documents", ("title", "extracted_text")),
    ("concepts_fts", "concepts", ("name", "definition")),
    ("flashcards_fts", "flashcards", ("front", "back")),
    ("chunks_fts", "document_chunks", ("text", "heading")),
)


def fts5_available(connection: Connection) -> bool:
    """Whether this SQLite build includes FTS5.

    FTS5 is a compile-time option. Every mainstream CPython build ships with it,
    but rather than make StudyForge uninstallable on an exotic build, the index
    creation is skipped and search reports itself unavailable.
    """
    try:
        connection.exec_driver_sql("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        connection.exec_driver_sql("DROP TABLE IF EXISTS _fts5_probe")
    except Exception:  # noqa: BLE001 - any failure means the option is absent
        return False
    return True


def create_indexes(connection: Connection, *, backfill: bool = True) -> bool:
    """Create the FTS tables and their sync triggers. Returns False if skipped."""
    if connection.dialect.name != "sqlite" or not fts5_available(connection):
        return False

    for fts_table, source, columns in INDEXES:
        column_list = ", ".join(columns)
        new_values = ", ".join(f"new.{column}" for column in columns)
        old_values = ", ".join(f"old.{column}" for column in columns)

        connection.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts_table} "
            f"USING fts5({column_list}, content='{source}', content_rowid='id', "
            f"tokenize='unicode61 remove_diacritics 2')"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {fts_table}_ai AFTER INSERT ON {source} BEGIN "
            f"INSERT INTO {fts_table}(rowid, {column_list}) VALUES (new.id, {new_values}); END"
        )
        # An external-content FTS5 table is updated by writing a 'delete' row
        # carrying the OLD values before inserting the new ones. Skipping that
        # would leave the old terms matchable forever.
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {fts_table}_ad AFTER DELETE ON {source} BEGIN "
            f"INSERT INTO {fts_table}({fts_table}, rowid, {column_list}) "
            f"VALUES ('delete', old.id, {old_values}); END"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {fts_table}_au AFTER UPDATE ON {source} BEGIN "
            f"INSERT INTO {fts_table}({fts_table}, rowid, {column_list}) "
            f"VALUES ('delete', old.id, {old_values}); "
            f"INSERT INTO {fts_table}(rowid, {column_list}) VALUES (new.id, {new_values}); END"
        )
        if backfill:
            # So search works immediately on an existing database, not only for
            # rows written from now on.
            connection.exec_driver_sql(
                f"INSERT INTO {fts_table}(rowid, {column_list}) "
                f"SELECT id, {column_list} FROM {source}"
            )
    return True


def drop_indexes(connection: Connection) -> None:
    if connection.dialect.name != "sqlite":
        return
    for fts_table, _source, _columns in INDEXES:
        for suffix in ("ai", "ad", "au"):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {fts_table}_{suffix}")
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {fts_table}")
