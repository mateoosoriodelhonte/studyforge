# StudyForge — a single process, a single SQLite file, no build toolchain at run time.
#
# Two stages so the final image carries no compiler and no uv cache. The image
# is intentionally plain: this application is one process reading a local file,
# and anything more elaborate would be infrastructure theatre.
#
# No BuildKit-only syntax is used, so this builds with any Docker version. Cache
# mounts would speed rebuilds slightly, at the cost of making the file
# unbuildable for anyone without buildx — a bad trade for a five-line install.
#
#   docker build -t studyforge .
#   docker run -p 8000:8000 -v studyforge-data:/data studyforge
#
# The named volume matters. Without it the database lives inside the container
# and disappears with it.
#
# StudyForge has no authentication by design. Do not expose this to an
# untrusted network without an authenticating reverse proxy. See SECURITY.md.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so a source-only change does not invalidate the layer.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-install-project --no-dev

COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
RUN uv sync --locked --no-dev


FROM python:3.12-slim-bookworm AS runtime

# Run as an unprivileged user with a writable data directory.
RUN groupadd --system --gid 1001 studyforge \
    && useradd --system --uid 1001 --gid studyforge --create-home studyforge \
    && mkdir -p /data \
    && chown -R studyforge:studyforge /data

WORKDIR /app

COPY --from=builder --chown=studyforge:studyforge /app/.venv /app/.venv
COPY --from=builder --chown=studyforge:studyforge /app/src /app/src
COPY --from=builder --chown=studyforge:studyforge /app/migrations /app/migrations
COPY --from=builder --chown=studyforge:studyforge /app/alembic.ini /app/alembic.ini

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    ENVIRONMENT=production \
    LOG_FORMAT=json \
    AI_PROVIDER=none

USER studyforge
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# Migrate, then serve. Running migrations on start is right for a single-process
# application with one writer; it would need rethinking for multiple replicas,
# which StudyForge deliberately does not support.
CMD ["sh", "-c", "alembic upgrade head && studyforge serve --host 0.0.0.0 --port 8000"]
