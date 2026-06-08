# Feature Specification: Foundation — Runnable, Reproducible, Secure Skeleton

**Feature Branch**: `001-foundation`

**Created**: 2026-06-08

**Status**: Draft

**Input**: User description: "Establish the Sous-Chef foundation: a runnable, reproducible, secure-by-default monolith skeleton. WHAT: a FastAPI service that boots, exposes GET /health, connects to Postgres (with pgvector), Redis, Vault, and a tracing backend, and is deployable to a public URL. It must read all secrets from Vault (never env/code), emit a trace for each request, and come up cleanly with one command from a fresh clone. WHY: reproducibility (P5) and security/privacy by default (P6) must exist from the first commit, and observability is cheaper to wire early than to retrofit."

## Clarifications

### Session 2026-06-08

- Q: How should Vault run in the local/dev stack, given one-command startup must not require manual unseal? → A: Vault dev mode locally — auto-unsealed, in-memory, fixed root token, secrets seeded on boot by a script; production-mode hardening is deferred to the deployment phase.
- Q: What should the CI smoke test bring up? → A: The full stack (Postgres+pgvector, Redis, Vault-dev, Phoenix) via CI service containers; the smoke test asserts /health reports healthy, verifying the real readiness contract end-to-end.
- Q: One health endpoint or split liveness/readiness probes? → A: A single /health readiness endpoint that verifies dependency reachability, used by both operators and the deploy platform; no separate liveness probe in this phase.

## User Scenarios & Testing *(mandatory)*

The actors here are the **developer-operator** who builds and runs the system and the **grader** who
clones it to evaluate it. This phase ships no cook-facing product behavior; it establishes the ground
the product is built on. Each story below is an independently demonstrable slice of "the skeleton works."

### User Story 1 - One-command local startup from a fresh clone (Priority: P1)

A developer-operator clones the repository to a machine that has never seen the project, runs a single
command, and the complete backing environment comes up: the application plus its database, cache, secret
store, and tracing backend. Nothing needs hand-editing, no missing-step surprises.

**Why this priority**: Reproducibility is a constitutional non-negotiable (P5) and the precondition for
every later phase. If a fresh clone cannot come up with one command, nothing else can be evaluated. This
is the smallest slice that delivers standalone value: a runnable environment.

**Independent Test**: On a clean checkout with no prior local state, run the single documented startup
command and confirm all services reach a running/healthy state without manual intervention.

**Acceptance Scenarios**:

1. **Given** a fresh clone with no pre-existing containers or volumes, **When** the operator runs the
   single documented startup command, **Then** the application service and the database, cache, secret
   store, and tracing services all start and report healthy.
2. **Given** the stack is running, **When** the operator stops and restarts it with the same command,
   **Then** it comes back up cleanly without manual repair.
3. **Given** a machine missing required configuration values, **When** startup is attempted, **Then** the
   failure is reported with a clear, actionable message rather than a silent or cryptic crash.

### User Story 2 - Health endpoint confirms the service and its dependencies are reachable (Priority: P1)

The application exposes a single health check that an operator (or an automated platform) can call to
confirm the service is up and able to reach its critical dependencies. This same check is what the
hosting platform uses to decide whether a deployment is healthy.

**Why this priority**: A health signal is the contract between the app and any orchestrator or deploy
platform; the deploy gate in Story 4 depends on it. It is independently testable the moment the app boots.

**Independent Test**: With the stack running, call the health endpoint and confirm a success response;
with a critical dependency made unavailable, confirm the health signal reflects the degraded state.

**Acceptance Scenarios**:

1. **Given** the application and its dependencies are running, **When** the health endpoint is called,
   **Then** it returns a success status indicating the service is ready.
2. **Given** a critical dependency is unreachable, **When** the health endpoint is called, **Then** the
   response distinguishes the unhealthy state from a healthy one (it does not falsely report healthy).
3. **Given** the deploy platform polls the health endpoint, **When** it returns success, **Then** the
   platform treats the deployment as live.

### User Story 3 - Secrets come only from the secret store, and never leak (Priority: P1)

Every secret the application needs (credentials, API keys) is read at runtime from the dedicated secret
store. No secret value is written into source code, into committed configuration, into container images,
or into any log line or trace span. An operator can inspect logs and traces and find no secret material.

**Why this priority**: Security & privacy by default is a constitutional non-negotiable (P6) and the chat
surface will later accept untrusted public input. Getting secret handling and redaction correct from the
first commit is far cheaper than retrofitting it.

**Independent Test**: Exercise a request that touches a secret-backed dependency, then inspect every log
line and emitted trace and confirm no secret value appears in cleartext; confirm the committed
example-config file contains only non-secret bootstrap values.

**Acceptance Scenarios**:

1. **Given** the application needs a credential, **When** it resolves that credential, **Then** the value
   is obtained from the secret store and never from committed code or configuration.
2. **Given** a request is processed, **When** its log lines and trace spans are written, **Then** no
   secret value appears in cleartext in either.
3. **Given** the repository is inspected, **When** the committed example-config file is reviewed, **Then**
   it contains only non-secret bootstrap values (such as service locations and the secret-store address),
   not any real secret.

### User Story 4 - Every request is observable, and a build deploys to a public URL (Priority: P2)

Each incoming request produces a trace in the tracing backend, so the operator can see request flow and
timing from the first commit. A build of the skeleton deploys to a publicly reachable URL, gated on the
health check, demonstrating the deployment path end-to-end before any product logic exists.

**Why this priority**: Observability is far cheaper to wire early than to retrofit, and proving the deploy
path now de-risks every later phase. It depends on Stories 1–3 being in place, so it is P2.

**Independent Test**: Send a request to the running service and confirm a corresponding trace appears in
the tracing backend; trigger a build and confirm it reaches a public URL whose health check passes.

**Acceptance Scenarios**:

1. **Given** the stack is running, **When** a request is sent to the service, **Then** a corresponding
   trace for that request is recorded in the tracing backend.
2. **Given** a recorded trace, **When** the operator inspects it, **Then** it contains no secret values
   (redaction has already been applied before the span was emitted).
3. **Given** a successful build, **When** it is deployed, **Then** the service is reachable at a public
   HTTPS URL and the platform's health check against it passes.

### Edge Cases

- **A backing service is slow or down at startup.** The application's readiness signal must reflect the
  dependency problem rather than reporting healthy; startup ordering must not produce a false-healthy app.
- **A required configuration value is missing on a fresh machine.** Startup must fail fast with a clear,
  actionable message naming what is missing — never start in a half-configured state.
- **The secret store is unreachable when a secret is needed.** The system must surface a clear error and
  must not fall back to a hardcoded or environment-embedded secret.
- **A log line or trace would otherwise contain a secret or personal data.** Redaction must run before the
  line is logged and before the span is emitted, so the unredacted value never reaches storage.
- **Repeated startups accumulate state.** A restart must be clean and reproducible, not dependent on
  leftover local state from a previous run.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST start its complete local environment — the application plus its database,
  cache, secret store, and tracing backend — from a fresh clone using a single documented command.
- **FR-002**: The application MUST expose a single `/health` readiness endpoint that reports whether the
  service is ready, reflecting the reachability of its critical dependencies. This same single endpoint
  serves both operators and the deploy platform; no separate liveness probe is introduced in this phase.
- **FR-003**: The deployment platform MUST use the health endpoint as the healthcheck that gates whether a
  deployment is considered live.
- **FR-004**: The application MUST obtain every secret it needs from the dedicated secret store at runtime.
- **FR-005**: The system MUST NOT store any secret value in source code, committed configuration, or
  container images. The committed example-config file MUST contain only non-secret bootstrap values.
- **FR-005a**: In the local/dev stack, the secret store MUST run in an auto-unsealed development mode and
  be seeded with its secrets automatically on startup, so the one-command fresh-clone flow requires no
  manual unseal or seeding step. Production-mode (sealed, persistent) hardening is deferred to deployment.
- **FR-006**: The application MUST produce a trace for each incoming request in the tracing backend.
- **FR-007**: The system MUST redact secret and personal data before any log line is written AND before
  any trace span is emitted, so no such value appears in cleartext in logs or traces.
- **FR-008**: A build of the skeleton MUST deploy to a publicly reachable HTTPS URL whose health check
  passes.
- **FR-009**: Continuous integration MUST run linting, type-checking, and a smoke test that boots the
  application and exercises the health endpoint, and MUST be green for the change to be accepted. The
  smoke test MUST stand up the full backing environment (database, cache, secret store, tracing backend)
  and assert the health endpoint reports healthy, verifying the readiness contract end-to-end.
- **FR-010**: The system MUST fail fast with a clear, actionable error when required configuration is
  missing or a critical dependency is unreachable, rather than starting in a partially-configured state.
- **FR-011**: Database schema changes MUST be applied through versioned migrations so that a fresh
  environment reaches the same schema state deterministically.
- **FR-012**: The skeleton MUST contain no cook-facing or product business logic and MUST NOT include any
  heavyweight local model runtime; container images MUST be kept small.

### Key Entities *(include if feature involves data)*

- **Service environment**: The set of running pieces that constitute a working system — the application
  and its database, cache, secret store, and tracing backend — brought up together by one command.
- **Health status**: The readiness signal the application reports, derived from whether it can reach its
  critical dependencies; consumed by operators and the deploy platform.
- **Secret**: A credential or key the application needs, held only in the secret store and resolved at
  runtime; never persisted in code, config, images, logs, or traces.
- **Request trace**: The observability record produced for each request, recorded in the tracing backend
  after redaction.
- **Schema version**: The deterministic, migration-tracked state of the database structure.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A person who has never run the project can, from a fresh clone, bring the entire environment
  to a healthy state with one command and no manual file edits, in under 10 minutes on a typical laptop.
- **SC-002**: The health endpoint returns success within 1 second when the service and its dependencies
  are healthy, and reports an unhealthy state whenever a critical dependency is unavailable — with zero
  false-healthy responses across the test scenarios.
- **SC-003**: Across all logs and traces produced during the acceptance scenarios, the number of secret
  values appearing in cleartext is zero.
- **SC-004**: 100% of requests sent during testing produce a corresponding trace in the tracing backend.
- **SC-005**: The continuous-integration pipeline (lint, type-check, smoke test) passes on the change, and
  a build is reachable at a public HTTPS URL whose health check passes.
- **SC-006**: Restarting the stack with the same command returns it to a healthy state in 100% of attempts
  without manual repair.

## Assumptions

- The target runtime for local development provides a container engine capable of running multiple
  services together; the grader's machine meets the same baseline.
- The hosting platform provides managed datastore connectivity and HTTPS termination, and supports a
  health-check-gated deploy; bootstrap connection values are injected by the platform while application
  secrets live in the secret store.
- Locally the secret store runs in development mode (auto-unsealed, ephemeral, fixed bootstrap token) and
  is seeded on boot; this keeps startup to one command. The production deployment uses a hardened,
  persistent secret-store configuration introduced in the deployment phase.
- This phase deliberately excludes all cook-facing product behavior (no recipes, search, favorites, agent,
  or guardrails) and excludes any local model runtime; those arrive in later phases.
- "A trace per request" covers ordinary application requests; trivial infrastructure probes may be exempt
  from full tracing without violating the requirement, provided application requests are always traced.
- Redaction in this phase covers the categories of secret and personal data known to flow through the
  skeleton (credentials, keys); the redaction surface expands as later phases add real input paths.
