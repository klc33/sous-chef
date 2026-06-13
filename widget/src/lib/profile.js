// Passwordless cook identity for the widget (FR-018).
//
// The cook is identified only by a UUID generated once in the browser and kept in localStorage. It is
// NOT a secret — it scopes favorites + seen-history server-side and nothing else. Every backend request
// carries it as the `X-Profile-ID` header (see api/client.js). There is no login.

const STORAGE_KEY = "souschef.profileId";

// Generate a fresh UUID, preferring the platform crypto API; fall back to a v4-shaped random string only
// if `crypto.randomUUID` is unavailable (older browsers / insecure origins), so we never throw here.
function generateId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // RFC-4122-ish fallback: random hex with the version/variant nibbles fixed.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Return the cook's stable profile-ID, generating and persisting one on first call. Reads localStorage
// once; if a value is already stored it is reused so favorites/history survive reloads and sessions.
export function getProfileId() {
  let id = null;
  try {
    id = localStorage.getItem(STORAGE_KEY);
  } catch {
    // localStorage can throw in private-mode/blocked-cookie contexts; fall through to an ephemeral id.
  }
  if (!id) {
    id = generateId();
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Best-effort persistence — if storage is unavailable the id lives only for this page load.
    }
  }
  return id;
}
