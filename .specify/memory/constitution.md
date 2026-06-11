<!--
SYNC IMPACT REPORT
==================
Version change: (template, unversioned) → 1.0.0
Bump rationale: Initial ratification of the Sous-Chef constitution from the template.
  First concrete adoption of all principles and governance, so MAJOR baseline 1.0.0.

Principles (template slot → adopted name):
  [PRINCIPLE_1_NAME] → I. Simplicity Over Complexity
  [PRINCIPLE_2_NAME] → II. Build Only What Is Required
  [PRINCIPLE_3_NAME] → III. Clear Separation of Concerns
  [PRINCIPLE_4_NAME] → IV. Testability
  [PRINCIPLE_5_NAME] → V. Reproducibility
  (expanded beyond 5 template slots, per user-supplied ten-principle set)
  VI. Security & Privacy by Default
  VII. Maintainability
  VIII. Documentation-First Development
  IX. Spec-Driven Development
  X. No Unnecessary Technologies or Features

Added sections:
  - Non-Negotiable Safety Invariants (new top-level section)
  - Technology & Stack Constraints (SECTION_2)
  - Development Workflow & Quality Gates (SECTION_3)

Removed sections: none (template placeholders fully replaced)

Templates requiring updates:
  ✅ .specify/templates/plan-template.md — Constitution Check section reviewed, generic gate language compatible
  ✅ .specify/templates/spec-template.md — reviewed, no constitution-specific edits required
  ✅ .specify/templates/tasks-template.md — reviewed, no constitution-specific edits required
  ✅ CLAUDE.md — Golden Rules already mirror these principles; consistent
  ⚠ .specify/templates/commands/*.md — directory not present in this install; no action

Follow-up TODOs: none
-->

# Sous-Chef Constitution

Sous-Chef is an AI recipe-discovery assistant for home cooks. This constitution is the
**highest-priority artifact** in the project. Every spec, plan, task, and line of code MUST
conform to it. Where any other document conflicts with this constitution, this constitution wins;
where a feature conflicts with a principle, the principle wins and the feature is cut or reshaped.

## Core Principles

### I. Simplicity Over Complexity
Prefer the simplest design that satisfies the requirement. This is a solo, two-week, junior-level
project, and complexity is the primary risk to shipping. The system MUST be a monolith, not
microservices; vectors live in pgvector inside Postgres, not a dedicated vector store; orchestration
is docker-compose, not Kubernetes; there is exactly one agent. When two designs both work, the
smaller one is correct.

### II. Build Only What Is Required
Implement exactly the MVP features defined in the active specification — nothing speculative. Scope
creep turns two-week projects into six-week projects. Features are frozen to the spec; new ideas go
to a "Future" list, never silently into this build. A task that is not traceable to a current
requirement MUST NOT be implemented.

### III. Clear Separation of Concerns
Each module has one job and layers do not reach around each other. The dependency direction is
strictly `api → services → repo → infra`. The `repo/` layer is the ONLY place that touches the
database. Services are split by audience into `services/user/` and `services/admin/`, mirrored in
`api/user/` and `api/admin/`. Files and folders MUST use clear, descriptive names that state their
single purpose. Auditable boundaries are what make the safety wall and grounding provable.

### IV. Testability
Every critical behavior has an automated test, and safety behaviors are gated in CI. "It worked in
the demo" is not evidence. Adapters MUST be mockable so external services can be faked in tests. The
constraint guard (the wall), freshness, redaction, and shopping-list math MUST be unit-tested. The
red-team gate (allergen-override + injection/jailbreak) and the redaction gate MUST pass in CI;
a regression in either blocks merge.

### V. Reproducibility
Anyone can clone the repo and run it identically; behavior is deterministic where it should be.
`docker-compose up` from a fresh clone MUST bring up the full stack. Schema changes go through
Alembic migrations. Dependencies are pinned (uv lockfile). Eval thresholds are committed to the
repo. The served classifier model is SHA-pinned. "Works on my laptop" disqualifies a change.

### VI. Security & Privacy by Default
Safe is the default; you opt into exposure, never into protection. The chat box is public, untrusted
input and cooks paste personal data. Therefore: all secrets live in Vault (never in `.env`, code, or
images); PII redaction runs before logs AND before any trace span is emitted; guardrails screen
input and output; all database access uses parameterized queries / ORM (injection-safe); the agent
loop is bounded in iterations and tokens.

### VII. Maintainability
Code MUST be readable, consistent, and changeable by a newcomer, because the author must answer for
any line on demand. Structure is consistent across the codebase; prompts live in `prompts/` and are
never hardcoded inline; lint and type-check are enforced; files are small and single-purpose.

### VIII. Documentation-First Development
Write the spec/contract before the code, and keep docs in sync with reality. A spec written first is
a thinking tool; a drifted doc is a lie. Specs precede code. When code and spec disagree, fix the
spec first (or regenerate from it) rather than letting them diverge.

### IX. Spec-Driven Development
The specification — not the code — is the source of truth, driven via SpecKit. Each phase runs
`specify → plan → tasks → implement`, and all artifacts are committed alongside the code. No vibe
coding: every line is generated against a reviewed task and owned by the author.

### X. No Unnecessary Technologies or Features
Every technology MUST earn its place by solving a stated problem. Résumé-driven dependencies bloat
the build and the attack surface. Specifically prohibited: `torch`/`transformers` in any container;
any dedicated vector database; Kubernetes; blob storage; full end-user authentication. Adding a
dependency requires a requirement behind it.

## Non-Negotiable Safety Invariants

These invariants are absolute and override convenience, performance, or feature goals:

- **The wall is the grade.** The system MUST NEVER surface a recipe that violates the cook's stated
  allergy or diet. This is enforced in deterministic code (`services/user/constraint_guard.py`) on
  every output path — never in a prompt, never by a model.
- **Ground everything.** The system MUST NEVER invent recipes or steps. Recipe lists come from
  retrieval; detail views render the recipe's stored steps verbatim. When no safe match exists, the
  system returns an honest empty result, never a fabricated one.
- **Hosted inference only.** The LLM and embeddings are hosted-API calls. No model weights are
  loaded into the application or any image for generation or embedding.
- **Lean classifier serving.** The intent classifier is trained offline and served via `joblib`
  only — no `torch`, no `transformers` at serve time.

## Technology & Stack Constraints

The approved stack is the lean set required by the principles above: FastAPI + Pydantic; PostgreSQL
with pgvector; Redis; HashiCorp Vault; Groq (chat-only LLM) with embeddings from a separate hosted
provider; scikit-learn + joblib for the classifier; deterministic in-process guardrails (regex
input/output rails — no framework dependency) + Presidio for PII; Arize Phoenix (self-hosted,
OpenTelemetry) for tracing; React + plain JavaScript/JSX
(no TypeScript) for the widget; Streamlit + streamlit-authenticator for the dashboard; Docker /
docker-compose; Railway for deployment; SpecKit for the lifecycle. Python dependencies are managed
with `uv` only (never `pip`), grouped so each image stays lean, and no image contains `torch`.
Introducing any technology outside this set requires a constitution amendment.

## Development Workflow & Quality Gates

Work proceeds one phase at a time. Per phase: `/speckit.specify → /speckit.plan → /speckit.tasks →
/speckit.implement`, with artifacts committed beside the code. Before any change is considered done,
`make lint && make test && make evals` MUST all be green, including the red-team gate and the
redaction test, and the change MUST be verified in the running stack (`make up`). Eval thresholds are
never weakened to make CI pass; if a gate fails, the cause is fixed. Every change must be traceable
to a requirement in the active spec.

## Governance

This constitution supersedes all other practices, specs, plans, tasks, and code. Amendments MUST be
made by editing this file, documenting the change in the Sync Impact Report at the top, and bumping
the version per the policy below. Every spec, plan, task list, and code review MUST verify compliance
with these principles; any deviation MUST be justified in writing or rejected. Complexity that
appears to violate Principle I or X MUST be explicitly justified against a stated requirement before
it is accepted.

Versioning policy (semantic):
- **MAJOR**: backward-incompatible governance changes, or removal/redefinition of a principle.
- **MINOR**: a new principle or section is added, or guidance is materially expanded.
- **PATCH**: clarifications, wording, and non-semantic refinements.

Compliance is reviewed at every SpecKit stage gate and at code review. Runtime development guidance
for agents lives in `CLAUDE.md`, which MUST stay consistent with this constitution; if they conflict,
this constitution wins and `CLAUDE.md` is corrected.

**Version**: 1.0.0 | **Ratified**: 2026-06-08 | **Last Amended**: 2026-06-08
