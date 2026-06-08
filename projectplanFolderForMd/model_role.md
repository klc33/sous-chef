# THE ROLE OF THE MODEL IN SOUSCHEF — WHAT IT DOES

A companion to [amer_idea-1.md](amer_idea-1.md) and [amer_idea-1-tech.md](amer_idea-1-tech.md).

SousChef contains **more than one model**, and they do *different* jobs. This document explains each
one's role — but it leads with the one that is genuinely **"the model" you build, own, and are graded
on**: the **intent classifier**. The others (the Groq LLM, the embedding model) are powerful hosted
models the project *uses*; the classifier is the model the project *makes*.

| Model | Type | Trained by you? | One-line role |
|---|---|---|---|
| **Intent classifier** | Classical ML (scikit-learn) | **Yes — offline** | The **router**: decides how each message is handled |
| **Groq LLM** | Hosted large language model | No (API) | The **reasoning + language** engine: understands, ranks, explains, drives the agent |
| **Embedding model** | Hosted embedding model | No (API) | The **retrieval** engine: turns text into vectors so search finds relevant recipes |
| *(Constraint guard)* | *Deterministic code — NOT a model* | — | The **safety wall**: filters allergen/diet violations |

---

## 1. THE MODEL YOU BUILD — THE INTENT CLASSIFIER (THE ROUTER)

**This is "the model" in the project sense** — the one you train, evaluate, and serve yourself.

### What it does
Every message a cook sends arrives as free text — "what's a good cold drink for summer?", "plan my
dinners", "talk to a human", "asdfgh". Before the system can do anything useful, it has to know **what
kind of request this is**. The classifier reads the message and assigns it **one intent label**:

```
find_recipe | plan_meals | nutrition_q | substitution | chitchat | out_of_scope
```

That single label is the **routing decision** — it's why the classifier is called *the router*:

- **Easy, enumerable intents** (`find_recipe`, a one-off `nutrition_q`, `chitchat`) are handled directly
  by a cheap, deterministic **workflow** — no LLM reasoning step is spent on them.
- **Hard, multi-step intents** (`plan_meals`, ambiguous turns) are **escalated to the bounded agent**,
  which is the expensive path.

So the model's job is to be the **traffic controller** at the front door: send most turns down the cheap
path and reserve the costly agent for the turns that actually need it.

### Why this model exists (the role it plays)
- **Cost control.** Without it, every message would hit the LLM just to figure out what it is. The
  classifier gives a cheap, instant signal instead — the project measures *what % of turns it keeps off
  the agent* and what that saves.
- **Determinism where you want it.** Routing is a decision you want to be fast, consistent, and
  explainable. A small trained classifier is all three; an LLM call is none of them.
- **It's the AI-engineering skill being taught.** You **train it offline** (notebook/Colab) on a labeled
  set, **compare three things on a real number** — classical ML (TF-IDF + logistic regression) vs. an
  **LLM zero-shot baseline**, on macro-F1, latency, and cost — **pick one and defend it**, then **serve
  it lean** (exported with `joblib`, run with scikit-learn + numpy, **no torch in the container**). Its
  macro-F1 is a **committed CI gate**, so the shipped model can't silently regress.

### What it does *not* do
It does **not** write answers, retrieve recipes, or enforce safety. It only **labels intent**. Everything
downstream acts on that label.

> **In one line:** the classifier is the brain that decides *how* each message gets handled — the cheap,
> trained gatekeeper that keeps the expensive LLM off the easy work.

---

## 2. THE MODEL YOU USE FOR LANGUAGE — THE GROQ LLM

### What it does
When a turn needs genuine language understanding or reasoning, the **Groq-hosted LLM** does three things:

1. **Understands** the cook's natural-language request (including category cues like "a hot drink" or
   constraints expressed in passing).
2. **Ranks and explains** the recipes that retrieval returned — it phrases the reply and orders the
   options, working **only with real, retrieved recipes**. It does **not invent recipes or steps**.
3. **Drives the bounded agent** — on hard turns (mainly meal planning) it is the reasoning engine that
   **chooses which tools to call** (`search_recipes`, `get_recipe`, `get_nutrition`,
   `build_shopping_list`, `substitute_ingredient`) and in what order, within a capped loop.

### Its role vs. the classifier's
- The **classifier decides whether the LLM is even needed** for this turn.
- The **LLM does the open-ended language/reasoning work** the classifier deliberately can't.

They are a team: a cheap model for the routine decision, an expensive model for the hard, ambiguous
ones. This hybrid (workflow + one bounded agent) is the production-honest pattern the project is built
around.

### Guardrails on this model
The LLM is powerful but untrusted with safety: its output still passes through the **deterministic
constraint guard** (allergens/diet) and the **output guardrails** (PII, leak checks). The model proposes;
the deterministic layers dispose.

---

## 3. THE MODEL YOU USE FOR SEARCH — THE EMBEDDING MODEL

### What it does
The **hosted embedding model** converts text into numeric vectors that capture meaning. It runs in two
places:

- **At ingestion:** every recipe is embedded and stored in **pgvector**.
- **At query time:** the cook's search ("something Thai I haven't made") is embedded, and pgvector finds
  the recipes whose vectors are most similar.

This is what makes **semantic discovery** work — matches aren't limited to literal keyword overlap, so a
query finds dishes that are *about* what the cook wants even when the words differ.

### Its role
It is the **retrieval engine behind RAG** — the thing that produces the candidate list of real recipes
that Feature 1 shows and that the LLM then ranks/explains. Its quality directly drives the retrieval
metrics (hit@k, MRR) the project gates on. *(Note: Groq is chat-only, so embeddings come from a separate
hosted embeddings provider — see the tech reference.)*

---

## 4. WHAT IS *NOT* A MODEL — THE CONSTRAINT GUARD

Worth stating plainly, because it's the most important safety component: **the allergen/diet "wall" is
not a model.** It is **deterministic code** — a hard filter that removes any recipe whose
ingredients/tags violate the cook's stated allergies or diet, applied to *every* recipe output.

**Why it's deliberately not a model:** safety you can prove must be predictable and testable. A model
"usually" refusing an allergen is not good enough; a deterministic rule either passes the red-team probe
or it doesn't — and that's exactly what the hard CI gate checks. The models propose options; this code
guarantees none of them can hurt the cook.

---

## HOW THE MODELS WORK TOGETHER — ONE TURN, END TO END

```
Cook: "plan me 3 quick vegetarian dinners"
   │
   ▼
[ Guardrails input rail ]  → not injection/jailbreak, allow
   │
   ▼
[ INTENT CLASSIFIER ]      → label = plan_meals   (the model decides: this is HARD)
   │  (escalate to agent)
   ▼
[ Groq LLM = agent brain ] → calls search_recipes (uses EMBEDDING MODEL → pgvector),
   │                          then build_shopping_list, within the bounded loop
   ▼
[ CONSTRAINT GUARD ]       → drops any non-vegetarian / allergen-violating recipe
   │
   ▼
[ Guardrails output rail ] → PII redaction + leak check
   │
   ▼
Cook sees: 3 real vegetarian dinner recipes + a shopping list
```

- The **classifier** decided the *route* (hard → agent).
- The **embedding model** found the *candidates*.
- The **Groq LLM** did the *reasoning and language* (tool choice, ranking, phrasing).
- The **constraint guard** (not a model) enforced *safety*.

That division of labor — a cheap trained model routing, hosted models doing retrieval and language, and
deterministic code owning safety — is the core engineering idea of SousChef.
