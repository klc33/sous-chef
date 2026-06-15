// The cook sign-in screen (009, FR-002/FR-011) — a clean, professional login with NO sign-up control.
//
// Cook accounts are admin-provisioned, so there is deliberately no "create account" path here: a cook
// either has credentials or asks an admin for them. On submit it calls POST /auth/login (api.login), which
// stores the cook-session token on success; the parent App then swaps this screen for the app. A wrong
// username/password is the backend's single generic 401 (no enumeration), shown as one calm message; any
// other failure reads as a connection problem.

import { useState } from "react";

import { api, ApiError } from "../api/client.js";

export default function Login({ onLoggedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // Authenticate, persist the session (handled inside api.login), and hand the username up to App. A 401 is
  // the expected bad-credentials path (one generic message — the backend never says which field was wrong);
  // anything else is surfaced as a reach-the-kitchen problem.
  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { username: who } = await api.login(username.trim(), password);
      onLoggedIn(who);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Invalid username or password."
          : "Couldn’t reach the kitchen. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login__card">
        <h1 className="login__brand">SousChef</h1>
        <p className="login__tagline">Sign in to discover recipes made for you.</p>

        {error && (
          <div className="banner banner--error" role="alert">
            {error}
          </div>
        )}

        <form className="login__form" onSubmit={handleSubmit}>
          <label className="login__field">
            <span>Username</span>
            <input
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={busy}
              required
            />
          </label>
          <label className="login__field">
            <span>Password</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
              required
            />
          </label>
          <button type="submit" className="login__submit" disabled={busy || !username || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="login__note">Accounts are created by your administrator.</p>
      </div>
    </div>
  );
}
