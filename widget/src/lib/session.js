// Cook session storage for the widget (009) — the cook-session JWT + the signed-in username.
//
// The token is the ONLY credential the widget holds: it rides in the Authorization header on every backend
// call (see api/client.js) and is persisted in localStorage so a refresh keeps the cook signed in. There is
// no password stored and no signup — accounts are admin-provisioned (FR-002). The token is treated as
// expired (and cleared) once its `exp` claim passes, so a stale token never lingers to cause a surprise
// mid-action 401. The backend remains the sole authority on validity; this client-side check only pre-empts
// an obviously-dead token.

const TOKEN_KEY = "souschef.cookToken";
const USER_KEY = "souschef.cookUsername";

// Decode a JWT payload (the middle segment) WITHOUT verifying it — the widget reads only the `exp` claim to
// pre-empt an expired token locally. Returns null on any malformed token so a bad value reads as "no
// session" rather than throwing.
function decodePayload(token) {
  try {
    const payload = token.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

// True when the token is absent/malformed or its `exp` (seconds since the epoch) is at or before now.
function isExpired(token) {
  const claims = decodePayload(token);
  if (!claims || typeof claims.exp !== "number") return true;
  return claims.exp * 1000 <= Date.now();
}

// Persist a fresh session after a successful login.
export function setSession(token, username) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, username);
  } catch {
    // localStorage can be blocked (private mode) — the session then lives only for this page load.
  }
}

// Return the stored token if present AND not expired; otherwise clear the session and return null.
export function getToken() {
  let token = null;
  try {
    token = localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
  if (!token) return null;
  if (isExpired(token)) {
    clearSession();
    return null;
  }
  return token;
}

// Return the signed-in cook's username (rendered in the header), or null when not signed in.
export function getUsername() {
  try {
    return localStorage.getItem(USER_KEY);
  } catch {
    return null;
  }
}

// Drop the session — on explicit sign-out, or after the backend rejects the token with a 401.
export function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch {
    // Best-effort — nothing to clean up if storage is unavailable.
  }
}
