"""Operator view: classifier metrics, the workflow-vs-agent split, gate status, and Phoenix deep-links.

Reads GET /admin/metrics and renders four panels: the served classifier's macro-F1 + per-class F1, the
routing split derived from the router's Redis counters, the current deterministic gate status, and links
out to Phoenix for per-turn traces and cost (the dashboard deep-links only — cost lives in Phoenix, R5).
Auth is re-applied so the page is gated.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Top-level `auth` import (not `dashboard.auth`): Streamlit runs pages with the MAIN script's directory
# (dashboard/) on sys.path, so auth.py resolves as a top-level module; `dashboard.*` would not import.
from auth import admin_client, require_login

# Same scannable verdict badges as the evals page.
_BADGE = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "⏭️ SKIP"}

require_login()

st.title("📊 Metrics")
st.caption("Classifier quality, the workflow-vs-agent routing split, gate status, and Phoenix deep-links.")

try:
    with admin_client() as client:
        resp = client.get("/admin/metrics")
    resp.raise_for_status()
    metrics = resp.json()
except Exception as exc:  # noqa: BLE001 — surface backend/transport errors instead of crashing
    st.error(f"Could not load metrics: {exc}")
    st.stop()

# ── Classifier ──────────────────────────────────────────────────────────────────────────────────
st.subheader("Intent classifier")
classifier = metrics["classifier"]
st.metric("Macro-F1 (held-out)", f"{classifier['macro_f1']:.3f}")
per_class = classifier.get("per_class") or {}
if per_class:
    frame = pd.DataFrame(
        [{"Intent": label, "F1": round(score, 3)} for label, score in per_class.items()]
    )
    st.dataframe(frame, width="stretch", hide_index=True)
else:
    st.info("No per-class scores (classifier artifact not available on this backend).")

# ── Routing split ───────────────────────────────────────────────────────────────────────────────
st.subheader("Routing split (workflow vs agent)")
routing = metrics["routing"]
left, mid, right = st.columns(3)
left.metric("Workflow", f"{routing['workflow_pct']:.1f}%")
mid.metric("Agent", f"{routing['agent_pct']:.1f}%")
right.metric("Total turns", routing["total_turns"])
if routing["total_turns"] == 0:
    st.caption("No turns routed yet — the split populates as cooks use the assistant.")

# ── Gate status ─────────────────────────────────────────────────────────────────────────────────
st.subheader("Gate status (deterministic)")
gates = metrics.get("gates") or []
if gates:
    rows = [
        {"Gate": g["name"], "Status": _BADGE.get(g["status"], g["status"]), "Detail": g["detail"]}
        for g in gates
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
else:
    st.info("Gate status unavailable.")

# ── Phoenix deep-links ──────────────────────────────────────────────────────────────────────────
st.subheader("Phoenix (traces & cost)")
phoenix = metrics.get("phoenix")
if phoenix and phoenix.get("ui_base_url"):
    st.markdown(f"- [Open Phoenix UI]({phoenix['ui_base_url']})")
    if phoenix.get("trace_deep_link"):
        st.markdown(f"- [View traces & per-turn cost]({phoenix['trace_deep_link']})")
    st.caption("Per-turn token cost is viewed in Phoenix, which owns trace + cost storage.")
else:
    st.info("Tracing is disabled on this deployment (no Phoenix endpoint configured).")
