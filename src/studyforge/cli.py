"""Command-line entry point: ``uv run studyforge <command>``."""

from __future__ import annotations

import argparse
import sys

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

    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
