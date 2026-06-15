"""Pydantic request/response models for cook auth + admin cook-management (009).

Mirrors contracts/cook-auth-api.md + admin-cooks-api.md. `CookView` is the ONLY cook projection sent to a
client and it NEVER carries `password_hash` OR `id` (the hash never leaves the backend, and the opaque id
is an internal owner key — the admin works with the `username` handle). No `role` field exists anywhere:
cook accounts are role-less. Password strength (<8 chars) and duplicate-username rules are enforced in the
service so they raise the project's AppError envelope (a single, consistent {code, detail} shape) rather
than Pydantic's default 422 — so `password` stays a plain `str` here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

__all__ = [
    "CookLoginRequest",
    "CookTokenResponse",
    "CreateCookRequest",
    "ResetCookPasswordRequest",
    "CookView",
]


class CookLoginRequest(BaseModel):
    """Credentials posted to POST /auth/login (the only unauthenticated cook route)."""

    username: str
    password: str


class CookTokenResponse(BaseModel):
    """The minted cook-session JWT plus the caller's username, returned by a successful login.

    No `role` is issued — cook accounts are role-less (contracts/cook-auth-api.md).
    """

    access_token: str
    token_type: str = "bearer"
    username: str


class CreateCookRequest(BaseModel):
    """Body for POST /admin/cooks — an operator admin provisions a cook (no self-service path exists).

    `password` is unconstrained here on purpose: the service applies the <8-char rule so a weak password
    surfaces as the app's AppError 422, not Pydantic's differently-shaped validation error.
    """

    username: str
    password: str
    display_name: str | None = None


class ResetCookPasswordRequest(BaseModel):
    """Body for POST /admin/cooks/{username}/reset-password — the admin-only recovery path (FR-010)."""

    password: str


class CookView(BaseModel):
    """Safe outward projection of a cook account — `password_hash` and `id` are never included (FR-016)."""

    model_config = ConfigDict(from_attributes=True)

    username: str
    display_name: str | None
    is_active: bool
    created_by: str | None
    created_at: datetime
