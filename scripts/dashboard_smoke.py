"""Headless smoke test for the Streamlit dashboard pages using streamlit.testing AppTest.

Runs app.py (the login gate) and each page in-process and prints any uncaught exception, so dashboard
runtime errors surface without a browser. The page scripts call `require_login()` first; for the page
tests we pre-patch the cached `auth` module so the gate is a no-op (the auth flow itself is exercised by
app.py separately), letting each page run its real /admin/* calls against the live backend.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("VAULT_ADDR", "http://localhost:8200")
os.environ.setdefault("VAULT_TOKEN", "root")
os.environ.setdefault("BACKEND_ADMIN_URL", "http://localhost:8000")
os.environ.setdefault("OPERATOR_USERNAME", "operator")

# Streamlit puts the main script's dir on sys.path at runtime; mimic that so `from auth import ...` works.
sys.path.insert(0, os.path.abspath("dashboard"))

from streamlit.testing.v1 import AppTest  # noqa: E402


def _report(label: str, at: AppTest) -> None:
    """Print a page's exception (if any) plus a short render summary so we can see it actually produced UI."""
    exc = at.exception
    if exc:
        print(f"\n[{label}] EXCEPTION:")
        for e in exc:
            print("   ", repr(e.value) if hasattr(e, "value") else e)
    else:
        n_err = len(at.error)
        n_df = len(at.dataframe)
        n_metric = len(at.metric)
        print(f"\n[{label}] OK — no exception (errors={n_err}, dataframes={n_df}, metrics={n_metric})")
        for er in at.error:
            print("    st.error:", er.value)


# 1) Login gate (no auth → should render the form and st.stop(), NOT crash).
app = AppTest.from_file("dashboard/app.py")
app.run(timeout=30)
_report("app.py (login gate)", app)

# 2) Pages — bypass the login gate by patching the cached auth module, then run real /admin/* calls.
import auth  # noqa: E402

auth.require_login = lambda *a, **k: "operator"  # type: ignore[assignment]

for page in ("dashboard/pages/1_corpus.py", "dashboard/pages/3_metrics.py", "dashboard/pages/2_evals.py"):
    at = AppTest.from_file(page)
    at.run(timeout=60)
    _report(page, at)
