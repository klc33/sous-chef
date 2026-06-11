"""GuardrailDecision — the shared verdict object both rails return.

A small value object so the input and output rails speak the same shape (data-model.md). `stage` says
which rail produced it, `action` is the verdict (allow / sanitize / refuse), and `reason` carries an
optional short explanation (e.g. why a turn was refused) for the response + traces. `sanitized_message`
is set only by the INPUT rail when it neutralized an injected fragment but a safe remainder survives
(FR-033): the endpoint routes that cleaned text instead of the raw message, serving the safe portion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Stage = Literal["input", "output"]
Action = Literal["allow", "sanitize", "refuse"]


@dataclass(frozen=True)
class GuardrailDecision:
    """One rail's verdict for a turn: which stage, the action taken, and an optional reason.

    `sanitized_message` carries the input rail's cleaned message when an embedded injection was stripped
    but a safe remainder remains; it stays `None` on a plain allow, a refusal, and every output decision.
    """

    stage: Stage
    action: Action
    reason: str | None = None
    sanitized_message: str | None = None
