"""Admin-only operator console: provision and manage operator accounts (008, US2).

Surfaces the US1 account API (`/admin/users/*`) in the dashboard: a create-account form, a table of every
account (username / role / status), and per-account deactivate / reactivate / reset-password actions — all
through `admin_client()`, which attaches the operator's JWT. The page is gated by `require_admin()`, so a
non-admin who deep-links here is fenced out (and the backend would 403 the calls regardless — the role wall
is server-side, FR-011). There is deliberately NO sign-up control anywhere: an account is born only here, by
an admin.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Top-level `auth` import (not `dashboard.auth`): Streamlit runs pages with the MAIN script's directory
# (dashboard/) on sys.path, so auth.py resolves as a top-level module; `dashboard.*` would not import.
from auth import admin_client, require_admin

_ROLES = ["user", "admin"]

require_admin()

st.title("👤 Operator Users")
st.caption("Provision and manage operator accounts. Accounts are created only here — there is no self sign-up.")


def _error_detail(resp) -> str:
    """Pull the human message out of the project error envelope `{error: {code, message}}` for display."""
    try:
        return resp.json()["error"]["message"]
    except Exception:  # noqa: BLE001 — fall back to the raw body if it isn't the expected envelope
        return resp.text


# ── create ───────────────────────────────────────────────────────────────────────────────────────────

st.subheader("Create account")
with st.form("create_user", clear_on_submit=True):
    new_username = st.text_input("Username")
    new_display = st.text_input("Display name (optional)")
    new_password = st.text_input("Initial password", type="password", help="At least 8 characters.")
    new_role = st.selectbox("Role", _ROLES, index=0)
    create_submitted = st.form_submit_button("Create account", type="primary")

if create_submitted:
    payload = {
        "username": new_username,
        "password": new_password,
        "role": new_role,
        "display_name": new_display or None,
    }
    try:
        with admin_client() as client:
            resp = client.post("/admin/users", json=payload)
    except Exception as exc:  # noqa: BLE001 — surface any transport error rather than crashing the page
        st.error(f"Could not reach the backend: {exc}")
    else:
        if resp.status_code == 201:
            st.success(f"Created account **{new_username}** ({new_role}).")
        else:
            # 409 duplicate username / 422 weak password map to the envelope's message.
            st.error(_error_detail(resp))

st.divider()

# ── list + per-account actions ───────────────────────────────────────────────────────────────────────

st.subheader("Accounts")
try:
    with admin_client() as client:
        list_resp = client.get("/admin/users")
    list_resp.raise_for_status()
    accounts = list_resp.json()
except Exception as exc:  # noqa: BLE001 — surface any backend/transport error to the admin, don't crash
    st.error(f"Could not load accounts: {exc}")
    st.stop()

if not accounts:
    st.info("No operator accounts yet.")
    st.stop()

# A scannable overview table; the per-row action controls follow below it.
frame = pd.DataFrame(accounts)
columns = ["username", "display_name", "role", "is_active", "created_by", "created_at"]
st.dataframe(frame[[c for c in columns if c in frame.columns]], width="stretch", hide_index=True)


def _post_action(path: str, *, json: dict | None = None) -> None:
    """POST an account action and surface success / the envelope error, then rerun to refresh the table."""
    try:
        with admin_client() as client:
            resp = client.post(path, json=json)
    except Exception as exc:  # noqa: BLE001 — surface transport errors instead of crashing
        st.error(f"Could not reach the backend: {exc}")
        return
    if resp.status_code == 200:
        st.rerun()
    else:
        # e.g. 409 last_admin on deactivating the only admin, 422 weak reset password, 404 unknown.
        st.error(_error_detail(resp))


for account in accounts:
    username = account["username"]
    active = account["is_active"]
    status = "active" if active else "disabled"
    with st.expander(f"{username} · {account['role']} · {status}"):
        toggle_col, reset_col = st.columns([1, 2])

        with toggle_col:
            if active:
                if st.button("Deactivate", key=f"deact_{username}"):
                    _post_action(f"/admin/users/{username}/deactivate")
            else:
                if st.button("Reactivate", key=f"react_{username}"):
                    _post_action(f"/admin/users/{username}/reactivate")

        with reset_col, st.form(f"reset_{username}", clear_on_submit=True):
            reset_pw = st.text_input(
                "New password", type="password", key=f"resetpw_{username}",
                help="At least 8 characters.",
            )
            if st.form_submit_button("Reset password"):
                _post_action(
                    f"/admin/users/{username}/reset-password", json={"password": reset_pw}
                )
