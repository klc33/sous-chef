"""Streamlit operator console — entry point: auth gate + landing/nav for the three admin views.

`streamlit run dashboard/app.py` lands here. It applies the cookie login gate (research R3) and renders a
landing page; the three operator views (corpus, evals, metrics) live in `dashboard/pages/` and Streamlit's
multipage nav lists them in the sidebar automatically. Each page re-applies the same gate, so a deep-linked
page is never reachable unauthenticated. All `/admin/*` traffic goes through the Vault-token client in
`auth.admin_client()`; this console never touches the database directly (separation of concerns, P III).
"""

from __future__ import annotations

import streamlit as st

# `auth` is a sibling module in this same directory. We import it as a TOP-LEVEL module (not
# `dashboard.auth`) because Streamlit puts the main script's directory (dashboard/) on sys.path — the
# repo root is not, and dashboard/ is not a package — so `import dashboard.*` would fail at runtime.
from auth import require_login

st.set_page_config(page_title="SousChef — Operator Console", page_icon="🍳", layout="wide")


def main() -> None:
    """Gate on operator login, then render the landing page that orients the operator to the three views."""
    require_login()

    st.title("🍳 SousChef — Operator Console")
    st.write(
        "Inspect the running system: browse the ingested corpus, run the eval gates on demand, and read "
        "the classifier / routing / gate metrics with deep-links into Phoenix for per-turn traces and cost."
    )
    st.subheader("Views")
    st.markdown(
        "- **Corpus** — page through the ingested recipes with their provenance and allergen/diet tags.\n"
        "- **Evals** — run the committed gates and compare measured scores to the thresholds.\n"
        "- **Metrics** — classifier macro-F1, the workflow-vs-agent split, gate status, and Phoenix links.\n"
    )
    st.caption("Use the sidebar to switch views. Your login is cookie-backed and survives a refresh.")


if __name__ == "__main__":
    main()
