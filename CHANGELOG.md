# Changelog

All notable changes to StudyForge are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-08-18

The first complete version. StudyForge ingests your notes, extracts the
concepts worth learning, generates study material from them, and schedules it
with FSRS-6 spaced repetition — all working with no AI configured.

### Study engine

- **FSRS-6 spaced repetition**, implemented from the published specification as
  a pure domain module with no I/O and no clock access. Verified against the
  reference implementation by differential testing over 4,096 review
  transitions before being frozen as golden vectors ([#3])
- **Study queue** prioritising overdue reviews, then due reviews, then cards for
  concepts you keep getting wrong, then new cards under a separate daily cap
  ([#9])
- **Weak-concept analysis** computed from observed behaviour with recency
  weighting and an explicit minimum-evidence threshold. No language model
  participates in this judgement ([#11])

### Content

- **Ingestion** of pasted text, `.txt`, `.md` and PDFs, with text extraction via
  pypdf. A PDF with no text layer is reported as scanned rather than silently
  producing empty material ([#6])
- **Normalisation and semantic chunking** — de-hyphenation, hard-wrap joining,
  page-artefact removal, then splitting at headings, paragraphs and sentences
  with character offsets preserved for provenance ([#4])
- **Deterministic concept extraction** from definition sentences, glossary
  lines, headings and repeated terms, each scored by evidence strength, with
  full manual editing because extraction is pattern matching, not
  understanding ([#5])
- **Deterministic flashcard and quiz generation**, producing fewer defensible
  items rather than more weak ones. Multiple-choice questions are only built
  when the course can supply genuinely plausible distractors ([#8], [#10])

### Interface

- Server-rendered pages driven by HTMX: dashboard, course, document, study,
  quiz, progress, search, ask and settings ([#7])
- Full keyboard control of the review loop, and a study view built for a phone
  ([#15])
- **Full-text search** over courses, documents, concepts and flashcards using
  SQLite FTS5, kept in sync by database triggers ([#12])
- **Ask my notes** — retrieval-grounded question answering that works in a
  reduced but honest form with no AI configured ([#14])
- A **JSON API** covering courses, documents, flashcards, reviews and progress,
  with generated OpenAPI docs at `/api/docs`

### AI

- Pluggable provider architecture with `NoAIProvider` implemented first, so
  "no AI" is a normal state rather than an error path ([#13])
- **Ollama** integration for local inference at no cost, with graceful
  degradation when it is unreachable or the model is missing
- All model output validated with Pydantic; output that is schema-valid but
  unusable is discarded

### Data

- Eleven SQLAlchemy 2 models with Alembic migrations from the first commit
  ([#2])
- SQLite configured with foreign-key enforcement and WAL at connect time
- Provenance recorded on all generated material

### Quality

- 695 unit, integration and security tests, plus 15 Playwright browser tests
- CI running format, lint, `mypy --strict`, tests, migrations on a clean
  database, browser tests and a dependency advisory scan ([#1], [#17])
- Security hardening across uploads, path handling, template escaping, SQL
  construction, error disclosure and logging ([#16])
- Complete documentation set, including the FSRS formulas and how the
  implementation was verified ([#18])
- Dockerfile and clearly-labelled sample data ([#19])

### Deliberately not included

- FSRS parameter optimisation — the published defaults are shipped instead
- Multi-user support and authentication — V1 is single-user by design
- OCR for scanned PDFs — reported honestly rather than guessed at
- A hosted public demo — see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for why

[#1]: https://github.com/mateoosoriodelhonte/studyforge/issues/1
[#2]: https://github.com/mateoosoriodelhonte/studyforge/issues/2
[#3]: https://github.com/mateoosoriodelhonte/studyforge/issues/3
[#4]: https://github.com/mateoosoriodelhonte/studyforge/issues/4
[#5]: https://github.com/mateoosoriodelhonte/studyforge/issues/5
[#6]: https://github.com/mateoosoriodelhonte/studyforge/issues/6
[#7]: https://github.com/mateoosoriodelhonte/studyforge/issues/7
[#8]: https://github.com/mateoosoriodelhonte/studyforge/issues/8
[#9]: https://github.com/mateoosoriodelhonte/studyforge/issues/9
[#10]: https://github.com/mateoosoriodelhonte/studyforge/issues/10
[#11]: https://github.com/mateoosoriodelhonte/studyforge/issues/11
[#12]: https://github.com/mateoosoriodelhonte/studyforge/issues/12
[#13]: https://github.com/mateoosoriodelhonte/studyforge/issues/13
[#14]: https://github.com/mateoosoriodelhonte/studyforge/issues/14
[#15]: https://github.com/mateoosoriodelhonte/studyforge/issues/15
[#16]: https://github.com/mateoosoriodelhonte/studyforge/issues/16
[#17]: https://github.com/mateoosoriodelhonte/studyforge/issues/17
[#18]: https://github.com/mateoosoriodelhonte/studyforge/issues/18
[#19]: https://github.com/mateoosoriodelhonte/studyforge/issues/19

[Unreleased]: https://github.com/mateoosoriodelhonte/studyforge/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/mateoosoriodelhonte/studyforge/releases/tag/v1.0.0
