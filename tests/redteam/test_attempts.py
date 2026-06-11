"""Red-team gate (US5 / SC-003): every probe in the battery must be refused by the input rail.

This is the executable form of the constitution's red-team gate. It loads `evals/redteam/attempts.yaml`
(the curated allergen-override + injection/jailbreak/prompt-leak probes), runs each through the deterministic
`input_rails.screen`, and asserts the achieved refusal rate meets `redteam.refusal_rate_min` from
`eval_thresholds.yaml` (1.0 — no probe may slip through). A single un-refused probe fails the build; the fix
is the rail, never the threshold (golden rule #6).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from app.guardrails import input_rails

# Repo root from this file: tests/redteam/test_attempts.py → parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATTEMPTS = _REPO_ROOT / "evals" / "redteam" / "attempts.yaml"
_THRESHOLDS = _REPO_ROOT / "eval_thresholds.yaml"


def _load_probes() -> list[dict[str, str]]:
    """Load the probe battery from attempts.yaml (each probe carries an id, category, and message)."""
    data = yaml.safe_load(_ATTEMPTS.read_text(encoding="utf-8"))
    return data["probes"]


def _refusal_threshold() -> float:
    """Read the required minimum refusal rate from the single eval-thresholds source of truth."""
    data = yaml.safe_load(_THRESHOLDS.read_text(encoding="utf-8"))
    return float(data["redteam"]["refusal_rate_min"])


_PROBES = _load_probes()


def test_battery_is_non_trivial() -> None:
    """Guard against an empty/partial battery silently passing the gate."""
    assert len(_PROBES) >= 15, "the red-team battery must cover the manipulation classes"


@pytest.mark.parametrize("probe", _PROBES, ids=[p["id"] for p in _PROBES])
def test_each_probe_is_refused(probe: dict[str, str]) -> None:
    """Each individual probe is refused — pinpoints exactly which probe regressed if the gate breaks."""
    decision = input_rails.screen(probe["message"])
    assert decision.action == "refuse", f"{probe['id']} was not refused: {probe['message']!r}"


def test_refusal_rate_meets_gate() -> None:
    """The aggregate refusal rate over the whole battery meets the gate (target 1.0 — all refused)."""
    refused = sum(1 for p in _PROBES if input_rails.screen(p["message"]).action == "refuse")
    rate = refused / len(_PROBES)
    assert rate >= _refusal_threshold(), f"refusal rate {rate:.3f} below gate {_refusal_threshold()}"
