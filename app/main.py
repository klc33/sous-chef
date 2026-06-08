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

from app.api.health import register_health_router
from app.api.user import register_user_routers
from app.config import get_settings
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

    # Build the DB and cache adapters (connections are opened lazily / on first use).
    db = Database(settings.postgres_url)
    cache = Cache(settings.redis_url)

    # Configure tracing → Phoenix (redacted, best-effort: None means run untraced).
    tracer = configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Log startup, then dispose the DB engine and Redis pool on shutdown."""
        log.info("startup", env=settings.env, version=settings.version)
        try:
            yield
        finally:
            db.dispose()
            cache.close()
            log.info("shutdown")

    app = FastAPI(title="SousChef Foundation API", version=settings.version, lifespan=lifespan)

    # Make the settings + adapters available to routers via app.state.
    app.state.settings = settings
    app.state.vault = vault
    app.state.db = db
    app.state.cache = cache

    # Emit one redacted span per request (no-op if tracing did not configure).
    add_tracing_middleware(app, tracer)

    register_error_handlers(app)
    register_health_router(app)
    register_user_routers(app)

    return app


app = create_app()
