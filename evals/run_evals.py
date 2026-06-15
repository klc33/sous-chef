"""Offline eval gate runner — `make evals`. Scores every eval suite against `eval_thresholds.yaml`.

"Evals are the grade" (constitution P6): this is the single command that runs the committed gates and
fails the build if any RAN gate scores below its threshold. It is intentionally split into two kinds of
gate:

  * **Deterministic gates** (always run, no network): the intent **classifier** macro-F1 on the held-out
    `evals/classifier/testset.csv`, the **red-team** refusal rate over `evals/redteam/attempts.yaml`
    through the deterministic input rail, and the **redaction** leak count over a battery of secrets. These
    are reproducible and are also covered by pytest in `make test`; running them here keeps `make evals` a
    complete, self-contained grade.
  * **Offline gates** (need a live stack + provider keys + an embedded corpus): **RAG hit@3 + MRR** on
    `evals/rag/golden.yaml` and **agent tool-selection** on `evals/agent_tool_selection/cases.yaml`. These
    call the real embeddings/Groq providers, so on an un-provisioned machine they SKIP (not fail) — run
    them after `make up` + `make ingest`. Safety never depends on these scores: the wall and the
    deterministic plan assembly hold regardless of which recipes rank or which tools the model picks.
  * **Report-only pass** (also offline): **faithfulness + answer-relevancy** scored by a *frozen* Groq
    judge over the same golden set. The judge is non-deterministic, so these rows are PASS/SKIP only and
    NEVER set the exit code (per the clarification) — they track retrieval/answer quality over time without
    flaking the merge gate.

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

# Where `run()` persists the last run's scores (one metric.json per suite). These are generated
# run-artifacts (gitignored), not committed source — written only when the suite actually produces a
# score, so a SKIP never clobbers the last good numbers.
_CLASSIFIER_METRIC = _REPO_ROOT / "evals" / "classifier" / "metric.json"
_RAG_METRIC = _REPO_ROOT / "evals" / "rag" / "metric.json"

# Frozen RAG-judge config (report-only faithfulness/answer-relevancy, research R1). The model id is
# PINNED so scores stay comparable run-to-run ("frozen judge"); reusing the existing Groq adapter means
# zero new dependencies (FR-030). Bump the pin deliberately, never silently — a changed judge invalidates
# trend comparisons. The prompt is code, kept in prompts/ (golden rule: prompts are version-controlled).
_JUDGE_MODEL = "llama-3.3-70b-versatile"
_JUDGE_PROMPT = _REPO_ROOT / "prompts" / "rag_judge.md"
_JUDGE_MAX_TOKENS = 200

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
    """One gate's outcome: its name, a PASS/FAIL/SKIP verdict, and a human-readable score/detail line.

    `metrics` carries the gate's structured numbers (e.g. `{"macro_f1": 0.97}`) so the CLI can persist them
    to a `metric.json`; it is internal to the runner (the wire schema in app/schemas/admin.py mirrors only
    name/status/detail) and is None on SKIP / missing-input, where there is no number to record.
    """

    name: str
    status: str  # "PASS" | "FAIL" | "SKIP"
    detail: str
    metrics: dict[str, Any] | None = None


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
        import csv

        import joblib
        from sklearn.metrics import f1_score

        model = joblib.load(_REPO_ROOT / "ml" / "artifacts" / "model.joblib")
        # Read the held-out set with the stdlib csv reader (not pandas): this gate also runs IN-PROCESS in
        # the lean backend image (POST /admin/evals/run), which carries scikit-learn + joblib but NOT
        # pandas (research R8 — no backend runtime dep added). The testset is tiny, so csv is ample.
        with _CLASSIFIER_TESTSET.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        truth = [row["label"] for row in rows]
        preds = model.predict([row["text"] for row in rows])
        macro_f1 = float(f1_score(truth, preds, average="macro"))
        # Per-class F1 too, so the persisted metric.json lets an operator spot a weak label.
        labels = sorted(set(truth))
        per = f1_score(truth, preds, average=None, labels=labels, zero_division=0)
        per_class = {label: float(score) for label, score in zip(labels, per, strict=False)}
    except FileNotFoundError as exc:
        # A missing artifact/testset means the model was never trained — that IS a failed grade, not a skip.
        return GateResult("classifier macro-F1", "FAIL", f"missing input ({exc!s})")
    status = "PASS" if macro_f1 >= floor else "FAIL"
    metrics = {"macro_f1": macro_f1, "per_class": per_class, "floor": floor}
    return GateResult("classifier macro-F1", status, f"{macro_f1:.3f} (floor {floor:.3f})", metrics)


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


def gate_rag(thresholds: dict[str, Any]) -> list[GateResult]:
    """Score RAG hit@3 AND MRR on the golden set (offline — needs corpus + embeddings); two gating rows.

    Runs the real grounded retrieval (`rag.retrieve`, k from thresholds) once per labeled query under a
    constraint-free default profile, then derives two deterministic, gating metrics from the SAME ranked
    result so a single retrieval pass grades both:

      * **hit@k** vs `rag.hit_at_k_min` — counts a hit when any `ideal` corpus row (matched by
        source/source_id) appears among the k surfaced cards; hit@3 is exactly what the cook sees (FR-006).
      * **MRR** vs `rag.mrr_min` — mean reciprocal rank of the FIRST ideal among the surfaced cards (0 when
        none surface). Rewards ranking the right recipe higher, not merely somewhere in the top-k.

    Both are pure functions of the ranking (deterministic), so both gate merges. They SKIP together under
    the same live-stack guard, so an un-provisioned environment never produces a false failure.
    """
    hit_floor = float(thresholds["rag"]["hit_at_k_min"])
    mrr_floor = float(thresholds["rag"]["mrr_min"])
    k = int(thresholds["rag"]["k"])
    handle = _open_session()
    if handle is None:
        return [
            GateResult("rag hit@3", "SKIP", "live stack unavailable"),
            GateResult("rag MRR", "SKIP", "live stack unavailable"),
        ]
    session, connection, engine = handle
    try:
        from app.schemas.recipe import Category
        from app.services.user import rag
        from app.services.user.constraint_guard import ConstraintProfile

        cases = yaml.safe_load(_RAG_GOLDEN.read_text(encoding="utf-8"))["cases"]
        hits = 0
        rr_total = 0.0
        for case in cases:
            ideal = {(i["source"], str(i["source_id"])) for i in case["ideal"]}
            # Golden uses the human-readable category ("cold drink"); the enum value is underscored.
            raw_category = case.get("category")
            category = Category(raw_category.replace(" ", "_")) if raw_category else None
            rows = rag.retrieve(
                session, case["query"], ConstraintProfile.default(), category=category, k=k
            )
            # Keep ranking ORDER (a list, not a set) so MRR can read the position of the first ideal.
            surfaced = [(r.source, str(r.source_id)) for r in rows]
            hits += int(any(s in ideal for s in surfaced))
            rr_total += next(
                (1.0 / rank for rank, s in enumerate(surfaced, start=1) if s in ideal), 0.0
            )
        n = len(cases) or 1
        hit_rate = hits / n
        mrr = rr_total / n
        return [
            GateResult(
                "rag hit@3",
                "PASS" if hit_rate >= hit_floor else "FAIL",
                f"{hit_rate:.3f} ({hits}/{len(cases)}, floor {hit_floor})",
                {"hit_at_k": hit_rate, "hit_floor": hit_floor, "k": k, "hits": hits, "cases": len(cases)},
            ),
            GateResult(
                "rag MRR",
                "PASS" if mrr >= mrr_floor else "FAIL",
                f"{mrr:.3f} (floor {mrr_floor})",
                {"mrr": mrr, "mrr_floor": mrr_floor},
            ),
        ]
    except Exception as exc:  # noqa: BLE001 — provider/corpus not ready ⇒ skip, never a false failure
        detail = f"not runnable ({exc!s})"
        return [GateResult("rag hit@3", "SKIP", detail), GateResult("rag MRR", "SKIP", detail)]
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

    tools.dispatch = _spy

    def _restore() -> None:
        """Reinstate the genuine dispatch function."""
        tools.dispatch = original

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


# ───────────────────────────── report-only quality pass (frozen judge, never gates) ─────────────────────────────


def _judge_context_line(recipe: Any) -> str:
    """Render one retrieved recipe as grounding context for the judge: title + key ingredients + steps.

    Hands the judge the same real, stored content the reply is supposed to be faithful to (no embeddings,
    no scores), so a faithfulness score reflects whether the reply's claims trace back to these corpus rows.
    """
    ingredients = ", ".join(ing.name for ing in recipe.ingredients[:6]) or "n/a"
    steps = " ".join(recipe.steps[:4]) if recipe.steps else "n/a"
    return f"- {recipe.title} (ingredients: {ingredients}; steps: {steps})"


def _parse_judge_scores(content: str) -> tuple[float, float]:
    """Extract (faithfulness, answer_relevancy) from the judge's JSON reply, tolerant of fences/stray prose.

    The judge is told to return a bare JSON object, but models occasionally wrap it in markdown fences or
    add a sentence; slicing from the first '{' to the last '}' recovers the object without a strict parser.
    A KeyError/ValueError/JSONDecodeError here propagates to the caller, where the whole report-only pass
    catches it and SKIPs — a malformed judge reply must never fail the build.
    """
    import json

    obj = json.loads(content[content.index("{") : content.rindex("}") + 1])
    return float(obj["faithfulness"]), float(obj["answer_relevancy"])


def gate_judge(thresholds: dict[str, Any]) -> list[GateResult]:
    """Report-only faithfulness + answer-relevancy via a frozen Groq judge — PASS/SKIP only, NEVER gates.

    For each golden query, runs the real grounded retrieval, builds the cook-facing reply with the same
    `rag` explainer the app uses, then asks the PINNED `_JUDGE_MODEL` to score the reply on two 0–1 axes
    from (query, retrieved context, generated reply): faithfulness (is every claim supported by the
    retrieved recipes?) and answer-relevancy (does the reply address the query?). These are
    non-deterministic model judgments, so per the clarification (research R1) they are emitted PASS/SKIP
    only and NEVER set the exit code — they track quality without flaking the merge gate. SKIPs cleanly when
    the stack/provider is unavailable or the judge returns nothing parseable.
    """
    handle = _open_session()
    if handle is None:
        return [
            GateResult("rag faithfulness (report-only)", "SKIP", "live stack unavailable"),
            GateResult("rag answer-relevancy (report-only)", "SKIP", "live stack unavailable"),
        ]
    session, connection, engine = handle
    try:
        # The judge instantiates the Groq adapter DIRECTLY (not the provider-agnostic `llm` facade): it is a
        # FROZEN judge pinned to `_JUDGE_MODEL` for run-to-run score comparability, so it must stay on Groq
        # even when the app itself runs on OpenAI (`LLM_PROVIDER=openai`). Routing it through the facade would
        # send a Groq model id to whatever provider is active and break the pin (005 seam, DECISIONS D9).
        from app.infra.llm.groq import GroqClient
        from app.schemas.recipe import Category
        from app.services.user import rag
        from app.services.user.constraint_guard import ConstraintProfile

        k = int(thresholds["rag"]["k"])
        judge_prompt = _JUDGE_PROMPT.read_text(encoding="utf-8")
        cases = yaml.safe_load(_RAG_GOLDEN.read_text(encoding="utf-8"))["cases"]
        faith_total = 0.0
        relevancy_total = 0.0
        scored = 0
        for case in cases:
            raw_category = case.get("category")
            category = Category(raw_category.replace(" ", "_")) if raw_category else None
            rows = rag.retrieve(
                session, case["query"], ConstraintProfile.default(), category=category, k=k
            )
            if not rows:  # nothing retrieved ⇒ no reply to judge; skip this case, don't penalize
                continue
            context = "\n".join(_judge_context_line(r) for r in rows)
            reply = rag._explain(case["query"], rows)  # the real grounded reply the cook would see
            messages = [
                {"role": "system", "content": judge_prompt},
                {
                    "role": "user",
                    "content": (
                        f"QUERY:\n{case['query']}\n\n"
                        f"RETRIEVED CONTEXT:\n{context}\n\n"
                        f"REPLY:\n{reply}"
                    ),
                },
            ]
            response = GroqClient().chat(messages, model=_JUDGE_MODEL, max_tokens=_JUDGE_MAX_TOKENS)
            faith, relevancy = _parse_judge_scores(response.choices[0].message.content or "")
            faith_total += faith
            relevancy_total += relevancy
            scored += 1
        if scored == 0:
            detail = "no cases scored"
            return [
                GateResult("rag faithfulness (report-only)", "SKIP", detail),
                GateResult("rag answer-relevancy (report-only)", "SKIP", detail),
            ]
        return [
            GateResult(
                "rag faithfulness (report-only)",
                "PASS",
                f"{faith_total / scored:.3f} (report-only, {scored} cases)",
                {"faithfulness": faith_total / scored, "cases_scored": scored},
            ),
            GateResult(
                "rag answer-relevancy (report-only)",
                "PASS",
                f"{relevancy_total / scored:.3f} (report-only, {scored} cases)",
                {"answer_relevancy": relevancy_total / scored},
            ),
        ]
    except Exception as exc:  # noqa: BLE001 — non-deterministic judge ⇒ never a build failure; SKIP
        detail = f"not runnable ({exc!s})"
        return [
            GateResult("rag faithfulness (report-only)", "SKIP", detail),
            GateResult("rag answer-relevancy (report-only)", "SKIP", detail),
        ]
    finally:
        session.close()
        connection.close()
        engine.dispose()


def thresholds() -> dict[str, Any]:
    """Public accessor for the committed thresholds (the in-process admin eval endpoint echoes these)."""
    return _thresholds()


def collect_results() -> list[GateResult]:
    """Run every gate and return the structured results list WITHOUT printing or setting an exit code.

    The shared core of `run()` and the in-process `POST /admin/evals/run`: deterministic gates always run,
    offline gates SKIP without a live stack, and the report-only judge rows are PASS/SKIP by construction.
    Returning the raw `GateResult` list lets the admin service serialize it straight to JSON and lets `run()`
    render the table + derive the exit code, so both surfaces grade identically from one place.
    """
    loaded = _thresholds()
    return [
        gate_classifier(loaded),
        gate_redteam(loaded),
        gate_redaction(loaded),
        *gate_rag(loaded),
        gate_agent_tool_selection(loaded),
        *gate_judge(loaded),
    ]


def _write_metric(path: Path, payload: dict[str, Any]) -> None:
    """Persist one suite's scores as a metric.json (stamped with a UTC `ran_at`), pretty-printed + sorted."""
    import json
    from datetime import UTC, datetime

    body = {**payload, "ran_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def persist_metrics(results: list[GateResult]) -> list[Path]:
    """Write evals/classifier/metric.json and evals/rag/metric.json from the run's structured metrics.

    Only suites that actually produced a score are written, so a SKIP (no live stack) never overwrites the
    last good numbers. The classifier file carries macro-F1 + per-class F1; the RAG file merges every
    `rag *` row that carried metrics — hit@k and MRR, plus the report-only faithfulness/answer-relevancy
    when the judge ran — under one object with each row's PASS/FAIL/SKIP status. Returns the paths written.
    """
    written: list[Path] = []
    classifier = next((r for r in results if r.name == "classifier macro-F1"), None)
    if classifier is not None and classifier.metrics is not None:
        _write_metric(_CLASSIFIER_METRIC, {**classifier.metrics, "status": classifier.status})
        written.append(_CLASSIFIER_METRIC)

    rag_metrics: dict[str, Any] = {}
    rag_statuses: dict[str, str] = {}
    for r in results:
        if r.name.startswith("rag ") and r.metrics is not None:
            rag_metrics.update(r.metrics)
            rag_statuses[r.name] = r.status
    if rag_metrics:
        _write_metric(_RAG_METRIC, {**rag_metrics, "statuses": rag_statuses})
        written.append(_RAG_METRIC)
    return written


def run() -> int:
    """Run every gate, print a results table, persist per-suite metric.json files, and return an exit code.

    Deterministic gates (classifier, red-team, redaction) always run; offline gates (RAG hit@3/MRR, agent,
    and the report-only judge) run when a live stack is reachable and SKIP otherwise. Only a FAIL on a gate
    that actually ran flips the exit code — the report-only judge rows are PASS/SKIP by construction and
    never gate — so an un-provisioned CI step still grades the deterministic safety/quality gates without
    false failures. The classifier/RAG scores are also written to metric.json (skipped suites are not).
    """
    results = collect_results()
    written = persist_metrics(results)

    width = max(len(r.name) for r in results)
    print("eval gates (vs eval_thresholds.yaml):\n")
    for r in results:
        print(f"  [{r.status:4}] {r.name.ljust(width)}  {r.detail}")
    if written:
        print("\nwrote: " + ", ".join(str(p.relative_to(_REPO_ROOT)) for p in written))

    failed = [r.name for r in results if r.status == "FAIL"]
    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        return 1
    print("\nall graded gates passed (skipped gates need `make up` + `make ingest`).")
    return 0


if __name__ == "__main__":  # pragma: no cover — manual offline entry point
    sys.exit(run())
