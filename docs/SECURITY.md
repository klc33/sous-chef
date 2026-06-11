# Security — 003 Intelligent Behavior

How the intelligent layer stays safe. Two guarantees dominate: **the allergen wall holds on every new
recipe path**, and **manipulation is refused deterministically**. Both are enforced in code (not prompts)
and covered by committed gates. Constitution principles cited as `P#`.

## Threat model

The untrusted input is the cook's free-text chat message (and, indirectly, any text a tool surfaces).
The assets we protect: (1) a cook's declared allergies/diet — surfacing a violating recipe is the worst
outcome; (2) provider secrets (Groq / embeddings / Vault keys); (3) the assistant's own instructions
(no system-prompt leak, no role takeover).

## 1. The allergen wall — deterministic, on every path

The wall is the grade (golden rule #1). Every recipe that can reach a cook is rendered **only** through
[app/services/shared/recipe_view.py](../app/services/shared/recipe_view.py), which requires a
`ConstraintProfile` and calls the deterministic
[app/services/user/constraint_guard.py](../app/services/user/constraint_guard.py). New paths added this
phase, all funneled through that one choke point:

| Path | Where the wall runs |
|---|---|
| RAG search | `rag.retrieve` filters the over-fetched pool via `constraint_guard.filter` (fail-closed) before top-3 |
| Agent tools | each recipe-returning tool wall-clears output via `recipe_view` before returning to the loop |
| Meal plan | every planned day's recipe is wall-cleared; `cuisine IS NULL` never counts toward variety |
| Substitution | curated map entries filtered to drop any substitute that contains/may-contain a declared allergen (fail-closed) |
| Output rail | re-asserts the wall by re-fetching each surfaced recipe by id and re-checking — defense in depth |

**Fail-closed everywhere**: a recipe whose allergens are uncertain, or whose id can't be parsed/resolved
on the output path, is treated as a violation and dropped — uncertainty never favors surfacing.

**Enforced by**: [tests/integration/test_wall_regression.py](../tests/integration/test_wall_regression.py)
enumerates every recipe path (recipe detail, RAG, agent tools, meal plan) and asserts a violating recipe
can never be surfaced. Adding an intelligent path that forgets the wall fails CI (SC-006).

## 2. Input rail — refuse manipulation before routing (P6)

[app/guardrails/input_rails.py](../app/guardrails/input_rails.py) screens the message with deterministic
regex/keyword patterns (no model, so the gate is reproducible) **before** it reaches the router:

- **Allergen/diet-override** ("ignore my peanut allergy", "override my diet", "I don't really have a dairy
  allergy") → **REFUSE the whole turn** with a safe message. The "safe remainder" of such a request is
  inseparable from an unsafe ask, so none of it is served. (The wall makes it structurally impossible past
  retrieval too — this is belt-and-suspenders.)
- **Injection / jailbreak / role-override / prompt-leak** ("ignore previous instructions", "you are now…",
  "reveal your system prompt", "DAN/developer mode") → **strip the offending sentence**. If a meaningful
  safe remainder survives (an injection embedded in a valid cooking request, FR-033), the **cleaned**
  remainder is served; if nothing safe is left, REFUSE.
- Otherwise allow-by-default: an unmatched message passes through unchanged.

A refusal never echoes the probe and never reveals internals. The `/chat` endpoint short-circuits on
`refuse` → `ChatResponse(refused=true, ...)` before any routing/LLM call.

**Enforced by**: [tests/redteam/test_attempts.py](../tests/redteam/test_attempts.py) drives
[evals/redteam/attempts.yaml](../evals/redteam/attempts.yaml) (17 probes across allergen-override,
injection, jailbreak, prompt-leak) at a hard **refusal_rate = 1.0** — a single un-refused probe fails the
build. The embedded-injection-in-a-valid-request case (neutralize + serve remainder) is covered in
[tests/unit/test_guardrails.py](../tests/unit/test_guardrails.py).

## 3. Output rail — redact + re-assert before anything leaves (P5, P6)

[app/guardrails/output_rails.py](../app/guardrails/output_rails.py) is the last gate every turn passes:

1. **Redaction** — runs [app/core/redaction.py](../app/core/redaction.py) over the free-text `reply` (the
   only field that can carry a leaked secret/PII value) so nothing sensitive reaches logs **or** a Phoenix
   span. This runs before the reply leaves **and** before any span is emitted (golden rule #5).
2. **Wall re-assertion** — re-fetches each surfaced recipe by id and re-runs `constraint_guard`, dropping
   any violator (fail-closed on an unparseable/unresolvable id).

**Enforced by**: [tests/unit/test_redaction.py](../tests/unit/test_redaction.py) and the
`redaction.leak_count_max: 0` gate (0 leaks tolerated over a battery of provider/Groq/bearer/Vault-shaped
secrets).

## 4. Injection-safe data access & secret handling

- **DB**: all recipe access goes through [app/repo/recipes.py](../app/repo/recipes.py) with ORM /
  parameterized queries only; the vector search binds `query_vec` and `exclude_ids` as parameters
  (injection-safe, P3, P6).
- **Secrets**: Groq + embeddings keys are read from Vault at runtime; `.env.example` holds only the Vault
  addr/token + service URLs — **no keys in code, `.env`, or any image** (P4). No torch in any image (P3).
- **Agent bounds**: the loop is capped in iterations + tokens and every tool input is Pydantic-validated
  (P6, SC-007), so a manipulated turn can't drive unbounded tool use.
- **Identity**: the cook is a passwordless `X-Profile-ID` header used only for favorites + seen-history;
  the owner/tenant is never taken from the request body.

## Success-criteria coverage

| Criterion | Mechanism | Gate |
|---|---|---|
| SC-003 100% manipulation refused | deterministic input rail | `redteam.refusal_rate_min: 1.0` |
| SC-004 0 allergen-leaking substitutions | curated map, wall-filtered fail-closed | `tests/unit/test_substitution.py` |
| SC-006 0 allergen recipes on any new path | `recipe_view`→`constraint_guard` choke point | `tests/integration/test_wall_regression.py` |
| SC-007 agent stays within bounds | iteration + token caps, validated tool inputs | `app/agent/loop.py` + agent-tool eval |
| P5 no secret/PII in logs or traces | redaction before log + before span | `redaction.leak_count_max: 0` |
