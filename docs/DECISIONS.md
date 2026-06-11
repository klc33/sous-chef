# Decisions — 003 Intelligent Behavior

Architecture decision records for the intelligent layer (smart retrieval, freshness, planning, the
guarded agent). Each entry states the decision, the alternatives weighed, and why. Constitution
principles are cited as `P#`.

## D1 — Intent routing: a trained classical model, not an LLM router

**Decision.** Route every turn with an offline-trained **TF-IDF (word 1–2 grams) + logistic regression**
classifier, served via `joblib` in [app/classifier/predict.py](../app/classifier/predict.py). An LLM
zero-shot router was kept only as a documented baseline, never served.

**Numbers** (held-out eval set `evals/classifier/testset.csv`, 48 rows; full report in
[app/classifier/model_card.md](../app/classifier/model_card.md)):

| Approach | Macro-F1 | Latency | Cost | Determinism |
|---|---|---|---|---|
| **TF-IDF + LogReg (served)** | **0.979** | < ~50 ms local CPU | $0 | deterministic |
| Groq LLM zero-shot (baseline) | not served | ~hundreds of ms / call | per-token | non-deterministic |

**Why.** The classical model matches or beats the LLM baseline on this fixed 6-label set while being free,
fast, deterministic, and **torch-free** to serve (P3, P10). Confidence-based escalation
(`router_confidence_threshold`, default 0.55) sends only low-confidence turns to the agent, so a
misroute degrades cost/quality — **never safety**, because the wall is downstream of routing on every
path. The artifact is SHA-256-pinned in the model card (P5).

**Alternatives rejected.** (a) LLM router — slower, costs per call, non-deterministic, and would make the
F1 gate meaningless. (b) Rules/keywords only — brittle on paraphrase; the labeled dataset + LogReg
generalize better and give a calibrated confidence for escalation.

## D2 — Vectors live in a `pgvector` column, not a separate vector store

**Decision.** Add `recipes.embedding vector(1536)` + an HNSW cosine index via Alembic
[0003_embeddings.py](../alembic/versions/0003_embeddings.py); search with one parameterized SQL query in
[app/repo/recipes.py](../app/repo/recipes.py) `search_by_vector`.

**Why.** pgvector was already enabled in `0001`. One database, one migration, one query layer
(`repo` is the only DB layer, P3) — no extra service to run, secure, or keep in sync (P1, P10). Category +
diet + seen-history are pushed into the same `WHERE` clause as cheap exact pre-filters.

## D3 — Embeddings from a separate hosted provider; Groq is chat-only

**Decision.** Embeddings (`text-embedding-3-small`, dim 1536) come from an OpenAI-compatible endpoint
behind [app/infra/embeddings.py](../app/infra/embeddings.py); Groq serves chat + tool-calling behind
[app/infra/llm_groq.py](../app/infra/llm_groq.py). Both keys live in Vault (P4).

**Why.** Groq is chat-only — it has no embeddings endpoint. Splitting the adapters keeps each external
dependency mockable in tests (P3, P4) and gives each path its own rate-limit bucket. The two Groq models
are also split: `groq_model` (`llama-3.1-8b-instant`) for the workflow path and `groq_agent_model`
(`llama-3.3-70b-versatile`) for the bounded agent, where reliable multi-tool calling matters.

## D4 — Over-fetch, then let the wall trim (never `LIMIT 3` before the allergen check)

**Decision.** `search_by_vector` returns an over-fetched candidate **pool** (`retrieval_candidate_pool`,
default 20), and [app/services/user/rag.py](../app/services/user/rag.py) applies the deterministic allergen
wall over the pool before slicing the **top 3** the cook sees.

**Why.** A hard `LIMIT 3` in SQL could under-return when compliant recipes rank deeper than violators.
Over-fetching guarantees 3 wall-cleared cards surface whenever they exist (P-safety: "the wall is the
grade"). The RAG gate therefore measures **hit@3** — what the cook actually sees — not hit@pool.

## D5 — Freshness is one global per-cook seen-set, reset on exhaustion

**Decision.** [app/services/user/freshness.py](../app/services/user/freshness.py) excludes a cook's
seen-history from retrieval and clears it when the compliant pool can no longer supply `k` unseen rows.
Favorites are never recorded into nor suppressed by seen-history.

**Why.** A single set per cook (not per-query) makes "same request twice → zero overlap" hold across
phrasings, and the reset-on-exhaustion guarantees discovery never dead-ends (SC-001). Exempting favorites
keeps saved recipes always reachable.

## D6 — Grounding: the LLM explains real rows; substitutions come from a curated map

**Decision.** Cards/plans/steps render only stored rows; the LLM ranks/phrases **only** the retrieved
recipes and never invents (recipe_explainer / agent_system prompts). Substitutions come from the curated
[app/services/shared/substitutions_data.py](../app/services/shared/substitutions_data.py), wall-filtered —
never generated.

**Why.** "Ground everything" (P-safety, SC-005). A no-match returns an honest empty result, not a
fabricated or constraint-relaxed recipe. A curated substitution map is safer and cheaper than another
model (P10).

## D7 — One bounded tool-calling agent for hard/multi-step intents

**Decision.** [app/agent/loop.py](../app/agent/loop.py) runs a single agent with five schema-validated
tools (`search_recipes`, `get_recipe`, `get_nutrition`, `build_shopping_list`, `substitute_ingredient`),
capped by `agent_max_iterations` (default 5) + a token budget, returning the best safe partial on bound.

**Why.** Exactly one agent (P1); every tool input is Pydantic-validated and every recipe output passes
the `recipe_view` wall choke point (P3, P6, SC-007). Bounds make the loop always terminate; a degraded
tool choice never produces an unsafe recipe because the wall is downstream of the tool.

## D8 — Deterministic guardrails before routing and before the reply leaves

**Decision.** [app/guardrails/input_rails.py](../app/guardrails/input_rails.py) screens the untrusted
message with deterministic patterns BEFORE routing (refuse allergen/diet-override outright; strip
injection/jailbreak fragments, serving any safe remainder).
[app/guardrails/output_rails.py](../app/guardrails/output_rails.py) redacts the reply and **re-asserts the
wall** on every recipe before the response leaves and before any Phoenix span.

**Why.** A deterministic rail makes the red-team gate provable and reproducible (P6) — refusal rate is a
hard 1.0 gate, not a model's best guess. The output rail re-asserting the wall is defense in depth: a new
code path that forgets the wall still cannot leak a violating recipe.
