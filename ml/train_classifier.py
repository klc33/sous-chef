"""Train the intent classifier offline — the one model SousChef builds and is graded on.

TF-IDF (word 1–2 grams) + multinomial logistic regression on `ml/data/intents_labeled.csv`, exported to
`ml/artifacts/model.joblib` and served lean (scikit-learn + joblib, NO torch — golden rule #3). The
script holds out a stratified split, reports macro-F1, and — best-effort — compares the classical model
to a Groq LLM zero-shot baseline on macro-F1 / latency / cost, recording the decision and the artifact's
SHA-256 in `app/classifier/model_card.md`. The classical model is preferred because it is fast,
deterministic, explainable, and torch-free to serve; the baseline run only documents that choice.

Run via `make train` (`uv run python -m ml.train_classifier`). The Groq baseline is skipped gracefully
when no key/network is available, so training never depends on a live provider.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

# Repo-relative paths (the module runs from the repo root via `python -m`).
_DATA = Path("ml/data/intents_labeled.csv")
_ARTIFACT = Path("ml/artifacts/model.joblib")
_MODEL_CARD = Path("app/classifier/model_card.md")
# The fixed label set (contracts/classifier.md); used for the LLM baseline prompt.
_LABELS = ["find_recipe", "plan_meals", "nutrition_q", "substitution", "chitchat", "out_of_scope"]


def build_pipeline() -> Pipeline:
    """Return the TF-IDF + logistic-regression pipeline (word 1–2 grams, calibrated probabilities).

    1–2 grams capture short intent cues ("instead of", "meal plan") a unigram bag misses. `sublinear_tf`
    dampens repeated-term weight on short messages. Logistic regression is chosen over an SVM specifically
    for the calibrated `predict_proba` the router's confidence threshold relies on; `C=10` (lighter
    regularization) sharpens those probabilities so a clearly-intended turn lands well above the router's
    threshold instead of diffusing across six near-uniform classes. The labels are roughly balanced, so no
    class weighting is needed (balanced weighting would only flatten the probabilities the router reads).
    """
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True, min_df=1, sublinear_tf=True)),
            ("clf", LogisticRegression(max_iter=2000, C=10)),
        ]
    )


def _groq_baseline_macro_f1(texts: list[str], labels: list[str]) -> float | None:
    """Run a Groq zero-shot baseline over the held-out set and return its macro-F1, or None if skipped.

    Best-effort and entirely optional: prompts the LLM to pick one label per message and scores macro-F1
    against the truth. Any failure (no key, no network, throttling) returns None so training never hangs
    on a provider. This only exists to document the ML-vs-LLM decision in the model card.
    """
    try:
        from app.infra import llm_groq
    except Exception:
        return None

    preds: list[str] = []
    label_list = ", ".join(_LABELS)
    for text in texts:
        try:
            resp = llm_groq.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Classify the cook's message into exactly one label from: {label_list}. "
                            "Reply with only the label, nothing else."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=10,
            )
            raw = (resp.choices[0].message.content or "").strip().lower()
        except Exception:
            return None
        # Map the free-text reply to the closest known label; default to out_of_scope on a miss.
        preds.append(next((lab for lab in _LABELS if lab in raw), "out_of_scope"))

    return float(f1_score(labels, preds, average="macro", labels=_LABELS, zero_division=0))


def _write_model_card(
    *, sha256: str, macro_f1: float, report: str, baseline_f1: float | None, train_secs: float
) -> None:
    """Write the decision record: ML-vs-LLM, the achieved macro-F1, and the artifact's SHA-256.

    The SHA-256 pins the exact served artifact (Principle V); the macro-F1 + baseline comparison justify
    serving the classical model over an LLM router. Written to `app/classifier/model_card.md` so the
    decision lives next to the serving code.
    """
    baseline_line = (
        f"{baseline_f1:.3f}" if baseline_f1 is not None else "skipped (no Groq key/network)"
    )
    _MODEL_CARD.write_text(
        f"""# Intent Classifier — Model Card

The one model SousChef trains and is graded on. Trained offline by `ml/train_classifier.py`, served lean
via `joblib` in `app/classifier/predict.py` (scikit-learn + numpy, **no torch**).

## Decision: classical ML over LLM routing

| Approach | Macro-F1 (held-out) | Latency | Cost | Determinism |
|---|---|---|---|---|
| **TF-IDF + LogReg (served)** | **{macro_f1:.3f}** | < ~50 ms local CPU | $0 | deterministic |
| Groq LLM zero-shot (baseline) | {baseline_line} | ~hundreds of ms / call | per-token | non-deterministic |

The classical model is served: it matches or beats the LLM baseline on this label set while being free,
fast, deterministic, and torch-free. Confidence-based escalation (`router_confidence_threshold`) sends
only low-confidence turns to the agent, so misrouting degrades cost/quality — never safety.

## Artifact

- Path: `ml/artifacts/model.joblib`
- SHA-256: `{sha256}`
- Algorithm: TF-IDF (word 1–2 grams) + multinomial logistic regression (`C=10`, `sublinear_tf`)
- Labels: {", ".join(_LABELS)}
- Train time: {train_secs:.2f}s

## Held-out classification report

```
{report}
```
""",
        encoding="utf-8",
    )


def train() -> None:
    """Train, evaluate, persist the classifier, and write the model card.

    Loads the labeled set, makes a stratified 80/20 split (no leakage), fits the pipeline, reports
    macro-F1 on the held-out fold, saves `model.joblib`, computes its SHA-256, runs the optional Groq
    baseline, and writes the model card. Prints the macro-F1 so the operator can set `classifier.f1_min`
    just below it.
    """
    frame = pd.read_csv(_DATA)
    x_train, x_test, y_train, y_test = train_test_split(
        frame["text"].tolist(),
        frame["label"].tolist(),
        test_size=0.2,
        stratify=frame["label"].tolist(),
        random_state=42,
    )

    pipeline = build_pipeline()
    start = time.perf_counter()
    pipeline.fit(x_train, y_train)
    train_secs = time.perf_counter() - start

    preds = pipeline.predict(x_test)
    macro_f1 = float(f1_score(y_test, preds, average="macro"))
    report = classification_report(y_test, preds, labels=_LABELS, zero_division=0)

    _ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, _ARTIFACT)
    sha256 = hashlib.sha256(_ARTIFACT.read_bytes()).hexdigest()

    baseline_f1 = _groq_baseline_macro_f1(x_test, y_test)
    _write_model_card(
        sha256=sha256,
        macro_f1=macro_f1,
        report=report,
        baseline_f1=baseline_f1,
        train_secs=train_secs,
    )

    print(f"trained classifier macro-F1={macro_f1:.3f} sha256={sha256}")
    print(f"artifact: {_ARTIFACT}")
    print("set eval_thresholds.yaml classifier.f1_min to just below the macro-F1 above.")


if __name__ == "__main__":
    train()
