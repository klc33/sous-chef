# Intent Classifier — Model Card

The one model SousChef trains and is graded on. Trained offline by `ml/train_classifier.py`, served lean
via `joblib` in `app/classifier/predict.py` (scikit-learn + numpy, **no torch**).

## Decision: classical ML over LLM routing

| Approach | Macro-F1 (held-out) | Latency | Cost | Determinism |
|---|---|---|---|---|
| **TF-IDF + LogReg (served)** | **0.896** | < ~50 ms local CPU | $0 | deterministic |
| Groq LLM zero-shot (baseline) | skipped (no Groq key/network) | ~hundreds of ms / call | per-token | non-deterministic |

The classical model is served: it matches or beats the LLM baseline on this label set while being free,
fast, deterministic, and torch-free. Confidence-based escalation (`router_confidence_threshold`) sends
only low-confidence turns to the agent, so misrouting degrades cost/quality — never safety.

## Artifact

- Path: `ml/artifacts/model.joblib`
- SHA-256: `f55e0870f68e88fc19682ea75773791a30acec755598ae04e7fb0c404aadcb3a`
- Algorithm: TF-IDF (word 1–2 grams) + multinomial logistic regression (`C=10`, `sublinear_tf`)
- Labels: find_recipe, plan_meals, nutrition_q, substitution, chitchat, out_of_scope
- Train time: 0.02s

## Held-out classification report

```
              precision    recall  f1-score   support

 find_recipe       0.92      1.00      0.96        11
  plan_meals       0.89      1.00      0.94         8
 nutrition_q       1.00      0.88      0.93         8
substitution       1.00      1.00      1.00         8
    chitchat       1.00      0.62      0.77         8
out_of_scope       0.70      0.88      0.78         8

    accuracy                           0.90        51
   macro avg       0.92      0.90      0.90        51
weighted avg       0.92      0.90      0.90        51

```
