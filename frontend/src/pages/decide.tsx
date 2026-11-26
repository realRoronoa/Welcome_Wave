/** Deciding an approval: the request, its busy and error state, and the decided state the card shows for a moment before the
 *  list reloads. Shared by every approval kind (Approvals.tsx, AgentApprovals.tsx). */
import { useEffect, useRef, useState } from "react";
import { ApiError, errorMessage, post } from "../api";
import { reducedMotion } from "../fx";

export type Outcome = { tone: "ok" | "warn"; title: string; text: string };

/** Sends a decision; keeps the card and shows why if it fails. On success the card shows the outcome for a moment, then the list reloads. */
export function useDecide(id: string, done: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const decide = (body: object, result: Outcome | ((response: any) => Outcome)) => {
    setBusy(true); setError(null);
    post(`/approvals/${id}/decide`, body)
      .then((r) => { setOutcome(typeof result === "function" ? result(r) : result); timer.current = window.setTimeout(done, reducedMotion() ? 900 : 1700); })
      .catch((e) => {
        // 409 still_preparing: the agent created this card but has not finished its run yet; nothing was decided
        setError(e instanceof ApiError && e.code === "still_preparing" ? "The agent is still finishing this proposal. Try again in a few seconds." : errorMessage(e));
        setBusy(false);
      });
  };
  return { busy, error, decide, outcome };
}

/** The decided state: a check that draws itself, then the card makes way. */
export function Decided({ o }: { o: Outcome }) {
  return (
    <div className={`dp-decided t-${o.tone}`} role="status">
      <svg className="dp-decided-mark" viewBox="0 0 52 52" aria-hidden="true">
        <circle cx="26" cy="26" r="23" />
        {o.tone === "ok" ? <path d="M15 27l7 7 15-16" /> : <path d="M26 15v14M26 36v.5" />}
      </svg>
      <div><p className="dp-decided-title">{o.title}</p><p className="dp-decided-text">{o.text}</p></div>
    </div>
  );
}
