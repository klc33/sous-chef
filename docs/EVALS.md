# Evals — Intelligent Behavior + Gated CI (003 / 004)

"Evals are the grade" (golden rule #6). Gate floors live in the single source of truth
[eval_thresholds.yaml](../eval_thresholds.yaml); **never weaken a threshold to pass CI — fix the cause.**

> **004 update:** the committed gates are now **merge-blocking in CI** (no longer a local-only ritual),
> the RAG suite also reports **MRR** (deterministic, gating), and a **frozen Groq judge** adds
> **report-only** faithfulness + answer-relevancy (measured, never gating). See §CI and gates 2 & 6 below.

## How to run

```bash
make test     # unit + integration + redteam (incl. wall regression over the new paths) + redaction
make evals    # every eval gate vs eval_thresholds.yaml (table of PASS/FAIL/SKIP)
```

`make evals` ([evals/run_evals.py](../evals/run_evals.py)) splits gates into two kinds:

- **Deterministic gates** (always run, no network): classifier macro-F1, red-team refusal rate, redaction
  leak count. Reproducible — also covered by pytest, so `make test` and `make evals` agree.
- **Offline gates** (need `make up` + `make ingest` + provider keys): RAG hit@3 and agent tool-selection.
  On an un-provisioned machine they **SKIP** (not fail); they call the real embeddings/Groq providers and
  an embedded corpus. Safety never depends on these scores — the wall holds regardless of ranking.

Exit code is non-zero iff a gate that actually **ran** scored below its floor; skipped gates don't fail
the build.

## The gates

### 1. Classifier macro-F1 — `classifier.f1_min: 0.90`

- **Suite**: [evals/classifier/testset.csv](../evals/classifier/testset.csv) (held-out, 6 labels:
  `find_recipe | plan_meals | nutrition_q | substitution | chitchat | out_of_scope`).
- **Metric**: macro-F1 of the served `ml/artifacts/model.joblib` over the testset.
- **Achieved**: **0.979** → floor pinned at **0.90** (below achieved, above the 0.85 target; robust to
  retrain variance). Full report + artifact SHA-256 in
  [app/classifier/model_card.md](../app/classifier/model_card.md).

### 2. RAG hit@3 + MRR — `rag.hit_at_k_min: 0.80`, `rag.mrr_min: 0.80`, `rag.k: 3` (offline)

- **Suite**: [evals/rag/golden.yaml](../evals/rag/golden.yaml) (labeled query → ideal corpus recipe ids).
- **hit@3** = fraction of queries where ≥1 ideal recipe appears among the **3 cards the cook actually
  sees** (FR-006), measured after the over-fetch pool is wall-trimmed (D4 in
  [DECISIONS.md](DECISIONS.md)). `k` was corrected from the foundation-phase placeholder of 5 to **3** so
  the gate measures what the cook sees.
- **MRR** (004, FR-007) = mean reciprocal rank of the **first** ideal recipe among the 3 surfaced cards
  (0 when none surface). Unlike hit@k it rewards ranking the right recipe *higher*, not just anywhere in
  the top-k. It is a pure function of the ranking → **deterministic**, so it gates alongside hit@k.
- **Achieved**: hit@3 **1.000 (10/10)** and MRR **0.933** against the embedded corpus (2,224 recipes) on
  2026-06-11; the golden ideals were re-curated to real corpus rows. Both floors stay a conservative
  **0.80** (below achieved, robust to hosted-embedding variance on the small query set); never weakened.
- **Run** after `make up` + `make ingest` (needs the embedded corpus + embeddings key).

### 3. Red-team refusal rate — `redteam.refusal_rate_min: 1.0`

- **Suite**: [evals/redteam/attempts.yaml](../evals/redteam/attempts.yaml) (17 probes: allergen-override,
  injection, jailbreak, prompt-leak).
- **Metric**: fraction of probes the deterministic input rail refuses. **Hard 1.0** — one un-refused probe
  fails the build.
- **Driver**: [tests/redteam/test_attempts.py](../tests/redteam/test_attempts.py) (per-probe + aggregate),
  also re-scored in `make evals`. See [SECURITY.md](SECURITY.md) §2.

### 4. Redaction leak count — `redaction.leak_count_max: 0`

- **Metric**: secrets (provider/Groq/bearer/Vault-shaped) fed through `core/redaction.redact` must never
  survive verbatim and must be masked. **0 leaks** tolerated.
- **Driver**: [tests/unit/test_redaction.py](../tests/unit/test_redaction.py), also re-scored in
  `make evals`. See [SECURITY.md](SECURITY.md) §3.

### 5. Agent tool-selection (offline, advisory)

- **Suite**: [evals/agent_tool_selection/cases.yaml](../evals/agent_tool_selection/cases.yaml) (message →
  expected/forbidden tools).
- **Metric**: per-case pass = all expected tools called AND no forbidden tool. Reported as accuracy; **no
  hard threshold** — tool choice degrades quality, never safety (SC-007), so it never fails the build. Run
  it against the live stack to track agent quality over time.

### 6. Faithfulness + answer-relevancy — frozen Groq judge, **report-only** (004, offline)

- **Suite**: the same [evals/rag/golden.yaml](../evals/rag/golden.yaml) queries; scored from the tuple
  (query, retrieved context, generated reply) — no `ragas` dependency, the judge instantiates the Groq
  adapter [app.infra.llm.groq.GroqClient](../app/infra/llm/groq.py) **directly** (not the provider-agnostic
  `llm` facade, 005) with a **pinned judge model id** ([prompts/rag_judge.md](../prompts/rag_judge.md)) so
  its scores stay comparable even when the app runs on OpenAI (see [DECISIONS.md](DECISIONS.md) D9).
- **Faithfulness** = is the reply grounded in the retrieved context (no invention)? **Answer-relevancy** =
  does the reply actually address the query? Both in [0,1].
- **Report-only by design** (FR-007, clarification): the judge is **non-deterministic**, so it must never
  gate a merge. These two rows are **PASS/SKIP only** and **never set the exit code** — they are KEYLESS in
  `eval_thresholds.yaml` on purpose. They *measure* quality so a regression is visible; the deterministic
  gates (1–4) are what actually block merge.
- **Achieved**: both **0.870** on 2026-06-11 against the embedded corpus. SKIP without provider keys.

## Current status

Deterministic gates (any machine, no network):

```
[PASS] classifier macro-F1   0.979 (floor 0.900)
[PASS] redteam refusal rate  1.000 (17/17, floor 1.0)
[PASS] redaction leak count  0 leak(s) (max 0)
```

Offline gates, run against the live stack (`make up` + `make ingest`, 2,224 recipes, 2026-06-11):

```
[PASS] rag hit@3             1.000 (10/10, floor 0.8)
[PASS] rag MRR               0.933 (floor 0.8)
[PASS] agent tool-selection  0.667 (4/6)   # advisory, no hard threshold
[PASS] faithfulness          0.870          # report-only — never gates
[PASS] answer-relevancy      0.870          # report-only — never gates
```

`make test`: **165 passed**. `make lint` (ruff + mypy): clean.

## CI — the gates block merge (004)

CI ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) enforces the gates on every `pull_request`
and on `push: main`, as **two merge-blocking jobs** (plus the existing `ruff` / `mypy` static jobs):

- **`gates`** — hermetic, **no service containers**. `uv sync` (backend + test + ml + evals) → `make train`
  (rebuild `ml/artifacts/model.joblib` fresh, so macro-F1 is real without committing a binary) →
  `python -m evals.run_evals`. Runs the **deterministic** gates (classifier macro-F1, red-team refusal 1.0,
  redaction 0 leaks); the offline RAG hit@3/MRR + agent + the report-only judge rows **SKIP** here (no
  provider keys / no embedded corpus). The merge gate is thus deterministic and reproducible by design.
- **`smoke`** — service-provisioned (Postgres + Redis + Vault). Boots the app, asserts `/health` 200, then
  runs the **full** suite `pytest tests/unit tests/integration tests/redteam`. The integration + red-team
  flows (chat, favorites, wall regression, admin) need the live stack, so they live here; the pure safety
  gates (red-team, redaction) run in **both** jobs — belt and suspenders.

A FAIL in either job blocks merge. To prove the gate bites: add an unrefused probe to
`evals/redteam/attempts.yaml` (or nudge a metric below floor) → `gates` goes red → revert. Never weaken a
threshold to make it pass (golden rule #6 / FR-010).

## Success-criteria → gate map

| Criterion | Gate / test |
|---|---|
| SC-001 same query → 0 overlap | `tests/unit/test_freshness.py` + `test_chat_flow.py` |
| SC-002 plan ≥3 cuisines / 1 scaled deduped list | `tests/unit/test_meal_plan.py` + `test_shopping_list.py` |
| SC-003 100% manipulation refused | `redteam.refusal_rate_min: 1.0` |
| SC-004 0 allergen-leaking substitutions | `tests/unit/test_substitution.py` |
| SC-005 0 invented recipes/steps | grounding (stored rows, verbatim steps, curated map) |
| SC-006 0 allergen recipes on any new path | `tests/integration/test_wall_regression.py` |
| SC-007 agent stays within bounds | `app/agent/loop.py` caps + agent-tool eval |
| SC-008 ranked compliant list, most relevant first | `rag.hit_at_k_min` + Story 1 |
