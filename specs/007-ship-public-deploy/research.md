# Phase 0 Research: Ship to a Public URL (v0.1.0)

Resolves the open how-to questions behind the plan. The product clarifications (deploy gate, Vault
hosting, public surface, seed corpus, CI evals) were settled in the spec's Clarifications section; this
document settles the **implementation approach** for each.

---

## R1 — Modelling multiple Railway services without IaC sprawl

**Decision**: Use **one Railway project** with several services, each configured by a **small per-service
TOML** plus the service's existing Dockerfile/plugin — the backend keeps the root `railway.toml`; the
dashboard, phoenix, and widget get `railway/<service>.toml`; Postgres and Redis are Railway **plugins**
(not custom services); Vault is a normal service backed by a **persistent volume**. Inter-service wiring
uses Railway's reference variables (e.g. the Postgres/Redis connection URLs and Vault's private address)
rather than hardcoded hosts.

**Rationale**: Railway's native unit is the service; a config file per service is the idiomatic,
low-ceremony way to pin build/start/health per service. This is plain platform configuration — no
Kubernetes manifests, no Terraform, no Helm — satisfying FR-012 ("no Kubernetes/IaC sprawl"). It mirrors
the already-working `docker-compose.yml` one-to-one (each compose service → one Railway service/plugin),
preserving local↔prod parity (constitution P5).

**Alternatives considered**:
- *Single mega-service running compose-in-a-container* — rejected: hides per-service health/scaling, fights
  the platform, and is harder to reason about than native services.
- *Terraform/Railway IaC provider* — rejected: explicit FR-012 violation; overkill for a solo demo.
- *Separate Railway projects per service* — rejected: breaks the private network and the "one project"
  instruction; complicates the shared Postgres.

---

## R2 — Production Vault posture (persistence + how key material arrives)

**Decision**: Run Vault as a Railway service in **non-dev server mode** with a **persistent volume** for
its storage backend. Seed real provider/app secrets **once, out-of-band, by the operator** via
`scripts/seed_vault.sh`, with the real keys exported in the operator's shell at seed time and written
straight to the prod Vault (they persist on the volume). **Railway variables hold bootstrap only** — the
Vault address + token and the platform-injected Postgres/Redis URLs — never the provider keys. The
backend's production start command **drops the boot-time seed step** that exists for local dev (the
local compose/`railway.toml` boot-seed writes dev placeholders); production relies on the already-seeded
persistent Vault.

**Rationale**: This honours the recorded secrets-split clarification exactly: app secrets live only in
Vault, and Railway variables are bootstrap-only. A persistent volume means seed-once is sufficient and
secrets survive redeploys, so we never need to re-inject key material on each boot (which would force keys
into Railway variables and violate FR-004/FR-006). It also matches the existing `railway.toml` note
("once a real seeded Vault is provisioned … drop the seed step"). The `seed_vault.sh` script already reads
keys from the operator's environment with dev fallbacks, so the same script serves both local dev (dev
mode + placeholders) and the one-time prod seed (server mode + real keys) — no new tooling.

**Operational note (fail-fast preserved)**: `app/infra/vault.py` already fails startup if Vault is
unreachable or a required secret is missing (FR-014). Running placeholder keys against a real provider
fails at the provider call — the intended signal to seed real keys.

**Alternatives considered**:
- *Vault dev mode in prod (in-memory, re-seeded each boot)* — rejected: ephemeral secrets, and re-seeding
  on boot would require keys in Railway variables (violates the split). This was the temporary local
  posture only.
- *HCP Vault (managed)* — rejected for v0.1.0: adds an external vendor and account; the clarification
  chose self-hosted Vault-as-a-service to keep the lean, single-platform story.
- *Skip Vault, use Railway variables for everything* — rejected: violates the constitution's "secrets in
  Vault" invariant and the recorded clarification.

---

## R3 — Committed seed corpus: format, reproducibility, and loading

**Decision**: Commit the corpus as **two aligned files** under `seeds/corpus/`: `recipes.jsonl` (one JSON
object per recipe: source id, title, category, ingredients, steps, diet/allergen tags, and the other
columns the `recipes` table needs) and `embeddings.npy` (a float32 matrix, row *i* = the embedding for
recipe *i* in `recipes.jsonl`, with the embedding **model id + dimension pinned** in a small
`manifest.json`). An **offline exporter** (`scripts/export_seed_corpus.py`) builds these from a populated
DB; an **at-deploy/CI/local loader** (`scripts/load_seed_corpus.py`) reads them and writes rows +
vectors into Postgres **through the existing repo/ORM path** (idempotent upsert keyed on source id).

**Rationale**: Shipping the embeddings as data (not recomputing them) makes local, CI, and prod
**byte-identical** corpora with zero provider calls at load time — exactly what FR-013 ("local and
production hold identical seed data") and reproducibility (P5) demand, and it keeps the CI eval job fast
and deterministic. JSONL + `.npy` are diff-friendly-enough, language-neutral, and need no extra deps
(`numpy` is already present). Loading through the repo layer (not raw SQL in app code) respects the
"repo is the only DB toucher" boundary (P3). Pinning the embedding model id guarantees query-time
embeddings (computed live from the same provider/model) live in the same vector space as the seeded ones.

**Size guard**: keep the seed corpus to the demo-relevant subset (enough that the demo scenario and the
RAG golden set return real results) rather than the full ingested corpus, so the committed artifact and
the image stay lean. If the vectors are large, store `embeddings.npy` via the existing Git LFS setup
(`.gitattributes` is present) rather than bloating the base repo.

**Alternatives considered**:
- *`pg_dump` SQL dump* — rejected: couples the artifact to a Postgres version/encoding and is opaque to
  review; harder to load in a fresh schema deterministically.
- *Re-run ingestion at deploy* — rejected by FR-013 (no prod ingestion) and because it needs provider keys
  + network at deploy and yields non-identical corpora run-to-run.
- *Recompute embeddings from committed text at load* — rejected: needs provider keys in CI/deploy, costs
  money/latency, and risks vector drift if the model changes.

---

## R4 — Running the full `make evals` in CI (no skips) within budget

**Decision**: Add a CI job that brings up **Postgres (pgvector) + Redis as service containers** and a
**dev-mode Vault step** (as the existing `smoke` job already does), **loads the committed seed corpus**
via `scripts/load_seed_corpus.py`, exposes **`GROQ_API_KEY` and `EMBEDDINGS_API_KEY` as GitHub Actions
secrets** (seeded into the dev Vault for the run), then runs **`make evals`** so the RAG hit@3/MRR and
agent tool-selection gates **actually execute** instead of skipping. The deterministic gates (classifier
macro-F1, red-team refusal, redaction) continue to run in the hermetic `gates` job too (belt-and-suspenders).
The report-only LLM-judge rows stay report-only (never set exit code), per the existing eval design.

**Rationale**: Q5 requires the full suite to gate the deploy. `evals/run_evals.py` already SKIPs the
offline gates when there's no corpus/keys; supplying both flips them to real RUN/PASS-FAIL. Using the
committed seed corpus (R3) keeps the job deterministic and fast (no ingestion). Keys as Actions secrets
keep them out of the repo/image (FR-004) while still letting the real providers be exercised. Branch
protection then makes this job a **required check**, which is what actually enforces "only a green main
deploys" under Railway's GitHub auto-deploy (FR-002/FR-002a).

**Cost/flake control**: the golden RAG + agent sets are small (~15 cases each), so provider spend per run
is bounded; the non-deterministic judge stays report-only so it can't flake the merge gate (matches the
existing constitution rule "evals are the grade; don't weaken thresholds").

**Alternatives considered**:
- *Keep evals hermetic, gate only deterministic safety* — rejected: Q5 explicitly chose the full suite.
- *Run evals on Railway post-deploy instead of CI* — rejected: that gates after the deploy, not before;
  branch-protection-on-CI is the pre-merge gate that keeps `main` green.
- *Mock the providers in CI* — rejected: mocked retrieval/agent scores don't grade the real system.

---

## R5 — Enforcing "only green main deploys" with Railway GitHub auto-deploy

**Decision**: Leave **Railway's GitHub integration auto-deploying `main`** (the existing posture) and add
**branch protection** on `main` that makes the CI jobs (ruff, mypy, gates, the new full-evals job, smoke)
**required status checks**, with PRs required (no direct pushes to `main`). Result: `main` only ever
advances to a green commit, and Railway then deploys that commit.

**Rationale**: This is the reconciled clarification (the user chose Railway auto-deploy + branch
protection over a workflow-issued deploy). It matches the existing `railway.toml`/`ci.yml` comments,
needs no `RAILWAY_TOKEN` in CI, and adds zero new moving parts — the gate is a GitHub setting, not code
(simplest design, P1). Non-main branches never touch production (FR-003) because Railway is bound to
`main` only.

**Alternatives considered**:
- *Workflow issues `railway up` on green* — rejected by the reconciliation (more moving parts, a token in
  CI, and diverges from the existing config). Recorded as the rejected option in the spec's Clarifications.

---

## Cross-cutting confirmations

- **Phoenix shares the one Postgres** (FR-011): already implemented via `PHOENIX_SQL_DATABASE_SCHEMA=phoenix`
  and `scripts/init_phoenix_schema.sql`; the Railway phoenix service points at the same Postgres plugin
  with the `phoenix` schema. A tracing outage must not take the cook app down — tracing is emitted via the
  `infra/tracing.py` adapter and is non-blocking on the request path.
- **Redaction before logs and spans** (constitution P5/VI): unchanged; runs identically in prod.
- **Lean images / no torch** (P3/P10): unchanged; the seed loader uses `numpy` only, no torch.
