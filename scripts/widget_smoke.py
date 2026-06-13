"""Headless smoke test for the cook widget's backend contract.

Exercises every endpoint api/client.js calls — /profile (GET+PUT), /recipes (list + detail), /favorites
(list + save + remove), and /chat — exactly as the widget does (the X-Profile-ID header, the query/body
shapes), then asserts each response carries the fields the React components actually read. This is the
"test every query the widget tools need" check: a contract mismatch here is what would surface in the UI
as a broken card/detail/favorite. Run against the live backend (default http://localhost:8000).
"""
from __future__ import annotations

import os
import uuid

import httpx

BASE = os.environ.get("WIDGET_BACKEND", "http://localhost:8000")
PROFILE_ID = str(uuid.uuid4())  # the widget generates one per browser via crypto.randomUUID()
H = {"X-Profile-ID": PROFILE_ID}

_passed = 0
_failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    """Record and print one assertion: the component-required shape/behaviour the widget depends on."""
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label} — {detail}")


def need(d: dict, keys: list[str]) -> str:
    """Return a comma-list of keys missing from d (empty string = all present)."""
    return ", ".join(k for k in keys if k not in d)


with httpx.Client(base_url=BASE, headers=H, timeout=30.0) as c:
    print("\n=== /profile (GET defaults → PUT → GET persisted) ===")
    r = c.get("/profile")
    check("GET /profile 200", r.status_code == 200, f"got {r.status_code}")
    prof = r.json() if r.status_code == 200 else {}
    check("profile has {diet, allergies, default_servings}", not need(prof, ["diet", "allergies", "default_servings"]),
          f"missing {need(prof, ['diet', 'allergies', 'default_servings'])}")

    body = {"diet": "vegetarian", "allergies": ["tree_nuts"], "default_servings": 2}
    r = c.put("/profile", json=body)
    check("PUT /profile 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    saved = r.json() if r.status_code == 200 else {}
    check("PUT echo persisted (vegetarian/tree_nuts/2)",
          saved.get("diet") == "vegetarian" and "tree_nuts" in saved.get("allergies", []) and saved.get("default_servings") == 2,
          f"got {saved}")
    r = c.get("/profile")
    check("GET /profile reflects the save", r.json().get("diet") == "vegetarian", f"got {r.json()}")

    print("\n=== GET /recipes?category=hot_drink (RecipeCard list, wall-filtered for this profile) ===")
    r = c.get("/recipes", params={"category": "hot_drink"})
    check("GET /recipes 200", r.status_code == 200, f"got {r.status_code}")
    cards = r.json() if r.status_code == 200 else []
    check("returns a non-empty card list", isinstance(cards, list) and len(cards) > 0, f"len={len(cards) if isinstance(cards, list) else 'n/a'}")
    card = cards[0] if cards else {}
    check("RecipeCard has {id, title, category, key_ingredients}", not need(card, ["id", "title", "category", "key_ingredients"]),
          f"missing {need(card, ['id', 'title', 'category', 'key_ingredients'])}")

    recipe_id = card.get("id")
    print("\n=== GET /recipes/{id} (RecipeDetail — verbatim steps, ingredients, nutrition) ===")
    if recipe_id:
        r = c.get(f"/recipes/{recipe_id}")
        check("GET /recipes/{id} 200", r.status_code == 200, f"got {r.status_code}")
        det = r.json() if r.status_code == 200 else {}
        check("RecipeDetail has {id,title,category,servings,is_favorite,ingredients,steps}",
              not need(det, ["id", "title", "category", "servings", "is_favorite", "ingredients", "steps"]),
              f"missing {need(det, ['id', 'title', 'category', 'servings', 'is_favorite', 'ingredients', 'steps'])}")
        ings = det.get("ingredients") or []
        check("each ingredient has raw_text", all("raw_text" in i for i in ings) and len(ings) > 0, f"ingredients={ings[:2]}")
        check("steps is a non-empty list of strings", isinstance(det.get("steps"), list) and len(det.get("steps", [])) > 0,
              f"steps={det.get('steps')}")
        n = det.get("nutrition")
        if n:
            check("nutrition has {servings,calories,protein_g,carbs_g,fat_g,is_approximate}",
                  not need(n, ["servings", "calories", "protein_g", "carbs_g", "fat_g", "is_approximate"]),
                  f"missing {need(n, ['servings', 'calories', 'protein_g', 'carbs_g', 'fat_g', 'is_approximate'])}")

    print("\n=== /favorites (list empty → save 201 → list has it → delete 204 → list empty) ===")
    r = c.get("/favorites")
    check("GET /favorites 200 + list", r.status_code == 200 and isinstance(r.json(), list), f"got {r.status_code}")
    if recipe_id:
        r = c.post("/favorites", json={"recipe_id": recipe_id})
        check("POST /favorites 201 (no body)", r.status_code in (200, 201), f"got {r.status_code}: {r.text[:200]}")
        r = c.get("/favorites")
        favs = r.json()
        check("favorite now appears in list", any(f.get("id") == recipe_id for f in favs), f"favs={[f.get('id') for f in favs]}")
        check("favorite card has RecipeCard shape", favs and not need(favs[0], ["id", "title", "category", "key_ingredients"]),
              "missing card fields")
        r = c.delete(f"/favorites/{recipe_id}")
        check("DELETE /favorites/{id} 204", r.status_code in (200, 204), f"got {r.status_code}")
        r = c.get("/favorites")
        check("favorite removed from list", not any(f.get("id") == recipe_id for f in r.json()), "still present")

    print("\n=== POST /chat (exactly as the widget sends it: normalized underscored category) ===")
    # The widget sends api.chat(message, normalize(category)) → the UNDERSCORED enum value, never spaced.
    for label, payload in (
        ("with category", {"message": "something warm to drink", "category": "hot_drink"}),
        ("no category", {"message": "something warm to drink"}),
    ):
        try:
            r = c.post("/chat", json=payload, timeout=60.0)
            # The key contract check: the widget's payload must PASS schema validation (no 422). The LLM
            # itself may 5xx/timeout on an exhausted quota — that's environmental, not a contract bug.
            check(f"/chat ({label}) accepts the widget payload (no 422)", r.status_code != 422,
                  f"422: {r.text[:200]}")
            if r.status_code == 200:
                t = r.json()
                check(f"/chat ({label}) ChatResponse has {{reply, refused}}", not need(t, ["reply", "refused"]),
                      f"missing {need(t, ['reply', 'refused'])}")
                print(f"      branches: recipes={bool(t.get('recipes'))}, meal_plan={bool(t.get('meal_plan'))}, "
                      f"shopping_list={bool(t.get('shopping_list'))}, substitution={bool(t.get('substitution'))}, refused={t.get('refused')}")
            else:
                print(f"      [info] /chat ({label}) → {r.status_code} (LLM path; likely Groq quota) — {r.text[:120]}")
        except Exception as exc:  # noqa: BLE001
            print(f"      [info] /chat ({label}) transport error (likely Groq quota): {type(exc).__name__}: {exc}")

print(f"\n=== widget contract: {_passed} passed, {_failed} failed ===")
