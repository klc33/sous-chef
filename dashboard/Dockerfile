# syntax=docker/dockerfile:1
# Streamlit operator-console image — its OWN image, never mixed into the backend (golden rule #3,
# constitution P10: keep images lean). Installs ONLY the `dashboard` extra (streamlit,
# streamlit-authenticator, httpx, pandas) — no backend libs, no torch. The dashboard reads Vault over
# HTTP (httpx, not hvac) and never imports the `app` package, so only the dashboard/ package plus the
# lockfile enter the build.
#
# Built from the repo ROOT context because pyproject.toml + uv.lock live there; the adjacent
# dashboard/Dockerfile.dockerignore trims that context down to just the lockfile + the dashboard/ tree.
FROM python:3.12-slim AS base

# uv binary; deps themselves are pinned by uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /srv

# 1) Dependency layer (cached): resolve ONLY the dashboard extra from the frozen lockfile, without the
#    project source yet, so this layer is reused across dashboard code changes. (A BuildKit cache mount
#    would trim the image, but Railway's builder rejects it — see the RUNBOOK image-size note.)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --extra dashboard

# 2) App layer: the Streamlit package (entry app.py + auth.py + pages/). It is imported as `dashboard.*`,
#    so it must sit as a subpackage under the workdir (streamlit run dashboard/app.py).
COPY dashboard ./dashboard

ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 8501

# Serve the multipage console on all interfaces so the published port is reachable. Headless disables the
# "open a browser" prompt; secrets (operator hash, cookie key, admin token) are read from Vault at runtime.
CMD ["streamlit", "run", "dashboard/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
