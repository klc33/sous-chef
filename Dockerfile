# syntax=docker/dockerfile:1
# Backend image for the SousChef monolith. Installs ONLY the `backend` extra via uv — no
# dev/test tooling, no torch — so the image stays small (golden rule #3, constitution P10).
FROM python:3.12-slim AS base

# uv binary (pinned by digest-less tag is fine here; deps themselves are pinned by uv.lock).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /srv

# curl is used by scripts/seed_vault.sh (writes dev secrets to Vault's KV HTTP API) and by
# the runbook healthcheck. --no-install-recommends keeps the layer tiny.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 1) Dependency layer (cached): resolve ONLY the backend extra from the frozen lockfile,
#    without installing the project source yet, so this layer is reused across code changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra backend

# 2) App layer: copy the source the backend actually needs, then finalize the environment.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra backend

ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 8000

# Default command (used by Railway). docker-compose overrides this to also run the
# Alembic baseline + Vault seed before launching uvicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
