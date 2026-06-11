"""Serve the offline-trained intent classifier — load once, predict fast, no torch.

The router (`services/user/router.py`) calls `predict(message)` on every turn to decide workflow vs
agent vs refuse. The `model.joblib` artifact (TF-IDF + logistic regression, trained by
`ml/train_classifier.py`) is loaded a single time and process-cached, so prediction is pure CPU and
fast (< ~50 ms target). `predict` returns the label plus the model's calibrated confidence (the max
class probability) so the router can escalate low-confidence turns to the safer agent path.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib

# Where ml/train_classifier.py writes the served artifact.
_ARTIFACT = Path("ml/artifacts/model.joblib")


@dataclass(frozen=True)
class IntentPrediction:
    """One classification result: the predicted intent, its confidence, and whether the input had signal.

    `has_signal` is False when the message matched NONE of the model's known vocabulary — the feature
    vector is empty, so the "prediction" is merely the model's bias prior, not a real inference. The router
    uses this to send zero-signal turns to a cheap clarification re-prompt instead of the expensive agent
    (FR-004a). Note a real but out-of-vocabulary one-word dish ("sushi") also has no signal and is
    indistinguishable from gibberish here — both get the same harmless re-prompt.
    """

    intent: str
    confidence: float
    has_signal: bool


@lru_cache
def _model() -> Any:
    """Load and cache the joblib pipeline once per process.

    lru_cache means the artifact is read from disk a single time; later calls reuse the in-memory
    pipeline. A missing artifact raises a clear error pointing at `make train` rather than failing deep
    inside scikit-learn.
    """
    if not _ARTIFACT.exists():
        raise FileNotFoundError(
            f"classifier artifact missing at {_ARTIFACT}; run `make train` to build it."
        )
    return joblib.load(_ARTIFACT)


def predict(message: str) -> IntentPrediction:
    """Classify one message into an intent label with a confidence score.

    Runs the cached pipeline's `predict_proba`, takes the highest-probability class as the label and that
    probability as the confidence. Also checks whether the input matched any known vocabulary by running
    the pipeline's vectorizer stage (`model[:-1]`, everything before the final estimator): an all-zero
    feature matrix (`nnz == 0`) means the message carried no signal, so the router can re-prompt cheaply
    instead of escalating to the agent. The confidence feeds the router's escalation threshold — a low
    value (with real signal) routes the turn to the more capable agent rather than a wrong handler.
    """
    model = _model()
    probabilities = model.predict_proba([message])[0]
    classes = model.classes_
    best_index = int(probabilities.argmax())
    # Vectorize-only pass (pipeline minus the final classifier): nnz==0 ⇒ no known terms matched.
    has_signal = model[:-1].transform([message]).nnz > 0
    return IntentPrediction(
        intent=str(classes[best_index]),
        confidence=float(probabilities[best_index]),
        has_signal=has_signal,
    )
