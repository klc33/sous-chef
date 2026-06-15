"""Integration tests for User Story 2 — operator login + identity (`/admin/auth/*`).

Drives the login boundary end-to-end against a live Postgres: a valid credential returns a JWT + role
(POST /admin/auth/login), a wrong password and an unknown username both collapse to ONE identical generic
401 (no account enumeration, FR-012), a deactivated account is denied login even with the right password,
and GET /admin/auth/me echoes the verified token's identity (the dashboard reads it to decide whether to
show the admin-only Users page). Login mints the same per-user JWT the rest of the `/admin` surface
verifies, so a token obtained here also authorizes `/admin/auth/me` via `require_operator`.

Vault + settings are faked on `app.state` (mirroring test_operator_users): the fake Vault hands back the
HS256 signing key and the fake settings carry the 8h TTL, so no real Vault is needed; the DB session is the
isolated test session the integration conftest provides. Error bodies use the project envelope
`{"error": {"code", "message"}}`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from app.api.admin import register_admin_routers
from app.api.deps import get_db
from app.config import VAULT_KEY_JWT_SIGNING_KEY
from app.core import security
from app.core.errors import register_error_handlers
from app.models.operator_account import OperatorAccount
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

# The HS256 signing key the fake Vault hands back; the backend signs/verifies every operator JWT with it.
_SIGNING_KEY = "test-jwt-signing-key-padded-to-min-32-bytes"  # noqa: S105 — a test fixture value
_TTL_MINUTES = 480


class _FakeVault:
    """Stand-in Vault adapter: returns the JWT signing key for that key, raising on anything else."""

    def get(self, key: str) -> str:
        """Return the fake signing key (the only secret the login boundary reads)."""
        if key == VAULT_KEY_JWT_SIGNING_KEY:
            return _SIGNING_KEY
        raise KeyError(key)


class _FakeSettings:
    """Minimal settings stand-in carrying just the knob the login route reads — the JWT TTL."""

    jwt_ttl_minutes = _TTL_MINUTES


@pytest.fixture
def make_admin_client(db_session: Session) -> Callable[..., AsyncClient]:
    """Return a factory for an ASGI client over an app wired with the admin routers + fake Vault/settings.

    Registers exactly the error handlers + admin routers (the same registration the real factory uses)
    with `get_db` overridden to the isolated test session and a fake Vault + settings on app.state, so the
    auth endpoints run their real logic — credential check, token mint, and the JWT guard — without a live
    Vault.
    """

    def _override_get_db() -> Iterator[Session]:
        """Yield the test session, committing on success so SAVEPOINT semantics mirror production."""
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    def _factory() -> AsyncClient:
        app = FastAPI()
        register_error_handlers(app)
        register_admin_routers(app)
        app.state.vault = _FakeVault()
        app.state.settings = _FakeSettings()
        app.dependency_overrides[get_db] = _override_get_db
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _factory


def _seed_account(
    session: Session, *, username: str, role: str, password: str, is_active: bool = True
) -> None:
    """Insert one operator account directly so login resolves a real row with a known password.

    Flushed (not committed) so it is visible to a request running in the same session. A request that ends
    in an `AppError` makes `get_db` roll back, discarding a still-pending seed, so each test seeds fresh and
    a failing-login test issues a single request.
    """
    account = OperatorAccount(
        username=username,
        role=role,
        password_hash=security.hash_password(password),
    )
    account.is_active = is_active
    session.add(account)
    session.flush()


# ── login: success ────────────────────────────────────────────────────────────────────────────────────


async def test_login_returns_jwt_and_role(make_admin_client, db_session) -> None:
    """Valid credentials return a bearer JWT carrying the account's identity + role (200)."""
    _seed_account(db_session, username="boss", role="admin", password="boss-password")
    async with make_admin_client() as client:
        resp = await client.post("/admin/auth/login", json={
            "username": "boss", "password": "boss-password"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "admin"
    assert body["username"] == "boss"
    # The minted token is a real, verifiable JWT whose claims echo the account.
    claims = security.decode_token(body["access_token"], signing_key=_SIGNING_KEY)
    assert claims["sub"] == "boss"
    assert claims["role"] == "admin"


# ── login: generic failure (no enumeration) ─────────────────────────────────────────────────────────────


async def test_login_wrong_password_is_generic_401(make_admin_client, db_session) -> None:
    """A wrong password for a real account is a generic 401 `auth_invalid_credentials` (FR-012)."""
    _seed_account(db_session, username="boss", role="admin", password="boss-password")
    async with make_admin_client() as client:
        resp = await client.post("/admin/auth/login", json={
            "username": "boss", "password": "not-the-password"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_invalid_credentials"


async def test_login_unknown_username_is_identical_401(make_admin_client) -> None:
    """An unknown username yields the SAME generic 401 body as a wrong password — no enumeration (FR-012)."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/auth/login", json={
            "username": "ghost", "password": "whatever-password"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_invalid_credentials"


async def test_login_deactivated_account_denied(make_admin_client, db_session) -> None:
    """A deactivated account is denied login even with the correct password (generic 401)."""
    _seed_account(
        db_session, username="ex", role="user", password="ex-password", is_active=False
    )
    async with make_admin_client() as client:
        resp = await client.post("/admin/auth/login", json={
            "username": "ex", "password": "ex-password"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth_invalid_credentials"


# ── identity echo ───────────────────────────────────────────────────────────────────────────────────────


async def test_me_echoes_token_claims(make_admin_client, db_session) -> None:
    """A token minted at login authorizes GET /admin/auth/me, which echoes `{username, role}`."""
    _seed_account(db_session, username="boss", role="admin", password="boss-password")
    async with make_admin_client() as client:
        login = await client.post("/admin/auth/login", json={
            "username": "boss", "password": "boss-password"})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        resp = await client.get("/admin/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"username": "boss", "role": "admin"}


async def test_me_requires_a_token(make_admin_client) -> None:
    """GET /admin/auth/me without a bearer token is a 401 (it is `require_operator`-gated)."""
    async with make_admin_client() as client:
        resp = await client.get("/admin/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "admin_unauthorized"
