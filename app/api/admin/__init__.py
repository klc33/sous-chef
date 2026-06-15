"""Operator-facing (admin) API package — corpus / evals / metrics / accounts, all behind operator JWT.

`register_admin_routers` mounts the admin routers in one call so the app factory wires the operator surface
the same way it wires the cook surface. Every route here depends on an `admin_deps` guard (`require_operator`
for the operational routes, `require_admin` for `/admin/users/*`), so the public widget (which holds no
token) cannot reach any of them (FR-029) and only an admin can manage accounts (FR-011).
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.admin import auth, cooks, corpus, evals, metrics, users


def register_admin_routers(app: FastAPI) -> None:
    """Attach the operator-only corpus + evals + metrics + account-management routers to the app.

    `auth` carries the one unauthenticated `/admin` route (login) plus `/admin/auth/me`; the rest of the
    surface stays guarded by `require_operator` / `require_admin` (`users` + `cooks` use `require_admin`).
    """
    app.include_router(auth.router)
    app.include_router(corpus.router)
    app.include_router(evals.router)
    app.include_router(metrics.router)
    app.include_router(users.router)
    app.include_router(cooks.router)
