// The free-text conversation input (FR-016).
//
// A single line the cook types a request into ("plan my week", "something with chicken", "swap the eggs").
// On send it hands the raw message up to the parent, which calls POST /chat and routes the response to the
// right render branch. The box stays dumb — it does not interpret the message itself.

import { useState } from "react";

// Plain-language "what can I ask?" guide for the hover tooltip. Each entry is one thing the cook can do
// and an example they can copy — written for non-technical home cooks, not in terms of the backend tools.
// Kept here (not the backend) because it's pure presentation copy for the widget.
const HELP_ITEMS = [
  { what: "Find a recipe", example: "Find me a spicy Thai noodle dish for dinner" },
  { what: "Plan meals + a shopping list", example: "Plan three dinners this week and give me one shopping list" },
  { what: "Check nutrition", example: "How many calories are in a chicken curry?" },
  { what: "Swap an ingredient", example: "What can I use instead of butter?" },
  { what: "See the full steps", example: "Tap any recipe card to open its full ingredients & steps" },
];

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
      {/* Hover/focus help: a "?" the cook can hover (or tab to) for a plain-language menu of what to ask.
          Pure CSS reveal (see .chat-help in styles.css) — :hover for mice, :focus-within for keyboard/touch. */}
      <div className="chat-help">
        <button
          type="button"
          className="chat-help__trigger"
          aria-label="How to use SousChef"
          aria-describedby="chat-help-tip"
        >
          i
        </button>
        <div className="chat-help__tip" role="tooltip" id="chat-help-tip">
          <p className="chat-help__title">Just type what you want — for example:</p>
          <ul className="chat-help__list">
            {HELP_ITEMS.map((item) => (
              <li key={item.what}>
                <span className="chat-help__what">{item.what}</span>
                <span className="chat-help__example">“{item.example}”</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <button type="submit" disabled={busy || !text.trim()}>
        {busy ? "…" : "Send"}
      </button>
    </form>
  );
}
