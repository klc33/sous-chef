# Implementation Plan: Ship to a Public URL (v0.1.0)

**Branch**: `007-ship-public-deploy` | **Date**: 2026-06-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-ship-public-deploy/spec.md`

## Summary

Promote the already-built SousChef monolith from a local docker-compose stack to a reproducible,
documented public deployment on Railway, and cut release `v0.1.0`. The core app logic is done; this
feature is **deployment topology, CI/CD gating, secrets posture, a committed seed corpus, and the
documentation set** — no new cook-facing behavior.

Concretely, the work closes the delta between today's state and the spec's clarified decisions:

- **Railway = one project, multiple services** — backend (FastAPI), dashboard (Streamlit, operator-gated),
  phoenix (tracing UI, operator-gated), and a static widget host (public) — plus the managed
  **PostgreSQL (pgvector)** and **Redis** plugins, plus **Vault as its own service** with a persistent
  volume. Today's `railway.toml` is backend-only; it becomes one service config among several.
- **Public surface = widget + backend API only**; dashboard and Phoenix live on separate, unadvertised
  operator URLs (FR-001a).
- **Secrets split** — Railway variables hold **bootstrap only** (Vault addr/token, the platform-injected
  Postgres/Redis URLs); Groq / embeddings / external API keys live **only in Vault**, seeded once by the
  operator into the persistent prod Vault per the runbook (never placed in Railway variables).
- **Deploy gate** — Railway's GitHub integration auto-deploys `main`; **branch protection** makes the
  full CI suite (lint, type-check, full `make evals` incl. red-team + redaction, tests) a required check,
  so `main` is only ever green (FR-002/FR-002a).
- **Full evals in CI** — a new CI job runs the *complete* `make evals` (including the RAG hit@3/MRR and
  agent tool-selection gates that currently SKIP) against ephemeral Postgres/Redis, a loaded **committed
  seed corpus**, and provider keys supplied as GitHub Actions secrets (Q5).
- **Committed seed corpus** — a pre-built, categorized + embedded dataset plus a loader, loaded at deploy
  and in CI, so local == prod data and the demo never hits a cold corpus (FR-013).
- **Docs** — create `docs/DESIGN.md`; bring `DECISIONS.md` (each decision carries a number),
  `EVALS.md`, `SECURITY.md`, and `RUNBOOK.md` (compose up → seed Vault → init Phoenix → deploy) current;
  refresh the stale README.
- **Release** — rehearse the demo scenario on the live URL, confirm a fresh clone reproduces, tag `v0.1.0`.

## Technical Context

**Language/Version**: Python 3.12 (backend, dashboard, evals, loaders); Node 20 (widget build, static
nginx serve); shell (`sh`) for seed/ops scripts. No TypeScript in the widget.

**Primary Dependencies**: FastAPI + Pydantic; SQLAlchemy + Alembic; pgvector; Redis; hvac (Vault);
Arize Phoenix + OpenTelemetry; scikit-learn + joblib (served classifier); Presidio (PII); Streamlit +
streamlit-authenticator; React + Vite. **No torch/transformers in any image** (constitution P3/P10).
Managed by `uv` only, grouped per image.

**Storage**: One PostgreSQL (pgvector) instance shared by the app (`public` schema) and Phoenix
(`phoenix` schema); Redis for cache/session; Vault for secrets (persistent volume in prod).

**Testing**: pytest (unit + integration + redteam); `make evals` against `eval_thresholds.yaml`; CI on
GitHub Actions; a live-URL demo rehearsal + a fresh-clone reproduction as release acceptance.

**Target Platform**: Railway (one project; services + Postgres/Redis plugins + a Vault service); local
parity via docker-compose. Public HTTPS with Railway-provisioned certificates.

**Project Type**: Web service monolith + two sibling surfaces (Streamlit dashboard, React widget) +
offline pipelines (ml/ingestion/evals). This feature is ops/release/docs over that existing layout.

**Performance Goals**: Not a perf feature. Preserve current per-turn behavior; deploy must pass the
`/health` readiness gate within `healthcheckTimeout`; CI full-eval job completes in a practical CI budget
(target < ~15 min) by loading the seed corpus rather than running ingestion.

**Constraints**: No Kubernetes, no IaC sprawl — orchestration stays docker-compose-style + Railway native
config (FR-012). Reuse the one Postgres for Phoenix (FR-011). Images stay lean (< ~500MB; no torch). No
application secret in repo/image/`.env` (FR-004). Local and prod hold identical seed data (FR-013).

**Scale/Scope**: Solo project, demo-scale traffic. Scope is one public environment + one local
reproduction path + the documentation set + the `v0.1.0` tag.

## Constitution Check

*GATE: must pass before Phase 0 and re-checked after Phase 1 design.*

| Principle | Gate | Verdict |
|-----------|------|---------|
| I. Simplicity | Monolith, compose-style orchestration, no K8s, one agent. | **PASS** — adds Railway service configs + CI job + a loader; no new architecture, no service sprawl. |
| II. Build only what's required | Every task traces to an FR. | **PASS** — tasks map to FR-001..FR-016; no speculative features. |
| III. Separation of concerns | `api → services → repo → infra`; repo is the only DB toucher. | **PASS** — deployment touches infra/config/CI only; the seed-corpus loader writes via the existing repo/migration path, not ad-hoc SQL in app code. |
| IV. Testability | Safety gated in CI. | **PASS, strengthened** — full `make evals` (incl. red-team + redaction) becomes a *required* merge check; the demo scenario is an explicit acceptance test. |
| V. Reproducibility | Fresh clone runs identically; deps pinned; thresholds committed. | **PASS, strengthened** — committed seed corpus makes local == prod; one-command bring-up preserved (`make up`). |
| VI. Security & privacy by default | Secrets in Vault; redaction before logs+spans; least public surface. | **PASS** — Vault-only app secrets with a documented bootstrap-only Railway-vars split; only widget+API public; dashboard/Phoenix operator-gated. |
| VII. Maintainability | Readable, consistent, documented. | **PASS** — docs set is a deliverable; configs are commented like the existing ones. |
| VIII. Documentation-first | Spec before code; docs in sync. | **PASS** — spec + this plan precede the deploy changes; README/docs drift is explicitly fixed. |
| IX. Spec-driven | `specify → plan → tasks → implement`, artifacts committed. | **PASS** — this is that flow. |
| X. No unnecessary tech | Approved stack only; no torch/vector-db/K8s. | **PASS** — no new technologies; Railway, Vault, Postgres, Redis, Phoenix, GitHub Actions are all already in the constitution/stack. |
| Safety invariants | Wall, grounding, hosted-only inference, lean classifier. | **PASS** — deployment changes do not touch these paths; the wall/grounding/redaction run identically in prod and are gate-verified. |

**Result: PASS — no violations, no Complexity Tracking entries required.** The seed-corpus artifact and
the Vault-on-Railway service are both explicitly within the lean stack and are justified by FR-013 and
the recorded secrets-split clarification.

## Project Structure

### Documentation (this feature)

```text
specs/007-ship-public-deploy/
├── plan.md              # This file
├── research.md          # Phase 0: decisions (multi-service Railway, Vault posture, seed corpus, CI evals)
├── data-model.md        # Phase 1: deployment topology, secret keyspace, seed-corpus artifact schema
├── quickstart.md        # Phase 1: how to verify (fresh-clone reproduce + live-URL demo + release)
├── contracts/           # Phase 1: deployment & gate contracts (services, health, secrets, CI)
│   ├── deployment-topology.md
│   ├── secrets-keyspace.md
│   ├── ci-gate.md
│   └── seed-corpus.md
└── checklists/
    └── requirements.md  # Spec quality checklist (from /speckit-specify)
```

### Source Code (repository root) — files this feature adds or changes

```text
sous-chef/
├── railway.toml                     # CHANGE: backend service config (drop boot-seed in prod; env wiring)
├── railway/                         # ADD: per-service Railway configs (no IaC sprawl — small TOML each)
│   ├── dashboard.toml               #   operator-gated Streamlit service (separate, unadvertised URL)
│   ├── phoenix.toml                 #   operator-gated Phoenix service (shared Postgres, `phoenix` schema)
│   ├── widget.toml                  #   public static widget host (Vite build → nginx)
│   └── vault.toml                   #   Vault service: persistent volume, prod (non-dev) server config
├── docker-compose.yml               # CHANGE: keep local parity; ensure seed-corpus load step exists locally
├── .github/workflows/ci.yml         # CHANGE: add full-`make evals` job (PG+Redis+seed corpus+provider secrets)
├── scripts/
│   ├── seed_vault.sh                # CHANGE: prod-safe path (real keys from operator env; persistent Vault)
│   ├── load_seed_corpus.py          # ADD: load the committed seed corpus into Postgres (deploy + CI + local)
│   └── export_seed_corpus.py        # ADD: build the committed seed corpus from a populated DB (offline)
├── seeds/
│   └── corpus/                      # ADD: committed, pre-built corpus (categorized + embedded) — see contract
│       ├── recipes.jsonl            #   recipe rows + category + diet/allergen tags
│       └── embeddings.npy           #   pinned embedding vectors aligned to recipes.jsonl
├── docs/
│   ├── DESIGN.md                    # ADD: architecture + request flow + deployment topology
│   ├── DECISIONS.md                 # CHANGE: ML-vs-LLM, chunking, agent-vs-workflow — each with a number
│   ├── EVALS.md                     # CHANGE: suites, thresholds, latest numbers (incl. red-team/redaction)
│   ├── SECURITY.md                  # CHANGE: secrets split, wall, grounding, redaction, guardrails, surface
│   └── RUNBOOK.md                   # CHANGE: compose up → seed Vault → init Phoenix → deploy → release
├── README.md                        # CHANGE: de-stale (not "foundation phase"); link docs/ + live URL
└── .env.example                     # CHANGE: add production-profile bootstrap notes (still non-secret only)
```

**Structure Decision**: Keep the existing monolith + sibling-surface layout untouched; this feature lives
in **config, CI, scripts/seed data, and docs**. Railway multi-service is expressed as one small TOML per
service under `railway/` (plus the root `railway.toml` for the backend) — native platform config, not a
new orchestration system, honoring "no Kubernetes/IaC sprawl." The seed corpus is a committed data
artifact under `seeds/` with an offline exporter and an at-deploy loader, so production never runs the
ingestion pipeline.

## Phases

### Phase 0 — Research (`research.md`)
Resolve the open how-to questions: modelling multiple Railway services without IaC sprawl; the
production Vault posture (persistent volume, seed-once vs seed-on-boot, where key material comes from);
the committed seed-corpus format (how to ship embeddings reproducibly and load them at deploy); and how
to make the full `make evals` actually run (not skip) in CI within budget. Each entry records
Decision / Rationale / Alternatives.

### Phase 1 — Design & Contracts (`data-model.md`, `contracts/`, `quickstart.md`)
- **data-model.md** — the deployment "entities": services, plugins, the secret keyspace (Vault keys vs
  Railway bootstrap vars), and the seed-corpus artifact schema + its mapping to existing tables.
- **contracts/** — deployment-topology (which service is public/operator-gated, ports, health),
  secrets-keyspace (exact key list and where each lives), ci-gate (jobs, required checks, branch
  protection), seed-corpus (file schema + load/export contract).
- **quickstart.md** — the runnable acceptance path: fresh-clone reproduce locally, deploy + demo on the
  live URL, and tag the release.

### Phase 2 — Tasks
Generated by `/speckit-tasks` (not here), ordered by the spec's user-story priorities: P1 (live demo,
green-main gate, fresh-clone reproduce) → P2 (secrets split, docs) → P3 (tag `v0.1.0`).

## Complexity Tracking

No constitution violations — this section is intentionally empty.
