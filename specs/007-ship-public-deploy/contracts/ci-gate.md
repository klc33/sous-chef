# Contract: CI Gate & Deploy Enforcement

Defines the CI jobs and how "only a green main deploys" is enforced. Verifies FR-002, FR-002a, FR-003,
SC-002, US2.

## CI jobs (GitHub Actions, on push to `main` + every PR)

| Job | What it runs | Services | Gate |
|-----|--------------|----------|------|
| `ruff` | `ruff check app alembic` | none | required |
| `mypy` | `mypy app` | none | required |
| `gates` | train classifier + deterministic `evals.run_evals` (classifier macro-F1, **red-team refusal = 1.0**, **redaction leaks = 0**) | none (hermetic) | required |
| `evals-full` *(new)* | **full `make evals`** incl. RAG hit@3/MRR + agent tool-selection (no skips) | Postgres(pgvector) + Redis service containers + dev Vault step; **seed corpus loaded**; `GROQ_API_KEY`/`EMBEDDINGS_API_KEY` as Actions secrets | required |
| `smoke` | boot app on real PG+Redis+Vault, assert `/health` 200, run unit+integration+redteam pytest | PG+Redis+Vault | required |

- The non-deterministic LLM-judge rows in `make evals` stay **report-only** (never set exit code).
- Thresholds come from committed `eval_thresholds.yaml`; **never weakened to pass** (constitution P6).

## Deploy enforcement
- **Branch protection on `main`**: require PRs (no direct pushes) and mark `ruff`, `mypy`, `gates`,
  `evals-full`, `smoke` as **required status checks**. ⇒ `main` only ever advances to a green commit.
- **Railway GitHub integration** auto-deploys `main` on push. Because `main` is always green, every
  production deploy corresponds to a passing commit (SC-002).
- Railway is bound to `main` only ⇒ non-main branches never reach production (FR-003).

## Acceptance probes (US2 independent test)
- A PR that fails any required check (e.g. a deliberately failing red-team probe) **cannot merge**, so it
  never reaches `main` and never deploys.
- A passing PR merges to `main` and Railway deploys that exact commit.
