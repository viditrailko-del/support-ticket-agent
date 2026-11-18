# Two targets from one file:
#   dev  (docker compose): all deps incl. pytest, source bind-mounted, auto-reload
#   prod (default):        runtime deps only, no uv, non-root, JSON logs, healthcheck
#
#   docker build -t support-agent-api .                  # prod (last stage = default)
#   docker build --target dev -t support-agent-api:dev .

FROM python:3.12-slim AS base
RUN pip install --no-cache-dir uv==0.13.7
# Keep the venv OUTSIDE /app so the bind-mounted source (and any Windows .venv) can't clash.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1
WORKDIR /app


FROM base AS dev
# Deps first (cached layer: only re-runs when deps change).
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project
COPY . .
EXPOSE 8000
CMD ["uv", "run", "--frozen", "fastapi", "dev", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS prod-deps
COPY pyproject.toml uv.lock .python-version ./
# --no-dev: no pytest/httpx in production. Bytecode compiled at build time.
RUN UV_COMPILE_BYTECODE=1 uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim AS prod
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json
RUN useradd --create-home --uid 10001 app
WORKDIR /app
# Same base image as prod-deps, so the venv's python symlinks stay valid.
COPY --from=prod-deps /opt/venv /opt/venv
# Only what runs: the app (prompts included) and migrations. No tests, eval, data generator.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
# Migrations are a separate deploy step (`alembic upgrade head`), not run on every start.
# uvicorn directly (not `fastapi run`) with a JSON log config, so even the server's first lines, before the
# app has configured logging, are JSON: the whole stream is one JSON object per line.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers",      "--log-config", "app/uvicorn_log.json"]
