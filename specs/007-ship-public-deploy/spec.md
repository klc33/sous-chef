# Feature Specification: Ship to a Public URL (v0.1.0)

**Feature Branch**: `007-ship-public-deploy`

**Created**: 2026-06-13

**Status**: Draft

**Input**: User description: "Ship Sous-Chef to a public URL, reproducibly and documented. The full stack runs on the host platform at a public HTTPS URL; only a green main deploys. All application secrets live in Vault; managed datastore credentials are injected by the platform. The repo comes up from a fresh clone with one command after seeding secrets, per a written runbook. Documentation explains the design, decisions (with numbers), evals, security model, and how to run. Acceptance: the demo scenario runs end-to-end on the live URL; a fresh clone reproduces locally; tag v0.1.0. Constraints: no Kubernetes/IaC sprawl; reuse the same Postgres for Phoenix; keep within the lean stack."

## Clarifications

### Session 2026-06-13

- Q: How is "only a green main deploys" enforced? → A: Railway's GitHub integration auto-deploys `main` on push; the gate is **branch protection** — a PR cannot merge into `main` until the required CI checks (lint, type-check, full `make evals` incl. red-team + redaction, full test suite) are green, so `main` is only ever a green commit. (Revises the earlier session answer of a workflow-issued deploy; this matches the existing `railway.toml`/`ci.yml` posture and "no IaC sprawl".)
- Q: Where does Vault run in production? → A: Vault runs as its own Railway service (container + persistent volume) in the same project, reached over Railway's private network, with its access token injected by the platform.
- Q: What is served at the public HTTPS URL? → A: Only the cook-facing app (widget + its API). The admin dashboard and the Phoenix tracing UI are deployed but operator-gated on a separate, unadvertised URL (behind existing auth), not on the public URL.
- Q: What populates the production database? → A: A committed, pre-built seed corpus (already categorized + embedded) loaded at deploy; no ingestion pipeline runs in production, so local and prod hold identical data.
- Q: How does the eval gate run in CI? → A: The full `make evals` suite (including red-team + redaction) runs in GitHub Actions against ephemeral Postgres/Redis service containers, with provider API keys supplied as GitHub Actions secrets; a green result is required before deploy.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A cook uses the live app at a public URL (Priority: P1)

A home cook opens the public HTTPS link, chats to discover recipes, opens a recipe card to read
its full stored steps, builds a small varied meal plan with a shopping list, and saves a favorite —
all against the deployed stack, with the safety wall and grounding behaving exactly as they do
locally.

**Why this priority**: The entire point of the release is a working, reachable product. If the live
URL cannot complete the core cook journey, nothing else about the deployment matters. This is the
minimum shippable outcome and the headline acceptance condition.

**Independent Test**: Visit the published HTTPS URL on a clean browser session with no local setup,
run the full demo scenario (chat → recipe cards → recipe detail → meal plan → shopping list →
favorite), and confirm each step returns real retrieved content with no allergy/diet violation and
no fabricated steps.

**Acceptance Scenarios**:

1. **Given** the published HTTPS URL, **When** a cook asks for recipe ideas, **Then** they receive a
   list of real retrieved recipe cards (never invented).
2. **Given** a returned card, **When** the cook opens it for detail, **Then** the app renders that
   recipe's stored steps verbatim.
3. **Given** a cook with a stated allergy/diet, **When** results are produced, **Then** no surfaced
   recipe violates the stated constraint, identical to local behavior.
4. **Given** the cook builds a meal plan, **When** they request a shopping list, **Then** a correct
   consolidated list is produced and a favorite can be saved and re-loaded.
5. **Given** the site is served, **When** the cook connects, **Then** the connection is HTTPS with a
   valid certificate.

---

### User Story 2 - Only a green main reaches production (Priority: P1)

The maintainer pushes to the main branch. The release pipeline runs the quality gates (lint, tests,
and evals including the red-team and redaction gates). Production is updated **only** when those
gates pass; a red main never becomes the live deployment.

**Why this priority**: The constitution makes evals the grade and forbids weakening thresholds to
pass. A public URL that can be updated from a failing build would let a safety regression (allergen
override, injection, PII leak) reach real cooks. Gating the deploy on a green main is the safety
contract of shipping publicly.

**Independent Test**: Push a commit that fails a gate (e.g., a deliberately failing red-team probe)
and confirm the live deployment does not change; push a passing commit and confirm the live
deployment updates.

**Acceptance Scenarios**:

1. **Given** a push to main, **When** the quality gates pass, **Then** the public deployment is
   updated to that commit.
2. **Given** a push to main, **When** any gate fails (lint, test, eval, red-team, or redaction),
   **Then** the public deployment is **not** updated and the failure is visible.
3. **Given** a push to a non-main branch, **When** it builds, **Then** it does not update the public
   production deployment.

---

### User Story 3 - A fresh clone reproduces the stack with one command (Priority: P1)

A new operator (or a reviewer on a clean machine) clones the repository, follows the written runbook
to seed secrets, and runs a single command to bring up the full stack locally — backend, database,
cache, secrets store, and tracing — matching the deployed behavior.

**Why this priority**: Reproducibility is a constitutional principle and an explicit acceptance
condition ("a fresh clone reproduces locally"). It is what lets anyone verify the system and what
makes the release trustworthy rather than a one-off that only runs on the author's laptop.

**Independent Test**: On a machine that has never run the project, clone the repo, follow the runbook
to seed secrets, run the documented single command, and confirm the same demo scenario completes
locally.

**Acceptance Scenarios**:

1. **Given** a fresh clone and the runbook, **When** the operator seeds secrets and runs the single
   documented bring-up command, **Then** the full stack starts and serves the app.
2. **Given** the stack is up locally, **When** the operator runs the demo scenario, **Then** it
   completes end-to-end with the same behavior as the live URL.
3. **Given** missing or unseeded secrets, **When** the operator starts the stack, **Then** they get
   a clear, actionable error pointing back to the seeding step (not a silent or cryptic failure).

---

### User Story 4 - Secrets are held in Vault; datastore credentials are injected by the platform (Priority: P2)

All application secrets (model/provider API keys, service tokens, etc.) are retrieved from Vault at
runtime, while the managed datastore connection credentials (database, cache) are supplied by the
hosting platform's environment injection rather than copied into code, images, or `.env`.

**Why this priority**: The security model is a release requirement and a constitutional invariant
("secrets live in Vault"). Getting the split right — Vault for app secrets, platform-injected
credentials for managed datastores — is what keeps keys out of the repo and images while still
letting the platform manage its own datastores.

**Independent Test**: Inspect the deployed image and repository and confirm no application secret is
present; confirm the running app reads app secrets from Vault and reads managed datastore
credentials from platform-injected configuration.

**Acceptance Scenarios**:

1. **Given** the deployed artifact, **When** it is inspected, **Then** no application secret value
   appears in the image, the repository, or any committed `.env`.
2. **Given** the running app, **When** it needs an application secret, **Then** it obtains it from
   Vault.
3. **Given** the running app, **When** it connects to a managed datastore, **Then** it uses
   credentials injected by the platform.
4. **Given** the example environment file, **When** it is reviewed, **Then** it contains only
   non-secret values (e.g., service addresses), never live keys.

---

### User Story 5 - Documentation explains design, decisions, evals, security, and how to run (Priority: P2)

A reviewer reads the project documentation and understands the system design, the key decisions
(supported by concrete numbers), the evaluation results and gates, the security model, and exactly
how to run and reproduce the project.

**Why this priority**: The release is explicitly "documented," and documentation-first is a
constitutional principle. Without it the live URL is unexplained and unverifiable by anyone but the
author; with it the project is reviewable and defensible line by line.

**Independent Test**: A reader unfamiliar with the project reads the docs and can (a) describe the
architecture, (b) cite at least one decision with its supporting numbers, (c) state the eval gates
and their results, (d) explain how secrets are handled, and (e) reproduce the stack from the runbook
without asking the author.

**Acceptance Scenarios**:

1. **Given** the documentation, **When** a reviewer reads it, **Then** the architecture and request
   flow are explained.
2. **Given** the documentation, **When** a reviewer looks for rationale, **Then** key decisions are
   justified with concrete numbers (e.g., eval scores, sizes, latencies, counts).
3. **Given** the documentation, **When** a reviewer looks for evaluation evidence, **Then** the eval
   suites, thresholds, and latest results (including red-team and redaction) are presented.
4. **Given** the documentation, **When** a reviewer looks for the security model, **Then** the
   secrets handling, the safety wall, grounding, redaction, and guardrails are described.
5. **Given** the documentation, **When** a reviewer wants to run it, **Then** the runbook gives the
   exact secret-seeding and bring-up steps for both local and deployed environments.

---

### User Story 6 - The release is tagged v0.1.0 (Priority: P3)

Once the live URL passes the demo scenario and a fresh clone reproduces locally, the maintainer
marks the exact released commit with the version tag `v0.1.0`.

**Why this priority**: The tag is the durable, citable marker of the first public release. It is an
explicit acceptance item but depends on every other story being satisfied first, so it is the final
step rather than an independent slice.

**Independent Test**: Confirm the tag `v0.1.0` exists and points at the commit that is live at the
public URL and that reproduces locally.

**Acceptance Scenarios**:

1. **Given** the acceptance conditions are met, **When** the maintainer tags the release, **Then** a
   `v0.1.0` tag exists on the released commit.
2. **Given** the `v0.1.0` tag, **When** it is inspected, **Then** it corresponds to the commit
   running at the public URL.

---

### Edge Cases

- **A gate fails after a previously green deploy**: the live deployment stays on the last green
  commit; the failing commit never replaces it.
- **Vault is unreachable at startup**: the app fails fast with a clear secrets-related error rather
  than starting in an insecure or degraded state.
- **A managed datastore credential is missing or wrong**: startup surfaces an actionable connection
  error that points to the platform-injection configuration, not a generic crash.
- **Phoenix tracing data shares the same database as the application**: application data and tracing
  data coexist in one Postgres without one corrupting or exhausting the other; a tracing failure
  must not take down the cook-facing app.
- **First-time bring-up before secrets are seeded**: the operator is blocked with a clear message
  directing them to the seeding step in the runbook.
- **Certificate/HTTPS not yet provisioned**: the public URL is not advertised as live until a valid
  certificate serves HTTPS.
- **Demo on a cold/empty corpus**: the deployed environment is populated from the committed, pre-built
  seed corpus (loaded at deploy; identical to local) so the demo scenario returns real results rather
  than empty lists.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The full application stack (cook-facing app, database, cache, secrets store, tracing)
  MUST run on the hosting platform; the **cook-facing app (widget + its API)** MUST be reachable at a
  single public HTTPS URL with a valid certificate.
- **FR-001a**: The admin dashboard and the Phoenix tracing UI MUST NOT be served on the public URL;
  they MAY be deployed on a separate, unadvertised operator-gated URL (behind existing
  authentication). Public attack surface is limited to the cook-facing app.
- **FR-002**: The public production deployment MUST update only from a `main` commit that passed all
  quality gates (lint, type-check, full eval suite including the red-team and redaction gates, and the
  test suite). Enforcement is via branch protection: a PR MUST NOT merge into `main` until the
  required CI checks are green, so a failing `main` cannot exist to be deployed.
- **FR-002a**: The CI quality gate MUST run the full `make evals` suite against ephemeral
  Postgres/Redis service containers, with provider API keys supplied as CI secrets (never committed).
  Railway's GitHub integration auto-deploys `main` on push; CI green-ness is enforced as a required
  status check by branch protection rather than by a workflow-issued deploy step.
- **FR-003**: Pushes to non-main branches MUST NOT update the public production deployment.
- **FR-004**: All application secrets MUST be stored in and retrieved from Vault at runtime; no
  application secret may appear in the repository, any committed environment file, or any built
  image.
- **FR-005**: Managed datastore credentials (database, cache) MUST be injected by the hosting
  platform and consumed from that injected configuration, not hardcoded or committed.
- **FR-006**: The committed example environment file MUST contain only non-secret values (service
  addresses/URLs, Vault address/token placeholders), never live keys.
- **FR-007**: From a fresh clone, after the operator seeds secrets per the runbook, a single
  documented command MUST bring up the full stack locally with behavior matching the deployment.
- **FR-008**: A written runbook MUST document the exact steps to seed secrets and to bring up the
  stack, for both the local and the deployed environments, including how to recover from common
  startup failures.
- **FR-009**: Documentation MUST explain the system design and request flow, the key decisions backed
  by concrete numbers, the evaluation suites/thresholds/latest results, the security model, and how
  to run and reproduce the project.
- **FR-010**: The deployed stack MUST preserve all constitutional safety invariants — the allergy/diet
  wall, grounded (never invented) recipes and verbatim stored steps, PII redaction before logging and
  before any trace span, input/output guardrails, and the bounded agent — identically to local
  behavior.
- **FR-011**: Phoenix tracing MUST reuse the same Postgres instance as the application (no separate
  datastore), and a tracing failure MUST NOT take down the cook-facing app.
- **FR-012**: The deployment MUST stay within the approved lean stack and MUST NOT introduce
  Kubernetes or sprawling infrastructure-as-code; orchestration remains docker-compose-style and the
  hosting platform's native configuration.
- **FR-013**: The deployed environment MUST be populated from a committed, pre-built seed corpus
  (already categorized and embedded), loaded at deploy time, so the demo scenario returns real
  retrieved results. The corpus ingestion pipeline MUST NOT run in production; local and production
  hold identical seed data.
- **FR-014**: On missing/unseeded secrets or missing datastore credentials, the stack MUST fail fast
  with a clear, actionable error rather than starting degraded or insecure.
- **FR-015**: The released commit — the one live at the public URL and reproducible from a fresh
  clone — MUST be marked with the version tag `v0.1.0`.
- **FR-016**: The demo scenario MUST be defined and documented so it can be run identically against
  the live URL and a local bring-up to verify acceptance.

### Key Entities *(include if feature involves data)*

- **Release**: A specific, citable version of the system marked `v0.1.0`, corresponding to one commit
  that is simultaneously live at the public URL and reproducible from a fresh clone.
- **Deployment Environment**: A running instance of the full stack (local from fresh clone, or hosted
  at the public URL), each behaving identically with respect to safety and grounding.
- **Secret**: An application credential (model/provider key, service token) held only in Vault, never
  in repo/image/env, distinct from platform-injected managed datastore credentials.
- **Quality Gate**: A pass/fail check (lint, tests, evals, red-team, redaction) whose collective green
  status is the precondition for updating the public deployment.
- **Runbook**: The written procedure for seeding secrets and bringing up the stack locally and on the
  platform, including failure recovery.
- **Demo Scenario**: The defined end-to-end cook journey used to verify acceptance on both the live
  URL and a local bring-up.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A first-time visitor can complete the entire demo scenario on the public HTTPS URL with
  a 100% step-completion rate and zero safety-wall or grounding violations.
- **SC-002**: 100% of production deployments correspond to a commit that passed every quality gate;
  zero deployments originate from a failing main.
- **SC-003**: On a machine that has never run the project, an operator following the runbook reaches a
  fully running stack with one bring-up command in under 30 minutes, excluding download time.
- **SC-004**: An inspection of the released image and repository finds zero application secrets; 100%
  of application secrets are served from Vault and 100% of managed datastore credentials come from
  platform injection.
- **SC-005**: A reviewer unfamiliar with the project can, using only the documentation, describe the
  architecture, cite at least one decision with its supporting numbers, state the eval gates and
  their latest results, explain the security model, and reproduce the stack — without contacting the
  author.
- **SC-006**: The live URL and a local fresh-clone bring-up produce identical safety behavior on the
  demo scenario (same wall enforcement, same grounding, same redaction), with zero observed
  divergence.
- **SC-007**: A `v0.1.0` tag exists and unambiguously identifies the single commit that is both live
  at the public URL and reproducible locally.
- **SC-008**: Phoenix tracing runs against the same Postgres as the application with zero additional
  datastores provisioned, and an induced tracing outage leaves the cook-facing app fully functional.

## Assumptions

- The hosting platform is Railway (per the constitution and stack), providing the public HTTPS
  endpoint, certificate, environment-variable injection for managed datastores, and a deploy that is
  driven from the main branch.
- The "single command" for local bring-up is the project's existing `make up`-style entry point (with
  `make seed` covering secret/corpus seeding per the runbook); the exact commands are fixed in the
  runbook.
- The quality gates that gate the deploy are the existing `make lint`, `make test`, and `make evals`
  suites (including the red-team and redaction gates) defined in the constitution and `CLAUDE.md`.
- The "demo scenario" is the canonical cook journey: chat for ideas → real retrieved recipe cards →
  open a card for verbatim stored steps → build a varied meal plan → generate a shopping list → save a
  favorite, exercised with an allergy/diet constraint to demonstrate the wall.
- The managed datastores injected by the platform are PostgreSQL (with pgvector) and Redis; Vault
  holds application secrets and is itself reachable by the deployed app.
- Documentation lives in the repository (README and the existing `projectplanFolderForMd/` and
  `specs/` artifacts) and is the canonical reference for design, decisions, evals, security, and
  runbook.
- "Numbers" in the documentation are drawn from committed eval results, corpus/model sizes, and
  measured latencies already produced by earlier features.
- This release targets the existing feature set already built (foundation, catalog/wall/favorites,
  intelligent behavior, evals/UIs, corpus data quality); it adds deployment, reproducibility, and
  documentation rather than new cook-facing features.
