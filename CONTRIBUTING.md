# Contributing

Thanks for taking a look. StudyForge is a personal open-source project, so
please open an issue before starting anything substantial — it may already be
deliberately out of scope.

## Getting set up

See [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md). The short version:

```bash
uv sync --all-groups
uv run studyforge db init
uv run pytest
```

## Before opening a pull request

```bash
uv run ruff format .
uv run ruff check .
uv run mypy
uv run pytest
```

All four must be clean. CI runs the same checks plus migrations on a clean
database, browser tests, and a dependency advisory scan.

## What a good change looks like

**Tests that describe behaviour.** A test name should tell you what broke.
Prefer `test_a_lapse_never_increases_stability` over `test_stability_3`.

**No weakened tests.** If a test fails, fix the code or explain why the test was
asserting the wrong thing. Deleting an assertion to get CI green is the one
thing that will get a PR closed.

**Respect the layer boundaries.** The domain layer imports no SQLAlchemy, no
FastAPI and no AI provider. See [ARCHITECTURE.md](ARCHITECTURE.md).

**Determinism where it is promised.** Chunking, concept extraction, generation,
queue building and scheduling are all deterministic and tested to be. If your
change introduces randomness or clock-reading into any of them, it needs a very
good reason.

**Honesty in the UI.** StudyForge does not claim to know things it does not.
Do not add a metric that implies confidence a small sample cannot support, and
do not present extracted concepts as authoritative.

**Documentation that stays true.** If you change what leaves the machine,
update `docs/PRIVACY.md`. If you change a security property, update
`SECURITY.md`. A document that no longer matches the code is worse than no
document.

## Commit messages

Conventional-ish prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`) with
a scope where it helps. The body should explain **why**, not restate the diff —
the diff is already in the commit.

## Reporting bugs

Include what you did, what you expected, what happened, and your OS and Python
version. If it involves a document, please say what kind (pasted text, `.txt`,
PDF) — **do not attach private study material.**

## Security

Please report vulnerabilities via a
[private advisory](https://github.com/mateoosoriodelhonte/studyforge/security/advisories/new)
rather than a public issue. See [SECURITY.md](SECURITY.md).

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
