"""Contract test for the provider-agnostic LLM seam (`app/infra/llm/`) — 005-pgadmin-and-openai.

The seam's promise is that swapping `LLM_PROVIDER` between `groq` and `openai` changes nothing the
callers (`services/user/rag.py`, `app/agent/loop.py`) observe: both adapters satisfy the same
`LLMClient` Protocol and return the **same normalized OpenAI-style shape** for a tool call
(`.choices[0].message.tool_calls[i].function.{name,arguments}`, `.usage.total_tokens`). This pins that
promise structurally and by shape, with **no real network** — each adapter's lazily-built SDK client is
monkeypatched to a fake transport, so no Vault read and no HTTP call occur (FR-004, FR-011, SC-004).
"""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from typing import Any

import pytest
from app.infra.llm.base import LLMClient
from app.infra.llm.groq import GroqClient
from app.infra.llm.openai import OpenAIClient

# Each adapter paired with the module whose `_client()` builds its SDK client — monkeypatched to a fake
# transport so the contract runs with no Vault read and no network. The fake's
# `.chat.completions.create(**kwargs)` returns one canned tool-call response, exactly the surface both
# real SDKs expose.
_ADAPTERS = [
    (GroqClient, "app.infra.llm.groq"),
    (OpenAIClient, "app.infra.llm.openai"),
]


def _canned_tool_call_response(**_kwargs: Any) -> Any:
    """A canned chat response carrying exactly one tool call — the normalized OpenAI-style shape.

    Built from `SimpleNamespace` so it matches the attribute access both SDKs (and the agent loop) use:
    `choices[0].message.{content,tool_calls}`, `tool_calls[i].{id, function.{name, arguments}}`, and
    `usage.total_tokens`. Ignores the request kwargs — it stands in for the provider's transport.
    """
    tool_call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="search_recipes", arguments='{"query": "thai dinner"}'),
    )
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(total_tokens=42),
    )


class _FakeTransport:
    """Stands in for a provider SDK client: `.chat.completions.create(**kwargs)` → canned response."""

    def __init__(self) -> None:
        """Wire `.chat.completions.create` to the canned tool-call response (no network)."""
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_canned_tool_call_response))


@pytest.mark.parametrize("client_cls", [GroqClient, OpenAIClient])
def test_adapter_satisfies_llm_client_protocol(client_cls: type) -> None:
    """Both adapters structurally satisfy the `LLMClient` Protocol (runtime isinstance + chat signature).

    The Protocol is `@runtime_checkable`, so isinstance verifies the method is present; we additionally
    assert `chat` exposes the contract's keyword-only params so an adapter can't drift from the seam.
    """
    client = client_cls()
    assert isinstance(client, LLMClient)

    params = inspect.signature(client.chat).parameters
    assert "messages" in params
    for kw in ("tools", "max_tokens", "model"):
        assert params[kw].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("client_cls, module_name", _ADAPTERS)
def test_adapters_expose_tool_call_at_same_normalized_paths(
    client_cls: type, module_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a mocked transport returning one tool call, both adapters expose it at identical paths.

    Monkeypatches the adapter module's `_client()` to the fake transport (so no Vault, no network), calls
    `chat(...)` with a tool spec, and asserts the same normalized response contract for both providers:
    tool name/arguments at `.choices[0].message.tool_calls[0].function.{name,arguments}` and the token
    count at `.usage.total_tokens` (FR-004, FR-011, SC-004).
    """
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "_client", lambda: _FakeTransport())

    response = client_cls().chat(
        [{"role": "user", "content": "thai dinner"}],
        tools=[{"type": "function", "function": {"name": "search_recipes"}}],
    )

    call = response.choices[0].message.tool_calls[0]
    assert call.function.name == "search_recipes"
    assert call.function.arguments == '{"query": "thai dinner"}'
    assert response.usage.total_tokens == 42
    assert response.choices[0].message.content is None
