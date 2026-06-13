# `railway/` — per-service Railway configs

Railway runs SousChef as **one project with multiple services**. This directory holds one small TOML
per service — native platform config, **not** a new orchestration system (no Kubernetes / IaC sprawl,
FR-012). The root [`railway.toml`](../railway.toml) configures the **backend** service; the files here
configure the rest.

## Services (see [contracts/deployment-topology.md](../../specs/007-ship-public-deploy/contracts/deployment-topology.md))

| File            | Service   | Surface                                                              |
|-----------------|-----------|---------------------------------------------------------------------|
| `widget.toml`   | widget    | **Public** static host (Vite build → nginx); `VITE_API_BASE` = public backend origin |
| `dashboard.toml`| dashboard | **Operator-gated** Streamlit on a separate, unadvertised URL (FR-001a) |
| `phoenix.toml`  | phoenix   | **Operator-gated** tracing UI; shares the one Postgres (`phoenix` schema); non-blocking on `/health` |
| `vault.toml`    | vault     | HashiCorp Vault in **server mode** with a **persistent volume** (not dev mode), private network only |

The managed **PostgreSQL (pgvector)** and **Redis** plugins are added in the Railway dashboard, not here.
Public surface is **widget + backend API only**; everything else lives on unadvertised operator URLs.

Secrets posture: Railway variables hold **bootstrap/non-secret only** (Vault addr/token, platform-injected
Postgres/Redis URLs). Provider keys (Groq, embeddings) live **only in Vault**, seeded once into the
persistent prod Vault per the [RUNBOOK](../docs/RUNBOOK.md).
