"""Pydantic request/response models for operator auth + account management (008).

Mirrors contracts/auth-api.md. `AccountView` is the ONLY account projection sent to a client and it
NEVER carries `password_hash` — the hash never leaves the backend (FR-016/FR-018). Password strength
(<8 chars) and duplicate-username rules are enforced in the service so they raise the project's AppError
envelope (a single, consistent {code, detail} shape), not Pydantic's default 422 — so `password` stays a
plain `str` here. `role` is a Literal so an out-of-range role is rejected at the boundary (FR-005).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "LoginRequest",
    "TokenResponse",
    "CreateUserRequest",
    "ResetPasswordRequest",
    "AccountView",
]

# The two valid operator roles, reused across the request/response models and matched by the DB CHECK.
Role = Literal["admin", "user"]


class LoginRequest(BaseModel):
    """Credentials posted to POST /admin/auth/login (the only unauthenticated /admin route)."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """The minted JWT plus the caller's identity, returned by a successful login."""

    access_token: str
    token_type: str = "bearer"
    role: Role
    username: str


class CreateUserRequest(BaseModel):
    """Body for POST /admin/users — an admin provisions a new account (no self-service path exists).

    `password` is unconstrained here on purpose: the service applies the <8-char rule so a weak password
    surfaces as the app's AppError 422, not Pydantic's differently-shaped validation error.
    """

    username: str
    password: str
    role: Role
    display_name: str | None = None


class ResetPasswordRequest(BaseModel):
    """Body for POST /admin/users/{username}/reset-password — the admin-only recovery path (FR-008a)."""

    password: str


class AccountView(BaseModel):
    """Safe outward projection of an operator account — `password_hash` is never included (FR-016)."""

    model_config = ConfigDict(from_attributes=True)

    username: str
    display_name: str | None
    role: Role
    is_active: bool
    created_by: str | None
    created_at: datetime
