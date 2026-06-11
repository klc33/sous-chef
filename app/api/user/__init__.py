"""Cook-facing (public, profile-scoped) API package.

`register_user_routers` mounts the user-facing routers onto the app in one call so the app factory (and
tests) wire the whole public surface the same way.
"""

from __future__ import annotations

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.user import chat, favorites, profile, recipes


def register_user_routers(app: FastAPI) -> None:
    """Attach the cook-facing routers (profile + recipes + favorites + chat) to the app.

    Also wires the per-profile slowapi rate limiter the /chat endpoint uses: the limiter instance is
    registered on app.state and its RateLimitExceeded handler installed so an over-budget cook gets a
    clean 429 instead of an unhandled error.
    """
    app.state.limiter = chat.limiter
    # slowapi's handler is typed for RateLimitExceeded specifically, not the generic Exception base
    # Starlette's stub expects — a known slowapi typing quirk; the runtime contract is correct.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    app.include_router(profile.router)
    app.include_router(recipes.router)
    app.include_router(favorites.router)
    app.include_router(chat.router)
