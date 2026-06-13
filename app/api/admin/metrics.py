"""GET /admin/metrics — classifier quality, routing split, gate status, and Phoenix links (operator-only).

Thin HTTP over `services/admin/metrics.summarize`: reads the cache adapter and settings from `app.state`
(the routing counters live in Redis; the Phoenix links come from config) and returns a `MetricsSummary`.
Guarded by `require_operator`. No DB session is taken — the metrics derive from Redis, the served artifact,
the eval testset, and config. Mirrors contracts/admin.openapi.yaml.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.admin_deps import OperatorAuth
from app.schemas.admin import MetricsSummary
from app.services.admin import metrics as metrics_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/metrics", response_model=MetricsSummary)
def get_metrics(_: OperatorAuth, request: Request) -> MetricsSummary:
    """Return the operator metrics summary, behind the admin-token guard.

    `_: OperatorAuth` runs the bearer-token check first (401/403 on a bad token). The cache adapter and
    settings come from `app.state`; each metric section degrades independently in the service, so this
    always returns a well-formed summary even when Redis is empty or the classifier artifact is absent.
    """
    state = request.app.state
    return metrics_service.summarize(state.cache, state.settings)
