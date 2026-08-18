# Deployment

## The recommendation: run it locally

StudyForge is designed to run on your own machine. That is not a limitation to
work around — it is the point. Your study material stays yours, there is nothing
to pay for, and there is no service to keep running.

```bash
uv sync && uv run studyforge db init && uv run studyforge serve
```

By default it binds to `127.0.0.1`, so it is reachable only from your own
computer.

## Why there is no public demo

A hosted demo would be nice for a portfolio, and I looked into it properly
before deciding against it. Checked **August 2026**:

**The decisive reason is the design, not the cost.** StudyForge has **no
authentication, by design** (see [SECURITY.md](../SECURITY.md)). A single shared
public instance would let any visitor read, edit and delete every other
visitor's notes, and upload files to the same store. Deploying it would mean
either shipping a security hole or bolting on a login that the application is
deliberately built not to need.

**The free-tier storage problem makes it worse rather than better.** The
genuinely free, no-credit-card option for a Python container is Hugging Face
Spaces, whose free tier gives ephemeral disk — the filesystem is wiped on every
rebuild, and persistent storage is a paid add-on. A spaced-repetition system
whose review history silently disappears is not a demonstration of a spaced
repetition system; it is a demonstration of losing data.

**No paid tier was enabled and no payment details were entered**, in keeping
with the project's zero-cost rule.

So instead of a misleading demo, this repository has:

- **screenshots** of every major screen, taken from the real application with
  the built-in sample data
- **`uv run studyforge db demo`**, which gets you a populated instance in about
  thirty seconds
- **a five-line quick start** verified on a clean clone

If you are evaluating this project, running it locally takes less time than
loading a hosted demo would.

## If you do want to host it

Everything below assumes **you are the only user**, or that you put an
authenticating reverse proxy in front.

### Docker

```bash
docker build -t studyforge .
docker run -p 8000:8000 -v studyforge-data:/data studyforge
```

The named volume matters: without it, your database lives inside the container
and disappears with it.

The image runs as a non-root user, installs no build toolchain in the final
layer, and runs migrations on start.

### Configuration for a hosted instance

```env
ENVIRONMENT=production
SECRET_KEY=<generate one>
DATA_DIR=/data
LOG_FORMAT=json
```

Generate a key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

In `production`, error pages show a fixed message rather than the exception,
and the session cookie is marked `Secure`. StudyForge logs a warning if
`SECRET_KEY` is left at its development default.

### What StudyForge deliberately does not include

- **No Kubernetes manifests, Helm charts or Terraform.** This is a single
  process with a single SQLite file. Orchestration would be infrastructure
  theatre.
- **No horizontal scaling story.** SQLite with WAL suits one user comfortably.
  Scaling out would mean PostgreSQL and a real authentication model, which is a
  different product.
- **No managed-database integration.** The model layer is written so PostgreSQL
  *would* work, but it is untested, so it is not claimed.

## Backups

Everything is in `DATA_DIR`. Copy the directory.

```bash
# With the app stopped, or using SQLite's own consistent backup:
sqlite3 data/studyforge.db ".backup 'backup.db'"
cp -r data/uploads backup-uploads/
```
