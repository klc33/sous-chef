"""Integration tests for User Story 3 — the operator admin API (corpus / evals / metrics).

Exercises the real admin surface end-to-end against a live Postgres: the Vault-token guard (401 without a
token, 403 with a wrong one, 200 with the right one), the read-only corpus projection, an on-demand eval
run returning the structured gate rows, and the metrics summary shape. The Vault / cache / settings adapters
are faked on `app.state` (mirroring the foundation conftest's fake-pinger pattern) so the test needs no real
Vault or Redis; the DB session is the isolated test session the integration conftest already provides.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from app.api.admin import register_admin_routers
from app.api.deps import get_db
from app.config import VAULT_KEY_ADMIN_API_TOKEN
from app.core.errors import register_error_handlers
from app.models.recipe import Ingredient, Recipe
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session

# The shared admin token the fake Vault hands back; the dashboard would present this on every call.
_ADMIN_TOKEN = "test-admin-token"  # noqa: S105 — a test fixture value, not a real secret
_AUTH = {"Authorization": f"Bearer {_ADMIN_TOKEN}"}


class _FakeVault:
    """Stand-in Vault adapter: returns the seeded admin token for the admin-token key, raising otherwise."""

    def get(self, key: str) -> str:
        """Return the fake admin token for the admin-token key (the only secret the admin API reads)."""
        if key == VAULT_KEY_ADMIN_API_TOKEN:
            return _ADMIN_TOKEN
        raise KeyError(key)


class _FakeRedis:
    """Minimal redis-client stand-in backed by a dict — enough for the routing counters' get()."""

    def __init__(self) -> None:
        """Start with no counters set (the empty-state the metrics endpoint must handle)."""
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Return the stored counter value or None (a never-incremented counter)."""
        return self._store.get(key)


class _FakeCache:
    """Cache adapter stand-in exposing a `.client` like the real `infra.cache.Cache`."""

    def __init__(self) -> None:
        """Hold a single fake redis client for the test."""
        self.client = _FakeRedis()


class _FakeSettings:
    """Settings stand-in exposing only the fields the admin metrics/traces path reads."""

    phoenix_collector_endpoint = "http://phoenix:6006"


@pytest.fixture
def make_admin_client(db_session: Session) -> Callable[..., AsyncClient]:
    """Return a factory for an ASGI client over an app wired with the admin routers + fake adapters.

    The app registers exactly the error handlers + admin routers (the same registration the real factory
    uses) with `get_db` overridden to the isolated test session and fake Vault/cache/settings on app.state,
    so the admin endpoints run their real logic without a live Vault or Redis.
    """

    def _override_get_db() -> Iterator[Session]:
        """Yield the test session, committing on success so SAVEPOINT semantics mirror production."""
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    def _factory() -> AsyncClient:
        app = FastAPI()
        register_error_handlers(app)
        register_admin_routers(app)
        app.state.vault = _FakeVault()
        app.state.cache = _FakeCache()
        app.state.settings = _FakeSettings()
        app.dependency_overrides[get_db] = _override_get_db
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _factory


def _seed_recipe(session: Session) -> None:
    """Insert one complete recipe so the corpus browse has a row to project."""
    session.add(
        Recipe(
            source="themealdb",
            source_id="admin-1",
            title="Inspectable Stew",
            category="dinner",
            cuisine="British",
            servings=2,
            steps=["Simmer.", "Serve."],
            allergens=["milk"],
            allergen_certain=True,
            is_vegetarian=True,
            is_vegan=False,
            is_pescatarian=True,
            is_complete=True,
            ingredients=[Ingredient(position=0, name="carrot", raw_text="1 carrot", allergen_tags=[])],
        )
    )
    session.flush()


# ── auth guard ────────────────────────────────────────────────────────────────────────────────────


async def test_corpus_requires_token(make_admin_client) -> None:
    """GET /admin/corpus is 401 without a bearer token and 403 with the wrong one — no token, no access."""
    async with make_admin_client() as client:
        missing = await client.get("/admin/corpus")
        wrong = await client.get("/admin/corpus", headers={"Authorization": "Bearer nope"})

    assert missing.status_code == 401
    assert wrong.status_code == 403


async def test_metrics_requires_token(make_admin_client) -> None:
    """GET /admin/metrics is 401 without a token — the public widget (no token) cannot reach admin."""
    async with make_admin_client() as client:
        resp = await client.get("/admin/metrics")
    assert resp.status_code == 401


# ── corpus ────────────────────────────────────────────────────────────────────────────────────────


async def test_corpus_returns_projected_page(make_admin_client, db_session) -> None:
    """With a valid token, the corpus browse returns a page projecting provenance + allergen/diet tags."""
    _seed_recipe(db_session)
    async with make_admin_client() as client:
        resp = await client.get("/admin/corpus", headers=_AUTH, params={"category": "dinner"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["page"] == 1
    titles = {item["title"] for item in body["items"]}
    assert "Inspectable Stew" in titles
    card = next(item for item in body["items"] if item["title"] == "Inspectable Stew")
    # Operator-only fields the cook card omits: provenance + allergen union + derived diet flags.
    assert card["source"] == "themealdb"
    assert card["source_id"] == "admin-1"
    assert card["allergens"] == ["milk"]
    assert set(card["diet_flags"]) == {"vegetarian", "pescatarian"}


async def test_corpus_rejects_unknown_category(make_admin_client) -> None:
    """An unknown category filter is a 400 (the five categories are fixed), not a 422 or a silent all-browse."""
    async with make_admin_client() as client:
        resp = await client.get("/admin/corpus", headers=_AUTH, params={"category": "brunch"})
    assert resp.status_code == 400


# ── evals ─────────────────────────────────────────────────────────────────────────────────────────


async def test_evals_run_returns_gate_rows(make_admin_client) -> None:
    """POST /admin/evals/run returns the structured gate rows, a thresholds echo, and a timestamp."""
    async with make_admin_client() as client:
        resp = await client.post("/admin/evals/run", headers=_AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["gates"], list) and body["gates"]
    for gate in body["gates"]:
        assert set(gate) >= {"name", "status", "detail"}
        assert gate["status"] in {"PASS", "FAIL", "SKIP"}
    # The deterministic safety gates always run (never SKIP) regardless of stack.
    names = {g["name"] for g in body["gates"]}
    assert "redteam refusal rate" in names
    assert "redaction leak count" in names
    assert isinstance(body["thresholds"], dict) and body["thresholds"]
    assert isinstance(body["ran_at"], str)


# ── metrics ───────────────────────────────────────────────────────────────────────────────────────


async def test_metrics_returns_summary_shape(make_admin_client) -> None:
    """GET /admin/metrics returns the classifier + routing + gates + phoenix summary, well-formed."""
    async with make_admin_client() as client:
        resp = await client.get("/admin/metrics", headers=_AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["classifier"]["macro_f1"], (int, float))
    routing = body["routing"]
    # No turns have been routed in this fake-cache test → a clean zeroed empty state.
    assert routing["total_turns"] == 0
    assert routing["workflow_pct"] == 0.0
    assert routing["agent_pct"] == 0.0
    assert isinstance(body["gates"], list)
    # Phoenix is deep-link only; the fake settings configure an endpoint so links are present.
    assert body["phoenix"]["ui_base_url"] == "http://phoenix:6006"
    assert body["phoenix"]["trace_deep_link"].endswith("/projects")
