"""Pytest fixtures for the foundation test suite.

Builds a hermetic FastAPI app wired with FAKE infra adapters whose ping() results are set per
test, plus an httpx ASGI client over it. This lets the /health contract — including the
no-false-healthy degraded path — be tested without a live stack. The very same health router
runs against the real adapters in the deployed app, so the contract under test is the real one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from app.api.health import register_health_router
from app.core.errors import register_error_handlers
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@dataclass
class FakePinger:
    """Stand-in for an infra adapter: ping() returns a preset boolean, touching nothing real."""

    reachable: bool

    def ping(self) -> bool:
        """Return the preset reachability."""
        return self.reachable


@dataclass
class FakeSettings:
    """Minimal settings stand-in exposing the non-secret fields /health reports."""

    env: str = "test"
    version: str = "0.1.0"


def build_test_app(*, postgres: bool = True, redis: bool = True, vault: bool = True) -> FastAPI:
    """Construct a FastAPI app with the health router and fake adapters on app.state.

    Each dependency's reachability is set independently so a test can drive the healthy path or
    a specific degraded path deterministically.
    """
    app = FastAPI()
    app.state.settings = FakeSettings()
    app.state.db = FakePinger(postgres)
    app.state.cache = FakePinger(redis)
    app.state.vault = FakePinger(vault)
    register_error_handlers(app)
    register_health_router(app)
    return app


@pytest.fixture
def make_client() -> Callable[..., AsyncClient]:
    """Return a factory that yields an ASGI client over an app with the given reachability.

    Usage: `async with make_client(redis=False) as client: ...`. Keyword args map to each
    dependency's ping() result.
    """

    def _factory(**reachability: bool) -> AsyncClient:
        app = build_test_app(**reachability)
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _factory
