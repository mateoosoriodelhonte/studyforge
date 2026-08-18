"""Command-line entry point: ``uv run studyforge <command>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from studyforge import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="studyforge",
        description="StudyForge - a local-first intelligent study system.",
    )
    parser.add_argument("--version", action="version", version=f"studyforge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the web application.")
    serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: localhost).")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true", help="Reload on code changes.")

    db = sub.add_parser("db", help="Database management.")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("init", help="Create or upgrade the local database to the latest schema.")
    db_sub.add_parser("current", help="Show the applied migration revision.")

    args = parser.parse_args(argv)

    if args.command == "db":
        return _run_db_command(args.db_command)

    if args.command == "serve":
        import uvicorn

        from studyforge.config import get_settings

        settings = get_settings()
        uvicorn.run(
            "studyforge.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_config=None,  # our own structured logging is already installed
            access_log=settings.log_level == "DEBUG",
        )
        return 0

    # argparse enforces `required=True` on the subparser, so there is no other
    # path here; this is belt-and-braces for a future subcommand.
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    raise AssertionError("unreachable")  # pragma: no cover


def _run_db_command(command: str) -> int:
    """Run Alembic programmatically so users need one tool, not two."""
    from alembic import command as alembic_command
    from alembic.config import Config

    from studyforge.config import get_settings

    settings = get_settings()
    settings.ensure_directories()

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))

    if command == "init":
        alembic_command.upgrade(config, "head")
        print(f"Database ready at {settings.database_url}")
        return 0
    if command == "current":
        alembic_command.current(config, verbose=True)
        return 0

    print(f"unknown db command {command!r}", file=sys.stderr)  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
