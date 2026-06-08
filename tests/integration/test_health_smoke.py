"""Smoke test for GET /health (US2 / SC-002).

Healthy path: all critical dependencies reachable → 200 + status "ok" + the contract shape
from contracts/health.openapi.yaml. Degraded path: one dependency unreachable → 503 + that
dependency "unreachable" + status "unhealthy" — the no-false-healthy guarantee.
"""

from __future__ import annotations


async def test_health_all_reachable_returns_200(make_client) -> None:
    """All of Postgres/Redis/Vault reachable → 200 with status ok and the full dependency map."""
    async with make_client(postgres=True, redis=True, vault=True) as client:
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["dependencies"]) == {"postgres", "redis", "vault"}
    assert all(state == "ok" for state in body["dependencies"].values())
    assert "env" in body
    assert "version" in body


async def test_health_dependency_down_returns_503(make_client) -> None:
    """A single unreachable dependency → 503, status unhealthy, never a false-healthy 200."""
    async with make_client(postgres=True, redis=False, vault=True) as client:
        resp = await client.get("/health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["dependencies"]["redis"] == "unreachable"
    assert body["dependencies"]["postgres"] == "ok"
    assert body["dependencies"]["vault"] == "ok"
