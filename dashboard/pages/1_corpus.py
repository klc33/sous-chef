"""Operator view: browse the ingested corpus (read-only) via GET /admin/corpus.

Pages through the backend's corpus projection — title, category, cuisine, provenance, and the allergen/diet
tags the cook card omits — so the operator can audit what ingestion produced. Read-only: the page renders a
table and a pager; it never mutates a recipe. Auth is re-applied here so a deep-link to this page is gated.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Top-level `auth` import (not `dashboard.auth`): Streamlit runs pages with the MAIN script's directory
# (dashboard/) on sys.path, so auth.py resolves as a top-level module; `dashboard.*` would not import.
from auth import admin_client, require_login

# The five fixed categories (plus "all") for the filter; mirrors the backend enum.
_CATEGORIES = ["all", "hot_drink", "cold_drink", "breakfast", "lunch", "dinner"]
_PAGE_SIZE = 50

require_login()

st.title("📚 Corpus")
st.caption("Read-only inspection of the ingested recipes — provenance and allergen/diet tags included.")

# Filters + pager state. The page number lives in session state so the prev/next buttons persist it.
category = st.selectbox("Category", _CATEGORIES, index=0)
if "corpus_page" not in st.session_state:
    st.session_state.corpus_page = 1

params: dict[str, object] = {"page": st.session_state.corpus_page, "page_size": _PAGE_SIZE}
if category != "all":
    params["category"] = category

try:
    with admin_client() as client:
        resp = client.get("/admin/corpus", params=params)
    resp.raise_for_status()
    data = resp.json()
except Exception as exc:  # noqa: BLE001 — surface any backend/transport error to the operator, don't crash
    st.error(f"Could not load corpus: {exc}")
    st.stop()

total = data["total"]
items = data["items"]
last_page = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

st.write(f"**{total}** complete recipes · page **{st.session_state.corpus_page}** of **{last_page}**")

if items:
    frame = pd.DataFrame(items)
    # Order columns for scanning: identity → provenance → safety tags.
    columns = ["title", "category", "cuisine", "source", "source_id", "allergens", "diet_flags"]
    frame = frame[[c for c in columns if c in frame.columns]]
    st.dataframe(frame, width="stretch", hide_index=True)
else:
    st.info("No recipes for this filter.")

# Prev / next pager — clamp at the ends so the page number stays in range.
prev_col, next_col = st.columns(2)
if prev_col.button("← Previous", disabled=st.session_state.corpus_page <= 1):
    st.session_state.corpus_page -= 1
    st.rerun()
if next_col.button("Next →", disabled=st.session_state.corpus_page >= last_page):
    st.session_state.corpus_page += 1
    st.rerun()
