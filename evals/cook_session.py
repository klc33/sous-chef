"""Demo/eval-cook session helper (009, FR-020) — authenticate offline tooling as the seeded demo cook.

Total gating means there is NO anonymous path: the eval runner and the stack smoke test must act as a real,
authenticated cook, not a bypass. This is the single place that obtains those credentials and a session, so
both consumers authenticate identically as the seeded demo cook (`demo_cook_username` + the Vault
`DEMO_COOK_PASSWORD` the backend bootstrapped with):

  * `bearer_headers(base_url)` — for the HTTP **smoke test**: log in over `/auth/login` and return the
    `Authorization: Bearer` header to attach to every gated call (`/chat`, `/profile`, …) in place of the
    retired `X-Profile-ID`.
  * `ensure_demo_cook(session)` — for the in-process **eval runner**: guarantee the demo cook exists and
    return its id, the owner key the offline gates thread into `profiles`/`seen_history` (whose FK now
    references `cook_accounts`, so a throwaway owner string no longer satisfies it).

It lives under `evals/` (the runner is its primary consumer) and is imported lazily by the smoke script so a
bare `python scripts/widget_smoke.py` still resolves it (the script puts the repo root on `sys.path`).
"""

from __future__ import annotations

import os

import httpx
from app.config import VAULT_KEY_DEMO_COOK_PASSWORD, get_settings
from app.infra.vault import VaultAdapter
from app.repo import cook_accounts as cook_repo
from app.services.user import cook_auth
from sqlalchemy.orm import Session


def demo_credentials() -> tuple[str, str]:
    """Return the seeded demo cook's `(username, password)` — the same creds the backend bootstrapped with.

    The username is the non-secret `demo_cook_username` setting; the password is the secret `DEMO_COOK_PASSWORD`
    (golden rule #4: secrets live in Vault). An explicit `DEMO_COOK_PASSWORD` in the environment wins (so a
    dev can drive the smoke test without reaching Vault), otherwise it is read from Vault exactly as the app
    does — keeping the login password identical to the one `bootstrap_demo_cook` hashed.
    """
    settings = get_settings()
    password = os.environ.get(VAULT_KEY_DEMO_COOK_PASSWORD)
    if not password:
        vault = VaultAdapter(settings)
        vault.load_secrets()
        password = vault.get(VAULT_KEY_DEMO_COOK_PASSWORD)
    return settings.demo_cook_username, password


def login(base_url: str, username: str, password: str, *, timeout: float = 30.0) -> str:
    """Log in over `POST /auth/login` and return the cook-session bearer token (raises on a non-200).

    A network/HTTP failure propagates to the caller, where the smoke test reports it as a failed contract —
    there is no anonymous fallback, by design (the app is gated).
    """
    resp = httpx.post(
        f"{base_url}/auth/login",
        json={"username": username, "password": password},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def bearer_headers(base_url: str, *, timeout: float = 30.0) -> dict[str, str]:
    """Log in as the demo cook against `base_url` and return its `Authorization: Bearer` header (smoke test)."""
    username, password = demo_credentials()
    token = login(base_url, username, password, timeout=timeout)
    return {"Authorization": f"Bearer {token}"}


def ensure_demo_cook(session: Session) -> str:
    """Ensure the seeded demo cook exists (idempotent) and return its id — the eval runner's owner key.

    On a live stack the app already bootstrapped it at startup, so `bootstrap_demo_cook` is a no-op and we
    just resolve the existing row; on a bare DB it seeds the cook first. The returned id is what the offline
    gates key their `profiles`/`seen_history` rows to, satisfying the new FK to `cook_accounts` (FR-020).
    """
    username, password = demo_credentials()
    cook = cook_auth.bootstrap_demo_cook(session, username=username, password=password)
    if cook is None:
        cook = cook_repo.get_by_username(session, username)
    assert cook is not None, "demo cook must exist after bootstrap"
    return cook.id
