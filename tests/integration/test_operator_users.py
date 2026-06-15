"""Integration tests for User Story 1 — the admin account-management API (`/admin/users/*`).

Drives the full provisioning surface end-to-end against a live Postgres with a directly minted admin JWT
(no login endpoint is needed for US1 — that is US2): create (201), duplicate username (409), weak password
(422), list, deactivate (200) and the last-active-admin guard (409), reactivate (200), reset-password (200),
and the 404s for an unknown target. The role boundary is asserted too: a valid `user`-role token is
authenticated but gets a 403 on every `/admin/users/*` route (the wall is server-side, not UI-only).

The Vault / cache / settings adapters are faked on `app.state` (mirroring the foundation conftest's pattern)
so the test needs no real Vault or Redis; the DB session is the isolated test session the integration
conftest already provides. Error bodies use the project envelope `{"error": {"code", "message"}}`.
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
from app.repo import operator_accounts as repo
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

# The HS256 signing key the fake Vault hands back; the backend verifies every operator JWT against it (008).
_SIGNING_KEY = "test-jwt-signing-key-padded-to-min-32-bytes"  # noqa: S105 — a test fixture value


class _FakeVault:
    """Stand-in Vault adapter: returns the JWT signing key for that key, raising on anything else."""

    def get(self, key: str) -> str:
        """Return the fake signing key (the only secret the admin auth boundary reads)."""
        if key == VAULT_KEY_JWT_SIGNING_KEY:
            return _SIGNING_KEY
        raise KeyError(key)


@pytest.fixture
def make_admin_client(db_session: Session) -> Callable[..., AsyncClient]:
    """Return a factory for an ASGI client over an app wired with the admin routers + a fake Vault.

    Registers exactly the error handlers + admin routers (the same registration the real factory uses)
    with `get_db` overridden to the isolated test session and a fake Vault on app.state, so the account
    endpoints run their real logic — service rules and the JWT guard — without a live Vault.
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
        app.dependency_overrides[get_db] = _override_get_db
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _factory


def _seed_account(session: Session, *, username: str, role: str, password: str = "operator-password") -> None:
    """Insert one active operator account directly so a minted token's `sub` resolves to a live row.

    Flushed (not committed) — the seed is visible to a request running in the same session. Note: a request
    that ends in an `AppError` makes `get_db` roll back, which discards a still-pending seed, so any test
    that asserts a *failing* request must seed fresh and issue a single request (see the role-boundary test).
    """
    account = OperatorAccount(
        username=username,
        role=role,
        password_hash=security.hash_password(password),
    )
    account.is_active = True
    session.add(account)
    session.flush()


def _bearer(username: str, role: str) -> dict[str, str]:
    """Mint a JWT for a (username, role) and wrap it as an Authorization header (no login round-trip)."""
    # A lightweight stand-in carrying just the claims `issue_token` reads — `sub` and `role`.
    account = OperatorAccount(username=username, role=role, password_hash="x")
    token = security.issue_token(account, signing_key=_SIGNING_KEY, ttl_minutes=480)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(db_session: Session) -> dict[str, str]:
    """Seed an active admin (`boss`) and return its Bearer header — the actor for the `/admin/users/*` calls.

    `require_operator` re-loads the token's `sub` from the SAME session and requires it active, so the
    account must really exist here. `boss` is the only active admin, which the last-admin guard test relies on.
    """
    _seed_account(db_session, username="boss", role="admin")
    return _bearer("boss", "admin")


# ── role boundary ───────────────────────────────────────────────────────────────────────────────────


# Every write/read route on the account surface, parametrized so each is checked with its own request.
_ADMIN_ROUTES = [
    ("post", "/admin/users", {"username": "x", "password": "longenough", "role": "user"}),
    ("get", "/admin/users", None),
    ("post", "/admin/users/boss/deactivate", None),
    ("post", "/admin/users/boss/reactivate", None),
    ("post", "/admin/users/boss/reset-password", {"password": "longenough"}),
]


@pytest.mark.parametrize(("method", "path", "body"), _ADMIN_ROUTES)
async def test_users_routes_require_admin_role(
    make_admin_client, db_session, method: str, path: str, body: dict | None
) -> None:
    """A valid `user`-role token is authenticated but 403 on every `/admin/users/*` route (FR-011).

    The token resolves to a live, active account (so it passes `require_operator`), yet `require_admin`
    rejects the non-admin role — proving the boundary is enforced in code, not merely hidden in the UI. Each
    route is its own parametrized case (one request per fresh session): a 403 makes `get_db` roll back, which
    would wipe the still-pending seed and turn a *subsequent* request into a spurious 401, so they don't share.
    """
    _seed_account(db_session, username="clerk", role="user")
    _seed_account(db_session, username="boss", role="admin")  # the target some routes act on
    headers = _bearer("clerk", "user")
    async with make_admin_client() as client:
        resp = await client.request(method, path, headers=headers, json=body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "admin_forbidden"


async def test_users_routes_require_token(make_admin_client) -> None:
    """No bearer token → 401 on the admin account surface (the public widget holds no token)."""
    async with make_admin_client() as client:
        resp = await client.get("/admin/users")
    assert resp.status_code == 401


# ── create ──────────────────────────────────────────────────────────────────────────────────────────


async def test_create_account_returns_201_and_safe_view(make_admin_client, admin_headers) -> None:
    """POST /admin/users creates an account (201) and returns an AccountView that omits the password hash."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/users", headers=admin_headers, json={
            "username": "alice", "password": "alice-password", "role": "user", "display_name": "Alice"})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "alice"
    assert body["display_name"] == "Alice"
    assert body["role"] == "user"
    assert body["is_active"] is True
    assert body["created_by"] == "boss"  # the acting admin is recorded as creator
    assert "password_hash" not in body and "password" not in body


async def test_create_duplicate_username_is_409(make_admin_client, admin_headers, db_session) -> None:
    """A second create with an existing username is a 409 `user_exists` (FR-009)."""
    _seed_account(db_session, username="dup", role="user")
    async with make_admin_client() as client:
        resp = await client.post("/admin/users", headers=admin_headers, json={
            "username": "dup", "password": "another-password", "role": "user"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "user_exists"


async def test_create_weak_password_is_422(make_admin_client, admin_headers) -> None:
    """An empty/`<8`-char password is rejected as a 422 `weak_password` (FR-010), no account created."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/users", headers=admin_headers, json={
            "username": "weakling", "password": "short", "role": "user"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "weak_password"


# ── list ────────────────────────────────────────────────────────────────────────────────────────────


async def test_list_accounts_includes_disabled(make_admin_client, admin_headers, db_session) -> None:
    """GET /admin/users lists every account (active and disabled) and never serializes a hash."""
    _seed_account(db_session, username="carol", role="user")
    repo.set_active(db_session, "carol", is_active=False)
    db_session.flush()
    async with make_admin_client() as client:
        resp = await client.get("/admin/users", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    by_name = {row["username"]: row for row in body}
    assert "boss" in by_name and "carol" in by_name
    assert by_name["carol"]["is_active"] is False
    assert all("password_hash" not in row for row in body)


# ── deactivate / reactivate ──────────────────────────────────────────────────────────────────────────


async def test_deactivate_then_reactivate(make_admin_client, admin_headers, db_session) -> None:
    """Deactivate flips `is_active` to False (200); reactivate flips it back to True (200)."""
    _seed_account(db_session, username="dave", role="user")
    async with make_admin_client() as client:
        off = await client.post("/admin/users/dave/deactivate", headers=admin_headers)
        on = await client.post("/admin/users/dave/reactivate", headers=admin_headers)

    assert off.status_code == 200 and off.json()["is_active"] is False
    assert on.status_code == 200 and on.json()["is_active"] is True


async def test_deactivate_last_admin_is_409(make_admin_client, admin_headers) -> None:
    """Deactivating the only active admin (`boss`) is refused with a 409 `last_admin` (FR-015 / SC-006)."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/users/boss/deactivate", headers=admin_headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "last_admin"


async def test_deactivate_unknown_is_404(make_admin_client, admin_headers) -> None:
    """Deactivating an unknown username is a 404 `user_not_found`."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/users/ghost/deactivate", headers=admin_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_found"


# ── reset-password ───────────────────────────────────────────────────────────────────────────────────


async def test_reset_password_changes_credential(make_admin_client, admin_headers, db_session) -> None:
    """Reset returns 200 and rotates the hash: the old password no longer verifies, the new one does."""
    _seed_account(db_session, username="erin", role="user", password="erin-original")
    async with make_admin_client() as client:
        resp = await client.post("/admin/users/erin/reset-password", headers=admin_headers,
                                 json={"password": "erin-rotated-pw"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "erin"
    assert "password_hash" not in body
    refreshed = repo.get_by_username(db_session, "erin")
    assert refreshed is not None
    assert not security.verify_password("erin-original", refreshed.password_hash)
    assert security.verify_password("erin-rotated-pw", refreshed.password_hash)


async def test_reset_password_weak_is_422(make_admin_client, admin_headers, db_session) -> None:
    """A weak new password is rejected as a 422 `weak_password` (same rule as creation, FR-010)."""
    _seed_account(db_session, username="frank", role="user")
    async with make_admin_client() as client:
        resp = await client.post("/admin/users/frank/reset-password", headers=admin_headers,
                                 json={"password": "short"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "weak_password"


async def test_reset_password_unknown_is_404(make_admin_client, admin_headers) -> None:
    """Resetting an unknown username is a 404 `user_not_found`."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/users/ghost/reset-password", headers=admin_headers,
                                 json={"password": "longenough"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_found"
