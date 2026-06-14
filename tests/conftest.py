"""Pytest fixtures for the foundation test suite.

Builds a hermetic FastAPI app wired with FAKE infra adapters whose ping() results are set per
test, plus an httpx ASGI client over it. This lets the /health contract — including the
no-false-healthy degraded path — be tested without a live stack. The very same health router
runs against the real adapters in the deployed app, so the contract under test is the real one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

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


def build_test_app(
    *, postgres: bool = True, redis: bool | None = True, vault: bool = True
) -> FastAPI:
    """Construct a FastAPI app with the health router and fake adapters on app.state.

    Each dependency's reachability is set independently so a test can drive the healthy path or
    a specific degraded path deterministically. `redis=None` wires NO cache at all (the Redis-optional
    deployment), so /health omits redis rather than reporting it.
    """
    app = FastAPI()
    app.state.settings = FakeSettings()
    app.state.db = FakePinger(postgres)
    app.state.cache = None if redis is None else FakePinger(redis)
    app.state.vault = FakePinger(vault)
    register_error_handlers(app)
    register_health_router(app)
    return app


def make_llm_response(
    content: str | None = None,
    *,
    tool_calls: list[Any] | None = None,
    total_tokens: int = 0,
) -> Any:
    """Build a canned OpenAI-style chat response — the shape both real adapters return (005 seam).

    Mirrors the normalized response contract every caller reads: `.choices[0].message.content`,
    `.choices[0].message.tool_calls`, and `.usage.total_tokens`. Both Groq's and OpenAI's SDKs return
    this same surface, so a single stand-in stands in for either provider behind the `llm` facade. Built
    from `SimpleNamespace` so attribute access matches the SDK objects without importing either SDK.
    """
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


class FakeLLMClient:
    """A provider-agnostic stand-in for the `llm` seam: `chat(...)` returns a canned response.

    Satisfies the `LLMClient` shape (a `chat(messages, *, tools, max_tokens, model)` method) without any
    network or SDK, so unit/integration tests can monkeypatch `llm.chat` (or inject this client) and drive
    the turn pipeline deterministically. The returned response carries the content/tool_calls/total_tokens
    set at construction; `calls` records each invocation's kwargs so a test can assert what was sent.
    """

    def __init__(
        self,
        content: str | None = "A grounded reply about real recipes.",
        *,
        tool_calls: list[Any] | None = None,
        total_tokens: int = 0,
    ) -> None:
        """Capture the canned reply fields every `chat(...)` call will echo back."""
        self._content = content
        self._tool_calls = tool_calls
        self._total_tokens = total_tokens
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> Any:
        """Record the call and return the canned OpenAI-style response (no network, no SDK)."""
        self.calls.append(
            {"messages": messages, "tools": tools, "max_tokens": max_tokens, "model": model}
        )
        return make_llm_response(
            self._content, tool_calls=self._tool_calls, total_tokens=self._total_tokens
        )


@pytest.fixture
def fake_llm_client() -> FakeLLMClient:
    """A `FakeLLMClient` whose `chat(...)` returns a canned OpenAI-style response (no network).

    Shared seam stand-in for any test that monkeypatches `app.infra.llm.chat` or injects a client: it
    avoids each test re-rolling the `choices[0].message` / `usage.total_tokens` shape by hand and keeps
    them provider-agnostic (the response shape is identical for Groq and OpenAI behind the facade).
    """
    return FakeLLMClient()


@pytest.fixture
def make_client() -> Callable[..., AsyncClient]:
    """Return a factory that yields an ASGI client over an app with the given reachability.

    Usage: `async with make_client(redis=False) as client: ...`. Keyword args map to each
    dependency's ping() result.
    """

    def _factory(**reachability: bool | None) -> AsyncClient:
        app = build_test_app(**reachability)
        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    return _factory
