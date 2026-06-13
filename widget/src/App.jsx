// The widget's single screen — wires constraints, category browse, chat, results, and favorites (FR-016, FR-023).
//
// State machine in one component (no router/store, per the constitution's plain-JSX rule): the left rail
// holds the cook's constraints + category chips + favorites toggle; the main column shows whatever the cook
// last did — a recipe detail, a category/browse grid, or a chat turn routed to the correct render branch.
// Every network call goes through api/client.js (which carries X-Profile-ID and hits only VITE_API_BASE).
// The widget is "dumb": it renders only backend-returned (already wall-filtered) data and invents nothing.

import { useEffect, useState } from "react";

import { api, ApiError } from "./api/client.js";
import { normalize, detectCategory } from "./lib/categories.js";
import ConstraintsForm from "./components/ConstraintsForm.jsx";
import CategoryChips from "./components/CategoryChips.jsx";
import ChatBox from "./components/ChatBox.jsx";
import RecipeCard from "./components/RecipeCard.jsx";
import RecipeDetail from "./components/RecipeDetail.jsx";
import Favorites from "./components/Favorites.jsx";
import RefusalNotice from "./components/RefusalNotice.jsx";
import MealPlanView from "./components/MealPlanView.jsx";
import ShoppingList from "./components/ShoppingList.jsx";
import SubstitutionCard from "./components/SubstitutionCard.jsx";

export default function App() {
  // The cook's constraints (mirrors /profile). Null until the first load resolves.
  const [profile, setProfile] = useState(null);
  const [savingProfile, setSavingProfile] = useState(false);

  // The active category browse (underscored value) and its returned cards.
  const [category, setCategory] = useState(null);
  const [cards, setCards] = useState([]);

  // The last chat turn's response (a ChatResponse), routed to a branch below.
  const [chatTurn, setChatTurn] = useState(null);

  // The opened recipe detail (a RecipeDetail), or null when not drilled in.
  const [detail, setDetail] = useState(null);

  // Favorites view: the saved cards + whether the panel is showing.
  const [favorites, setFavorites] = useState([]);
  const [showFavorites, setShowFavorites] = useState(false);
  const [favoriteIds, setFavoriteIds] = useState(new Set());

  // Cross-cutting UI state. `loading` is the kind of pending work ("search" | "planning" | "detail" | null)
  // so the planning path can show its distinct slower progress (FR-023); `error` is a typed, recoverable
  // banner message (never a raw stack).
  const [loading, setLoading] = useState(null);
  const [error, setError] = useState(null);

  // Turn any thrown ApiError into a calm banner message; a refusal is NOT routed here (it's a 200).
  function reportError(e) {
    if (e instanceof ApiError) {
      const msg =
        e.kind === "rate_limited"
          ? "You’re going a little fast — give it a moment."
          : e.kind === "not_found"
            ? "That recipe isn’t available."
            : e.kind === "bad_profile"
              ? "Couldn’t identify your session — please retry."
              : "Couldn’t reach the kitchen. Check your connection and retry.";
      setError(msg);
    } else {
      setError("Something went wrong. Please try again.");
    }
  }

  // Load the cook's constraints + favorites once on mount.
  useEffect(() => {
    (async () => {
      try {
        const [p, favs] = await Promise.all([api.getProfile(), api.listFavorites()]);
        setProfile(p);
        setFavorites(favs);
        setFavoriteIds(new Set(favs.map((f) => f.id)));
      } catch (e) {
        reportError(e);
      }
    })();
  }, []);

  // Persist edited constraints (PUT /profile) and refresh the cached profile.
  async function handleSaveProfile(next) {
    setSavingProfile(true);
    setError(null);
    try {
      const saved = await api.putProfile(next);
      setProfile(saved);
    } catch (e) {
      reportError(e);
    } finally {
      setSavingProfile(false);
    }
  }

  // Browse a category: clears any open detail/chat turn and loads the wall-filtered cards.
  async function handleSelectCategory(value) {
    setCategory(value);
    setShowFavorites(false);
    setDetail(null);
    setChatTurn(null);
    setError(null);
    setLoading("search");
    try {
      setCards(await api.listRecipes(value));
    } catch (e) {
      setCards([]);
      reportError(e);
    } finally {
      setLoading(null);
    }
  }

  // Open a recipe's full detail (GET /recipes/{id}).
  async function handleOpen(id) {
    setError(null);
    setLoading("detail");
    try {
      setDetail(await api.getRecipe(id));
    } catch (e) {
      reportError(e);
    } finally {
      setLoading(null);
    }
  }

  // Send a chat turn. A planning request takes the slower agent path, so we flag "planning" for a distinct
  // progress state (FR-023). The response is stored and routed to a render branch below.
  async function handleChat(message) {
    setShowFavorites(false);
    setDetail(null);
    setError(null);
    // Heuristic for the progress label only — the backend still classifies the real intent.
    const isPlanning = /\bplan\b|\bweek\b|\bmeal plan\b/i.test(message);
    setLoading(isPlanning ? "planning" : "search");
    // Category context for retrieval: an active chip wins; otherwise, if the message itself names a
    // category (e.g. "hot drink"), honor it so results stay in-category instead of leaking cross-category
    // once freshness has exhausted the matches.
    const chosenCategory = category ? normalize(category) : detectCategory(message);
    try {
      const turn = await api.chat(message, chosenCategory || undefined);
      setChatTurn(turn);
      setCards([]);
    } catch (e) {
      reportError(e);
    } finally {
      setLoading(null);
    }
  }

  // Toggle a favorite from any card/detail; optimistically reflect it and refresh the favorites list.
  async function handleToggleFavorite(id) {
    const isFav = favoriteIds.has(id);
    setError(null);
    try {
      if (isFav) {
        await api.removeFavorite(id);
      } else {
        await api.saveFavorite(id);
      }
      const favs = await api.listFavorites();
      setFavorites(favs);
      setFavoriteIds(new Set(favs.map((f) => f.id)));
      // Keep an open detail's heart in sync.
      setDetail((d) => (d && d.id === id ? { ...d, is_favorite: !isFav } : d));
    } catch (e) {
      reportError(e);
    }
  }

  // Pick what fills the main column. Order: an open detail wins; then favorites panel; then the last chat
  // turn (routed to its branch); then a category grid; else the welcome empty state.
  function renderMain() {
    if (loading === "planning") {
      return <div className="loading">Planning your week… this takes a few seconds.</div>;
    }
    if (loading) {
      return <div className="loading">Loading…</div>;
    }
    if (detail) {
      return (
        <RecipeDetail
          recipe={detail}
          onBack={() => setDetail(null)}
          onToggleFavorite={handleToggleFavorite}
        />
      );
    }
    if (showFavorites) {
      return (
        <Favorites favorites={favorites} onOpen={handleOpen} onRemove={handleToggleFavorite} />
      );
    }
    if (chatTurn) {
      return renderChatTurn(chatTurn);
    }
    if (category) {
      return renderGrid(cards);
    }
    return (
      <div className="empty">
        <p>Pick a category or ask for an idea to get started.</p>
      </div>
    );
  }

  // Route one /chat response to the correct branch (ui-contracts.md ChatTurnView).
  function renderChatTurn(turn) {
    if (turn.refused) return <RefusalNotice reply={turn.reply} />;
    if (turn.meal_plan) {
      return (
        <>
          {turn.reply && <p className="glue">{turn.reply}</p>}
          <MealPlanView
            plan={turn.meal_plan}
            onOpen={handleOpen}
            onToggleFavorite={handleToggleFavorite}
          />
          {turn.shopping_list && <ShoppingList list={turn.shopping_list} />}
        </>
      );
    }
    if (turn.shopping_list) return <ShoppingList list={turn.shopping_list} />;
    if (turn.substitution) return <SubstitutionCard substitution={turn.substitution} />;
    if (turn.recipes?.length > 0) {
      return (
        <>
          {turn.reply && <p className="glue">{turn.reply}</p>}
          {renderGrid(turn.recipes)}
        </>
      );
    }
    // Nothing to show — an honest empty state carrying the backend's reply (no fabricated content).
    return (
      <div className="empty">
        <p>{turn.reply || "No matches — try another idea or category."}</p>
      </div>
    );
  }

  // A grid of recipe cards with favorite state wired in.
  function renderGrid(list) {
    if (!list || list.length === 0) {
      return (
        <div className="empty">
          <p>Nothing here that fits your constraints — try another category.</p>
        </div>
      );
    }
    return (
      <div className="grid">
        {list.map((r) => (
          <RecipeCard
            key={r.id}
            recipe={r}
            onOpen={handleOpen}
            onToggleFavorite={handleToggleFavorite}
            isFavorite={favoriteIds.has(r.id)}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app__header">
        <h1>SousChef</h1>
        <button
          type="button"
          className={`link${showFavorites ? " link--active" : ""}`}
          onClick={() => {
            setShowFavorites((s) => !s);
            setDetail(null);
          }}
        >
          ♥ Favorites ({favorites.length})
        </button>
      </header>

      {error && (
        <div className="banner banner--error" role="alert">
          {error}
          <button type="button" className="banner__close" onClick={() => setError(null)}>
            ×
          </button>
        </div>
      )}

      <div className="app__body">
        <aside className="app__rail">
          {profile && (
            <ConstraintsForm
              profile={profile}
              onSave={handleSaveProfile}
              saving={savingProfile}
            />
          )}
          <CategoryChips selected={category} onSelect={handleSelectCategory} />
        </aside>

        <main className="app__main">
          <ChatBox onSend={handleChat} busy={!!loading} />
          {renderMain()}
        </main>
      </div>
    </div>
  );
}
