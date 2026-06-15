"""Integration tests for User Story 2 — the admin cook-management API (`/admin/cooks/*`).

Drives the full provisioning surface end-to-end against a live Postgres with a directly minted operator
admin JWT (008's `require_admin`): create (201), duplicate username (409), weak password (422), list,
deactivate/reactivate (200), reset-password (200), and the 404s for an unknown target. The role boundary is
asserted too: a valid non-admin operator (`user` role) token is authenticated but gets a 403 on every
`/admin/cooks/*` route — the wall is server-side, not UI-only (FR-003).

The actor here is an 008 *operator* admin (cook accounts have no role of their own); the JWT is minted with
the operator signing key. The Vault / settings adapters are faked on `app.state` (mirroring the operator
account tests) so the test needs no real Vault or Redis; the DB session is the isolated test session the
integration conftest provides. Error bodies use the project envelope `{"error": {"code", "message"}}`.
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
from app.repo import cook_accounts as cook_repo
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

# The HS256 signing key the fake Vault hands back; the backend verifies every operator JWT against it (008).
_SIGNING_KEY = "test-jwt-signing-key-padded-to-min-32-bytes"  # noqa: S105 — a test fixture value


class _FakeVault:
    """Stand-in Vault adapter: returns the operator JWT signing key, raising on anything else."""

    def get(self, key: str) -> str:
        """Return the fake signing key (the only secret `/admin/cooks/*` auth reads)."""
        if key == VAULT_KEY_JWT_SIGNING_KEY:
            return _SIGNING_KEY
        raise KeyError(key)


@pytest.fixture
def make_admin_client(db_session: Session) -> Callable[..., AsyncClient]:
    """Return a factory for an ASGI client over an app wired with the admin routers + a fake Vault.

    Registers exactly the error handlers + admin routers (the same registration the real factory uses) with
    `get_db` overridden to the isolated test session and a fake Vault on app.state, so the cook endpoints run
    their real logic — service rules and the operator JWT guard — without a live Vault.
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


def _seed_operator(session: Session, *, username: str, role: str) -> None:
    """Insert one active operator account directly so a minted token's `sub` resolves to a live row.

    Flushed (not committed) — the seed is visible to a request running in the same session. Note: a request
    that ends in an `AppError` makes `get_db` roll back, which discards a still-pending seed, so any test that
    asserts a *failing* request must seed fresh and issue a single request (see the role-boundary test).
    """
    account = OperatorAccount(
        username=username,
        role=role,
        password_hash=security.hash_password("operator-password"),
    )
    account.is_active = True
    session.add(account)
    session.flush()


def _bearer(username: str, role: str) -> dict[str, str]:
    """Mint an operator JWT for a (username, role) and wrap it as an Authorization header (no login)."""
    # A lightweight stand-in carrying just the claims `issue_token` reads — `sub` and `role`.
    account = OperatorAccount(username=username, role=role, password_hash="x")
    token = security.issue_token(account, signing_key=_SIGNING_KEY, ttl_minutes=480)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(db_session: Session) -> dict[str, str]:
    """Seed an active operator admin (`boss`) and return its Bearer header — the actor for `/admin/cooks/*`.

    `require_operator` re-loads the token's `sub` from the SAME session and requires it active, so the
    operator account must really exist here.
    """
    _seed_operator(db_session, username="boss", role="admin")
    return _bearer("boss", "admin")


# ── role boundary ─────────────────────────────────────────────────────────────────────────────────────

# Every write/read route on the cook surface, parametrized so each is checked with its own request.
_COOK_ROUTES = [
    ("post", "/admin/cooks", {"username": "x", "password": "longenough"}),
    ("get", "/admin/cooks", None),
    ("post", "/admin/cooks/someone/deactivate", None),
    ("post", "/admin/cooks/someone/reactivate", None),
    ("post", "/admin/cooks/someone/reset-password", {"password": "longenough"}),
]


@pytest.mark.parametrize(("method", "path", "body"), _COOK_ROUTES)
async def test_cooks_routes_require_admin_role(
    make_admin_client, db_session, method: str, path: str, body: dict | None
) -> None:
    """A valid non-admin operator (`user` role) token is authenticated but 403 on every `/admin/cooks/*`.

    The token resolves to a live, active operator (so it passes `require_operator`), yet `require_admin`
    rejects the non-admin role — proving the boundary is enforced in code, not merely hidden in the UI. Each
    route is its own parametrized case (one request per fresh session): a 403 makes `get_db` roll back, which
    would wipe the still-pending seed and turn a *subsequent* request into a spurious 401, so they don't share.
    """
    _seed_operator(db_session, username="clerk", role="user")
    headers = _bearer("clerk", "user")
    async with make_admin_client() as client:
        resp = await client.request(method, path, headers=headers, json=body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "admin_forbidden"


async def test_cooks_routes_require_token(make_admin_client) -> None:
    """No bearer token → 401 on the cook-management surface (the public widget holds no token)."""
    async with make_admin_client() as client:
        resp = await client.get("/admin/cooks")
    assert resp.status_code == 401


# ── create ────────────────────────────────────────────────────────────────────────────────────────────


async def test_create_cook_returns_201_and_safe_view(make_admin_client, admin_headers) -> None:
    """POST /admin/cooks creates a cook (201) and returns a CookView that omits the hash and the opaque id."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/cooks", headers=admin_headers, json={
            "username": "alice", "password": "alice-password", "display_name": "Alice"})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "alice"
    assert body["display_name"] == "Alice"
    assert body["is_active"] is True
    assert body["created_by"] == "boss"  # the acting operator admin is recorded as creator
    assert "password_hash" not in body and "password" not in body
    assert "id" not in body and "role" not in body  # cook accounts are role-less; id never serialized


async def test_create_duplicate_username_is_409(make_admin_client, admin_headers) -> None:
    """A second create with an existing username is a 409 `cook_exists` (FR-006)."""
    async with make_admin_client() as client:
        first = await client.post("/admin/cooks", headers=admin_headers, json={
            "username": "dup", "password": "first-password"})
        assert first.status_code == 201, first.text
        resp = await client.post("/admin/cooks", headers=admin_headers, json={
            "username": "dup", "password": "another-password"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "cook_exists"


async def test_create_weak_password_is_422(make_admin_client, admin_headers) -> None:
    """An empty/`<8`-char password is rejected as a 422 `weak_password` (FR-007/FR-010), no cook created."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/cooks", headers=admin_headers, json={
            "username": "weakling", "password": "short"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "weak_password"


# ── list ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_list_cooks_includes_disabled(make_admin_client, admin_headers, db_session) -> None:
    """GET /admin/cooks lists every cook (active and disabled) and never serializes a hash or id."""
    async with make_admin_client() as client:
        created = await client.post("/admin/cooks", headers=admin_headers, json={
            "username": "carol", "password": "carol-password"})
        assert created.status_code == 201, created.text
        off = await client.post("/admin/cooks/carol/deactivate", headers=admin_headers)
        assert off.status_code == 200, off.text
        resp = await client.get("/admin/cooks", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    by_name = {row["username"]: row for row in body}
    assert "carol" in by_name
    assert by_name["carol"]["is_active"] is False
    assert all("password_hash" not in row and "id" not in row for row in body)


# ── deactivate / reactivate ───────────────────────────────────────────────────────────────────────────


async def test_deactivate_then_reactivate(make_admin_client, admin_headers) -> None:
    """Deactivate flips `is_active` to False (200); reactivate flips it back to True (200)."""
    async with make_admin_client() as client:
        created = await client.post("/admin/cooks", headers=admin_headers, json={
            "username": "dave", "password": "dave-password"})
        assert created.status_code == 201, created.text
        off = await client.post("/admin/cooks/dave/deactivate", headers=admin_headers)
        on = await client.post("/admin/cooks/dave/reactivate", headers=admin_headers)

    assert off.status_code == 200 and off.json()["is_active"] is False
    assert on.status_code == 200 and on.json()["is_active"] is True


async def test_deactivate_unknown_is_404(make_admin_client, admin_headers) -> None:
    """Deactivating an unknown username is a 404 `cook_not_found`."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/cooks/ghost/deactivate", headers=admin_headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "cook_not_found"


# ── reset-password ────────────────────────────────────────────────────────────────────────────────────


async def test_reset_password_changes_credential(make_admin_client, admin_headers, db_session) -> None:
    """Reset returns 200 and rotates the hash: the old password no longer verifies, the new one does."""
    async with make_admin_client() as client:
        created = await client.post("/admin/cooks", headers=admin_headers, json={
            "username": "erin", "password": "erin-original"})
        assert created.status_code == 201, created.text
        resp = await client.post("/admin/cooks/erin/reset-password", headers=admin_headers,
                                 json={"password": "erin-rotated-pw"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "erin"
    assert "password_hash" not in body
    refreshed = cook_repo.get_by_username(db_session, "erin")
    assert refreshed is not None
    assert not security.verify_password("erin-original", refreshed.password_hash)
    assert security.verify_password("erin-rotated-pw", refreshed.password_hash)


async def test_reset_password_weak_is_422(make_admin_client, admin_headers) -> None:
    """A weak new password is rejected as a 422 `weak_password` (same rule as creation, FR-010)."""
    async with make_admin_client() as client:
        created = await client.post("/admin/cooks", headers=admin_headers, json={
            "username": "frank", "password": "frank-password"})
        assert created.status_code == 201, created.text
        resp = await client.post("/admin/cooks/frank/reset-password", headers=admin_headers,
                                 json={"password": "short"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "weak_password"


async def test_reset_password_unknown_is_404(make_admin_client, admin_headers) -> None:
    """Resetting an unknown username is a 404 `cook_not_found`."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/cooks/ghost/reset-password", headers=admin_headers,
                                 json={"password": "longenough"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "cook_not_found"
