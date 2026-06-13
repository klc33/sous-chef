# Feature Specification: Provable & Usable — Gated Evals + the Two UIs

**Feature Branch**: `004-evals-and-uis`

**Created**: 2026-06-11

**Status**: Draft

**Input**: User description: "Make Sous-Chef provable and usable: gated evaluations plus the two UIs.
Operator browses the corpus, runs eval suites on demand, inspects traces/cost, and a refresh does not
log them out. Cook uses a chat widget: pick a category, see recipe cards, click for full steps, save
favorites. Eval suites with committed thresholds gate CI (classifier macro-F1; agent tool-selection;
RAG hit@k/MRR/faithfulness/answer-relevancy; red-team allergen-override + injection/jailbreak ALL
refused; redaction; stack smoke). React widget is plain JS/JSX, talks only to the backend, attaches the
profile-ID header. Streamlit dashboard is login-protected with a cookie that survives refresh. Every
gate is enforced in CI and blocks merge on regression; both UIs work against the backend. No new runtime
dependencies beyond eval/test/UI; keep the wall and grounding tests as the hard gates."

## Overview

This feature makes Sous-Chef **provable** (the behavior of the assistant — its safety, grounding, and
quality — is measured by committed eval suites that gate merges) and **usable** (the two human surfaces
exist: the cook's chat widget and the operator's dashboard). It builds on the Phase 3 backend
(`/chat`, `/recipes`, `/favorites`, `/profile`, the wall, the agent, the classifier, retrieval) — it
does **not** add new cook-facing business logic. It populates the evaluation suites the prior phases left
as scaffolding, wires them into CI as merge-blocking gates, and ships the React widget and Streamlit
dashboard that talk to the existing backend.

## Clarifications

### Session 2026-06-11

- Q: RAG faithfulness/answer-relevancy — how computed, and is it a hard merge gate? → A: A frozen LLM
  judge scores faithfulness and answer relevancy; only **hit@k and MRR are hard merge gates**, while
  faithfulness and answer relevancy are report-only (measured and tracked, not gating) — keeping the
  merge gate deterministic and preserving wall/grounding/red-team/redaction as the hard gates.
- Q: Where do the operator dashboard's login credentials live? → A: The operator password hash and the
  cookie-signing key are resolved from **Vault** at startup via the existing secrets adapter and the auth
  config is assembled in memory — nothing sensitive is committed to the repo or baked into an image.
- Q: Canonical category representation for the widget? → A: The widget standardizes internally on the
  **underscored** values (`hot_drink`, `cold_drink`, `breakfast`, `lunch`, `dinner`) used by the catalog
  endpoints the chips call, maps them to display labels for the UI, and normalizes the spaced `/chat`
  form on input — isolating the wire discrepancy to one normalization point.
- Q: Is the agent tool-selection suite a merge gate? → A: No — it is **report-only** (measured and tracked
  every run, never blocks merge). Tool choice is a non-deterministic LLM decision that degrades quality,
  not safety (SC-007), so gating it would flake CI; this keeps the merge gate deterministic, the same
  rationale as the RAG faithfulness/answer-relevancy decision above.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Gated evaluations make the assistant provable (Priority: P1)

The operator (and CI) can run a committed set of evaluation suites with committed thresholds. The suites
measure classifier quality, agent tool-selection, RAG quality, safety (red-team), PII redaction, and a
full-stack smoke check. Every gate runs in CI on each change and **blocks merge** when a result regresses
below its committed threshold. The safety wall, grounding, red-team, and redaction checks are the hard
gates that can never be weakened or bypassed to make CI pass.

**Why this priority**: "The evals are the grade" and "the wall is the grade" are the project's defining
rules. Without committed, merge-blocking gates, no UI polish matters — a future change could silently
reopen the allergen hole or break grounding. This story is the proof that the safety and quality claims
are real, so it is the most critical slice and is independently valuable even before either UI exists.

**Independent Test**: Run the full eval command from a clean checkout; observe a pass/fail line per suite
versus its committed threshold. Force a regression (e.g., introduce one unrefused allergen-override
probe) and confirm the gate fails and would block merge; revert and confirm green.

**Acceptance Scenarios**:

1. **Given** a clean checkout, **When** the full evaluation run executes, **Then** each suite
   (classifier, agent tool-selection, RAG, red-team, redaction, stack smoke) reports a measured result
   compared against its committed threshold and an overall pass/fail.
2. **Given** the red-team suite, **When** every allergen-override and injection/jailbreak probe is
   evaluated, **Then** 100% are refused or filtered; a single unrefused probe fails the gate.
3. **Given** a fake secret pasted into a chat turn, **When** the redaction suite inspects the resulting
   logs and trace spans, **Then** the secret never appears unredacted in either; any leak fails the gate.
4. **Given** a change that drops classifier macro-F1 (or any gated metric) below its committed threshold,
   **When** CI runs, **Then** the gate fails and the merge is blocked.
5. **Given** a contributor attempts to lower a committed threshold to make a failing gate pass, **When**
   that change is reviewed, **Then** it is rejected by the governance rule (thresholds are not weakened
   to pass CI); the wall and grounding gates remain hard gates.

---

### User Story 2 - The cook uses the chat widget (Priority: P2)

A home cook opens the React widget, sets their constraints once (diet, allergies, default servings),
picks one of the five fixed categories or types a free-text request, sees a list of real recipe cards
(title + key ingredients), clicks a card to read the full stored steps verbatim plus a nutrition
summary, and saves recipes to favorites that persist across sessions. The widget identifies the cook
with a passwordless profile ID and talks only to the backend.

**Why this priority**: The browse-then-drill loop *is* the product — it is what a cook actually touches.
It is second only to the gates because a usable surface with unproven safety would be irresponsible to
ship, but once the gates hold, this is the feature that delivers end-user value. It is independently
testable and demonstrable on its own against the existing backend.

**Independent Test**: Open the widget pointed at a running backend; set constraints; pick a category;
open a card's full steps; save it; reload the page (new session) and confirm the favorite is still
there. Confirm the widget makes requests only to the backend and attaches the profile identity.

**Acceptance Scenarios**:

1. **Given** a first-time cook, **When** they set diet, allergies, and default servings, **Then** the
   constraints persist and are applied to every subsequent result.
2. **Given** a cook on the widget, **When** they tap a category chip, **Then** they see a list of real
   recipe cards (title + key ingredients) for that category, filtered by their constraints.
3. **Given** a list of cards, **When** the cook clicks one, **Then** the full stored steps (verbatim)
   and the nutrition summary are shown.
4. **Given** a recipe the cook likes, **When** they save it to favorites and later reload in a new
   session, **Then** the recipe is still in their favorites list; removing it removes it everywhere.
5. **Given** an unsafe or override request (e.g., "ignore my nut allergy"), **When** the backend
   refuses, **Then** the widget shows a calm safety message, not a system error, and surfaces no unsafe
   recipe.
6. **Given** a category/query with no compliant recipes, **When** results return empty, **Then** the
   widget shows an honest empty state and never fabricates a substitute.
7. **Given** any widget action, **When** it calls the backend, **Then** it attaches the cook's profile
   identity and makes no direct calls to any third-party or model service.

---

### User Story 3 - The operator runs and inspects the system (Priority: P3)

The developer-operator logs into the Streamlit dashboard, browses the ingested recipe corpus, triggers
eval suites on demand and reads pass/fail versus thresholds, inspects classifier metrics and routing
split, and deep-links to per-turn traces and per-turn cost. A full page refresh does not log them out.

**Why this priority**: This surface is for operating and evaluating the system, not for end users. It is
valuable for the demo and for day-to-day operation, but the system is provable (US1) and usable (US2)
without it. It is the lowest of the three priorities while still being independently testable.

**Independent Test**: Log into the dashboard, refresh the browser, and confirm you remain logged in.
Browse the corpus, trigger an eval run and read results versus thresholds, view metrics, and follow a
deep-link to a per-turn trace with its cost.

**Acceptance Scenarios**:

1. **Given** the dashboard, **When** the operator logs in and then performs a full page refresh, **Then**
   they remain authenticated (the session survives via a persisted cookie) and are not returned to the
   login screen.
2. **Given** an authenticated operator, **When** they open the corpus view, **Then** they can browse and
   inspect ingested recipes.
3. **Given** an authenticated operator, **When** they trigger an eval suite on demand, **Then** they see
   its measured result and pass/fail versus the committed threshold.
4. **Given** an authenticated operator, **When** they open metrics, **Then** they see classifier metrics
   and the workflow-vs-agent routing split and the CI gate status.
5. **Given** an authenticated operator, **When** they open a turn's trace, **Then** they reach the
   per-turn trace and its token cost (deep-linked to the trace tooling).
6. **Given** an unauthenticated visitor, **When** they request any dashboard page, **Then** access is
   denied until login; the public cook widget can never reach operator functions.

---

### Edge Cases

- **Forced regression**: a deliberately-broken safety probe or a metric pushed below threshold must turn
  the gate red and block merge — the gate's own correctness is part of the test.
- **Empty corpus / no compliant recipe**: the widget renders an honest empty state; eval suites still run
  and report (they do not error out on empty inputs they are designed for).
- **Backend unreachable / slow**: the widget shows loading and error states; a slow agent turn (planning)
  shows progress rather than appearing frozen; it never invents content to fill a gap.
- **Refusal vs. error**: a safe refusal (`refused=true`) is visually distinct from a network/system error
  in the widget.
- **Wall removes a saved favorite**: a previously-saved recipe that now violates the cook's constraints is
  omitted from the favorites list (server-side), and the widget explains the omission rather than showing
  a broken entry.
- **Refresh during an operator action**: a page refresh keeps the operator logged in and does not lose the
  selected corpus/eval view's basic navigability.
- **Category representation mismatch**: the widget standardizes on the underscored category values used by
  the catalog endpoints, maps them to display labels, and normalizes the spaced `/chat` form on input, so
  the two backend category spellings never reach the cook as raw tokens.
- **Threshold present but unmet on first real run**: a newly-populated suite whose real numbers fall short
  of a placeholder threshold fails honestly (the fix is the cause, not the threshold).

## Requirements *(mandatory)*

### Functional Requirements

#### Gated evaluations & CI (US1)

- **FR-001**: The system MUST provide a committed, version-controlled set of evaluation suites covering:
  intent-classifier quality, agent tool-selection correctness, RAG retrieval+generation quality, safety
  red-team, PII redaction, and full-stack smoke.
- **FR-002**: Each gated suite MUST have a committed threshold stored in the repository, and an evaluation
  run MUST compare each suite's measured result against its threshold and report pass/fail per suite plus
  an overall result.
- **FR-003**: The evaluation gates MUST run in CI on every change and MUST block merge whenever any gated
  suite regresses below its committed threshold.
- **FR-004**: The red-team suite MUST include allergen-override attempts and prompt-injection/jailbreak
  attempts; ALL probes MUST be refused or filtered (refusal rate = 100%). This is a hard gate.
- **FR-005**: The redaction suite MUST demonstrate that a pasted fake secret never appears unredacted in
  application logs OR in emitted trace spans. This is a hard gate.
- **FR-006**: The classifier suite MUST report macro-F1 (and per-class F1) on a held-out test set and gate
  on a committed macro-F1 minimum.
- **FR-007**: The RAG suite MUST report hit@k, MRR, faithfulness, and answer relevancy on a golden set.
  Hit@k and MRR MUST gate on committed minimums (deterministic hard gates). Faithfulness and answer
  relevancy MUST be computed by a frozen LLM judge and reported/tracked (report-only); they MUST NOT
  block merge, so the merge gate stays deterministic.
- **FR-008**: The agent tool-selection suite MUST verify, per case, that the agent selects the correct
  tool(s) or correctly selects none, and MUST report the measured pass rate. Because tool choice is a
  non-deterministic LLM decision that degrades quality rather than safety (SC-007), this suite is
  **report-only** — tracked each run but it does NOT block merge (keeping the merge gate deterministic).
- **FR-009**: The stack smoke suite MUST verify the full stack comes up clean from a fresh clone and
  answers a basic health/turn check.
- **FR-010**: The safety wall and grounding tests MUST remain hard gates; committed thresholds MUST NOT be
  weakened to make a failing gate pass — a failing gate is fixed at the cause.
- **FR-011**: Evaluation runs MUST be reproducible: the same inputs and committed thresholds yield the
  same pass/fail outcome, runnable identically locally and in CI.
- **FR-012**: Operators and contributors MUST be able to run the full evaluation set with a single
  documented command, both locally and as the CI gate.

#### Cook chat widget (US2)

- **FR-013**: The cook MUST be able to set their constraints (diet, allergies, default servings) and have
  them persist across sessions; these constraints drive the safety wall on every result.
- **FR-014**: The cook MUST be able to pick one of the five fixed categories (hot drink, cold drink,
  breakfast, lunch, dinner) and see a list of real recipe cards (title + key ingredients) for it.
- **FR-015**: The cook MUST be able to click a recipe card to view the full stored steps rendered verbatim
  plus the recipe's nutrition summary.
- **FR-016**: The cook MUST be able to converse in free text and receive grounded responses (recipe cards,
  a meal plan, a shopping list, or a substitution result) as returned by the backend turn, with no content
  invented client-side.
- **FR-017**: The cook MUST be able to save a recipe to favorites with one action, view their favorites
  list, and remove a favorite; favorites persist across sessions.
- **FR-018**: The widget MUST identify the cook with a passwordless profile identity generated and stored
  client-side, and MUST attach that identity on every backend request.
- **FR-019**: The widget MUST communicate ONLY with the Sous-Chef backend and MUST NOT call any
  third-party, model, or embedding service directly — grounding and safety stay server-side.
- **FR-020**: The widget MUST render a backend safe-refusal as a calm, distinct message (not a system
  error) and MUST surface no recipe the backend withheld.
- **FR-021**: The widget MUST handle an honest empty result (no compliant recipes) without fabricating
  substitutes, showing an empty/encouraging state instead.
- **FR-022**: The widget MUST be built as a plain JavaScript/JSX surface (no TypeScript), consistent with
  the project's stack constraints.
- **FR-023**: The widget MUST present distinct, non-frozen loading states for fast (search) and slow
  (planning) turns, and recoverable error states when the backend is unreachable.

#### Operator dashboard (US3)

- **FR-024**: The operator MUST be able to browse and inspect the ingested recipe corpus from the
  dashboard.
- **FR-025**: The operator MUST be able to trigger eval suites on demand and read each suite's pass/fail
  versus its committed threshold.
- **FR-026**: The operator MUST be able to view classifier metrics, the workflow-vs-agent routing split,
  and CI gate status.
- **FR-027**: The operator MUST be able to reach per-turn traces and per-turn cost via deep-links to the
  trace tooling.
- **FR-028**: The dashboard MUST be login-protected, and the authenticated session MUST survive a full
  page refresh via a persisted cookie (a refresh does not log the operator out). The operator password
  hash and the cookie-signing key MUST be resolved from Vault at startup via the existing secrets adapter
  (assembled in memory); they MUST NOT be committed to the repo or included in any image.
- **FR-029**: The dashboard MUST be a separate surface from the cook widget; the public widget MUST NOT be
  able to reach operator functions, and unauthenticated visitors MUST be denied dashboard access.

#### Cross-cutting constraints

- **FR-030**: This feature MUST NOT introduce new application runtime dependencies beyond those required
  for the evaluation/test tooling and the two UIs (eval/test libraries and the UI frameworks already in
  the approved stack); no image gains `torch`/`transformers` or any prohibited technology.
- **FR-031**: PII redaction MUST run before any data is written to logs or emitted to traces, reaffirmed
  and proven by the redaction gate (FR-005).
- **FR-032**: The two UIs MUST work against the existing backend contracts without changing the wall or
  grounding guarantees; the widget displays only what the backend (already wall-filtered) returns.

### Key Entities *(include if feature involves data)*

- **Eval Suite**: A named, committed collection of test cases for one capability (classifier, agent
  tool-selection, RAG, red-team, redaction, stack smoke), with the metric(s) it measures.
- **Eval Threshold**: The committed minimum (or required rate, e.g., 100% for red-team/redaction) a suite
  must meet to pass; lives in version control alongside the suites.
- **Eval Result / Report**: The measured outcome of a run per suite, the comparison to threshold, and the
  overall pass/fail used by CI to allow or block merge.
- **Cook Profile / Constraints**: The cook's diet, allergies, and default servings (from the existing
  profile contract), scoped to a passwordless profile identity; drives the wall.
- **Profile Identity**: The client-generated, passwordless identifier attached to every widget request,
  scoping favorites and seen-history only.
- **Recipe Card / Recipe Detail**: The wall-filtered card list (title + key ingredients) and the verbatim
  full-steps + nutrition detail returned by the backend.
- **Favorite**: A saved recipe association persisted per profile across sessions.
- **Operator Session**: The authenticated dashboard session, persisted in a cookie so it survives refresh.
- **Trace / Cost Summary**: The per-turn trace and token-cost view the operator deep-links into.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of red-team probes (allergen-override + injection/jailbreak) are refused or filtered;
  a single failing probe blocks merge.
- **SC-002**: In the redaction check, a pasted fake secret appears redacted in 100% of inspected log and
  trace outputs (zero unredacted leaks); any leak blocks merge.
- **SC-003**: Every gated eval suite has a committed threshold, and a forced regression below any
  threshold demonstrably turns the gate red and blocks merge.
- **SC-004**: Classifier macro-F1, RAG hit@k, and RAG MRR each meet or exceed their committed thresholds
  on the held-out/golden sets — these are the gated metrics. The agent tool-selection pass rate, RAG
  faithfulness, and RAG answer relevancy are measured and reported each run (report-only, not gating).
- **SC-005**: A first-time cook completes the core loop — set constraints → pick a category → open a
  recipe's full verbatim steps → save it → see it in favorites after a page reload — unaided.
- **SC-006**: A repeated category/query surfaces at least some new recipes (freshness is visible) until
  the matching pool is exhausted.
- **SC-007**: The cook widget makes zero direct calls to any service other than the Sous-Chef backend, and
  attaches the profile identity on 100% of requests.
- **SC-008**: A safe refusal and an empty result are both rendered without any fabricated recipe content.
- **SC-009**: The operator logs in and performs a full page refresh with zero unexpected logouts, then
  triggers each eval suite and reads its pass/fail versus threshold from the dashboard.
- **SC-010**: The operator can reach a per-turn trace with its token cost from the dashboard.
- **SC-011**: The full stack comes up clean from a fresh clone and passes the smoke check on the first
  documented run.
- **SC-012**: The full evaluation set runs identically with one documented command locally and in CI, and
  no new prohibited runtime dependency is added to any image.

## Assumptions

- **Backend is in place**: Phase 3 (`003-intelligent-behavior`) already provides `/chat`, `/recipes`,
  `/recipes/{id}`, `/favorites`, and `/profile`, the constraint guard (the wall), the classifier, the
  agent, retrieval/freshness, redaction, and tracing. This feature consumes those contracts and does not
  add new cook-facing business logic.
- **Suites populate existing scaffolding**: the `evals/` suites and `eval_thresholds.yaml` exist as
  scaffolding from prior phases; this feature populates them with real cases and sets real thresholds.
- **Threshold values**: committed thresholds start from defensible placeholders and are tightened to real
  measured numbers; red-team refusal and redaction are fixed at 100% (hard gates) and are never lowered.
- **RAG quality metrics**: faithfulness and answer relevancy are computed by a frozen LLM judge (eval
  tooling, not an application runtime dependency) and are report-only; only hit@k and MRR gate merges
  (see Clarifications). `k` for hit@k defaults to the number of cards the cook actually sees (top 3),
  reconciled with retrieval's over-fetch pool.
- **Category representation**: the two backend contracts spell categories differently (underscored in the
  recipes/favorites/profile contracts, spaced in the chat contract); the widget standardizes internally
  on the underscored values, maps them to human-readable labels for display, and normalizes the spaced
  `/chat` form on input (see Clarifications).
- **Operator auth**: the dashboard login is a single-operator, cookie-persisted login (no end-user account
  system); the operator password hash and cookie-signing key come from Vault at startup (see
  Clarifications), not from a committed config file or self-registration.
- **CI as the gate**: merge-blocking is enforced by the CI pipeline; "blocks merge" means the gated CI job
  must be green for the branch to merge.
- **Profile identity**: the widget generates and stores a passwordless profile ID client-side and sends it
  as the agreed identity header on every request; it is not a security boundary, only a scope for
  favorites and seen-history.

## Out of Scope

- New cook-facing features or backend business logic beyond wiring the existing contracts to the UIs.
- Real end-user authentication/accounts (passwordless profile ID only, per the constitution).
- Any new datastore, vector database, or prohibited technology; any `torch`/`transformers` in an image.
- Building the trace storage/UI itself (the dashboard deep-links to the existing self-hosted trace tool).
- Production hardening of the dashboard beyond cookie-persisted single-operator login.
