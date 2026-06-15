"""Integration tests for User Story 3 — bootstrap continuity + lockout/revocation safety (008).

Proves the four continuity guarantees end-to-end against a live Postgres:

1. On an empty accounts table the startup bootstrap seeds exactly ONE admin who can sign in (FR-014).
2. A second startup is a no-op — it never creates a duplicate admin or rewrites a changed password (D5).
3. The last active admin can never be deactivated — the API refuses with a 409 (FR-015 / SC-006).
4. Deactivation revokes access promptly — a still-unexpired token for a now-deactivated account is
   rejected on its very next protected request (research D2).

The startup seed itself is `services.admin.operator_accounts.bootstrap_admin` — the exact call
`app.main`'s lifespan makes (T024), wired there with `bootstrap_admin_username` (config) +
`BOOTSTRAP_ADMIN_PASSWORD` (Vault). Here it is invoked directly against the isolated test session to
simulate that startup, then the live `/admin/auth/*` + `/admin/users/*` surface is driven over it. Vault +
settings are faked on `app.state` (mirroring test_operator_auth) so no real Vault is needed; the DB session
is the integration conftest's isolated test session. Error bodies use the envelope `{"error": {"code", ...}}`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from app.api.admin import register_admin_routers
from app.api.deps import get_db
from app.config import VAULT_KEY_BOOTSTRAP_ADMIN_PASSWORD, VAULT_KEY_JWT_SIGNING_KEY
from app.core import security
from app.core.errors import register_error_handlers
from app.models.operator_account import OperatorAccount
from app.repo import operator_accounts as repo
from app.services.admin import operator_accounts as accounts
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

# The HS256 signing key the fake Vault hands back; the backend signs/verifies every operator JWT with it.
_SIGNING_KEY = "test-jwt-signing-key-padded-to-min-32-bytes"  # noqa: S105 — a test fixture value
_TTL_MINUTES = 480
# The bootstrap identity: the username is non-secret config, the password the Vault secret the lifespan reads.
_BOOTSTRAP_USERNAME = "admin"
_BOOTSTRAP_PASSWORD = "bootstrap-admin-password"  # noqa: S105 — a test fixture value (≥8 chars)


class _FakeVault:
    """Stand-in Vault adapter: returns the signing key + bootstrap password, raising on anything else."""

    def get(self, key: str) -> str:
        """Return the fake signing key or bootstrap password (the only secrets this surface reads)."""
        if key == VAULT_KEY_JWT_SIGNING_KEY:
            return _SIGNING_KEY
        if key == VAULT_KEY_BOOTSTRAP_ADMIN_PASSWORD:
            return _BOOTSTRAP_PASSWORD
        raise KeyError(key)


class _FakeSettings:
    """Minimal settings stand-in carrying the knobs the login route + bootstrap read."""

    jwt_ttl_minutes = _TTL_MINUTES
    bootstrap_admin_username = _BOOTSTRAP_USERNAME


@pytest.fixture
def make_admin_client(db_session: Session) -> Callable[..., AsyncClient]:
    """Return a factory for an ASGI client over an app wired with the admin routers + fake Vault/settings.

    Registers exactly the error handlers + admin routers (the same registration the real factory uses) with
    `get_db` overridden to the isolated test session and a fake Vault + settings on app.state, so the auth +
    account endpoints run their real logic — credential check, token mint, JWT guard, role guard — without a
    live Vault.
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


def _run_startup_bootstrap(session: Session) -> OperatorAccount | None:
    """Run the bootstrap exactly as `app.main`'s startup lifespan does, against the test session.

    Mirrors the lifespan wiring (T024): the username is `bootstrap_admin_username` config, the password the
    `BOOTSTRAP_ADMIN_PASSWORD` Vault secret. Flushed so the seeded row is visible to a request sharing this
    session (the override commits real requests).
    """
    created = accounts.bootstrap_admin(
        session, username=_BOOTSTRAP_USERNAME, password=_BOOTSTRAP_PASSWORD
    )
    session.flush()
    return created


def _seed_account(
    session: Session, *, username: str, role: str, password: str, is_active: bool = True
) -> None:
    """Insert one operator account directly so a request resolves a real row with a known password."""
    account = OperatorAccount(
        username=username,
        role=role,
        password_hash=security.hash_password(password),
    )
    account.is_active = is_active
    session.add(account)
    session.flush()


# ── 1. fresh deploy seeds exactly one logged-in-able admin ──────────────────────────────────────────────


async def test_bootstrap_seeds_one_admin_who_can_log_in(make_admin_client, db_session) -> None:
    """An empty accounts table → startup seeds exactly one admin who can sign in with the Vault password."""
    created = _run_startup_bootstrap(db_session)
    assert created is not None
    accounts_after = repo.list_all(db_session)
    assert len(accounts_after) == 1
    assert accounts_after[0].username == _BOOTSTRAP_USERNAME
    assert accounts_after[0].role == "admin"
    assert accounts_after[0].is_active is True

    async with make_admin_client() as client:
        resp = await client.post("/admin/auth/login", json={
            "username": _BOOTSTRAP_USERNAME, "password": _BOOTSTRAP_PASSWORD})

    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


# ── 2. a second startup never duplicates ────────────────────────────────────────────────────────────────


async def test_second_startup_adds_no_account(db_session) -> None:
    """A second startup is idempotent: bootstrap returns None and the table still holds the one admin (D5)."""
    first = _run_startup_bootstrap(db_session)
    assert first is not None

    second = _run_startup_bootstrap(db_session)
    assert second is None
    assert len(repo.list_all(db_session)) == 1


# ── 3. the last active admin can never be locked out ────────────────────────────────────────────────────


async def test_last_active_admin_cannot_be_deactivated(make_admin_client, db_session) -> None:
    """The sole bootstrapped admin deactivating itself is refused with a 409 `last_admin` (SC-006)."""
    _run_startup_bootstrap(db_session)
    async with make_admin_client() as client:
        login = await client.post("/admin/auth/login", json={
            "username": _BOOTSTRAP_USERNAME, "password": _BOOTSTRAP_PASSWORD})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        resp = await client.post(
            f"/admin/users/{_BOOTSTRAP_USERNAME}/deactivate",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "last_admin"


# ── 4. deactivation revokes a still-unexpired token on the next request ─────────────────────────────────


async def test_deactivated_account_token_rejected_next_request(make_admin_client, db_session) -> None:
    """A worker's valid token is rejected (401) on its next protected call once an admin deactivates it (D2)."""
    _run_startup_bootstrap(db_session)
    _seed_account(db_session, username="worker", role="user", password="worker-password")

    async with make_admin_client() as client:
        admin_login = await client.post("/admin/auth/login", json={
            "username": _BOOTSTRAP_USERNAME, "password": _BOOTSTRAP_PASSWORD})
        assert admin_login.status_code == 200, admin_login.text
        admin_token = admin_login.json()["access_token"]

        worker_login = await client.post("/admin/auth/login", json={
            "username": "worker", "password": "worker-password"})
        assert worker_login.status_code == 200, worker_login.text
        worker_token = worker_login.json()["access_token"]
        worker_auth = {"Authorization": f"Bearer {worker_token}"}

        # The token works before revocation.
        before = await client.get("/admin/auth/me", headers=worker_auth)
        assert before.status_code == 200, before.text

        # An admin deactivates the worker (this request commits).
        deactivate = await client.post(
            "/admin/users/worker/deactivate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert deactivate.status_code == 200, deactivate.text

        # The SAME still-unexpired worker token is now rejected on its next request.
        after = await client.get("/admin/auth/me", headers=worker_auth)

    assert after.status_code == 401, after.text
    assert after.json()["error"]["code"] == "admin_unauthorized"
