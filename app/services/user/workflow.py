"""The deterministic workflow — handlers for the easy (non-agent) intents.

After the router picks `route == "workflow"`, `handle(...)` dispatches on the intent to the matching
handler and returns the turn's `ChatResponse`. This phase implements the two intents that need no corpus
work — `chitchat` (a friendly grounded reply) and `out_of_scope` (a safe redirect back to what SousChef
does). The corpus-backed intents are dispatched here too and each delegates to its own service:
`find_recipe`/`nutrition_q` to rag + nutrition (US1) and `substitution` to the curated allergen-safe swap
service (US4). Every reply is grounded — nothing is invented (golden rule #2).

The cook's `ConstraintProfile` + `profile_id` flow in from the endpoint (trusted context), and a DB
session is threaded through so the corpus handlers have their only DB access when they are wired.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.repo import profiles as repo_profiles
from app.schemas.chat import ChatResponse
from app.schemas.recipe import Category
from app.services.user import nutrition as nutrition_service
from app.services.user import rag, substitution
from app.services.user.constraint_guard import ConstraintProfile

# What SousChef can actually do — used by the out_of_scope redirect and the chitchat capability reply.
_CAPABILITIES = (
    "I can find real recipes, plan a few days of varied meals with one shopping list, "
    "answer nutrition questions, and suggest allergen-safe ingredient swaps."
)

# Servings an unknown cook (no stored profile) is assumed to cook for — mirrors GET /profile + the
# recipes detail default, so nutrition scales consistently whether or not the cook has a row.
_DEFAULT_SERVINGS = 2


def _chitchat(message: str) -> ChatResponse:
    """Return a friendly, grounded reply for small talk — no recipe content, no invention.

    Chitchat needs no corpus access; it just keeps the conversation warm and reminds the cook what the
    assistant can do, so a greeting naturally leads into a real request.
    """
    return ChatResponse(
        reply=f"Hi! {_CAPABILITIES} What are you in the mood for?",
        intent="chitchat",
    )


def _low_signal(message: str) -> ChatResponse:
    """Return a cheap clarification re-prompt for a zero-signal turn (FR-004a).

    The message matched no known vocabulary, so the classifier had nothing real to act on. Rather than
    spend an expensive agent call on it, ask the cook to say what they want — this also gracefully handles
    a one-word out-of-vocabulary dish ("sushi") by nudging them to phrase a full request.
    """
    return ChatResponse(
        reply="I didn't catch that — what would you like to cook?",
        intent="low_signal",
    )


def _out_of_scope(message: str) -> ChatResponse:
    """Return a safe redirect for a request outside SousChef's domain (FR-002).

    Refuses to wander off-topic and steers the cook back to cooking — honest about the boundary without
    attempting an answer it has no grounding for.
    """
    return ChatResponse(
        reply=f"That's outside what I can help with. {_CAPABILITIES}",
        intent="out_of_scope",
    )


def _find_recipe(
    session: Session,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
    category: Category | None,
) -> ChatResponse:
    """Serve a find_recipe turn: ranked, real, wall-cleared cards plus a grounded reply (FR-006/US1).

    Delegates entirely to `rag.search`, which embeds the query, retrieves over the pool, applies the wall,
    and phrases the reply about only the real cards. An empty card list is the honest "no safe match"
    answer (FR-009) and is returned as-is.
    """
    result = rag.search(session, message, cp, profile_id, category=category)
    return ChatResponse(reply=result.reply, intent="find_recipe", recipes=result.cards)


def _nutrition_q(
    session: Session,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
) -> ChatResponse:
    """Answer a nutrition question by resolving the dish to the best real recipe and scaling its nutrition.

    Per FR-034: retrieve the single best wall-cleared recipe for the question (`rag.retrieve(k=1)`), then
    rescale that recipe's stored nutrition to the cook's servings via the Phase 2 nutrition service. No
    match → an honest "couldn't find that dish"; a matched recipe without stored nutrition → an honest
    "no nutrition info". The numbers come only from a stored row (grounded; never fabricated).
    """
    matches = rag.retrieve(session, message, cp, k=1)
    if not matches:
        return ChatResponse(
            reply="I couldn't find that dish among my recipes, so I can't give its nutrition.",
            intent="nutrition_q",
        )
    recipe = matches[0]
    if recipe.nutrition is None:
        return ChatResponse(
            reply=f"I found {recipe.title}, but I don't have nutrition information for it.",
            intent="nutrition_q",
        )
    # Read the cook's serving size from their stored profile (permissive default when they have no row).
    profile = repo_profiles.get(session, profile_id)
    servings = profile.default_servings if profile is not None else _DEFAULT_SERVINGS
    n = nutrition_service.scale(recipe.nutrition, servings)
    approx = " (approximate)" if n.is_approximate else ""
    # Mirror the detail view's honesty: when some ingredients weren't measured, say how many did contribute.
    coverage = ""
    if n.unmapped_ingredient_count > 0:
        mapped = len(recipe.ingredients) - n.unmapped_ingredient_count
        coverage = f" Estimated from {mapped} of {len(recipe.ingredients)} ingredients."
    reply = (
        f"{recipe.title}, scaled to {servings} serving(s){approx}: "
        f"~{n.calories:.0f} kcal, {n.protein_g:.0f} g protein, "
        f"{n.carbs_g:.0f} g carbs, {n.fat_g:.0f} g fat.{coverage}"
    )
    return ChatResponse(reply=reply, intent="nutrition_q")


def _substitution(message: str, cp: ConstraintProfile) -> ChatResponse:
    """Answer a substitution turn with curated, allergen-safe swaps for the named ingredient (US4/FR-022).

    Parses the target ingredient out of the cook's free text and runs the curated lookup, dropping any
    swap that introduces a declared allergen (fail-closed in the `substitution` service). When no
    ingredient can be identified, ask the cook to name it; when the curated table has nothing safe (or no
    entry), the structured result carries `none_safe=true` and the reply says so honestly. The reply only
    ever names curated, allergen-safe substitutes — never an invented one (golden rule #2).
    """
    result = substitution.from_message(message, cp)
    if result is None:
        return ChatResponse(
            reply="Which ingredient would you like to replace?",
            intent="substitution",
        )
    if result.none_safe:
        reply = (
            f"I don't have a safe substitute for {result.ingredient} given your allergies — "
            "I won't suggest anything that isn't safe."
        )
    else:
        reply = (
            f"Instead of {result.ingredient}, you could use: "
            f"{', '.join(result.substitutes)}."
        )
    return ChatResponse(reply=reply, intent="substitution", substitution=result)


def handle(
    session: Session,
    intent: str,
    message: str,
    cp: ConstraintProfile,
    profile_id: str,
    category: Category | None = None,
) -> ChatResponse:
    """Dispatch a workflow-routed turn to its intent handler and return the ChatResponse.

    `chitchat` and `out_of_scope` are fully handled here; `find_recipe` (ranked discovery) and
    `nutrition_q` (grounded scaled nutrition) are wired in US1; `substitution` (curated allergen-safe
    swaps) is wired in US4. The optional `category` is the cook's explicit category hint from the request, passed
    through to retrieval as a pre-filter. An unknown intent falls back to the safe out-of-scope redirect so
    no turn is ever left unanswered.
    """
    if intent == "low_signal":
        return _low_signal(message)
    if intent == "chitchat":
        return _chitchat(message)
    if intent == "out_of_scope":
        return _out_of_scope(message)
    if intent == "find_recipe":
        return _find_recipe(session, message, cp, profile_id, category)
    if intent == "nutrition_q":
        return _nutrition_q(session, message, cp, profile_id)
    if intent == "substitution":
        return _substitution(message, cp)
    # Defensive default: any unexpected label gets the safe redirect, never an error or invented content.
    return _out_of_scope(message)
