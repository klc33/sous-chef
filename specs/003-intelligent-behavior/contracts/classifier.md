# Contract: Intent Classifier (the router)

The one model the project trains and is graded on. Trained offline, served lean via `joblib`
(scikit-learn + numpy, **no torch**). This contract fixes the label set, the serving interface, the
routing map, and the CI gate.

## Labels (fixed, exactly one per message)

```
find_recipe | plan_meals | nutrition_q | substitution | chitchat | out_of_scope
```

## Serving interface — `app/classifier/predict.py`

- `predict(message: str) -> IntentPrediction` where
  `IntentPrediction = { intent: <one label>, confidence: float (0..1) }`.
- Loads `ml/artifacts/model.joblib` once (process-cached); pure CPU; target < ~50 ms.
- The served artifact is **SHA-256 pinned** and recorded in `app/classifier/model_card.md`.

## Routing map — `services/user/router.py`

| Intent | Route | Handler |
|---|---|---|
| `find_recipe` | workflow | `services/user/rag.search` (ranked cards) |
| `nutrition_q` | workflow | `services/user/nutrition` via recipe lookup |
| `substitution` | workflow | `services/user/substitution` (curated, wall-filtered) |
| `chitchat` | workflow | canned safe reply |
| `plan_meals` | **agent** | `app/agent/loop` (search + build_shopping_list) |
| `out_of_scope` | refuse | safe canned redirect |
| *zero-signal (no known vocabulary matched → prediction is the bare prior)* | **clarify** | cheap safe clarification re-prompt, never the agent (FR-004a) |
| *any, confidence < threshold (≈0.55) WITH real matched signal* | **agent** | escalate to the safe, more-capable path |

Zero-signal vs. ambiguity: a turn that matches **no** known intent vocabulary gives the agent nothing to
act on — its "prediction" is just the model's prior — so it gets a cheap clarification re-prompt and never
reaches the (expensive) agent (FR-004a). This is *not* spam detection: a one-word out-of-vocabulary dish
("sushi") is indistinguishable from gibberish here and gets the same harmless re-prompt. A low-confidence
turn that *does* match known terms is genuinely ambiguous and still escalates to the agent. Misrouting must
never produce an unsafe result: every route still passes the wall + output rail (FR-004).

## Training & evaluation

- Dataset: `ml/data/intents_labeled.csv` (`text,label`), ~50–100 examples/label, stratified held-out split,
  no leakage. Built/extended this phase.
- `ml/train_classifier.py`: TF-IDF (word 1–2 grams) + logistic regression → `model.joblib` + metrics.
- **Baseline comparison** (model_role.md requirement): classical model vs a Groq LLM zero-shot baseline on
  **macro-F1, latency, cost**, decision recorded in `model_card.md`.

## CI gate — `eval_thresholds.yaml`

- `classifier.f1_min`: macro-F1 on `evals/classifier/testset.csv` (held-out). Target ≥ **0.85**; set to
  just below the achieved score and **never weakened** later (golden rule #6). A drop below the floor
  fails the build.
