# Contract: Deployment Topology

Defines what runs where, what is public, and the health/promotion contract. Verifies FR-001, FR-001a,
FR-011, FR-012, SC-001, SC-006, SC-008.

## Services & exposure

| Unit | Public? | Health/readiness | Must NOT |
|------|---------|------------------|----------|
| `backend` (API) | Yes (API origin) | `GET /health` → 200 only when Postgres+Redis+Vault reachable | start with unseeded Vault or unmigrated DB (fail fast) |
| `widget` (static) | Yes (advertised URL) | nginx serves `index.html`; built against the public backend origin | be served from a non-HTTPS origin |
| `dashboard` (Streamlit) | No — operator-gated, unadvertised | reachable only behind cookie auth | appear on the public URL |
| `phoenix` (tracing UI) | No — operator-gated, unadvertised | UI loads; writes to `phoenix` schema | take the cook app down if it fails |
| Postgres / Redis / Vault | No — private network | plugin/service health | have public ingress |

## Promotion contract (FR-002 health gate)
- A new `backend` deployment is **promoted only when `/health` returns 200** within
  `healthcheckTimeout`; otherwise the rollout holds and the previous green deployment stays live.
- Startup order (prod backend): `alembic upgrade head` → serve. Corpus is already present on the
  persistent Postgres after the first-deploy load; Vault is already seeded (persistent volume).

## Shared-Postgres contract (FR-011 / SC-008)
- Phoenix uses the **same** Postgres as the app, isolated in the `phoenix` schema
  (`PHOENIX_SQL_DATABASE_SCHEMA=phoenix`); the app uses `public`. Zero additional datastores provisioned.
- A Phoenix/tracing outage leaves `/health` and the cook journey fully functional (tracing is
  non-blocking on the request path).

## Parity contract (FR-007 / SC-006)
- Every deployed unit has a local `docker-compose.yml` counterpart; local and prod produce identical
  safety behavior (wall, grounding, redaction) on the demo scenario.

## No-sprawl contract (FR-012)
- Orchestration is docker-compose locally + Railway native per-service config (one TOML each). **No**
  Kubernetes, Helm, or Terraform/IaC.
