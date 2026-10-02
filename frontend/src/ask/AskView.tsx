import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { Answer, User } from "../types";
import { TOPIC_KEYWORDS } from "../mockData";
import TrustBadge from "../shared/TrustBadge";
import { DEPARTMENT_LABEL } from "../shared/labels";
import { DocumentIcon, SearchIcon } from "../shared/icons";

interface Props {
  user: User;
  answers: Answer[];
  setAnswers: Dispatch<SetStateAction<Answer[]>>;
}

/**
 * One exchange in the transcript. An answered turn stores the answer's **id**,
 * not a copy of the answer — so when a domain expert verifies that same answer
 * in the Verify queue, a transcript already on screen re-renders as verified
 * rather than keeping a stale snapshot.
 *
 * A `missing` turn has no id because nothing is stored for an unanswerable
 * question: `not_found` is a response, not a record.
 */
type Turn =
  | { kind: "question"; key: string; text: string }
  | { kind: "answer"; key: string; answerId: string; latencyMs: number }
  | { kind: "missing"; key: string; question: string };

/** Fake semantic search: substring match against the department's keyword sets. */
function findAnswerId(question: string, department: User["department"]): string | null {
  const needle = question.toLowerCase();
  for (const topic of TOPIC_KEYWORDS[department]) {
    if (topic.keywords.some((keyword) => needle.includes(keyword))) return topic.answerId;
  }
  return null;
}

export default function AskView({ user, answers, setAnswers }: Props) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [loading, setLoading] = useState(false);
  const timer = useRef<number | null>(null);
  const seq = useRef(0);

  // A pending answer must not land after the component unmounts.
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  /** Only this department's answers — role scoping, visible in the suggestions. */
  const inScope = answers.filter((answer) => answer.department === user.department);

  function ask(raw: string) {
    const text = raw.trim();
    if (!text || loading) return;

    // The match is resolved now, from the current `answers`, so the timeout
    // callback never reads a stale closure.
    const matchedId = findAnswerId(text, user.department);
    const matched = matchedId ? answers.find((answer) => answer.id === matchedId) : undefined;

    // A verified answer is served from the store, so it comes back noticeably
    // faster than one that has to be generated. The timing is part of the
    // product's argument, not decoration.
    const latencyMs = matched?.status === "verified" ? 260 : 820;

    const key = `turn-${++seq.current}`;
    setTurns((prev) => [...prev, { kind: "question", key, text }]);
    setQuestion("");
    setLoading(true);

    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const answerKey = `turn-${++seq.current}`;

      if (matched) {
        setAnswers((prev) =>
          prev.map((answer) =>
            answer.id === matched.id ? { ...answer, askedCount: answer.askedCount + 1 } : answer,
          ),
        );
        setTurns((prev) => [
          ...prev,
          { kind: "answer", key: answerKey, answerId: matched.id, latencyMs },
        ]);
      } else {
        // Nothing is written to `answers` here — see the Turn docblock.
        setTurns((prev) => [...prev, { kind: "missing", key: answerKey, question: text }]);
      }

      setLoading(false);
      timer.current = null;
    }, latencyMs);
  }

  return (
    <div className="ask-layout">
      <section className="card accent-teal">
        <span className="accent-bar" aria-hidden="true" />
        <header className="card-head">
          <h2>Ask anything in {DEPARTMENT_LABEL[user.department]}</h2>
        </header>
        <p className="lede">
          Retrieval is scoped to your department before a single word is generated. Questions
          outside it return nothing rather than a guess.
        </p>

        <form
          className="ask-box"
          onSubmit={(event) => {
            event.preventDefault();
            ask(question);
          }}
        >
          <span className="ask-icon" aria-hidden="true">
            <SearchIcon />
          </span>
          <input
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="What should I do first?"
            aria-label="Your question"
          />
          <button type="submit" className="btn btn-teal" disabled={!question.trim() || loading}>
            {loading ? "Searching…" : "Ask"}
          </button>
        </form>

        <div className="chip-row">
          {inScope.map((answer) => (
            <button
              key={answer.id}
              type="button"
              className="chip"
              disabled={loading}
              onClick={() => ask(answer.question)}
            >
              {answer.question}
            </button>
          ))}
        </div>

        <ul className="legend">
          <li>
            <TrustBadge status="verified" />
            <span>a person confirmed it</span>
          </li>
          <li>
            <TrustBadge status="ai_draft" />
            <span>generated, not reviewed yet</span>
          </li>
          <li>
            <TrustBadge status="stale" />
            <span>its source changed since sign-off</span>
          </li>
        </ul>
      </section>

      <section className="transcript" aria-live="polite">
        {turns.length === 0 && !loading && (
          <div className="empty">
            <span className="empty-icon" aria-hidden="true">
              <SearchIcon />
            </span>
            <p>
              <strong>Nothing asked yet.</strong> Pick one of the questions above, or type your own.
            </p>
          </div>
        )}

        {turns.map((turn) => {
          if (turn.kind === "question") {
            return (
              <article key={turn.key} className="turn turn-question">
                <h3>{turn.text}</h3>
              </article>
            );
          }

          if (turn.kind === "missing") {
            return (
              <article key={turn.key} className="turn turn-answer">
                <TrustBadge status="not_found" />
                <p className="answer-text">
                  Nothing in {DEPARTMENT_LABEL[user.department]}'s sources covers this. Rephrase it,
                  or ask a domain expert to answer and verify it so the next person gets it
                  instantly.
                </p>
              </article>
            );
          }

          // Looked up live rather than snapshotted, so a verification elsewhere
          // in the app is reflected here immediately.
          const answer = answers.find((item) => item.id === turn.answerId);
          if (!answer) return null;

          return (
            <article key={turn.key} className="turn turn-answer">
              <TrustBadge status={answer.status} verifiedBy={answer.verifiedBy} />
              <p className="answer-text">{answer.text}</p>

              {answer.status === "stale" && answer.staleReason && (
                <p className="answer-flag">{answer.staleReason}</p>
              )}

              <footer className="evidence">
                <span className="evidence-label">
                  {answer.status === "verified" ? "Served from a verified answer" : "Generated"} in{" "}
                  {(turn.latencyMs / 1000).toFixed(2)}s · asked {answer.askedCount}{" "}
                  {answer.askedCount === 1 ? "time" : "times"}
                </span>
                {answer.evidence.map((item) => (
                  <span key={`${item.source}-${item.section}`} className="citation">
                    <DocumentIcon />
                    {item.source} — {item.section}
                  </span>
                ))}
              </footer>
            </article>
          );
        })}

        {loading && (
          <article className="turn turn-answer" aria-busy="true">
            <p className="searching">Searching {DEPARTMENT_LABEL[user.department]} sources…</p>
            <span className="skeleton" style={{ width: "72%" }} />
            <span className="skeleton" style={{ width: "94%" }} />
            <span className="skeleton" style={{ width: "48%" }} />
          </article>
        )}
      </section>
    </div>
  );
}
