"""Operator login + admin-API client for the Streamlit dashboard — both boundaries, secrets from Vault.

Two boundaries, exactly as research R3. The HUMAN boundary is `streamlit-authenticator`: a single operator
logs in and the cookie (signed with a Vault key) survives a refresh. The MACHINE boundary is the shared
admin token, also from Vault, which `admin_client()` attaches to every `/admin/*` call. This module is the
dashboard image's only secrets touchpoint — it reads Vault over HTTP (the dashboard image carries httpx, not
hvac, keeping it lean) and never imports the backend `app` package. Nothing secret is committed or imaged.
"""

from __future__ import annotations

import os

import httpx
import streamlit as st
import streamlit_authenticator as stauth

# Vault KV v2 read path for the app's secret bundle (mount "secret", path "sous-chef") — the same path the
# backend and seed script use. KV v2 nests the values under data.data.
_VAULT_SECRET_URL = "/v1/secret/data/sous-chef"

# Vault key names (must match app.config.VAULT_KEY_* and scripts/seed_vault.sh).
_KEY_PASSWORD_HASH = "OPERATOR_PASSWORD_HASH"
_KEY_COOKIE_KEY = "DASHBOARD_COOKIE_KEY"
_KEY_ADMIN_TOKEN = "ADMIN_API_TOKEN"

# Cookie name for the operator session; the signing key comes from Vault so the cookie survives refresh.
_COOKIE_NAME = "souschef_operator"
_COOKIE_EXPIRY_DAYS = 7


@st.cache_resource(show_spinner=False)
def _vault_secrets() -> dict[str, str]:
    """Read the operator secrets from Vault once per process (cached), failing loud if Vault is unreachable.

    Uses the bootstrap VAULT_ADDR/VAULT_TOKEN from the environment (non-secret locators) to GET the KV v2
    path over HTTP. Cached as a resource so the dashboard does not re-hit Vault on every Streamlit rerun.
    A missing var or an unreachable Vault raises, which the caller surfaces as a clear startup error rather
    than booting a half-authenticated dashboard.
    """
    addr = os.environ["VAULT_ADDR"].rstrip("/")
    token = os.environ["VAULT_TOKEN"]
    resp = httpx.get(f"{addr}{_VAULT_SECRET_URL}", headers={"X-Vault-Token": token}, timeout=5.0)
    resp.raise_for_status()
    data: dict[str, str] = resp.json()["data"]["data"]
    return data


def _authenticator() -> stauth.Authenticate:
    """Build the streamlit-authenticator with the single operator credential assembled from Vault + env.

    The username is non-secret (env OPERATOR_USERNAME); the bcrypt password HASH and the cookie-signing key
    come from Vault. `auto_hash=False` is critical: the password is ALREADY a bcrypt hash, so the library
    must not re-hash it.

    NOT cached: streamlit-authenticator's `Authenticate(...)` constructs a cookie manager that issues a
    Streamlit *widget* command, which is illegal inside an `@st.cache_resource` function (it raises
    "widget command in a cached function" and corrupts the cookie token — "Token must be bytes"). The
    cookie manager must also run on every rerun to read/refresh the cookie, so we rebuild each run; only
    the upstream Vault read is cached (`_vault_secrets`), which is the part worth memoizing.
    """
    secrets = _vault_secrets()
    username = os.environ.get("OPERATOR_USERNAME", "operator")
    credentials = {
        "usernames": {
            username: {
                "name": username,
                "email": f"{username}@souschef.local",
                "password": secrets[_KEY_PASSWORD_HASH],
            }
        }
    }
    return stauth.Authenticate(
        credentials,
        _COOKIE_NAME,
        secrets[_KEY_COOKIE_KEY],
        _COOKIE_EXPIRY_DAYS,
        auto_hash=False,
    )


def require_login() -> str:
    """Render the login gate and HALT the page unless the operator is authenticated; return the username.

    Every page calls this first. It renders the login form (the authenticator reads/writes the signed cookie,
    so a refresh stays logged in), then branches on the authentication status stored in session state: a wrong
    password shows an error and stops; an empty form prompts and stops; success renders a sidebar logout and
    returns the operator name so the page can proceed. `st.stop()` guarantees no page body runs unauthorized.
    """
    authenticator = _authenticator()
    authenticator.login(location="main")

    status = st.session_state.get("authentication_status")
    if status is False:
        st.error("Incorrect username or password.")
        st.stop()
    if status is None:
        st.info("Please log in to access the operator console.")
        st.stop()

    name = st.session_state.get("name", "operator")
    with st.sidebar:
        st.caption(f"Signed in as **{name}**")
        authenticator.logout("Log out", location="sidebar")
    return name


def admin_client() -> httpx.Client:
    """Return an httpx client pointed at the backend admin API with the Vault admin token attached.

    Reads the non-secret BACKEND_ADMIN_URL from the environment and the shared admin token from Vault, and
    bakes the `Authorization: Bearer` header into the client so every page's `/admin/*` call is authorized.
    A generous timeout covers the on-demand eval run (the deterministic gate set, run synchronously).
    """
    base_url = os.environ.get("BACKEND_ADMIN_URL", "http://backend:8000").rstrip("/")
    token = _vault_secrets()[_KEY_ADMIN_TOKEN]
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
