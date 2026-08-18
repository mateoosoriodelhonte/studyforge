# Changelog

All notable changes to StudyForge are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Python engineering foundation: `uv`-managed 3.12 project, Ruff, mypy (strict),
  pytest with coverage, and GitHub Actions CI ([#1](https://github.com/mateoosoriodelhonte/studyforge/issues/1)).
- Environment-driven configuration with safe defaults, so a fresh clone runs
  with no `.env` file at all.
- Structured logging that emits named domain events and truncates long values so
  private study material never lands in a log.
- FastAPI application factory and a `studyforge serve` CLI entry point.
- FSRS-6 spaced-repetition engine as a pure domain module, verified against the
  reference implementation by differential testing over 4,096 review transitions
  ([#3](https://github.com/mateoosoriodelhonte/studyforge/issues/3)).
- Persistence layer: 11 SQLAlchemy 2 models, Alembic migrations from the first
  commit, and SQLite configured with foreign-key enforcement and WAL
  ([#2](https://github.com/mateoosoriodelhonte/studyforge/issues/2)).
