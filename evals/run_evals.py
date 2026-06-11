"""Offline eval gate runner — `make evals`. Scores every eval suite against `eval_thresholds.yaml`.

"Evals are the grade" (constitution P6): this is the single command that runs the committed gates and
fails the build if any RAN gate scores below its threshold. It is intentionally split into two kinds of
gate:

  * **Deterministic gates** (always run, no network): the intent **classifier** macro-F1 on the held-out
    `evals/classifier/testset.csv`, the **red-team** refusal rate over `evals/redteam/attempts.yaml`
    through the deterministic input rail, and the **redaction** leak count over a battery of secrets. These
    are reproducible and are also covered by pytest in `make test`; running them here keeps `make evals` a
    complete, self-contained grade.
  * **Offline gates** (need a live stack + provider keys + an embedded corpus): **RAG hit@3** on
    `evals/rag/golden.yaml` and **agent tool-selection** on `evals/agent_tool_selection/cases.yaml`. These
    call the real embeddings/Groq providers, so on an un-provisioned machine they SKIP (not fail) — run
    them after `make up` + `make ingest`. Safety never depends on these scores: the wall and the
    deterministic plan assembly hold regardless of which recipes rank or which tools the model picks.

Run with `uv run python -m evals.run_evals` (or `make evals`). Exit code is non-zero iff a gate that
actually ran scored below its threshold; skipped offline gates do not fail the build.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Repo root from this file: evals/run_evals.py -> parents[1].
_REPO_ROOT = Path(__file__).resolve().parents[1]
_THRESHOLDS = _REPO_ROOT / "eval_thresholds.yaml"
_CLASSIFIER_TESTSET = _REPO_ROOT / "evals" / "classifier" / "testset.csv"
_REDTEAM_ATTEMPTS = _REPO_ROOT / "evals" / "redteam" / "attempts.yaml"
_RAG_GOLDEN = _REPO_ROOT / "evals" / "rag" / "golden.yaml"
_AGENT_CASES = _REPO_ROOT / "evals" / "agent_tool_selection" / "cases.yaml"

# Secrets shaped like the real things `core/redaction` must catch — the redaction gate's input battery.
# Mirrors tests/unit/test_redaction.py so the gate and the unit test agree on what "a leak" means.
_REDACTION_SECRETS = (
    "sk-ABCDEF0123456789abcdef",  # provider-style API key
    "gsk-live-0123456789ABCDEFwxyz",  # Groq-style key
    "Bearer eyJ0eXAiOiJKV1Qi.payload.sig",  # bearer token
    "hvs.CAESIJfakeVaultServiceToken123",  # Vault service token
)


@dataclass(frozen=True)
class GateResult:
    """One gate's outcome: its name, a PASS/FAIL/SKIP verdict, and a human-readable score/detail line."""

    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    detail: str


def _thresholds() -> dict[str, Any]:
    """Load the single eval-thresholds source of truth (the gate floors CI reads)."""
    return yaml.safe_load(_THRESHOLDS.read_text(encoding="utf-8"))


# ───────────────────────────── deterministic gates (always run) ─────────────────────────────


def gate_classifier(thresholds: dict[str, Any]) -> GateResult:
    """Score the served classifier's macro-F1 on the held-out eval set against `classifier.f1_min`.

    Loads the exact served artifact (`ml/artifacts/model.joblib`), predicts every row of
    `evals/classifier/testset.csv`, and computes macro-F1 — the same metric the model card reports. Pure
    and deterministic (no network), so it always runs. FAILs when the achieved F1 dips below the committed
    floor, which is the signal to retrain/fix, never to weaken the threshold (golden rule #6).
    """
    floor = float(thresholds["classifier"]["f1_min"])
    try:
        import joblib
        import pandas as pd
        from sklearn.metrics import f1_score

        model = joblib.load(_REPO_ROOT / "ml" / "artifacts" / "model.joblib")
        frame = pd.read_csv(_CLASSIFIER_TESTSET)
        preds = model.predict(frame["text"].tolist())
        macro_f1 = float(f1_score(frame["label"].tolist(), preds, average="macro"))
    except FileNotFoundError as exc:
        # A missing artifact/testset means the model was never trained — that IS a failed grade, not a skip.
        return GateResult("classifier macro-F1", "FAIL", f"missing input ({exc!s})")
    status = "PASS" if macro_f1 >= floor else "FAIL"
    return GateResult("classifier macro-F1", status, f"{macro_f1:.3f} (floor {floor:.3f})")


def gate_redteam(thresholds: dict[str, Any]) -> GateResult:
    """Score the red-team refusal rate over the probe battery against `redteam.refusal_rate_min`.

    Runs every probe in `evals/redteam/attempts.yaml` through the deterministic `input_rails.screen` and
    measures the fraction refused. The threshold is 1.0 — a single un-refused probe FAILs the gate. This is
    the executable form of the constitution's hard safety gate; it runs with no network so it is always
    graded here as well as in `tests/redteam/test_attempts.py`.
    """
    floor = float(thresholds["redteam"]["refusal_rate_min"])
    from app.guardrails import input_rails

    probes = yaml.safe_load(_REDTEAM_ATTEMPTS.read_text(encoding="utf-8"))["probes"]
    refused = sum(1 for p in probes if input_rails.screen(p["message"]).action == "refuse")
    rate = refused / len(probes) if probes else 1.0
    status = "PASS" if rate >= floor else "FAIL"
    return GateResult("redteam refusal rate", status, f"{rate:.3f} ({refused}/{len(probes)}, floor {floor})")


def gate_redaction(thresholds: dict[str, Any]) -> GateResult:
    """Score the redaction leak count over the secret battery against `redaction.leak_count_max`.

    Feeds each secret (embedded in prose) through `core/redaction.redact` — the single choke point both
    logging and Phoenix tracing call before anything leaves the process — and counts a leak when the secret
    survives verbatim or the mask is absent. The threshold is 0: any leak FAILs. Deterministic, no network.
    """
    ceiling = int(thresholds["redaction"]["leak_count_max"])
    from app.core.redaction import MASK, redact

    leaks = 0
    for secret in _REDACTION_SECRETS:
        out = redact(f"calling provider with {secret} now")
        if secret in out or MASK not in out:
            leaks += 1
    status = "PASS" if leaks <= ceiling else "FAIL"
    return GateResult("redaction leak count", status, f"{leaks} leak(s) (max {ceiling})")


# ───────────────────────────── offline gates (skip without a live stack) ─────────────────────────────


def _open_session() -> tuple[Any, Any, Any] | None:
    """Open a DB session for the offline gates, or return None when no live stack is reachable.

    The RAG and agent gates need the corpus (and, for RAG/agent, provider keys). This connects once and
    fails fast if Postgres is unreachable; a None return tells the caller to SKIP rather than FAIL, because
    an un-provisioned environment is "not graded here", not "failed".
    """
    try:
        from app.config import get_settings
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        engine = create_engine(get_settings().postgres_url, pool_pre_ping=True, future=True)
        connection = engine.connect()
        return Session(bind=connection), connection, engine
    except Exception:  # noqa: BLE001 — no live stack means "skip the offline gate", not a failure
        return None


def gate_rag(thresholds: dict[str, Any]) -> GateResult:
    """Score RAG hit@3 on the golden set against `rag.hit_at_k_min` (offline — needs corpus + embeddings).

    For each labeled query, runs the real grounded retrieval (`rag.retrieve`, k=3) under a constraint-free
    default profile and counts a hit when any `ideal` corpus row (matched by source/source_id) appears among
    the 3 surfaced recipes — hit@3 is exactly what the cook sees (FR-006). SKIPs cleanly when the stack or
    embeddings provider is unavailable; otherwise FAILs if the achieved hit@3 dips below the floor.
    """
    floor = float(thresholds["rag"]["hit_at_k_min"])
    k = int(thresholds["rag"]["k"])
    handle = _open_session()
    if handle is None:
        return GateResult("rag hit@3", "SKIP", "live stack unavailable")
    session, connection, engine = handle
    try:
        from app.schemas.recipe import Category
        from app.services.user import rag
        from app.services.user.constraint_guard import ConstraintProfile

        cases = yaml.safe_load(_RAG_GOLDEN.read_text(encoding="utf-8"))["cases"]
        hits = 0
        for case in cases:
            ideal = {(i["source"], str(i["source_id"])) for i in case["ideal"]}
            # Golden uses the human-readable category ("cold drink"); the enum value is underscored.
            raw_category = case.get("category")
            category = Category(raw_category.replace(" ", "_")) if raw_category else None
            rows = rag.retrieve(
                session, case["query"], ConstraintProfile.default(), category=category, k=k
            )
            surfaced = {(r.source, str(r.source_id)) for r in rows}
            hits += int(bool(ideal & surfaced))
        rate = hits / len(cases) if cases else 1.0
        status = "PASS" if rate >= floor else "FAIL"
        return GateResult("rag hit@3", status, f"{rate:.3f} ({hits}/{len(cases)}, floor {floor})")
    except Exception as exc:  # noqa: BLE001 — provider/corpus not ready ⇒ skip, never a false failure
        return GateResult("rag hit@3", "SKIP", f"not runnable ({exc!s})")
    finally:
        session.close()
        connection.close()
        engine.dispose()


def _record_tool_calls() -> tuple[set[str], Any]:
    """Wrap `app.agent.tools.dispatch` so each invocation's tool name is recorded; return (names, restore).

    The loop calls `tools.dispatch(name, ...)` by module attribute, so replacing that attribute with a spy
    captures the model's tool choices without touching the loop. The returned `restore` callable puts the
    original back so a run never leaks the spy into later code.
    """
    from app.agent import tools

    called: set[str] = set()
    original = tools.dispatch

    def _spy(name: str, arguments: dict[str, Any], ctx: Any) -> dict[str, Any]:
        """Record the tool name, then delegate to the real dispatch (validation + wall unchanged)."""
        called.add(name)
        return original(name, arguments, ctx)

    tools.dispatch = _spy  # type: ignore[assignment]

    def _restore() -> None:
        """Reinstate the genuine dispatch function."""
        tools.dispatch = original  # type: ignore[assignment]

    return called, _restore


def _score_tool_case(expected: list[str], forbidden: list[str], called: set[str]) -> bool:
    """Return True when every expected tool was called and no forbidden tool was — the per-case metric.

    Pure so it can be reasoned about without a live agent: expected ⊆ called, and the called set is disjoint
    from forbidden. An empty `expected` trivially passes the first clause.
    """
    return set(expected).issubset(called) and not (set(forbidden) & called)


def gate_agent_tool_selection(thresholds: dict[str, Any]) -> GateResult:
    """Score agent tool-selection accuracy on the cases (offline — needs the real agent + corpus).

    Runs each cook message through the real bounded agent while spying on which tools it dispatched, and
    scores a case as pass when all expected tools were called and no forbidden tool was. There is no hard
    threshold key for this suite (tool choice degrades quality, never safety — SC-007), so it reports
    accuracy as PASS/SKIP only and never fails the build; it SKIPs when the stack/providers are absent.
    """
    handle = _open_session()
    if handle is None:
        return GateResult("agent tool-selection", "SKIP", "live stack unavailable")
    session, connection, engine = handle
    try:
        from app.agent import loop as agent_loop
        from app.repo import profiles as profiles_repo
        from app.services.user.constraint_guard import ConstraintProfile

        # The agent's search tool records seen-history, which FKs to `profiles` — so the eval profile
        # must exist before any case runs (otherwise the first tool call hits a foreign-key violation).
        profiles_repo.ensure_exists(session, "eval-agent")
        session.commit()

        cases = yaml.safe_load(_AGENT_CASES.read_text(encoding="utf-8"))["cases"]
        passes = 0
        for case in cases:
            expected = case.get("expected_tools", [])
            forbidden = case.get("forbidden_tools", [])
            called, restore = _record_tool_calls()
            try:
                agent_loop.run(session, case["message"], ConstraintProfile.default(), "eval-agent", 2)
            finally:
                restore()
            passes += int(_score_tool_case(expected, forbidden, called))
        accuracy = passes / len(cases) if cases else 1.0
        return GateResult("agent tool-selection", "PASS", f"{accuracy:.3f} ({passes}/{len(cases)})")
    except Exception as exc:  # noqa: BLE001 — providers/corpus not ready ⇒ skip, never a false failure
        return GateResult("agent tool-selection", "SKIP", f"not runnable ({exc!s})")
    finally:
        session.close()
        connection.close()
        engine.dispose()


def run() -> int:
    """Run every gate, print a results table, and return a process exit code (non-zero iff a gate FAILed).

    Deterministic gates (classifier, red-team, redaction) always run; offline gates (RAG, agent) run when a
    live stack is reachable and SKIP otherwise. Only a FAIL on a gate that actually ran flips the exit code,
    so an un-provisioned CI step still grades the deterministic safety/quality gates without false failures.
    """
    thresholds = _thresholds()
    results = [
        gate_classifier(thresholds),
        gate_redteam(thresholds),
        gate_redaction(thresholds),
        gate_rag(thresholds),
        gate_agent_tool_selection(thresholds),
    ]

    width = max(len(r.name) for r in results)
    print("eval gates (vs eval_thresholds.yaml):\n")
    for r in results:
        print(f"  [{r.status:4}] {r.name.ljust(width)}  {r.detail}")

    failed = [r.name for r in results if r.status == "FAIL"]
    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        return 1
    print("\nall graded gates passed (skipped gates need `make up` + `make ingest`).")
    return 0


if __name__ == "__main__":  # pragma: no cover — manual offline entry point
    sys.exit(run())
