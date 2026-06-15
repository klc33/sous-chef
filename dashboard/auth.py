"""Operator login + admin-API client for the Streamlit dashboard (008, US2) — JWT from the backend.

Two boundaries, per research R3. The HUMAN boundary is a professional login form: the operator's
username/password is POSTed to the backend's `POST /admin/auth/login`, and the BACKEND (not the dashboard)
checks the credential and mints a per-user JWT carrying `{sub, role, exp}`. The MACHINE boundary is that
JWT, which `admin_client()` attaches as `Authorization: Bearer` to every `/admin/*` call. The dashboard
NEVER sees a password hash and never mints a token — it only stores the JWT it is handed (no more
`OPERATOR_PASSWORD_HASH` / shared `ADMIN_API_TOKEN`).

Refresh-persistence: the JWT is wrapped in a small cookie-layer token signed with the Vault
`DASHBOARD_COOKIE_KEY` and stored in the browser, so a page refresh re-hydrates the session (mirroring
streamlit-authenticator's own cookie pattern). The backend JWT's `exp` (8h) is the AUTHORITATIVE session
clock: the cookie window is set wider (day-grained) so the cookie never lapses before a still-valid token,
and an expired/absent JWT is treated as logged-out regardless of the cookie.
"""

from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime, timedelta

import extra_streamlit_components as stx
import httpx
import jwt
import streamlit as st

# Vault KV v2 read path for the app's secret bundle (mount "secret", path "sous-chef") — the same path the
# backend and seed script use. KV v2 nests the values under data.data.
_VAULT_SECRET_URL = "/v1/secret/data/sous-chef"

# Vault key name for the cookie-signing secret (must match app.config.VAULT_KEY_DASHBOARD_COOKIE_KEY and
# scripts/seed_vault.sh). The dashboard no longer reads any password hash or shared admin token from Vault.
_KEY_COOKIE_KEY = "DASHBOARD_COOKIE_KEY"

# Cookie that carries the operator's signed session across a refresh. Its window is day-grained and set
# wider than the 8h JWT TTL so the cookie never expires before a still-valid token (the token ends the
# session). The cookie-layer JWT is signed with the Vault cookie key, like streamlit-authenticator's own.
_COOKIE_NAME = "souschef_operator"
_COOKIE_EXPIRY_DAYS = 1

# Session-state keys holding the live JWT + the identity decoded from it (the current run's source of truth).
_SS_TOKEN = "operator_token"
_SS_USERNAME = "operator_username"
_SS_ROLE = "operator_role"


@st.cache_resource(show_spinner=False)
def _vault_secrets() -> dict[str, str]:
    """Read the dashboard's secrets from Vault once per process (cached), failing loud if Vault is down.

    Uses the bootstrap VAULT_ADDR/VAULT_TOKEN from the environment (non-secret locators) to GET the KV v2
    path over HTTP. Cached as a resource so the dashboard does not re-hit Vault on every Streamlit rerun. A
    missing var or an unreachable Vault raises, surfaced by the caller as a clear startup error rather than
    a half-authenticated dashboard.
    """
    addr = os.environ["VAULT_ADDR"].rstrip("/")
    token = os.environ["VAULT_TOKEN"]
    resp = httpx.get(f"{addr}{_VAULT_SECRET_URL}", headers={"X-Vault-Token": token}, timeout=5.0)
    resp.raise_for_status()
    data: dict[str, str] = resp.json()["data"]["data"]
    return data


def _backend_base_url() -> str:
    """Return the backend base URL for `/admin/*` calls (non-secret env, compose default)."""
    return os.environ.get("BACKEND_ADMIN_URL", "http://backend:8000").rstrip("/")


def _cookie_manager() -> stx.CookieManager:
    """Build the browser-cookie manager (rebuilt each run, like streamlit-authenticator's own).

    A stable `key` keeps Streamlit from registering a new component id on every rerun. The manager is what
    actually writes/deletes the cookie in the browser; reads go through `st.context.cookies` for reliability.
    """
    return stx.CookieManager(key="souschef_operator_cookies")


def _token_is_live(token: str) -> bool:
    """Return True when the backend JWT is unexpired — the authoritative session clock (8h `exp`).

    Decoded WITHOUT signature verification because the dashboard does not hold the backend signing key (only
    the backend does); we read `exp` to decide whether to even present the token. The backend still verifies
    the signature on every call, so a tampered token is rejected server-side regardless.
    """
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    exp = claims.get("exp")
    return exp is not None and float(exp) > datetime.now(UTC).timestamp()


def _encode_cookie(token: str, username: str, role: str, cookie_key: str) -> str:
    """Sign a small cookie-layer token (carrying the backend JWT + identity) with the Vault cookie key."""
    exp_date = (datetime.now(UTC) + timedelta(days=_COOKIE_EXPIRY_DAYS)).timestamp()
    payload = {"access_token": token, "username": username, "role": role, "exp_date": exp_date}
    return jwt.encode(payload, cookie_key, algorithm="HS256")


def _decode_cookie(raw: str, cookie_key: str) -> dict[str, str] | None:
    """Verify + decode the cookie-layer token; return its payload, or None if invalid/expired.

    Verifies the cookie signature against the Vault cookie key (a tampered cookie is rejected) and checks the
    cookie's own `exp_date`. The inner backend JWT's freshness is re-checked separately by `_token_is_live`,
    so a stale token is never honored even if the wider cookie window has not lapsed.
    """
    try:
        data = jwt.decode(raw, cookie_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if float(data.get("exp_date", 0)) < datetime.now(UTC).timestamp():
        return None
    return data


def _hydrate_from_cookie() -> bool:
    """Re-populate session state from the signed cookie on a fresh run (refresh-persistence); True if logged in.

    Reads the raw cookie via `st.context.cookies`, verifies + decodes it with the Vault cookie key, and only
    accepts it when the wrapped backend JWT is still live (8h `exp`). On success the token + identity land in
    session state so the rest of the run treats the operator as signed in.
    """
    raw = st.context.cookies.get(_COOKIE_NAME)
    if not raw:
        return False
    data = _decode_cookie(raw, _vault_secrets()[_KEY_COOKIE_KEY])
    if data is None or not _token_is_live(data["access_token"]):
        return False
    st.session_state[_SS_TOKEN] = data["access_token"]
    st.session_state[_SS_USERNAME] = data["username"]
    st.session_state[_SS_ROLE] = data["role"]
    return True


def _current_session() -> bool:
    """Return True when a live operator session exists for this run (session state, else the cookie).

    Session state is the current run's source of truth; a still-live token there wins immediately. Otherwise
    we try to re-hydrate from the cookie (the refresh case). A token present but expired is treated as
    logged-out (the backend JWT `exp` is authoritative).
    """
    token = st.session_state.get(_SS_TOKEN)
    if token and _token_is_live(token):
        return True
    return _hydrate_from_cookie()


def _clear_session() -> None:
    """Drop the in-memory session and delete the browser cookie (sign-out / expiry)."""
    for key in (_SS_TOKEN, _SS_USERNAME, _SS_ROLE):
        st.session_state.pop(key, None)
    # No cookie to delete (already absent) is a no-op — a sign-out from a cookieless session is fine.
    with contextlib.suppress(KeyError):
        _cookie_manager().delete(_COOKIE_NAME)


def _render_login() -> None:
    """Render the professional login form and authenticate against the backend; NO sign-up control.

    On submit, POSTs the credentials to `POST /admin/auth/login`. A 200 stores the minted JWT + identity in
    session state, writes the signed refresh cookie, and reruns into the console. Any failure shows the
    backend's single generic message (no enumeration). There is deliberately no account-creation path here —
    accounts are provisioned only by an admin on the Users page.
    """
    st.markdown("## 🍳 SousChef — Operator Console")
    st.caption("Sign in with your operator account to continue.")
    with st.form("operator_login"):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", type="primary")

    if not submitted:
        return
    try:
        resp = httpx.post(
            f"{_backend_base_url()}/admin/auth/login",
            json={"username": username, "password": password},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        st.error(f"Could not reach the backend: {exc}")
        return
    if resp.status_code != 200:
        # The backend returns one generic body for wrong password / unknown user / disabled account.
        st.error("Invalid username or password.")
        return

    body = resp.json()
    st.session_state[_SS_TOKEN] = body["access_token"]
    st.session_state[_SS_USERNAME] = body["username"]
    st.session_state[_SS_ROLE] = body["role"]
    cookie_key = _vault_secrets()[_KEY_COOKIE_KEY]
    _cookie_manager().set(
        _COOKIE_NAME,
        _encode_cookie(body["access_token"], body["username"], body["role"], cookie_key),
        expires_at=datetime.now() + timedelta(days=_COOKIE_EXPIRY_DAYS),
    )
    st.rerun()


def require_login() -> str:
    """Render the login gate and HALT the page unless an operator is signed in; return the username.

    Every page calls this first. If a live session exists (session state or the refresh cookie) it renders a
    sidebar identity + sign-out and returns the operator's username; otherwise it shows the login form and
    `st.stop()`s so no page body runs unauthorized.
    """
    if not _current_session():
        _render_login()
        st.stop()

    username = st.session_state[_SS_USERNAME]
    role = st.session_state[_SS_ROLE]
    with st.sidebar:
        st.caption(f"Signed in as **{username}** · _{role}_")
        if st.button("Log out"):
            _clear_session()
            st.rerun()
    return username


def require_admin() -> str:
    """Gate an admin-only page: require login, then require the `admin` role; HALT non-admins. Return username.

    The role is read from the verified token's session state, but it is the BACKEND that truly enforces the
    boundary (`/admin/users/*` is `require_admin`-gated), so a non-admin who deep-links here is both fenced
    out of the UI and would be 403'd by the API regardless.
    """
    username = require_login()
    if st.session_state.get(_SS_ROLE) != "admin":
        st.error("You need an admin account to manage operator users.")
        st.stop()
    return username


def admin_client() -> httpx.Client:
    """Return an httpx client pointed at the backend admin API with the operator's JWT attached.

    Reads the live token from session state (the page has passed `require_login`, so it is present) and bakes
    `Authorization: Bearer <jwt>` into the client so every `/admin/*` call is authorized as this operator. A
    generous timeout covers the on-demand eval run (the deterministic gate set, run synchronously).
    """
    token = st.session_state.get(_SS_TOKEN, "")
    return httpx.Client(
        base_url=_backend_base_url(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
