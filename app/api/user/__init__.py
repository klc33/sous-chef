"""Cook-facing (public, profile-scoped) API package.

`register_user_routers` mounts the user-facing routers onto the app in one call so the app factory (and
tests) wire the whole public surface the same way.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.user import favorites, profile, recipes


def register_user_routers(app: FastAPI) -> None:
    """Attach the cook-facing routers (profile + recipes + favorites) to the app."""
    app.include_router(profile.router)
    app.include_router(recipes.router)
    app.include_router(favorites.router)
