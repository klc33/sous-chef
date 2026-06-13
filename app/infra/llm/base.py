"""The `LLMClient` Protocol — the single internal contract every chat/agent generation flows through.

Defined as a `typing.Protocol` (not an ABC) so the SDK-wrapping adapters need no inheritance and the
contract test is a pure structural check — `@runtime_checkable` lets that test assert each adapter
satisfies the shape with an `isinstance`. One method, `chat(...)`, whose signature is already the
seam the existing Groq adapter used, so the call sites change only their import. Both adapters return
the **OpenAI-style response object** the agent already reads (`.choices[0].message.{content,tool_calls}`,
`.usage.total_tokens`) — no custom DTO, so the bounded loop and history serialization are untouched
(Decision 2). The package facade (`app/infra/llm/__init__.py`) is the only symbol callers import.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """A hosted chat-completion provider exposing one normalized `chat(...)` method.

    Implementations (`GroqClient`, `OpenAIClient`) wrap their SDK and return the raw OpenAI-style
    response object. The keyword-only params mirror what the callers pass: optional native `tools`,
    a per-call `max_tokens` output cap (the agent's budget), and a `model` override (defaulting to the
    provider's workflow model; the agent passes the provider's stronger agent model).
    """

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> Any:
        """Call the hosted chat completion (optional native tool-calling); return the raw response."""
        ...
