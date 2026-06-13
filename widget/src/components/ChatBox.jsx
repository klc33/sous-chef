// The free-text conversation input (FR-016).
//
// A single line the cook types a request into ("plan my week", "something with chicken", "swap the eggs").
// On send it hands the raw message up to the parent, which calls POST /chat and routes the response to the
// right render branch. The box stays dumb — it does not interpret the message itself.

import { useState } from "react";

// `onSend(message)` dispatches the turn; `busy` disables input while a turn is in flight (search or the
// slower planning path) so the cook can't double-send.
export default function ChatBox({ onSend, busy }) {
  const [text, setText] = useState("");

  // Submit the trimmed message and clear the box; ignore empty/whitespace-only input.
  function handleSubmit(e) {
    e.preventDefault();
    const msg = text.trim();
    if (!msg || busy) return;
    onSend(msg);
    setText("");
  }

  return (
    <form className="chatbox" onSubmit={handleSubmit}>
      <input
        type="text"
        value={text}
        placeholder="Ask for an idea, a plan, or a swap…"
        onChange={(e) => setText(e.target.value)}
        disabled={busy}
        aria-label="Message"
      />
      <button type="submit" disabled={busy || !text.trim()}>
        {busy ? "…" : "Send"}
      </button>
    </form>
  );
}
