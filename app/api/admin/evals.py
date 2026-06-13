"""POST /admin/evals/run — run the committed eval gates on demand and return the results (operator-only).

Thin HTTP over `services/admin/evals.run`: invokes the same gate set as `make evals` in-process and returns
the `EvalRunResult` (gate rows + thresholds echo + timestamp). Guarded by `require_operator`. No DB session
is needed — the runner opens its own connection for the offline gates and SKIPs them when no live stack is
reachable, so this endpoint always returns the deterministic gate verdicts. Mirrors contracts/admin.openapi.yaml.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin_deps import OperatorAuth
from app.schemas.admin import EvalRunResult
from app.services.admin import evals as evals_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/evals/run", response_model=EvalRunResult)
def run_evals(_: OperatorAuth) -> EvalRunResult:
    """Run the eval gates and return the structured results, behind the admin-token guard.

    `_: OperatorAuth` enforces the bearer-token check before any gate runs (401/403 on a bad token). The
    run is synchronous (the deterministic gate set is fast); offline gates SKIP cleanly when this host has
    no corpus/provider keys, so the response is always well-formed.
    """
    return evals_service.run()
