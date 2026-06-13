"""On-demand eval runs for the operator dashboard — invoke the committed gate runner in-process.

`run()` here calls `evals.run_evals.collect_results()` (the same gate set `make evals` runs) and returns
the structured `GateResult` rows plus an echo of `eval_thresholds.yaml`, so the dashboard shows measured-
vs-floor side by side. Deterministic gates (classifier macro-F1, red-team, redaction) run anywhere; the
offline RAG/agent/judge gates SKIP cleanly when this host lacks the corpus/provider keys (research R4).
Reusing the runner means the dashboard and CI grade identically — there is no second, drifting code path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from evals import run_evals

from app.schemas.admin import EvalRunResult, GateResult


def run() -> EvalRunResult:
    """Run the eval gates in-process and return the gate rows, the thresholds echo, and a timestamp.

    Maps each `evals.run_evals.GateResult` dataclass onto the wire `GateResult` schema field-for-field,
    echoes the committed thresholds so the page can render measured-vs-floor, and stamps the completion
    instant in UTC ISO-8601. Synchronous: the deterministic gate set is fast and this is an operator-
    triggered action, so no job queue is warranted (research R4).
    """
    results = run_evals.collect_results()
    gates = [GateResult(name=r.name, status=r.status, detail=r.detail) for r in results]
    return EvalRunResult(
        gates=gates,
        thresholds=run_evals.thresholds(),
        ran_at=datetime.now(UTC).isoformat(),
    )
