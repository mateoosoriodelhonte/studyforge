# Privacy

StudyForge is built so that your study material stays yours. This document says
exactly what that means, and exactly when it stops being true.

## The short version

**With the default configuration (`AI_PROVIDER=none`), nothing you put into
StudyForge leaves your computer.** There is no account, no sync, no telemetry,
no analytics, no crash reporting and no update check. The application makes no
outbound network requests at all.

## What is stored, and where

Everything lives under `DATA_DIR`, which defaults to `./data/` in the directory
you run StudyForge from:

| Path | Contents |
|---|---|
| `data/studyforge.db` | Courses, documents, extracted text, chunks, concepts, flashcards, review history, quizzes, attempts |
| `data/studyforge.db-wal`, `-shm` | SQLite write-ahead log (transient) |
| `data/uploads/` | The original files you uploaded, under generated names |

Delete that directory and StudyForge has forgotten everything. There is no
second copy anywhere.

Uploaded files are written with `0600` permissions — readable only by your user
account.

## What leaves your machine

### With `AI_PROVIDER=none` — the default

**Nothing.** No request is made to any host.

The vendored copy of HTMX is served from your own machine specifically so that
the interface renders with no network access whatsoever. There are no CDN
links, no web fonts and no remote images anywhere in the application; a test
enforces this.

### With `AI_PROVIDER=ollama`

Ollama runs on your own hardware. StudyForge sends requests to
`OLLAMA_BASE_URL` — by default `http://localhost:11434` — and **nothing reaches
the internet**.

What is sent, and only when you invoke a feature that needs it:

| Feature | What is sent |
|---|---|
| Ask my notes | The question, plus up to 5 retrieved passages (≤1,500 characters each) |
| Explain an answer | The question, the expected answer, and the retrieved passages |
| Generate cards or a quiz | One passage from the document you asked about |

What is **never** sent, under any configuration:

- your database, or any part of it beyond the passages named above
- documents other than the one you are acting on
- API keys, tokens, `.env` contents or any configuration value
- file paths, filenames or anything about your filesystem
- your review history, schedule, progress or usage patterns
- any identifier for you or your machine

The prompt is assembled in `services/ask.py` and `ai/ollama.py`, and an
integration test asserts that an unrelated document in the same course does not
appear in the request body.

> If you point `OLLAMA_BASE_URL` at a machine that is not your own, the
> passages go to that machine. That is your choice to make, but it is worth
> being explicit that the setting controls it.

## What is never given to a language model

Beyond data, some *decisions* are deliberately kept away from AI:

- **Spaced-repetition scheduling.** Intervals are computed by FSRS-6
  arithmetic. A model cannot see the memory state, suggest an interval, or
  influence the queue.
- **Weak-concept classification.** Whether you are struggling with a concept is
  computed from your answers. A model may later *explain* a concept; it never
  decides which ones need work.
- **SQL and filesystem paths.** No model output is ever used to build a query
  or a path.
- **Rendering.** Model output is escaped like any other untrusted text. No
  model-authored HTML is rendered.

## Logging

StudyForge logs named events — `document_uploaded`, `review_completed`,
`ai_request_failed` — with identifiers and counts, to stderr only. Nothing is
written to a log file unless you redirect it yourself.

Long string values are truncated at 200 characters before being logged, so a
stray field cannot copy your notes into a log. Secrets are never logged.

## The demo instance

There isn't one, and that is deliberate. See
[DEPLOYMENT.md](DEPLOYMENT.md#why-there-is-no-public-demo) — the short reason is
that StudyForge has no authentication by design, so a shared public instance
would let anyone read and delete everyone else's notes.

## Verifying this yourself

The claims above are checkable:

```bash
# Watch for outbound connections while you use it (macOS/Linux)
lsof -i -P | grep -i studyforge

# Confirm no CDN or remote assets in any template
grep -rn "https://" src/studyforge/web/templates/ src/studyforge/web/static/app.css

# Read every place an outbound request is made
grep -rn "httpx" src/studyforge/
```

The last one returns exactly one module: `ai/ollama.py`.
