FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
# No BuildKit cache mounts: Railway's builder rejects them without a service-specific id.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
WORKDIR /app

# Dependencies first: this layer is reused while pyproject.toml and uv.lock do not change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TASKS_DB=/app/data/tasks.sqlite3 \
    APP_MEMORY_DB=/app/data/app_memory.sqlite3 \
    TRACES_DIR=/app/data/traces
WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app --no-create-home app \
    && mkdir -p /app/data && chown app:app /app/data
COPY --from=builder /app/.venv /app/.venv
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

# The entrypoint starts as root only to fix ownership of the mounted volume, then
# drops to the unprivileged user before launching the service.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["aprendiz-api"]
