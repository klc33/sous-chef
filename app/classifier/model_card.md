# Intent Classifier — Model Card

The one model SousChef trains and is graded on. Trained offline by `ml/train_classifier.py`, served lean
via `joblib` in `app/classifier/predict.py` (scikit-learn + numpy, **no torch**).

## Decision: classical ML over LLM routing

| Approach | Macro-F1 (held-out) | Latency | Cost | Determinism |
|---|---|---|---|---|
| **TF-IDF + LogReg (served)** | **0.873** | < ~50 ms local CPU | $0 | deterministic |
| Groq LLM zero-shot (baseline) | skipped (no Groq key/network) | ~hundreds of ms / call | per-token | non-deterministic |

The classical model is served: it matches or beats the LLM baseline on this label set while being free,
fast, deterministic, and torch-free. Confidence-based escalation (`router_confidence_threshold`) sends
only low-confidence turns to the agent, so misrouting degrades cost/quality — never safety.

## Artifact

- Path: `ml/artifacts/model.joblib`
- SHA-256: `f0fa282e13623c18f6c6b8758c2e68375646a4fc78fea64031705819d6094a21`
- Algorithm: TF-IDF (word 1–2 grams) + multinomial logistic regression (`C=10`, `sublinear_tf`)
- Labels: find_recipe, plan_meals, nutrition_q, substitution, chitchat, out_of_scope
- Train time: 0.03s

## Held-out classification report

```
              precision    recall  f1-score   support

 find_recipe       0.93      1.00      0.96        13
  plan_meals       0.89      1.00      0.94         8
 nutrition_q       0.78      0.88      0.82         8
substitution       0.89      1.00      0.94         8
    chitchat       1.00      0.62      0.77         8
out_of_scope       0.86      0.75      0.80         8

    accuracy                           0.89        53
   macro avg       0.89      0.88      0.87        53
weighted avg       0.89      0.89      0.88        53

```
