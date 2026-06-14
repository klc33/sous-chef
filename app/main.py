"""FastAPI application factory for the SousChef monolith.

Build order (fail-fast): settings → logging → Vault (load secrets) → DB/cache adapters → app
with a lifespan that disposes them on shutdown. Any failure to construct settings or reach
Vault propagates and aborts startup rather than booting a half-wired app (FR-010).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import register_admin_routers
from app.api.health import register_health_router
from app.api.user import register_user_routers
from app.config import VAULT_KEY_LANGSMITH_API_KEY, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.infra.cache import Cache
from app.infra.db import Database
from app.infra.tracing import add_tracing_middleware, configure_tracing
from app.infra.vault import VaultAdapter


def create_app() -> FastAPI:
    """Construct and return the FastAPI app with all foundation wiring in place.

    Steps run in dependency order; an exception raised here propagates and aborts the process,
    which is the intended fail-fast behavior (FR-010).
    """
    settings = get_settings()
    configure_logging(env=settings.env)
    log = structlog.get_logger()

    # Connect to Vault and load secrets up front (fail-fast if unreachable / unauthenticated).
    vault = VaultAdapter(settings)
    vault.load_secrets()
    log.info("vault.secrets_loaded")

    # Build the DB adapter (connections open lazily). The cache is OPTIONAL: only built when REDIS_URL is
    # configured — without it the app runs cache-less (the routing-split metric simply reports empty).
    db = Database(settings.postgres_url)
    cache = Cache(settings.redis_url) if settings.redis_url else None

    # Configure tracing → Phoenix (self-hosted) or LangSmith Cloud, per settings.tracing_provider
    # (redacted, best-effort: None means run untraced). The LangSmith API key is a Vault secret read
    # here best-effort — a missing key just disables tracing and must never break startup (Decision 7).
    tracing_api_key = None
    if settings.tracing_provider.lower() == "langsmith":
        try:
            tracing_api_key = vault.get(VAULT_KEY_LANGSMITH_API_KEY)
        except Exception:  # noqa: BLE001 — absent key disables tracing, never fails boot
            log.warning("tracing.langsmith_key_missing")
    tracer = configure_tracing(settings, api_key=tracing_api_key)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Log startup, then dispose the DB engine and Redis pool on shutdown."""
        log.info("startup", env=settings.env, version=settings.version)
        try:
            yield
        finally:
            db.dispose()
            if cache is not None:
                cache.close()
            log.info("shutdown")

    app = FastAPI(title="SousChef Foundation API", version=settings.version, lifespan=lifespan)

    # Make the settings + adapters available to routers via app.state.
    app.state.settings = settings
    app.state.vault = vault
    app.state.db = db
    app.state.cache = cache

    # Cross-origin access for the cook widget (a browser SPA on its own origin calling this backend at
    # VITE_API_BASE). Without this, the browser blocks every widget request at the CORS preflight. The
    # allow-list is non-secret config (widget origins, not credentials); we allow the cook verbs + the
    # X-Profile-ID identity header the widget sends on every call. No credentials/cookies are used by the
    # widget (identity is the header), so allow_credentials stays False.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.widget_origins_list,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["X-Profile-ID", "Content-Type"],
        allow_credentials=False,
    )

    # Emit one redacted span per request (no-op if tracing did not configure).
    add_tracing_middleware(app, tracer)

    register_error_handlers(app)
    register_health_router(app)
    register_user_routers(app)
    # Operator-only surface (corpus / evals / metrics), each guarded by the Vault admin token. The public
    # widget holds no token and cannot reach these (FR-029); the Streamlit dashboard sends the token.
    register_admin_routers(app)

    return app


app = create_app()
