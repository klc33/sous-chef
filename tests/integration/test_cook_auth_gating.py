"""Integration tests for User Story 1 — a cook signs in and the app is gated (009, FR-001/FR-012).

Drives the real auth + gate end to end against a real DB: a seeded cook logs in via `POST /auth/login`,
the minted cook-session JWT then opens the gated cook endpoints, and `GET /auth/me` echoes the cook. The
negative half is the grade: every cook endpoint answers 401 with no token AND with a legacy `X-Profile-ID`
header (there is no anonymous path), while the open routes (`/auth/login`, `/health`) need no token. Login
failures collapse to one generic 401 (no enumeration).
"""

from __future__ import annotations

import pytest
from app.core import security
from app.models.cook_account import CookAccount
from sqlalchemy.orm import Session

# A real password for the login round-trip (the `auth` conftest fixture seeds tokens directly; here we want
# the password path itself under test, so we hash a known password).
_USERNAME = "alice"
_PASSWORD = "thai-night-supper-1"

# The gated cook endpoints, as (method, path, json-body) — each must 401 without a valid cook token.
_GATED = [
    ("GET", "/profile", None),
    ("GET", "/recipes?category=dinner", None),
    ("GET", "/favorites", None),
    ("POST", "/chat", {"message": "hi"}),
]


@pytest.fixture
def alice(db_session: Session) -> CookAccount:
    """Seed one active cook with a known password so the real `/auth/login` password path can be exercised."""
    cook = CookAccount(
        username=_USERNAME,
        display_name=_USERNAME,
        is_active=True,
        password_hash=security.hash_password(_PASSWORD),
        created_by=None,
    )
    db_session.add(cook)
    db_session.flush()
    return cook


async def _request(client, method: str, path: str, *, headers=None, json=None):
    """Dispatch one request by verb so the gated-endpoint sweep can be table-driven."""
    return await client.request(method, path, headers=headers or {}, json=json)


async def test_login_issues_token_that_opens_the_gate(make_user_client, alice) -> None:
    """A valid login returns a cook JWT; that Bearer token then opens /profile and /auth/me echoes the cook."""
    async with make_user_client() as client:
        login = await client.post("/auth/login", json={"username": _USERNAME, "password": _PASSWORD})
        assert login.status_code == 200
        body = login.json()
        assert body["token_type"] == "bearer"
        assert body["username"] == _USERNAME
        token = body["access_token"]
        assert token

        headers = {"Authorization": f"Bearer {token}"}
        me = await client.get("/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json() == {"username": _USERNAME}

        # A gated endpoint now works with the token (defaults for a never-set profile).
        profile = await client.get("/profile", headers=headers)
        assert profile.status_code == 200


@pytest.mark.parametrize(("method", "path", "json_body"), _GATED)
async def test_gated_endpoint_rejects_missing_token(make_user_client, method, path, json_body) -> None:
    """Every cook endpoint is 401 `cook_unauthorized` with no Authorization header (no anonymous path)."""
    async with make_user_client() as client:
        resp = await _request(client, method, path, json=json_body)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "cook_unauthorized"


@pytest.mark.parametrize(("method", "path", "json_body"), _GATED)
async def test_gated_endpoint_rejects_legacy_profile_header(
    make_user_client, method, path, json_body
) -> None:
    """A legacy `X-Profile-ID` header carries no Bearer token, so it is rejected too — 401 (FR-012)."""
    async with make_user_client() as client:
        resp = await _request(
            client, method, path, headers={"X-Profile-ID": "legacy-anon"}, json=json_body
        )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "cook_unauthorized"


async def test_health_is_open(make_user_client) -> None:
    """`GET /health` needs no token — it is liveness/smoke, not a cook surface (gating-model.md)."""
    async with make_user_client() as client:
        resp = await client.get("/health")
    assert resp.status_code == 200


async def test_login_failure_is_one_generic_401(make_user_client, alice) -> None:
    """A wrong password (and an unknown username) both return the same generic 401 — no enumeration (FR-008)."""
    async with make_user_client() as client:
        wrong = await client.post("/auth/login", json={"username": _USERNAME, "password": "nope"})
        unknown = await client.post("/auth/login", json={"username": "ghost", "password": _PASSWORD})

    for resp in (wrong, unknown):
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "auth_invalid_credentials"
