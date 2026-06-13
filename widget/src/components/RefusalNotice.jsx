// A calm safety refusal — visually distinct from a system error (FR-020).
//
// When the backend returns `refused: true` (a guardrail declined the turn), the cook sees a composed,
// non-alarming note carrying only the backend's safe `reply`. This is NOT an error state: nothing broke,
// the assistant simply won't do that. Kept separate from the network/error banner on purpose.

// `reply` is the backend's safe refusal text (already grounded + redacted server-side).
export default function RefusalNotice({ reply }) {
  return (
    <div className="refusal" role="status">
      <span className="refusal__icon" aria-hidden="true">
        ⚠
      </span>
      <p>{reply || "I can’t help with that request."}</p>
    </div>
  );
}
