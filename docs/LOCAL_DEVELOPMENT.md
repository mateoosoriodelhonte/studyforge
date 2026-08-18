# Local development

## Setup

You need [uv](https://docs.astral.sh/uv/). It installs the correct Python
itself, so that is the only prerequisite.

```bash
git clone https://github.com/mateoosoriodelhonte/studyforge.git
cd studyforge
uv sync --all-groups        # runtime + dev + browser-test dependencies
uv run studyforge db init
uv run studyforge db demo   # optional sample data
uv run studyforge serve --reload
```

## Everyday commands

```bash
uv run studyforge serve --reload      # dev server on :8000
uv run pytest                         # unit, integration and security tests
uv run pytest --cov                   # with coverage
uv run pytest -k fsrs -v              # one area
uv run ruff format .                  # format
uv run ruff check . --fix             # lint
uv run mypy                           # type check (strict)
```

Browser tests need a one-off browser download:

```bash
uv run --group e2e playwright install chromium
uv run --group e2e pytest tests/e2e
```

They are excluded from the default `pytest` run because they need a live server
and are an order of magnitude slower. CI runs them as their own job.

## Database

```bash
uv run studyforge db init          # create or upgrade to the latest schema
uv run studyforge db current       # show the applied revision
uv run alembic revision --autogenerate -m "add thing"
uv run alembic upgrade head
uv run alembic downgrade -1
```

Alembic reads its URL from `Settings`, not from `alembic.ini`, so a migration
can never be applied to a different database than the application is using.

**After changing a model, always generate a migration and run `alembic check`.**
CI fails on drift between models and migrations.

The FTS5 index tables are excluded from autogenerate — they are created by raw
DDL in `studyforge/fts.py`, which both the migration and the test fixtures call
so there is exactly one definition.

To start over:

```bash
rm -rf data && uv run studyforge db init
```

## Project layout

```
src/studyforge/
├── domain/          pure algorithms — no I/O, no framework, no AI
│   ├── study/       FSRS-6, queue building, weak-concept analysis
│   ├── text/        normalisation, chunking
│   ├── concepts/    deterministic concept extraction
│   └── generation/  flashcards and quiz questions
├── documents/       upload validation, storage, text extraction
├── ai/              provider protocol, NoAI, Ollama
├── models/          SQLAlchemy 2 models
├── services/        transactions and orchestration
├── web/             HTML routers, templates, static assets
└── api/             JSON API
tests/
├── unit/            domain algorithms
├── integration/     services and routes against a real database
├── security/        named attacks
└── e2e/             Playwright, against a live server
```

## Conventions

**The domain layer stays pure.** If you find yourself importing SQLAlchemy or
FastAPI into `domain/`, the logic belongs in a service instead. This is what
keeps the algorithms testable with literals.

**Nothing in the domain reads the clock.** Pass the time in. A schedule that
depends on `datetime.now()` cannot be tested.

**Services raise domain errors**, not `HTTPException`. `web/errors.py` is the
one place that maps an error to a status code.

**Error messages are written for the person reading them**, never for a log.
They are rendered directly in the UI, so they must not contain a path, a
library name or a stack frame.

**Type hints everywhere.** mypy runs in `--strict` mode and CI enforces it.

**Tests say what they are testing.** `test_forgetting_a_review_card_drops_it_into_relearning`
tells you what broke; `test_review_2` does not.

## Adding an AI provider

1. Implement the `AIProvider` protocol in `ai/`.
2. `status()` must never raise; every other method raises only
   `AIUnavailableError`.
3. Validate all output with the Pydantic models in `ai/base.py`.
4. Add a branch to `ai/factory.py` and a member to `AIProvider` in `config.py`.
5. Test every failure mode against a mock transport. **CI must never contact a
   live model.**
6. Update `docs/PRIVACY.md` to say what leaves the machine. This is required,
   not optional.

## Troubleshooting

**`no such table: courses_fts`** — the FTS migration has not run.
`uv run studyforge db init`.

**Search returns nothing for content you know exists** — the index is
maintained by triggers, so rows written before the migration are backfilled by
it. If you loaded data by unusual means, re-run the migration.

**`database is locked`** — another StudyForge process holds the write lock. WAL
plus a 5-second busy timeout handles normal contention; two servers on one
database file is the usual cause.

**Playwright tests fail to start** — run `uv run --group e2e playwright install chromium`.

**Changes to a template are not showing** — Jinja templates are read from disk
per request, but `--reload` is still needed for Python changes.
