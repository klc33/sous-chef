# SousChef — How to Use Embeddings (End to End)

This is the complete walkthrough for embeddings in SousChef: what they are, every step to wire them in,
and how they power recipe search. Embeddings are the engine behind **RAG** — they turn a fuzzy request
("something Thai I haven't made") into a vector and find the recipes whose vectors are closest, so the
app surfaces *real* dishes instead of inventing them.

**Where embeddings live in the stack:**
```
ingestion/embed_recipes.py  →  embeds each recipe  →  pgvector column (with metadata)
app/infra/embeddings.py     →  the provider adapter (one place, swappable)
app/services/user/rag.py    →  embeds the query, runs the vector search at request time
app/repo/recipes.py         →  the actual pgvector SQL (the only place that touches the DB)
```

> Key fact (decided in the plan): **Groq is chat-only and has no embeddings endpoint.** Embeddings come
> from a *separate* hosted provider. This guide uses **OpenAI `text-embedding-3-small` (1536 dims)** as
> the concrete example; alternatives are Cohere `embed-v3` or Jina. The model's **dimension is fixed and
> must match your pgvector column exactly.**

---

## Step 0 — Understand the one rule that breaks everything
The **same model** must embed both the recipes (at ingestion) and the query (at request time), and your
pgvector column dimension must equal that model's output dimension (1536 for `text-embedding-3-small`).
If you change the model later, you must **re-embed the whole corpus** and migrate the column. Pin the
model name and dimension in config so this can't drift.

---

## Step 1 — Get an API key and store it in Vault
Embeddings are a paid hosted API. The key is a secret → it goes in **Vault, never `.env`**.

```powershell
# (local dev) write the embeddings key into Vault — see scripts/seed_vault.sh
vault kv put secret/sous-chef EMBEDDINGS_API_KEY="sk-..."
```
The backend and the ingestion job read it through the Vault adapter (`app/infra/vault.py`), so no key
ever lands in code, an image, or git.

---

## Step 2 — Add the dependency (uv, grouped)
The embeddings client is needed by **both** the backend (query-time) and the ingestion job (corpus-time),
so add it to both groups (see [dependencies.md](dependencies.md)):

```powershell
uv add --optional backend openai
uv add --group ingestion openai
```
No `pip`. (`openai` is just the SDK; you point it at the embeddings model — you are not using a chat model.)

---

## Step 3 — Configure the model + dimension (one source of truth)
Put the model name and its dimension in typed config so ingestion and query-time always agree.

```python
# app/config.py  (Pydantic settings)
class Settings(BaseSettings):
    embeddings_model: str = "text-embedding-3-small"
    embeddings_dim: int = 1536          # MUST match the model and the pgvector column
    # the key itself is resolved from Vault, not here
```

---

## Step 4 — Build the embeddings adapter (one place, swappable)
All provider calls live behind a single adapter so the rest of the app never imports the SDK directly —
and tests can mock it.

```python
# app/infra/embeddings.py
from openai import OpenAI
from app.config import settings
from app.infra.vault import get_secret

_client = OpenAI(api_key=get_secret("EMBEDDINGS_API_KEY"))

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings → list of vectors (each length settings.embeddings_dim)."""
    resp = _client.embeddings.create(model=settings.embeddings_model, input=texts)
    return [d.embedding for d in resp.data]

def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]
```
Notes: **batch** (send many texts per call — far cheaper/faster than one-at-a-time), and add a small
retry/backoff for rate limits. Keep this the *only* file that imports the embeddings SDK.

---

## Step 5 — Enable pgvector and add the embedding column
Enable the extension and store one vector per recipe, **with the metadata you'll filter on** (category,
diet, allergens, cuisine) so retrieval can pre-filter cheaply.

```python
# alembic migration (once)
op.execute("CREATE EXTENSION IF NOT EXISTS vector")

# app/models/recipe.py
from pgvector.sqlalchemy import Vector
from app.config import settings

class RecipeEmbedding(Base):
    __tablename__ = "recipe_embeddings"
    recipe_id  = Column(ForeignKey("recipes.id"), primary_key=True)
    embedding  = Column(Vector(settings.embeddings_dim))   # 1536
    category   = Column(String, index=True)   # hot drink | cold drink | breakfast | lunch | dinner
    diet       = Column(ARRAY(String))         # e.g. ["vegetarian"]
    allergens  = Column(ARRAY(String))         # e.g. ["tree_nut"]
    cuisine    = Column(String, index=True)
```

---

## Step 6 — Decide what text to embed (the chunking choice)
Don't embed the giant instructions blob. Embed a **composed "recipe document"** that captures what a cook
searches by — this is the one non-naive chunking decision the plan asks you to make and defend:

```python
# app/services/shared/recipe_view.py
def embedding_text(recipe) -> str:
    return (
        f"{recipe.title}. "
        f"Category: {recipe.category}. Cuisine: {recipe.cuisine}. "
        f"Ingredients: {', '.join(i.name for i in recipe.ingredients)}."
    )
```
**Why:** title + category + cuisine + ingredients is what queries actually match on; the step-by-step
text adds noise. Record this choice (and the hit@k it gives) in `docs/DECISIONS.md`.

---

## Step 7 — Embed the corpus at ingestion time
After fetching + categorizing + extracting ingredients, embed every recipe and store the vector + metadata.

```python
# ingestion/embed_recipes.py
from app.infra.embeddings import embed_texts
from app.services.shared.recipe_view import embedding_text

def embed_corpus(recipes, repo, batch_size=128):
    for batch in chunked(recipes, batch_size):           # batch for cost/speed
        texts   = [embedding_text(r) for r in batch]
        vectors = embed_texts(texts)
        for r, v in zip(batch, vectors):
            repo.upsert_embedding(
                recipe_id=r.id, embedding=v,
                category=r.category, diet=r.diet, allergens=r.allergens, cuisine=r.cuisine,
            )
```
Run it as part of the pipeline:
```powershell
uv run python -m ingestion.run_ingest     # fetch → categorize → extract → EMBED
```
This is a **one-time cost** per recipe — you embed at ingestion, not on every request.

---

## Step 8 — Build the vector index (fast search)
A sequential scan is fine for a few hundred rows but slow for thousands. Add an ANN index matching your
distance metric. With OpenAI embeddings use **cosine** distance:

```sql
CREATE INDEX ON recipe_embeddings
USING hnsw (embedding vector_cosine_ops);
-- (or ivfflat with lists=100 for a smaller corpus)
```
Use the **same operator** (`vector_cosine_ops` / `<=>`) in your queries that you used to build the index.

---

## Step 9 — Retrieve at query time (embed → filter → search)
At request time: embed the query with the **same model**, then search pgvector **with the metadata
pre-filter and freshness exclusion baked into the SQL** (cheap, and part of the safety story).

```python
# app/repo/recipes.py  — the ONLY place that touches the DB
def search(self, qvec, category, diet, allergens, exclude_ids, k=10):
    return self.session.execute(
        select(RecipeEmbedding.recipe_id)
        .where(RecipeEmbedding.category == category)                 # category filter (the 5 chips)
        .where(~RecipeEmbedding.allergens.overlap(allergens))        # never an allergen the cook has
        .where(RecipeEmbedding.recipe_id.notin_(exclude_ids))        # freshness: skip already-seen
        .order_by(RecipeEmbedding.embedding.cosine_distance(qvec))   # nearest by meaning
        .limit(k)
    ).scalars().all()
```
```python
# app/services/user/rag.py
def retrieve(query, prefs, seen_ids):
    qvec = embed_text(query)                      # same model as ingestion
    ids  = recipes_repo.search(
        qvec, category=prefs.category, diet=prefs.diet,
        allergens=prefs.allergens, exclude_ids=seen_ids, k=10,
    )
    recipes = recipes_repo.get_many(ids)
    return constraint_guard.filter(recipes, prefs)   # THE WALL — deterministic, final safety net
```
Order of operations matters: **embed → metadata pre-filter + freshness in SQL → nearest-neighbour →
constraint guard**. The allergen filter appears twice on purpose (once in SQL for efficiency, once in the
deterministic guard as the non-negotiable wall).

---

## Step 10 — Wire it into the chat flow
- The **workflow** path (easy `find_recipe` intents) calls `rag.retrieve(...)` directly.
- The **agent** path calls the same retrieval through its `search_recipes` tool.
Either way the LLM only *ranks and explains* the retrieved recipes — it never invents them. The detail
view renders stored steps verbatim.

---

## Step 11 — Evaluate and tune (prove it with a number)
Embeddings quality is measured, not assumed. Build the RAG golden set and gate it in CI.

```
evals/rag/golden.yaml   # ~15 triples: query → ideal answer → ground-truth recipe ids
uv run python evals/run_evals.py
```
Report **hit@k** and **MRR** (did the right recipes come back, and how high?) plus faithfulness. Use these
numbers to choose between options and record the winner in `docs/DECISIONS.md`:
- The composed embedding text (Step 6) vs. alternatives.
- One improvement: a **rerank** step, or tighter **metadata pre-filtering**.
- (Optionally) a different embedding model — but re-embed the corpus if you switch.

---

## Step 12 — Operate it (cost, caching, drift)
- **Cost:** embed the corpus **once** at ingestion; each query is a single short embed. Batch ingestion
  calls. Per-turn cost shows up in Phoenix traces.
- **Cache:** you may cache the query→vector for identical repeated queries (Redis), but freshness already
  varies the *results*, so this is optional.
- **Dimension/model drift:** if you change the model, change `embeddings_dim`, migrate the column,
  rebuild the index, and **re-embed everything**. Never mix vectors from two models in one column.
- **Privacy:** the query text flows through redaction before it's logged/traced (the embed call payload
  is covered by the same redaction rule).

---

## End-to-end recap
1. Key in Vault → 2. `uv add` the client (grouped) → 3. pin model+dim in config → 4. adapter in
`app/infra/embeddings.py` → 5. pgvector column + metadata → 6. choose the embedding text → 7. embed the
corpus at ingestion → 8. build the ANN index → 9. embed the query + filtered vector search → 10. feed the
workflow/agent → 11. evaluate (hit@k/MRR) and tune → 12. operate (cost, drift, privacy).

That chain is RAG in SousChef: embeddings make discovery *semantic*, pgvector makes it *fast*, the
metadata filter + constraint guard make it *safe*, and the LLM only ever ranks real, retrieved recipes.
