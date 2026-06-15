"""Admin-only cook console: provision and manage cook accounts (009, US2).

Surfaces the US2 cook API (`/admin/cooks/*`) in the dashboard: a create-cook form, a table of every cook
account (username / status), and per-account deactivate / reactivate / reset-password actions — all through
`admin_client()`, which attaches the operator's JWT. The page is gated by `require_admin()`, so a non-admin
operator who deep-links here is fenced out (and the backend would 403 the calls regardless — the boundary is
server-side, FR-003). There is deliberately NO sign-up control anywhere: a cook account is born only here, by
an operator admin. Cook accounts are role-less, so (unlike the operator Users page) there is no role control.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Top-level `auth` import (not `dashboard.auth`): Streamlit runs pages with the MAIN script's directory
# (dashboard/) on sys.path, so auth.py resolves as a top-level module; `dashboard.*` would not import.
from auth import admin_client, require_admin

require_admin()

st.title("🍳 Cook Accounts")
st.caption("Provision and manage cook accounts. Accounts are created only here — there is no self sign-up.")


def _error_detail(resp) -> str:
    """Pull the human message out of the project error envelope `{error: {code, message}}` for display."""
    try:
        return resp.json()["error"]["message"]
    except Exception:  # noqa: BLE001 — fall back to the raw body if it isn't the expected envelope
        return resp.text


# ── create ────────────────────────────────────────────────────────────────────────────────────────────

st.subheader("Create cook")
with st.form("create_cook", clear_on_submit=True):
    new_username = st.text_input("Username")
    new_display = st.text_input("Display name (optional)")
    new_password = st.text_input("Initial password", type="password", help="At least 8 characters.")
    create_submitted = st.form_submit_button("Create cook", type="primary")

if create_submitted:
    payload = {
        "username": new_username,
        "password": new_password,
        "display_name": new_display or None,
    }
    try:
        with admin_client() as client:
            resp = client.post("/admin/cooks", json=payload)
    except Exception as exc:  # noqa: BLE001 — surface any transport error rather than crashing the page
        st.error(f"Could not reach the backend: {exc}")
    else:
        if resp.status_code == 201:
            st.success(f"Created cook **{new_username}**.")
        else:
            # 409 duplicate username / 422 weak password map to the envelope's message.
            st.error(_error_detail(resp))

st.divider()

# ── list + per-account actions ────────────────────────────────────────────────────────────────────────

st.subheader("Cooks")
try:
    with admin_client() as client:
        list_resp = client.get("/admin/cooks")
    list_resp.raise_for_status()
    cooks = list_resp.json()
except Exception as exc:  # noqa: BLE001 — surface any backend/transport error to the admin, don't crash
    st.error(f"Could not load cooks: {exc}")
    st.stop()

if not cooks:
    st.info("No cook accounts yet.")
    st.stop()

# A scannable overview table; the per-row action controls follow below it.
frame = pd.DataFrame(cooks)
columns = ["username", "display_name", "is_active", "created_by", "created_at"]
st.dataframe(frame[[c for c in columns if c in frame.columns]], width="stretch", hide_index=True)


def _post_action(path: str, *, json: dict | None = None) -> None:
    """POST a cook action and surface success / the envelope error, then rerun to refresh the table."""
    try:
        with admin_client() as client:
            resp = client.post(path, json=json)
    except Exception as exc:  # noqa: BLE001 — surface transport errors instead of crashing
        st.error(f"Could not reach the backend: {exc}")
        return
    if resp.status_code == 200:
        st.rerun()
    else:
        # e.g. 422 weak reset password, 404 unknown.
        st.error(_error_detail(resp))


for cook in cooks:
    username = cook["username"]
    active = cook["is_active"]
    status = "active" if active else "disabled"
    with st.expander(f"{username} · {status}"):
        toggle_col, reset_col = st.columns([1, 2])

        with toggle_col:
            if active:
                if st.button("Deactivate", key=f"deact_{username}"):
                    _post_action(f"/admin/cooks/{username}/deactivate")
            else:
                if st.button("Reactivate", key=f"react_{username}"):
                    _post_action(f"/admin/cooks/{username}/reactivate")

        with reset_col, st.form(f"reset_{username}", clear_on_submit=True):
            reset_pw = st.text_input(
                "New password", type="password", key=f"resetpw_{username}",
                help="At least 8 characters.",
            )
            if st.form_submit_button("Reset password"):
                _post_action(
                    f"/admin/cooks/{username}/reset-password", json={"password": reset_pw}
                )
