"""Unit tests for the metric.json persistence in evals.run_evals (per-suite score snapshots).

Pins that `persist_metrics` writes evals/classifier/metric.json + evals/rag/metric.json from the run's
structured GateResult.metrics, merges the RAG rows (hit/MRR + report-only judge) into one object, stamps
`ran_at`, and — crucially — never writes a suite that SKIPped (so a stack-less run can't clobber the last
good numbers). The module path constants are redirected to a tmp dir so no real artifact is touched.
"""

from __future__ import annotations

import json

from evals import run_evals
from evals.run_evals import GateResult


def test_persist_writes_classifier_and_rag_from_metrics(tmp_path, monkeypatch) -> None:
    """A run with classifier + RAG metrics writes both metric.json files with the merged scores."""
    cpath = tmp_path / "classifier" / "metric.json"
    rpath = tmp_path / "rag" / "metric.json"
    cpath.parent.mkdir(parents=True)
    rpath.parent.mkdir(parents=True)
    monkeypatch.setattr(run_evals, "_CLASSIFIER_METRIC", cpath)
    monkeypatch.setattr(run_evals, "_RAG_METRIC", rpath)

    results = [
        GateResult("classifier macro-F1", "PASS", "0.979 (floor 0.900)",
                   {"macro_f1": 0.979, "per_class": {"find_recipe": 1.0}, "floor": 0.9}),
        GateResult("redteam refusal rate", "PASS", "1.000", None),  # no metrics → ignored
        GateResult("rag hit@3", "PASS", "0.9 (9/10, floor 0.7)",
                   {"hit_at_k": 0.9, "hit_floor": 0.7, "k": 3, "hits": 9, "cases": 10}),
        GateResult("rag MRR", "PASS", "0.8 (floor 0.6)", {"mrr": 0.8, "mrr_floor": 0.6}),
        GateResult("rag faithfulness (report-only)", "PASS", "0.95", {"faithfulness": 0.95, "cases_scored": 10}),
    ]
    written = run_evals.persist_metrics(results)
    assert set(written) == {cpath, rpath}

    cdoc = json.loads(cpath.read_text(encoding="utf-8"))
    assert cdoc["macro_f1"] == 0.979
    assert cdoc["per_class"] == {"find_recipe": 1.0}
    assert cdoc["status"] == "PASS"
    assert "ran_at" in cdoc

    rdoc = json.loads(rpath.read_text(encoding="utf-8"))
    # The hit, MRR, and report-only judge rows are merged into one RAG object.
    assert rdoc["hit_at_k"] == 0.9 and rdoc["mrr"] == 0.8 and rdoc["faithfulness"] == 0.95
    assert rdoc["statuses"]["rag hit@3"] == "PASS"
    assert "ran_at" in rdoc


def test_skipped_rag_does_not_write_or_clobber(tmp_path, monkeypatch) -> None:
    """When RAG SKIPs (no metrics), its metric.json is neither created nor overwritten."""
    cpath = tmp_path / "classifier" / "metric.json"
    rpath = tmp_path / "rag" / "metric.json"
    cpath.parent.mkdir(parents=True)
    rpath.parent.mkdir(parents=True)
    rpath.write_text('{"hit_at_k": 0.88, "stale": true}', encoding="utf-8")  # last good run
    monkeypatch.setattr(run_evals, "_CLASSIFIER_METRIC", cpath)
    monkeypatch.setattr(run_evals, "_RAG_METRIC", rpath)

    results = [
        GateResult("classifier macro-F1", "PASS", "0.979", {"macro_f1": 0.979, "per_class": {}, "floor": 0.9}),
        GateResult("rag hit@3", "SKIP", "live stack unavailable", None),
        GateResult("rag MRR", "SKIP", "live stack unavailable", None),
    ]
    written = run_evals.persist_metrics(results)
    assert written == [cpath]  # only the classifier was written
    # The pre-existing RAG snapshot is untouched.
    assert json.loads(rpath.read_text(encoding="utf-8")) == {"hit_at_k": 0.88, "stale": True}
