"""GET /health — readiness over the critical dependencies (Postgres, Vault, and Redis when configured).

Returns 200 + a per-dependency map only when ALL wired dependencies are reachable; 503 when any is
unreachable — never a false-healthy 200 (SC-002). Redis is OPTIONAL (config.redis_url): when no cache is
wired it is omitted from the map and does not gate health. The adapters are read from app.state, so the
same router serves both the deployed app (real adapters) and tests (fakes). Response matches
contracts/health.openapi.yaml.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# Reachability labels per the OpenAPI contract.
_OK = "ok"
_UNREACHABLE = "unreachable"


def _evaluate(request: Request) -> tuple[int, dict[str, Any]]:
    """Ping each critical dependency and assemble the (status_code, body) for /health.

    Each dependency maps to "ok"/"unreachable"; overall status is "ok" (200) only when every
    dependency is reachable, otherwise "unhealthy" (503). Adapter ping()s never raise — they
    return False on failure — so a down dependency is reported, not turned into a 500.
    """
    state = request.app.state
    deps = {
        "postgres": state.db.ping(),
        "vault": state.vault.ping(),
    }
    # Redis is optional (config.redis_url): only gate on it when a cache is actually wired. With no cache
    # the service runs Redis-less, so /health must not report it unreachable and must not 503 on its absence.
    cache = getattr(state, "cache", None)
    if cache is not None:
        deps["redis"] = cache.ping()
    all_ok = all(deps.values())
    body: dict[str, Any] = {
        "status": "ok" if all_ok else "unhealthy",
        "env": state.settings.env,
        "version": state.settings.version,
        "dependencies": {name: (_OK if ok else _UNREACHABLE) for name, ok in deps.items()},
    }
    return (200 if all_ok else 503), body


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Handle GET /health by evaluating dependency reachability and returning the contract body."""
    status_code, body = _evaluate(request)
    return JSONResponse(status_code=status_code, content=body)


def register_health_router(app: FastAPI) -> None:
    """Attach the /health route to the app (called by the app factory and by tests)."""
    app.include_router(router)
