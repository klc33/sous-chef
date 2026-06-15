"""Integration test for User Story 3 — deactivation revokes access promptly (009, FR-009 / SC-006).

Proves the two halves of revocation end to end against a real DB: once an admin deactivates a cook, that
cook can no longer (a) obtain a NEW session — `/auth/login` collapses to the same generic 401 even with the
correct password — and (b) keep using an ALREADY-ISSUED one — a token minted while the cook was active is
rejected on its very next gated `/chat` call, because `require_cook` re-loads the `sub` cook per request and
refuses an inactive account. The token is never persisted server-side, so this re-derivation IS the
revocation mechanism (no session store to purge).

Deactivation is done directly through the repo here (the admin `/admin/cooks/{username}/deactivate` route is
covered by `test_admin_cooks.py`); this test owns the *consequence* of deactivation on the cook surface.
"""

from __future__ import annotations

import pytest
from app.core import security
from app.models.cook_account import CookAccount
from app.repo import cook_accounts as cook_repo
from sqlalchemy.orm import Session

# A real password so the genuine `/auth/login` password path mints the "previously-issued" token.
_USERNAME = "revoked-cook"
_PASSWORD = "weeknight-pasta-7"


@pytest.fixture
def active_cook(db_session: Session) -> CookAccount:
    """Seed one ACTIVE cook with a known password — the account an admin will later deactivate."""
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


async def test_deactivation_denies_new_login_and_revokes_a_live_token(
    make_user_client, db_session, active_cook
) -> None:
    """A deactivated cook is denied a fresh login AND has her already-issued token rejected next request.

    The live token is proven to work (200 on a gated endpoint) BEFORE deactivation, so the post-deactivation
    401 is unambiguously revocation — not a token that was never valid.
    """
    async with make_user_client() as client:
        # Sign in while active → a real, live cook-session token (the one we'll try to keep using).
        login = await client.post("/auth/login", json={"username": _USERNAME, "password": _PASSWORD})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # The token opens the gate while the cook is active (baseline: it really was valid).
        assert (await client.get("/profile", headers=headers)).status_code == 200

        # The admin deactivates the cook (its effect on the cook surface is what this test owns).
        cook_repo.set_active(db_session, _USERNAME, is_active=False)
        db_session.flush()

        # (a) No NEW session: even the correct password yields the same generic 401 (a disabled cook
        #     cannot sign in — `authenticate` returns None for an inactive account).
        relogin = await client.post("/auth/login", json={"username": _USERNAME, "password": _PASSWORD})
        assert relogin.status_code == 401
        assert relogin.json()["error"]["code"] == "auth_invalid_credentials"

        # (b) No CONTINUED use: the previously-issued, still-unexpired token is rejected on the next gated
        #     call — `require_cook` re-loads the cook and refuses it now that `is_active` is false (FR-009).
        revoked = await client.post("/chat", headers=headers, json={"message": "hi"})
        assert revoked.status_code == 401
        assert revoked.json()["error"]["code"] == "cook_unauthorized"
