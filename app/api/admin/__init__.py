"""Operator-facing (admin) API package — corpus / evals / metrics, all behind the Vault admin token.

`register_admin_routers` mounts the three admin routers in one call so the app factory wires the operator
surface the same way it wires the cook surface. Every route here depends on `admin_deps.require_operator`,
so the public widget (which holds no token) cannot reach any of them (FR-029).
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.admin import corpus, evals, metrics


def register_admin_routers(app: FastAPI) -> None:
    """Attach the operator-only corpus + evals + metrics routers to the app."""
    app.include_router(corpus.router)
    app.include_router(evals.router)
    app.include_router(metrics.router)
