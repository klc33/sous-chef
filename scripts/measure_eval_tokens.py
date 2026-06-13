"""One-off: measure real Groq token usage (input/output) for the OFFLINE eval gates.

Wraps `GroqClient.chat` to accumulate the provider-reported `usage` (prompt/completion tokens) from every
LLM call the eval suite makes — the RAG reply and the agent tool-selection loop (both via the `llm` facade,
005 seam) and the frozen judge (which uses the Groq adapter directly). Under the default `groq` provider all
three routes ultimately call `GroqClient.chat`, so patching that one method captures them all. Read-only
measurement; it changes no thresholds and ships nothing. Run from the host against the live stack (localhost
ports) with `LLM_PROVIDER` unset/`groq`.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.infra.llm.groq import GroqClient
from evals import run_evals

_orig_chat = GroqClient.chat
_per_model: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0})


def _wrapped(self: GroqClient, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
    """Call the real Groq chat, then tally prompt/completion tokens from the response usage by model."""
    resp = _orig_chat(self, messages, **kwargs)
    usage = getattr(resp, "usage", None)
    model = kwargs.get("model") or "default(groq_model)"
    if usage is not None:
        row = _per_model[model]
        row["calls"] += 1
        row["prompt"] += int(getattr(usage, "prompt_tokens", 0) or 0)
        row["completion"] += int(getattr(usage, "completion_tokens", 0) or 0)
    return resp


# Patch the class method: the `llm` facade (rag + agent) and the frozen judge both call GroqClient.chat
# under the default groq provider, so this single wrap captures every eval LLM call.
GroqClient.chat = _wrapped  # type: ignore[method-assign]

results = run_evals.collect_results()

print("\n=== Groq token usage for the offline eval gates (one run) ===")
tot_calls = tot_p = tot_c = 0
for model, row in sorted(_per_model.items()):
    print(f"  {model:32s} calls={row['calls']:3d}  in={row['prompt']:7d}  out={row['completion']:7d}")
    tot_calls += row["calls"]
    tot_p += row["prompt"]
    tot_c += row["completion"]
print(f"  {'TOTAL':32s} calls={tot_calls:3d}  in={tot_p:7d}  out={tot_c:7d}  (sum={tot_p + tot_c})")

print("\n=== gate outcomes (sanity) ===")
for r in results:
    print(f"  [{r.status}] {r.name}: {r.detail}")
