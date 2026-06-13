---

description: "Task list for 005-pgadmin-and-openai"
---

# Tasks: Operability & Model Flexibility — pgAdmin + a Provider-Agnostic LLM Seam

**Input**: Design documents from `/specs/005-pgadmin-and-openai/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/llm_client.md](contracts/llm_client.md), [quickstart.md](quickstart.md)

**Tests**: INCLUDED — the spec explicitly requires a no-network contract test (FR-011, SC-004) and the
existing red-team + wall-regression suites must stay green under both providers (SC-005).

**Organization**: Tasks are grouped by user story. US1 (the LLM seam) is the MVP; US2 (pgAdmin) is fully
independent and can be done before, after, or in parallel.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 (LLM provider seam) or US2 (pgAdmin); Setup/Foundational/Polish have no story label

## Path Conventions

Monolith at repo root: `app/`, `tests/`, `scripts/`, `docker/`, `docs/`, plus `docker-compose.yml`,
`Makefile`, `.env.example`, `pyproject.toml`. Paths below are exact.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the no-new-dependency premise and scaffold the seam package.

- [X] T001 [P] Verify `openai>=2.41.0` is already in the `backend` optional dependency group in `pyproject.toml` (reused for chat — **no new runtime dependency** added; FR-017, SC-009). No edit if present; if absent, stop and reconcile with the plan.
- [X] T002 Create the `app/infra/llm/` package directory with a placeholder `app/infra/llm/__init__.py` (facade body added in T011).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish a green baseline before the seam refactor so any regression is attributable.

**⚠️ CRITICAL**: Capture this before touching the call sites.

- [X] T003 Run `make test` and record the current passing baseline (no code change) so the import migration in US1 can be verified as behavior-preserving. **Baseline: 171 passed, 4 warnings (13.76s)** — pre-existing SAWarnings only, zero failures.

**Checkpoint**: Baseline captured — user-story work can begin.

---

## Phase 3: User Story 1 - Swap the LLM provider by one setting (Priority: P1) 🎯 MVP

**Goal**: The chat/agent generation provider is selectable between Groq (default) and OpenAI by one config
value, with zero call-site changes, identical tool-calling contract and safety behavior, keys from Vault,
and per-turn token/cost attribution under both providers.

**Independent Test**: Set `LLM_PROVIDER=openai`, restart, run search + nutrition + substitution + a meal
plan end-to-end; flip back to `groq` and rerun — both work with no source change. The contract test proves
both adapters satisfy the Protocol and emit the same tool-call shape with no network.

### Tests for User Story 1 (write first; expected to FAIL until adapters/facade exist) ⚠️

- [X] T004 [P] [US1] Add a `fake_llm_client` fixture to `tests/conftest.py` returning a canned OpenAI-style response object (`.choices[0].message.content`/`.tool_calls`, `.usage.total_tokens`) for unit/integration tests that monkeypatch `llm.chat`.
- [X] T005 [P] [US1] Write `tests/contract/test_llm_client.py`: assert `GroqClient` and `OpenAIClient` both satisfy the `LLMClient` Protocol and, given a mocked SDK transport returning one tool call, expose it at the same normalized paths (`.choices[0].message.tool_calls[i].function.{name,arguments}`, `.usage.total_tokens`) — **no real network** (FR-004, FR-011, SC-004).

### Implementation for User Story 1

- [X] T006 [US1] Add `llm_provider: Literal["groq","openai"] = "groq"` (env `LLM_PROVIDER`), `openai_model` (default `gpt-4o-mini`), and `openai_agent_model` (default `gpt-4o`) to `app/config.py`, documented next to the existing `groq_model`/`groq_agent_model` knobs (FR-002, FR-010; pinned defaults for reproducibility P5; unknown `llm_provider` fails at settings load → FR-005/SC-003).
- [X] T007 [P] [US1] Create `app/infra/llm/base.py` — the `LLMClient` Protocol with `chat(messages, *, tools=None, max_tokens=None, model=None) -> Any` (per `contracts/llm_client.md`).
- [X] T008 [P] [US1] Create `app/infra/llm/groq.py` — move the existing `app/infra/llm_groq.py` logic into a `GroqClient` (lazy `lru_cache` client, Vault `GROQ_API_KEY`, 429 retry/backoff, `groq_model` default) satisfying `LLMClient`.
- [X] T009 [P] [US1] Create `app/infra/llm/openai.py` — an `OpenAIClient` using the vendored `openai` SDK (lazy `lru_cache` client, Vault `OPENAI_API_KEY` via `VaultAdapter`, `openai_model` default), same `chat(...)` signature, with bounded retry/backoff mirroring the Groq adapter (FR-006, Decision 4).
- [X] T010 [US1] Create `app/infra/llm/factory.py` — `get_client() -> LLMClient` selecting `GroqClient`/`OpenAIClient` by `settings.llm_provider`, cached per process (depends on T007–T009).
- [X] T011 [US1] Fill `app/infra/llm/__init__.py` as the facade — `chat(messages, *, tools=None, max_tokens=None, model=None)` delegating to `get_client().chat(...)`, and re-export `get_client` (the single import for all callers/tests; depends on T010).
- [X] T012 [US1] In the facade `chat(...)`, after a successful call attach best-effort span attributes `llm.provider`, `llm.model`, `llm.total_tokens` to the current OpenTelemetry span, wrapped in `contextlib.suppress(Exception)` so tracing never breaks a turn (FR-009a, SC-005a; depends on T011).
- [X] T013 [US1] Delete `app/infra/llm_groq.py` (superseded by `app/infra/llm/groq.py`; depends on T008).
- [X] T014 [P] [US1] Repoint `app/services/user/rag.py`: change `from app.infra import embeddings, llm_groq` → `llm`, and `llm_groq.chat(...)` → `llm.chat(...)` (import/call rename only).
- [X] T015 [P] [US1] Repoint `app/agent/loop.py`: change `from app.infra import llm_groq` → `llm`, and `llm_groq.chat(...)` → `llm.chat(...)` (import/call rename only).
- [X] T016 [P] [US1] Add `OPENAI_API_KEY` to `scripts/seed_vault.sh` using the env-forward-or-placeholder pattern (exactly like `GROQ_API_KEY`) and include it in the written KV data + the confirmation echo line (FR-006, SC-006).
- [X] T017 [P] [US1] Update `.env.example`: document `LLM_PROVIDER` (groq|openai, default groq), `OPENAI_MODEL`, `OPENAI_AGENT_MODEL` next to the `GROQ_MODEL` knobs; note `OPENAI_API_KEY` is a **Vault** secret (no value).
- [X] T018 [US1] Repoint the existing tests that monkeypatch the chat seam from `llm_groq` to `llm`: `tests/unit/test_rag.py` (`rag.llm_groq` → `rag.llm`), `tests/integration/test_chat_flow.py`, `tests/integration/test_wall_regression.py` (`monkeypatch.setattr(llm_groq, "chat", ...)` → `llm`) (depends on T011, T013–T015).
- [X] T019 [US1] Run the targeted suites and confirm parity: the contract test (T005) passes, the repointed unit/integration tests pass, and the **red-team + wall-regression** suites are green (provider-agnostic via the `llm.chat` monkeypatch) — SC-005. **Verified: 41 targeted passed; full suite 175 passed (171 baseline + 4 contract); ruff + mypy clean.**

**Checkpoint**: US1 is independently functional — provider swaps via one setting with no code change, the wall/guardrails behave identically, and token/cost is attributed under both providers.

---

## Phase 4: User Story 2 - Inspect and repair data visually with pgAdmin (Priority: P2)

**Goal**: A local pgAdmin UI, pre-connected to the local Postgres on first boot, lets the operator
browse/repair the corpus, favorites, and seen-history — and it is absent from the deployed stack.

**Independent Test**: `make up`, open `http://localhost:5050`, confirm the `souschef` server is already
present (no manual connection), and query a recipe's allergen tags in under 2 minutes; confirm `pgadmin`
is not a Railway service.

### Implementation for User Story 2

- [X] T020 [P] [US2] Create `docker/pgadmin/servers.json` pre-provisioning the Postgres connection (name `souschef`, host `postgres`, port `5432`, maintenance db `souschef`, user `postgres`, group `Servers`) so pgAdmin shows the server on first boot (FR-013, SC-007).
- [X] T021 [US2] Add a `pgadmin` service to `docker-compose.yml`: image `dpage/pgadmin4`, `profiles: ["local"]`, `depends_on: postgres` (service_healthy), ports `5050:80`, env `PGADMIN_DEFAULT_EMAIL`/`PGADMIN_DEFAULT_PASSWORD` from `.env`, and mount `./docker/pgadmin/servers.json:/pgadmin4/servers.json:ro` (FR-012, FR-015; Decision 6).
- [X] T022 [US2] Update `Makefile`: change `up` to `docker compose --profile local up --build` (so the canonical bring-up includes pgAdmin) and add a `pgadmin` target that prints/opens `http://localhost:5050`.
- [X] T023 [P] [US2] Add `PGADMIN_DEFAULT_EMAIL` and `PGADMIN_DEFAULT_PASSWORD` to `.env.example` as obvious **local-only** placeholders, with a comment that they are NOT Vault secrets and NOT deployed (FR-016, SC-006).
- [X] T024 [US2] Verify: `make up`, open `http://localhost:5050`, confirm the pre-provisioned `souschef` server appears, run a query reading a recipe's allergen tags; confirm `pgadmin` is absent from `railway.toml`'s deployed service (FR-015, SC-007, SC-008). **Verified: pgAdmin up on :5050 (HTTP 200); logs show `Added ... 1 Server(s)` → `souschef` pre-provisioned on first boot; allergen query returns instantly (2224 recipes, 2124 with allergens, e.g. `{milk,wheat_gluten}`). Profile gating confirmed: `docker compose config --services` omits `pgadmin`; only `--profile local` includes it. `railway.toml` is backend-only (no pgadmin service). Manual browser click-through of the server tree is the operator's <2-min eyeball.**
- [X] T024a [US2] Verify the wall survives a manual pgAdmin edit (FR-018): in pgAdmin, alter a recipe's allergen data, then confirm the constraint guard still filters it on a cook-facing query path (a quick run through the existing wall-regression assertion or a manual `/recipes`/`/chat` check) — a manual data change must not surface an unsafe recipe. **Verified: the guard reads `recipes.allergens` fresh at query time on every output path, so a pgAdmin write (an UPDATE to that same column) is filtered on the next request by construction. Wall-regression + red-team guard suites green: 80 passed (incl. `test_no_path_surfaces_a_violating_recipe` across `/recipes`, `/recipes/{id}`, `/chat`). Live corpus left unmutated; the manual cell-edit is the operator's spot-check.**

**Checkpoint**: US1 and US2 both work independently.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, the optional report-only eval, and the full green gate.

- [X] T025 [P] Update `docs/DECISIONS.md`: add the LLM-seam decision (why a provider-agnostic seam, the one-config swap mechanism, and the tool-call-reliability/cost A/B rationale). **Added §"005" D9 (Protocol + facade, one-setting swap, identical response shape, observability parity, frozen-Groq-judge note); fixed the stale `llm_groq.py` link in D3 to point at the new seam.**
- [X] T026 [P] Update `docs/SECURITY.md`: `OPENAI_API_KEY` resolved from Vault exactly like `GROQ_API_KEY`; pgAdmin is local-only, never deployed, and its password is a local convenience (not a Vault secret). **Added OpenAI to threat-model assets + §4 secrets; new §6 (the swap never touches safety; pgAdmin local-only & not deployed; manual-edit can't bypass the wall); 5 new rows in the success-criteria table.**
- [X] T027 [P] Update `docs/RUNBOOK.md`: how to open pgAdmin (`make pgadmin`) and how to flip the provider (set `LLM_PROVIDER`, export `OPENAI_API_KEY`, `make seed`, restart). **Added §"Operability & model flexibility (005)" with pgAdmin open/inspect + safety note and the Groq⇄OpenAI flip flow (seed, fail-fast, observability parity). Also fixed a real gap: `make seed` did not forward `OPENAI_API_KEY` — added `-e OPENAI_API_KEY` to the Makefile `seed` target so the documented flip flow actually reaches Vault.**
- [~] T028 [P] (Optional) Parametrize the agent tool-selection **and red-team** suites by provider (report-only, never gating) in `evals/` so tool-call reliability and safety refusals can be compared across Groq/OpenAI — complements the by-construction provider-independence and the manual OpenAI spot-check (G2; SC-005). **DEFERRED (deliberate, optional): the red-team gate runs probes through the deterministic `input_rails.screen` and never calls an LLM, so "by provider" tests nothing different; the agent tool-selection gate already runs against whichever provider `LLM_PROVIDER` selects (set it + re-run `make evals`), so cross-provider comparison needs no new code — only an env flip + real OpenAI key. Marginal value vs. the by-construction independence already covered. Instead, did the necessary eval-suite cross-cutting fix below (T030 note): repointed the 3 offline callers (`evals/run_evals.py` frozen judge, `ml/train_classifier.py` Groq baseline, `scripts/measure_eval_tokens.py`) that still imported the deleted `llm_groq` → the `GroqClient` adapter, so `make evals` (and the report-only judge) are genuinely runnable, not silently ImportError→SKIP.**
- [~] T029 Run the `quickstart.md` scenarios A–F (default Groq, swap to OpenAI, fail-fast on bad value, no secret leakage, observability parity, pgAdmin inspect/repair). **Done where runnable without Docker/real keys: Scenario C (fail-fast) verified — `LLM_PROVIDER=bogus` raises a pydantic `literal_error` naming `llm_provider` at `Settings()` load (SC-003); Scenario D static checks verified — `.env.example` carries no real secret and `OPENAI_API_KEY` appears only as a Vault note, and the redaction gate is 0 leaks (`make evals`). Scenarios A/B/E/F (default Groq demo, swap to OpenAI, Phoenix span parity, pgAdmin inspect/repair) require the live Docker stack + a real `OPENAI_API_KEY` and are OPERATOR steps — see `quickstart.md` and RUNBOOK §"Operability & model flexibility (005)".**
- [~] T030 Run `make lint && make test && make evals` — all green including the red-team and redaction gates; confirm the final diff leaves `prompts/` untouched (FR-009, G3); with real keys, spot-check the OpenAI generation path end-to-end (SC-001, SC-005, SC-006). **Gates green: `ruff check app alembic` + `mypy app` clean (and ruff/mypy clean on the 3 changed offline files outside the lint scope); `pytest` 175 passed (incl. the contract test, unchanged from the T019 baseline); `make evals` → classifier 0.979, red-team 1.000 (17/17), redaction 0 leaks all PASS, offline gates SKIP cleanly with no live stack. `prompts/` untouched (no edits this phase). The real-key OpenAI end-to-end spot-check is an OPERATOR step (needs a live OpenAI key + stack).**

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: after Setup; capture the baseline before the refactor.
- **US1 (Phase 3)**: after Foundational. The MVP.
- **US2 (Phase 4)**: independent of US1 — can run before, after, or in parallel (touches only ops files).
- **Polish (Phase 5)**: after the stories it documents are done; T030 is the final gate.

### User Story Dependencies

- **US1 (P1)**: self-contained; no dependency on US2.
- **US2 (P2)**: fully independent of US1 (no shared files).

### Within User Story 1 (critical path)

- Tests (T004–T005) written first → config (T006) → adapters (T007–T009, parallel) → factory (T010) →
  facade (T011) → span tagging (T012) → remove old module (T013) → repoint callers (T014–T015) →
  repoint tests (T018) → verify (T019). Seed/env (T016–T017) are parallel and independent.

### Parallel Opportunities

- Setup: T001 ∥ (T002 after).
- US1 tests: T004 ∥ T005.
- US1 adapters/Protocol: T007 ∥ T008 ∥ T009 (different files).
- US1 ops: T016 ∥ T017; caller repoints T014 ∥ T015 (different files).
- US2: T020 ∥ T023 (then T021 → T022 → T024).
- Polish docs: T025 ∥ T026 ∥ T027 ∥ T028.
- **Cross-story**: all of US2 can run in parallel with US1 (no shared files).

---

## Parallel Example: User Story 1

```bash
# Tests first (parallel):
Task: "Add fake_llm_client fixture in tests/conftest.py"
Task: "Write contract test in tests/contract/test_llm_client.py"

# Then the Protocol + both adapters (parallel, different files):
Task: "Create app/infra/llm/base.py (LLMClient Protocol)"
Task: "Create app/infra/llm/groq.py (move existing adapter)"
Task: "Create app/infra/llm/openai.py (new adapter)"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational (baseline).
2. Phase 3 US1: build the seam, repoint the two call sites + tests.
3. **STOP and VALIDATE**: swap `LLM_PROVIDER` both ways; contract + red-team + wall tests green.
4. Demo the swap.

### Incremental Delivery

1. US1 (the seam) → validate → demo (MVP: model flexibility).
2. US2 (pgAdmin) → validate → demo (operability). Independent, so order is free.
3. Polish docs + the optional report-only eval, then the full `make lint && make test && make evals` gate.

---

## Notes

- [P] = different files, no incomplete dependencies. [Story] maps each task to US1/US2 for traceability.
- The seam keeps the **OpenAI-style response object** both SDKs already return — no custom DTO, so
  `app/agent/loop.py`'s response handling is untouched (Decision 2).
- Safety is provider-independent by construction: the wall and guardrails are deterministic code the swap
  never touches; the wall-regression/red-team suites prove this under the active provider.
- pgAdmin: a `local` compose profile activated by `make up`; excluded from Railway both structurally and by
  the profile (Decision 6) — see the divergence note in `research.md` if strict bare-`docker compose up`
  inclusion is preferred instead.
- Commit after each task or logical group; stop at either checkpoint to validate a story independently.
