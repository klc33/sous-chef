# Contract: `LLMClient` Seam (`app/infra/llm/`)

The single internal contract every chat/agent generation flows through. Defined as a `typing.Protocol`
so adapters need no inheritance and the contract test is a pure structural + shape check.

## Protocol (`app/infra/llm/base.py`)

```python
from typing import Any, Protocol

class LLMClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> Any:
        """Call hosted chat completion (optional native tool-calling); return the raw response object."""
        ...
```

## Facade (`app/infra/llm/__init__.py`)

```python
def chat(messages, *, tools=None, max_tokens=None, model=None) -> Any
def get_client() -> LLMClient        # re-exported from factory for tests/introspection
```

- `chat(...)` resolves the active client via `get_client()` and delegates. This is the **only** symbol
  callers and tests import: `from app.infra import llm; llm.chat(...)`.
- After a successful call, the facade attaches best-effort span attributes (`llm.provider`, `llm.model`,
  `llm.total_tokens`) to the current OpenTelemetry span, suppressing any tracing error.

## Factory (`app/infra/llm/factory.py`)

```python
def get_client() -> LLMClient:
    provider = get_settings().llm_provider   # Literal["groq","openai"]
    # groq -> GroqClient(); openai -> OpenAIClient(); cached per process
```

- Selection is by `settings.llm_provider`. An invalid value cannot reach here (rejected at settings load).

## Request contract

| Param | Type | Required | Meaning |
|---|---|---|---|
| `messages` | `list[dict]` | yes | OpenAI-style chat messages (`role`, `content`, and for tool turns `tool_calls`/`tool_call_id`). |
| `tools` | `list[dict] \| None` | no | Tool/function specs; when present the adapter sets `tool_choice="auto"`. |
| `max_tokens` | `int \| None` | no | Per-call output cap (the agent's per-call budget). |
| `model` | `str \| None` | no | Model override; defaults to the provider's workflow model (`groq_model` / `openai_model`). The agent passes the provider's agent model. |

## Response contract (normalized — identical across providers)

| Path | Type | Meaning |
|---|---|---|
| `.choices[0].message.content` | `str \| None` | Assistant text. |
| `.choices[0].message.tool_calls` | `list \| None` | Tool calls, or `None`/empty when the model answered directly. |
| `.choices[0].message.tool_calls[i].id` | `str` | Tool-call id (echoed back on the `tool` result message). |
| `.choices[0].message.tool_calls[i].function.name` | `str` | Tool name. |
| `.choices[0].message.tool_calls[i].function.arguments` | `str` (JSON) | Tool arguments as a JSON string. |
| `.usage.total_tokens` | `int` | Cumulative tokens (agent budget + span attribution); treat missing as 0. |

## Error contract

- Selected provider's Vault secret missing → `StartupConfigError` (clear, fail-fast) on first call.
- Transient rate-limit (429 / provider rate-limit error) → bounded retry/backoff inside the adapter, then
  raise; callers (`agent/loop.py`) already log-and-degrade gracefully on a raised error.
- Unknown `LLM_PROVIDER` → raised at settings load, before any call.

## Test obligations

1. **Structural**: `isinstance`-style/`hasattr` check that `GroqClient` and `OpenAIClient` both satisfy
   `LLMClient` (callable `chat` with the right keyword params).
2. **Shape parity**: with a mocked SDK transport returning one tool call, both adapters expose it at the
   exact paths in the response contract above. **No network.**
3. **Fake client**: a `FakeLLMClient` fixture (in `conftest.py`) returns a canned response object matching
   the response contract, used by unit/integration tests via monkeypatching `llm.chat`.
4. **Safety unchanged**: the red-team and wall-regression suites pass under **both** providers (the latter
   by monkeypatching `llm.chat`, provider-agnostic by construction).
