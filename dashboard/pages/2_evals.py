"""Operator view: run the eval gates on demand via POST /admin/evals/run and show pass/fail vs threshold.

A button triggers the in-process gate runner on the backend (the same gates `make evals` runs) and renders
the returned rows as a table with a PASS/FAIL/SKIP badge per gate plus the measured-vs-threshold detail.
Thresholds are echoed alongside so the operator sees the committed floors. Never weakens a threshold — this
is a read of the grade, not a control over it (golden rule #6). Auth is re-applied so the page is gated.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Top-level `auth` import (not `dashboard.auth`): Streamlit runs pages with the MAIN script's directory
# (dashboard/) on sys.path, so auth.py resolves as a top-level module; `dashboard.*` would not import.
from auth import admin_client, require_login

# Emoji badges so a PASS/FAIL/SKIP verdict is scannable at a glance in the results table.
_BADGE = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "⏭️ SKIP"}

require_login()

st.title("🧪 Evals")
st.caption(
    "Run the committed gate suite on demand. Deterministic gates (classifier macro-F1, red-team, redaction) "
    "always run; the offline RAG/agent/judge gates SKIP unless this backend has the corpus and provider keys."
)

if st.button("▶ Run evals", type="primary"):
    try:
        with admin_client() as client:
            resp = client.post("/admin/evals/run")
        resp.raise_for_status()
        result = resp.json()
    except Exception as exc:  # noqa: BLE001 — show the operator the failure rather than crashing the page
        st.error(f"Eval run failed: {exc}")
        st.stop()

    st.success(f"Ran at {result['ran_at']}")

    rows = [
        {"Gate": g["name"], "Status": _BADGE.get(g["status"], g["status"]), "Detail": g["detail"]}
        for g in result["gates"]
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # A red gate anywhere means the build is not provable — call it out loudly.
    failed = [g["name"] for g in result["gates"] if g["status"] == "FAIL"]
    if failed:
        st.error(f"FAILED gates: {', '.join(failed)} — fix the cause, never weaken a threshold.")
    else:
        st.info("No gate failed (skipped gates need the live corpus + provider keys).")

    with st.expander("Committed thresholds (eval_thresholds.yaml)"):
        st.json(result["thresholds"])
else:
    st.info("Click **Run evals** to grade the current build against the committed thresholds.")
