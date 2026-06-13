"""Operator metrics — classifier quality, the routing split, current gate status, and Phoenix links.

Assembles the `MetricsSummary` the dashboard renders, deriving everything on READ (no metrics table,
data-model): classifier macro-F1 + per-class F1 from the served artifact scored on the held-out eval set;
the workflow-vs-agent split from the router's two Redis counters; the current deterministic gate verdicts
from the committed runner; and Phoenix deep-links from config. Every part degrades gracefully — a missing
artifact or an unreachable Redis yields an honest zeroed section rather than a 500, so the page always loads.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from app.config import Settings
from app.schemas.admin import (
    ClassifierMetrics,
    GateResult,
    MetricsSummary,
    RoutingSplit,
)
from app.services.admin import traces
from app.services.user import router as router_service

# Served classifier artifact + the held-out eval set it is scored on (same inputs as the CI gate).
_ARTIFACT = Path("ml/artifacts/model.joblib")
_TESTSET = Path("evals/classifier/testset.csv")


def _classifier_metrics() -> ClassifierMetrics:
    """Score the served classifier on the held-out testset, returning macro-F1 + per-class F1.

    Loads the exact served `model.joblib` and the `evals/classifier/testset.csv` (stdlib csv — the backend
    image has no pandas), predicts every row, and computes macro-F1 plus per-label F1 the operator can scan
    for a weak class. A missing artifact/testset (e.g. an env that never ran `make train`) is not an error
    here — it returns a zeroed result so the metrics page still renders.
    """
    try:
        import joblib
        from sklearn.metrics import f1_score

        model = joblib.load(_ARTIFACT)
        with _TESTSET.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        truth = [row["label"] for row in rows]
        preds = model.predict([row["text"] for row in rows])
        labels = sorted(set(truth))
        per = f1_score(truth, preds, average=None, labels=labels, zero_division=0)
        per_class = {label: float(score) for label, score in zip(labels, per, strict=False)}
        macro_f1 = float(f1_score(truth, preds, average="macro", zero_division=0))
        return ClassifierMetrics(macro_f1=macro_f1, per_class=per_class)
    except (FileNotFoundError, KeyError):
        # No trained artifact / unexpected testset shape ⇒ render an empty section, never a 500.
        return ClassifierMetrics(macro_f1=0.0, per_class={})


def _routing_split(cache: Any) -> RoutingSplit:
    """Read the two router Redis counters and turn them into a workflow-vs-agent percentage split.

    Reads `routing:workflow` / `routing:agent` (incremented per turn by `router.record_decision`), treats a
    missing key or unreachable Redis as 0, and computes each side's share of the total. With no turns yet
    (or Redis down) the percentages are 0 and `total_turns` is 0 — a clean empty state, not an error.
    """
    workflow = _counter(cache, router_service.ROUTING_COUNTER_WORKFLOW)
    agent = _counter(cache, router_service.ROUTING_COUNTER_AGENT)
    total = workflow + agent
    if total == 0:
        return RoutingSplit(workflow_pct=0.0, agent_pct=0.0, total_turns=0)
    return RoutingSplit(
        workflow_pct=round(100.0 * workflow / total, 1),
        agent_pct=round(100.0 * agent / total, 1),
        total_turns=total,
    )


def _counter(cache: Any, key: str) -> int:
    """Return one Redis counter as an int, treating a missing key / cache / Redis error as 0 (best-effort)."""
    if cache is None:
        return 0
    try:
        value = cache.client.get(key)
    except Exception:  # noqa: BLE001 — metrics read must never fail the endpoint
        return 0
    return int(value) if value is not None else 0


def _gate_status() -> list[GateResult]:
    """Return the current DETERMINISTIC gate verdicts (classifier, red-team, redaction) for the status panel.

    Runs only the three pure/offline gates — fast and stack-free — rather than the full suite, so a metrics
    read does not churn DB sessions for the offline RAG/agent/judge gates. Reuses the committed runner so the
    dashboard's "is the build healthy" panel grades identically to CI. Maps the runner dataclass to the wire
    schema; any unexpected runner error degrades to an empty list so the page still loads.
    """
    try:
        from evals import run_evals

        loaded = run_evals.thresholds()
        rows = [
            run_evals.gate_classifier(loaded),
            run_evals.gate_redteam(loaded),
            run_evals.gate_redaction(loaded),
        ]
        return [GateResult(name=r.name, status=r.status, detail=r.detail) for r in rows]
    except Exception:  # noqa: BLE001 — gate status is informational; never 500 the metrics page
        return []


def summarize(cache: Any, settings: Settings) -> MetricsSummary:
    """Assemble the full operator MetricsSummary from its independently-degrading parts.

    Classifier metrics (from the artifact + testset), the routing split (from Redis), the current gate
    status (from the runner), and Phoenix deep-links (from config) are gathered separately so a failure in
    one section leaves the others intact. The result is the single payload `GET /admin/metrics` returns.
    """
    return MetricsSummary(
        classifier=_classifier_metrics(),
        routing=_routing_split(cache),
        gates=_gate_status(),
        phoenix=traces.phoenix_links(settings),
    )
