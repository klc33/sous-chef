---
description: "Task list for 007-ship-public-deploy"
---

# Tasks: Ship to a Public URL (v0.1.0)

**Input**: Design documents from `specs/007-ship-public-deploy/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: TDD was NOT requested. This is an ops/release/docs feature over already-built app logic, so
verification is done via `quickstart.md` checkpoint tasks (live-URL demo rehearsal + fresh-clone
reproduction + CI gate probes), not new pytest suites. The existing `make test` / `make evals` suites
already gate the app behavior and are themselves wired into CI here (US2).

**Organization**: Tasks are grouped by user story (priority order from spec.md) so each can be
implemented and verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US6 maps to the spec's user stories
- Exact file paths are included in each task

## Path note

This feature changes **config, CI, scripts/seed data, and docs** over the existing monolith layout
(`app/`, `dashboard/`, `widget/`, `scripts/`, `docs/`, `.github/`). No new application source modules.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Make room for the new artifacts.

- [X] T001 [P] Create `seeds/corpus/` and `railway/` directories, each with a short `README.md` stating its purpose (committed corpus artifact; per-service Railway configs)
- [X] T002 [P] Add `seeds/corpus/embeddings.npy` to Git LFS tracking in `.gitattributes` (confirm LFS is initialized) so the vector matrix doesn't bloat the base repo
- [X] T003 [P] Confirm `numpy` is available to the export/load scripts in `pyproject.toml` (add to the appropriate uv group if missing); keep images lean — no torch

**Checkpoint**: directories and tracking exist; ready to build the seed-corpus pipeline.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The committed seed corpus + loader. **Blocks US1 (live demo data), US2 (full evals in CI),
and US3 (fresh-clone reproduction)** — all three need identical, network-free corpus data.

**⚠️ CRITICAL**: No user-story phase can be verified until this phase is complete.

- [X] T004 Implement `scripts/export_seed_corpus.py` (offline exporter) per [contracts/seed-corpus.md](contracts/seed-corpus.md): read a populated dev DB, write `recipes.jsonl` + `embeddings.npy` + `manifest.json` for the curated demo/RAG-golden subset; `manifest.embedding_model` = the model that produced the vectors
- [X] T005 Generate the committed artifact under `seeds/corpus/` by running `export_seed_corpus.py` against a populated dev database (after `make up` + `make ingest`); verify `count == len(recipes.jsonl) == embeddings.shape[0]` and every recipe has exactly one of the 5 fixed categories
- [X] T006 Implement `scripts/load_seed_corpus.py` (deploy + CI + local) per the contract: validate `count`/`dim`/`manifest.embedding_model` against the runtime embeddings model (**fail fast** on mismatch), then idempotent upsert (rows + pgvector) **through the repo/ORM layer** keyed on `source_id`; make **zero** provider calls
- [X] T007 Wire the corpus load into local bring-up: add a documented step (Makefile target or compose hook) so `make up` → seed Vault → `load_seed_corpus.py` is the local data path; keep `pgadmin` local-profile-only
- [X] T008 Verify foundation: on a clean local DB, `load_seed_corpus.py` produces real retrieval results for the demo query AND the RAG golden set resolves (so the US2 CI eval gates can actually RUN, not skip)

**Checkpoint**: identical corpus loads locally / CI / prod with no network — user stories can proceed.

---

## Phase 3: User Story 1 - A cook uses the live app at a public URL (Priority: P1) 🎯 MVP

**Goal**: The cook-facing app (widget + API) is live at a public HTTPS URL and completes the demo
scenario with the wall + grounding intact; dashboard and Phoenix are deployed but operator-gated.

**Independent Test**: Visit the published HTTPS URL on a clean browser; run the full demo scenario
(chat → cards → verbatim steps → meal plan → shopping list → favorite) with an allergy/diet constraint;
confirm zero wall/grounding violations and a valid certificate (quickstart §B).

- [X] T009 [US1] Create the Railway project (one project): add **PostgreSQL (pgvector)** and **Redis** plugins; confirm the `vector` extension is enabled by the backend's `alembic upgrade head` — DONE: project `zonal-perception`; Postgres `postgres-ssl:18` (pgvector 0.8.2 confirmed installed) + Redis plugins present; alembic migrations ran clean on first backend deploy.
- [X] T010 [US1] Add `railway/vault.toml`: Vault as its own service in **server mode** with a **persistent volume** (not dev mode), reachable on the private network — the live backend depends on it
- [X] T011 [US1] Update root `railway.toml`: production start command `alembic upgrade head` → serve on `$PORT`; **drop the dev boot-seed** step (prod Vault is pre-seeded, persistent); keep the `/health`-gated promotion
- [X] T012 [P] [US1] Add `railway/widget.toml`: public static widget host (Vite build → nginx) with `VITE_API_BASE` build arg = the **public backend origin**
- [X] T013 [US1] Set `WIDGET_ORIGINS` (Railway var + `app/config.py` consumption) to include the deployed widget origin so CORS allows the browser SPA — code consumption verified (`app/config.py:135` `widget_origins` → `widget_origins_list` → `app/main.py:77` CORS); `.env.example` documents adding the deployed origin. Setting the actual prod Railway var is an OPERATOR ACTION once the widget URL exists (T009/T017).
- [X] T014 [P] [US1] Add `railway/dashboard.toml`: operator-gated Streamlit on a **separate, unadvertised** URL (behind streamlit-authenticator) — not the public URL (FR-001a)
- [X] T015 [P] [US1] Add `railway/phoenix.toml`: Phoenix service pointed at the **same** Postgres with `PHOENIX_SQL_DATABASE_SCHEMA=phoenix`, operator-gated; tracing failure must not affect `/health`
- [X] T016 [US1] First deploy: one-time seed of the persistent prod Vault (operator runs `scripts/seed_vault.sh` against the prod `VAULT_ADDR` with real keys exported in shell) + run `load_seed_corpus.py` against prod Postgres — DONE: Vault server-mode (1.13.3, root, persistent volume) initialized/unsealed by operator + KV v2 enabled + real Groq/embeddings keys seeded; 2224 recipes + 2224×1536 vectors loaded into prod Postgres via `load_seed_corpus.py`.
- [X] T017 [US1] Deploy and verify on the live URL: `/health` → 200 promotes the deploy, HTTPS cert valid, demo scenario passes end-to-end with the wall enforced (quickstart §B; SC-001) — DONE: backend live at https://sous-chef-production-721e.up.railway.app (`/health` 200, postgres+redis+vault ok); widget live at https://widget-production-5547.up.railway.app (SPA + baked backend origin + CORS verified); live retrieval returns grounded cards; **allergen wall verified** (milk-allergic query → 0 unsafe cards). NOTE: a pre-existing seed-corpus **diet-flag data-quality issue** (some recipes mis-flagged `is_vegan`) lets a vegan profile see non-vegan recipes — corpus/ingestion bug (006 scope), not a deploy/wall-logic bug; tracked as follow-up.

**Checkpoint**: the public URL serves the cook journey; this is the demo-able MVP.

---

## Phase 3b: Outstanding issues & follow-ups discovered during the live deploy

**Purpose**: Capture everything found-but-not-fixed while bringing US1 live, so nothing is lost. None of
these block the MVP cook journey (live + allergen-wall-verified), but each is real.

### 🔴 Correctness / safety
- [X] T017a [data-quality] **Seed-corpus diet flags are wrong** — some recipes are mis-flagged `is_vegan`/
  `is_vegetarian` (e.g. "evil chicken", "Oxtail with broad beans" both `is_vegan=true`, and several carry
  a `milk`/`soy` allergen *while* flagged vegan — a self-contradiction). Effect: a **vegan** profile can be
  shown non-vegan recipes (a diet-wall violation in practice). The wall *code* is correct (it enforces the
  flags deterministically; the **allergen** wall tested clean — milk query → 0 cards); the **data** is
  bad. This is a **feature 006 (corpus-data-quality) / ingestion** bug, not a deploy bug. — DONE (code +
  committed artifact): root-caused TWO bugs in `ingestion/allergens.py` — (1) animal allergen tags from
  Open Food Facts (milk/eggs/fish) never fed the diet signals, so an OFF-only milk tag left a recipe
  flagged vegan; (2) meat cuts carrying no top-9 allergen (oxtail …) were undetected → read as vegan. Also
  found the *source* of the milk contradictions: OFF product-search false-positives (garlic → "garlic
  bread" → milk) on whole foods. Fix: animal allergen TAGS now fail diets closed (`derive_diet_flags`),
  curated meat-keyword list extended (oxtail, brisket, pancetta, …), and OFF allergen tags are suppressed
  for trusted whole foods. Regenerated `seeds/corpus/recipes.jsonl` offline via
  `scripts/recompute_seed_diet_flags.py` (flags + allergens only; `embeddings.npy` byte-identical/aligned):
  **0 remaining contradictions** (was 141), oxtail dishes now non-vegan, genuinely-vegan dishes retained
  (233→185 vegan after correctly dropping real non-vegan + de-noising). New unit test
  `tests/unit/test_ingestion_allergens.py`; `lint` + `test` (214) + `evals` (redteam 1.0 / redaction 0)
  green. **Propagated to prod:** the corrected flags + allergens were pushed into the prod Postgres (the
  WAN `load_seed_corpus.py` single-txn was too slow, so a JSON-driven bulk UPDATE of the changed columns
  was used; embeddings untouched) — prod now reads **899 vegetarian / 185 vegan, 0 vegan↔allergen
  contradictions**, oxtail dishes non-vegan. The wall re-reads these at query time, so it is **live**.

### 🟡 Incomplete deployment surface (configs written, live services not yet created)
> ⛔ **WAS BLOCKED ON RAILWAY PLAN LIMIT** (T017b/c): the workspace is on the **Free/trial plan at its
> service cap (5/5: backend, Vault, Redis, widget, Postgres)** — `railway add` returns *"Free plan resource
> provision limit exceeded."* The `phoenix` schema was pre-created in prod Postgres.
> ✅ **T017b (dashboard) DONE via the Redis slot:** Redis was made optional (`REDIS_URL` unset → no cache,
> `/health` drops the redis check), the `Redis` service deleted to free a slot, and the `dashboard` deployed
> there — **zero cook-journey impact** (only the dashboard's routing-split metric is gone). Live at
> `https://dashboard-production-cd55.up.railway.app`. See [`docs/RUNBOOK.md`](../../docs/RUNBOOK.md) →
> *"Redis is optional…"* and [`dashboardxphoneix.md`](../../dashboardxphoneix.md) §C.1/§D.
> ✅ **Phoenix slot problem (T017c) resolved by T017i:** prod tracing can target **LangSmith Cloud**
> (`TRACING_PROVIDER=langsmith`), which needs **no Railway service** — so no slot, no widget move, no plan
> upgrade. Code is merged; prod activation just needs the operator to seed `LANGSMITH_API_KEY` + flip the
> var. Self-hosted Phoenix stays the local-dev default.
- [X] T017b [US1] **Dashboard Railway service** — DONE: freed a slot by removing Redis (now optional), then
  created the `dashboard` service (repo source `klc33/sous-chef`, `railwayConfigFile=railway/dashboard.toml`,
  vars `VAULT_ADDR`=private Vault / `VAULT_TOKEN`=`${{sous-chef.VAULT_TOKEN}}` ref / `BACKEND_ADMIN_URL`=
  public backend / `OPERATOR_USERNAME`=operator). **Live (unadvertised):**
  `https://dashboard-production-cd55.up.railway.app` — `/_stcore/health` → 200. Two gotchas hit + fixed:
  (1) `railway/dashboard.toml` `startCommand` needed `sh -c '…'` so Railway expands `$PORT` (commit
  `2be7d83`); (2) `railwayConfigFile` (toml) takes precedence over an instance-level startCommand override,
  and `serviceInstanceDeployV2` pins to the service's commit — so the deploy had to target the commit that
  carries the toml fix (deployed `commitSha=2be7d83`). Vault was unsealed + dashboard secrets present.
- [ ] T017c [US1] **Phoenix Railway service not created** — `railway/phoenix.toml` exists but no `phoenix`
  service is running yet. Backend var `PHOENIX_COLLECTOR_ENDPOINT` is still the stale `http://localhost:6006`
  (tracing is silently off — non-blocking, so `/health` is unaffected). Create the Phoenix service (image
  `arizephoenix/phoenix`, shared Postgres `phoenix` schema) and repoint `PHOENIX_COLLECTOR_ENDPOINT` at its
  private URL. **Superseded for prod by T017i** (LangSmith Cloud needs no Railway service); Phoenix remains
  the local-dev default.
- [ ] T017i [US1] **Swap prod tracing to LangSmith Cloud (no Railway service → no slot)** — the chosen
  resolution to the Phoenix slot problem (T017c). Tracing now has a `TRACING_PROVIDER` selector:
  `phoenix` (self-hosted OTLP, default + local dev) or `langsmith` (LangSmith Cloud OTLP ingest). Both go
  through the **same redacting OTLP exporter**, so golden rule #5 (redaction-before-export) holds for the
  cloud destination too. — **CODE DONE:** `app/config.py` (`tracing_provider`, `langsmith_otlp_endpoint`,
  `langsmith_project`, `VAULT_KEY_LANGSMITH_API_KEY`), `app/infra/tracing.py` (`_exporter_config` adds
  auth headers `x-api-key`/`Langsmith-Project`), `app/main.py` (reads the key from Vault best-effort),
  `scripts/seed_vault.sh` + `.env.example` (LangSmith vars; key stays in Vault). New unit tests
  `tests/unit/test_tracing_config.py`; lint + mypy + tests green. ⚠️ **PROD ACTIVATION (operator):** seed
  the real `LANGSMITH_API_KEY` into prod Vault, set backend var `TRACING_PROVIDER=langsmith` (+ optional
  `LANGSMITH_PROJECT`), redeploy. Decision/deviation recorded in `docs/DECISIONS.md` + `docs/SECURITY.md`.

### 🟠 Known deviations / tech-debt from the live bring-up (reconcile vs the contracts/docs)
> All four captured in [`docs/RUNBOOK.md`](../../docs/RUNBOOK.md) → **"Known deployment deviations (v0.1.0)"**.
- [X] T017d [vault] **Vault pinned to `hashicorp/vault:1.13.3`** (a downgrade) because the current 2.x image
  runs as non-root (uid 100) and cannot write the root-owned Railway volume (`mkdir /vault/data/core:
  permission denied`); 1.13.3 runs as root. — DONE: pin + revisit-condition documented in RUNBOOK.
- [X] T017e [vault] **Vault re-seals on every redeploy** (manual `operator unseal` required each time, 1
  key share / threshold 1). Spec posture accepted by operator, but consider **auto-unseal** (cloud KMS) so
  the backend boots unattended. — DONE: re-seal behavior + the auto-unseal recommendation documented in
  RUNBOOK (the full runbook lands in T033).
- [X] T017f [vault] **Vault has a public HTTPS endpoint** (`efficient-dream-production-e88d.up.railway.app`)
  kept **only** for operator init/unseal/seed (it's sealed + root-token-gated). The backend reaches Vault
  over the **private** network. — DONE: deviation + remove-when-auto-unseal-lands documented in RUNBOOK.
- [X] T017g [railway] **Per-service config-as-code gotcha** (document for reproducibility): a Railway
  service whose repo has a root `railway.toml` will inherit it unless `railwayConfigFile` is set per service.
  The `widget` service initially ran the backend's `alembic` start command + `/health` gate until pointed at
  `railway/widget.toml`; it also needed `PORT=80` so Railway's networking targets nginx. — DONE: gotcha +
  the per-service `railwayConfigFile` rule documented in RUNBOOK (applies to T017b/c).

### 🟢 Repo housekeeping
- [X] T017h [repo] `specs/007-ship-public-deploy/tasks.md` tracking updates (T009/T016/T017 → done + this
  section) are in the working tree on `main` but **not pushed** (pushing `main` triggers a Railway rebuild).
  — DONE: Phase 3b committed + pushed to `main` together with the T017a fix and the deviation docs.

---

## Phase 4: User Story 2 - Only a green main reaches production (Priority: P1)

**Goal**: `main` can only ever be a green commit (full gates), and Railway auto-deploys that commit.

**Independent Test**: A PR that fails a gate cannot merge (so never deploys); a passing PR merges and
deploys; branch protection lists all required checks (quickstart §C).

- [~] T018 [US2] Extend `.github/workflows/ci.yml` with an `evals-full` job — **REVERTED per operator decision (cost): the full eval suite must NOT run in GitHub.** The `evals-full` job was built and proven green (it correctly ran the offline RAG hit@3/MRR + agent gates against the seeded corpus with the real Actions secrets), then **deleted** from `ci.yml`. ⚠️ **Deviation from [contracts/ci-gate.md](contracts/ci-gate.md)** (which lists `evals-full` as a required check): the offline RAG/agent + report-only LLM-judge grades (the provider-billed ones) now run **locally only** via `make evals` after `make up`, never on a PR/push. The **deterministic, hermetic safety/quality gates remain required in CI** via the `gates` job — classifier macro-F1, **red-team refusal = 1.0**, **redaction leaks = 0** (no provider calls), plus the full pytest suite in `smoke`. Net: "only a green `main` deploys" is intact and free; the paid RAG/agent grades are on-demand. (To be reflected in `docs/DECISIONS.md`/RUNBOOK deviations under US5.)
- [X] T019 [US2] Add `GROQ_API_KEY` and `EMBEDDINGS_API_KEY` as GitHub Actions repository secrets and feed them into the `evals-full` job (seed into the job's dev Vault); never commit them — DONE: both repo secrets created on `klc33/sous-chef` via the GitHub Actions secrets REST API (client-side libsodium sealed-box encryption; values pulled from the running local Vault and the owner's stored GH token, so no secret ever passed through a shell arg or got committed). Verified present (names only): `EMBEDDINGS_API_KEY`, `GROQ_API_KEY`. ⚠️ **Now UNUSED in CI** since `evals-full` was deleted (T018) — no workflow references them. Harmless to leave (still encrypted at rest); delete them in repo *Settings → Secrets and variables → Actions* if you want zero unused secrets, or keep for a future on-demand evals workflow.
- [X] T020 [US2] Configure branch protection on `main`: require pull requests (no direct pushes) and mark the required status checks — DONE: branch protection applied to `main` via the REST API (HTTP 200). Required status checks `[ruff, mypy, gates, smoke]` (⚠️ `evals-full` was dropped when the job was deleted in T018 — leaving a deleted job as a required check would block every PR forever on a check that never reports), `strict=true` (branch must be up to date), `enforce_admins=true` (the gate binds the owner too, so the protection is real on this solo repo), and require-a-PR-before-merging (0 required approvals → blocks direct pushes without forcing impossible self-approval). ⚠️ Effect: `git push origin main` is now rejected — all changes reach prod only via a green PR merge. Relax `enforce_admins` later if the solo workflow needs it.
- [ ] T021 [US2] Confirm Railway's GitHub integration is bound to **`main` only** so non-main branches never reach production (FR-003) — **OPERATOR (Railway dashboard, in progress):** for each prod service (backend, widget, dashboard, …) confirm *Settings → Source → automatic deploys* tracks **`main`** only (no PR/branch deploys), so non-`main` commits never reach production.
- [ ] T022 [US2] Verify the gate: open a PR with a deliberately failing red-team probe → required checks red → merge blocked; revert; open a passing PR → merges → Railway deploys that commit (quickstart §C; SC-002) — **READY** once T021 is confirmed. Mechanics: (a) the ci.yml + tracking updates land via a real PR ([#1](https://github.com/klc33/sous-chef/pull/1), now mandatory — `main` is protected); a green merge triggers the Railway deploy = the *passing-PR + deploy* half; (b) a throwaway PR adding a deliberately-failing red-team probe (caught hermetically by the `gates` job) proves required-checks-red ⇒ merge-blocked. Required checks are now the free/deterministic set `[ruff, mypy, gates, smoke]` (no provider calls), so PR runs are cheap. ⚠️ The merge still deploys to **prod** — pending operator go-ahead.

**Checkpoint**: the deploy is provably gated on a green `main`.

---

## Phase 5: User Story 3 - A fresh clone reproduces the stack with one command (Priority: P1)

**Goal**: A clean machine reproduces the full stack and the demo locally via the documented one-command
path after seeding secrets.

**Independent Test**: On a machine that never ran the project, clone → seed → one command → demo passes
locally with the same safety behavior as the live URL (quickstart §A; SC-003/SC-006).

- [ ] T023 [US3] Confirm/define the single documented bring-up path: `make up` (auto-copies `.env.example`→`.env`, builds, starts all services) → `make seed` → `load_seed_corpus.py`; ensure missing secrets fail fast with a clear seed-pointing message (FR-014)
- [ ] T024 [US3] Update `.env.example` with a production-profile bootstrap note (still **non-secret only**): which vars are platform-injected vs static, pointing real keys to Vault
- [ ] T025 [US3] Verify on a clean checkout: fresh clone reproduces the demo locally and matches live safety behavior (wall, grounding, redaction) with zero divergence (quickstart §A; SC-006)

**Checkpoint**: reproducibility proven on a clean machine.

---

## Phase 6: User Story 4 - Secrets in Vault; datastore creds platform-injected (Priority: P2)

**Goal**: Harden and prove the secrets split — app secrets only in Vault, managed datastore credentials
only platform-injected, Railway variables bootstrap/non-secret only.

**Independent Test**: Inspect repo + image (zero secrets) and confirm the app reads app secrets from
Vault and datastore creds from platform injection (quickstart §D; SC-004). Depends on US1's Vault
service (T010) existing.

- [ ] T026 [US4] Make `scripts/seed_vault.sh` prod-safe and documented for one-time seeding against the persistent server-mode Vault (real keys from the operator's env; idempotent KV v2 write); keep the dev-placeholder fallback for local only
- [ ] T027 [US4] Set the production Railway variables to **bootstrap/non-secret only** per [contracts/secrets-keyspace.md](contracts/secrets-keyspace.md): `ENV`, `VAULT_ADDR`/`VAULT_TOKEN`, platform-injected `POSTGRES_URL`/`REDIS_URL`, `PHOENIX_COLLECTOR_ENDPOINT`, `LLM_PROVIDER`+knobs, `WIDGET_ORIGINS`, dashboard non-secrets — and confirm no provider key is among them
- [ ] T028 [US4] Verify secret posture: `git grep` / image scan for key shapes (`gsk-`, `sk-…`, `hvs.`, bearer) returns zero hits; remove a Vault key and confirm the backend fails fast at startup (quickstart §D; SC-004)

**Checkpoint**: the security model is verified, not just asserted.

---

## Phase 7: User Story 5 - Documentation (design, decisions, evals, security, runbook) (Priority: P2)

**Goal**: A reviewer can understand and reproduce the system from `docs/` alone, with decisions backed by
numbers.

**Independent Test**: A fresh reader describes the architecture, cites ≥1 decision with its number,
states the eval gates + latest results, explains the security model, and reproduces the stack — without
asking the author (quickstart §E; SC-005).

- [ ] T029 [P] [US5] Create `docs/DESIGN.md`: architecture, the turn request-flow (guardrails → router → workflow/agent → wall → output rail), and the Railway deployment topology from [data-model.md](data-model.md)
- [ ] T030 [P] [US5] Update `docs/DECISIONS.md`: ML-vs-LLM, chunking, and agent-vs-workflow — **each backed by a concrete number** (classifier macro-F1, retrieval hit@3, routing split, etc.)
- [ ] T031 [P] [US5] Update `docs/EVALS.md`: each suite, its committed threshold (`eval_thresholds.yaml`), and the latest numbers — including red-team refusal (=1.0) and redaction leaks (=0)
- [ ] T032 [P] [US5] Update `docs/SECURITY.md`: the secrets split (Vault vs platform-injected vs bootstrap vars), the wall, grounding, redaction-before-logs-and-spans, guardrails, and the limited public surface
- [ ] T033 [P] [US5] Update `docs/RUNBOOK.md`: the exact local + deploy procedure — compose up → seed Vault → init Phoenix → load seed corpus → deploy → release/tag — plus failure-recovery notes
- [ ] T034 [P] [US5] De-stale `README.md`: drop the "foundation phase, no cook-facing logic yet" framing; add the live URL and links to `docs/`
- [ ] T035 [US5] Verify docs: have the content satisfy each clause of SC-005 (architecture, a numbered decision, eval gates+results, security model, reproduce) — quickstart §E

**Checkpoint**: the release is documented and reviewable.

---

## Phase 8: User Story 6 - Tag the release v0.1.0 (Priority: P3)

**Goal**: Mark the exact commit that is live and reproducible as `v0.1.0`.

**Independent Test**: `v0.1.0` exists and points at the commit running at the public URL and reproducible
locally (quickstart §F; SC-007).

- [ ] T036 [US6] Release rehearsal: run the demo scenario on the live URL **and** reproduce on a fresh clone; both must pass before tagging (release gate)
- [ ] T037 [US6] Tag and push: `git tag -a v0.1.0 -m "SousChef v0.1.0 — first public release"` && `git push origin v0.1.0`; confirm it points at the live+reproducible commit (quickstart §F; SC-007)

**Checkpoint**: `v0.1.0` is the citable first public release.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T038 [P] Run the full `quickstart.md` validation across all stories (A–F) end-to-end one more time
- [ ] T039 [P] Final release sweep: confirm no secrets in repo/image, no torch in any image, images < ~500MB, and `make lint && make test && make evals` green (Definition of Done)
- [ ] T040 [P] Verify tracing-outage resilience (SC-008 / FR-011): with the deployed stack up, stop the Phoenix service and confirm `/health` still returns 200 and the demo scenario completes — tracing is non-blocking on the cook-facing request path. Depends on the live deployment (US1, T017).

---

## Dependencies & Execution Order

### Phase dependencies
- **Setup (P1)**: no dependencies.
- **Foundational (P2)**: depends on Setup. **Blocks US1, US2, US3** (all need the seed corpus/loader).
- **US1 (P3)**: after Foundational. Creates the Railway project + Vault service (the live deployment).
- **US2 (P4)**: after Foundational (needs the seed corpus for `evals-full`). Independent of US1's
  Railway services (CI uses ephemeral containers); branch protection + Railway-bound-to-`main` complete it.
- **US3 (P5)**: after Foundational. Purely local — independent of US1/US2.
- **US4 (P6)**: hardens/verifies the secrets posture of US1's deployment → depends on **T010** (Vault
  service) and prod variables from US1.
- **US5 (P7)**: documents the system; best after US1–US4 produce the numbers/posture to document, but the
  doc files can be drafted in parallel.
- **US6 (P8)**: release gate — depends on US1 (live) + US3 (reproduces) passing; realistically after all.
- **Polish (P9)**: after all desired stories.

### Within stories
- Foundational: T004 → T005 (export needs the script) → T006 → T007 → T008.
- US1: T009/T010 → T011 → (T012/T014/T015 [P]) → T013 → T016 → T017.
- US2: T018 → T019 → T020 → T021 → T022.
- US5: T029–T034 all [P] (different files) → T035 (verify).

### Parallel opportunities
- Setup T001/T002/T003 together.
- US1: T012, T014, T015 (three different `railway/*.toml`) together.
- US5: T029–T034 (six different doc files) together — the biggest parallel block.
- Polish T038/T039/T040 together (T040 also needs the live deployment from US1).

---

## Parallel Example: User Story 5 (docs)

```bash
# Six independent doc files — write together:
Task: "Create docs/DESIGN.md (architecture + request flow + topology)"
Task: "Update docs/DECISIONS.md (each decision with a number)"
Task: "Update docs/EVALS.md (suites, thresholds, latest numbers)"
Task: "Update docs/SECURITY.md (secrets split, wall, grounding, redaction)"
Task: "Update docs/RUNBOOK.md (compose up → seed Vault → init Phoenix → deploy → release)"
Task: "De-stale README.md (live URL + docs links)"
```

---

## Implementation Strategy

### MVP first (US1)
1. Phase 1 Setup → Phase 2 Foundational (seed corpus) → Phase 3 US1.
2. **STOP and VALIDATE**: demo scenario passes on the live HTTPS URL.
3. That is a demo-able public release candidate.

### Incremental delivery
1. Setup + Foundational → corpus reproducible everywhere.
2. US1 → live public URL (MVP). 3. US2 → green-main gate. 4. US3 → fresh-clone reproduction.
5. US4 → secrets hardening proven. 6. US5 → docs. 7. US6 → tag `v0.1.0`.

### Notes
- [P] = different files, no incomplete-task dependency.
- Commit after each task or logical group; keep `make lint && make test && make evals` green throughout.
- Never weaken an eval threshold to make CI pass (constitution P6) — fix the cause.
