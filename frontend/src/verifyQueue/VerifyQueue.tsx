import { useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { Answer, User } from "../types";
import TrustBadge from "../shared/TrustBadge";
import { DEPARTMENT_LABEL, formatDate } from "../shared/labels";
import { CheckIcon, DocumentIcon } from "../shared/icons";

interface Props {
  user: User;
  answers: Answer[];
  setAnswers: Dispatch<SetStateAction<Answer[]>>;
}

/**
 * The only place a draft becomes canonical.
 *
 * Two sections, because a domain expert has two duties: sign off on new drafts,
 * and re-confirm answers whose sources moved underneath them. The four-view
 * scope has no separate staleness screen, so the backlog lives here next to the
 * drafts rather than nowhere.
 *
 * Scoped to the expert's own department, per the build spec — an admin sees
 * their own department here and the whole organisation on the Dashboard.
 */
export default function VerifyQueue({ user, answers, setAnswers }: Props) {
  /** Edited answer text, keyed by answer id. Absent key means untouched. */
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [lastAction, setLastAction] = useState<string>("");

  const mine = answers.filter((answer) => answer.department === user.department);
  const pending = mine.filter((answer) => answer.status === "ai_draft");
  const stale = mine.filter((answer) => answer.status === "stale");

  function verify(answer: Answer) {
    const edited = drafts[answer.id];
    const text = (edited ?? answer.text).trim();
    if (!text) {
      setLastAction("An answer cannot be empty.");
      return;
    }

    setAnswers((prev) =>
      prev.map((item) =>
        item.id === answer.id
          ? {
              ...item,
              status: "verified",
              text,
              verifiedBy: user.name,
              verifiedAt: new Date().toISOString(),
              staleReason: undefined,
            }
          : item,
      ),
    );

    setDrafts((prev) => {
      const next = { ...prev };
      delete next[answer.id];
      return next;
    });

    setLastAction(
      edited !== undefined && edited.trim() !== answer.text
        ? "Edited and verified. This is the answer everyone gets now."
        : "Verified. This is the answer everyone gets now.",
    );
  }

  return (
    <div className="queue-layout">
      <section className="card accent-amber">
        <span className="accent-bar" aria-hidden="true" />
        <header className="card-head">
          <h2>Waiting on you</h2>
          <span className="count">{pending.length}</span>
        </header>
        <p className="lede">
          Verifying makes an answer canonical — it is served ahead of any new draft from then on,
          with your name on it. Edit anything that is not quite right before you sign off.
        </p>

        {lastAction && (
          <p className="action-note" role="status">
            {lastAction}
          </p>
        )}

        {pending.length === 0 ? (
          <div className="empty">
            <span className="empty-icon" aria-hidden="true">
              <CheckIcon />
            </span>
            <p>
              <strong>Queue clear.</strong> No {DEPARTMENT_LABEL[user.department]} drafts are waiting
              on your review.
            </p>
          </div>
        ) : (
          <ul className="queue">
            {pending.map((answer) => {
              const value = drafts[answer.id] ?? answer.text;
              const isEdited = value !== answer.text;

              return (
                <li key={answer.id} className="queue-item">
                  <div className="queue-item-head">
                    <h3>{answer.question}</h3>
                    <TrustBadge status="ai_draft" />
                  </div>

                  <label className="queue-edit-label" htmlFor={`draft-${answer.id}`}>
                    Answer text{isEdited ? " (edited)" : ""}
                  </label>
                  <textarea
                    id={`draft-${answer.id}`}
                    className="queue-edit"
                    value={value}
                    rows={3}
                    onChange={(event) =>
                      setDrafts((prev) => ({ ...prev, [answer.id]: event.target.value }))
                    }
                  />

                  <footer className="evidence">
                    <span className="evidence-label">
                      asked {answer.askedCount} {answer.askedCount === 1 ? "time" : "times"}
                    </span>
                    {answer.evidence.map((item) => (
                      <span key={`${item.source}-${item.section}`} className="citation">
                        <DocumentIcon />
                        {item.source} — {item.section}
                      </span>
                    ))}
                  </footer>

                  <div className="queue-actions">
                    {isEdited && (
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() =>
                          setDrafts((prev) => {
                            const next = { ...prev };
                            delete next[answer.id];
                            return next;
                          })
                        }
                      >
                        Discard edits
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn-amber btn-sm"
                      onClick={() => verify(answer)}
                    >
                      {isEdited ? "Save & verify" : "Verify"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="card accent-coral">
        <span className="accent-bar" aria-hidden="true" />
        <header className="card-head">
          <h2>Sources moved underneath these</h2>
          <span className="count">{stale.length}</span>
        </header>
        <p className="lede">
          These were verified, then the document behind them changed. They stay downgraded until
          someone confirms they are still correct.
        </p>

        {stale.length === 0 ? (
          <div className="empty">
            <span className="empty-icon" aria-hidden="true">
              <CheckIcon />
            </span>
            <p>
              <strong>Nothing stale.</strong> Every verified {DEPARTMENT_LABEL[user.department]}{" "}
              answer still matches its source.
            </p>
          </div>
        ) : (
          <ul className="queue">
            {stale.map((answer) => {
              const value = drafts[answer.id] ?? answer.text;
              const isEdited = value !== answer.text;

              return (
                <li key={answer.id} className="queue-item">
                  <div className="queue-item-head">
                    <h3>{answer.question}</h3>
                    <TrustBadge status="stale" />
                  </div>

                  {answer.staleReason && <p className="answer-flag">{answer.staleReason}</p>}

                  <label className="queue-edit-label" htmlFor={`stale-${answer.id}`}>
                    Answer text{isEdited ? " (edited)" : ""}
                  </label>
                  <textarea
                    id={`stale-${answer.id}`}
                    className="queue-edit"
                    value={value}
                    rows={3}
                    onChange={(event) =>
                      setDrafts((prev) => ({ ...prev, [answer.id]: event.target.value }))
                    }
                  />

                  <footer className="evidence">
                    <span className="evidence-label">
                      last signed off by {answer.verifiedBy ?? "—"}
                      {answer.verifiedAt ? ` on ${formatDate(answer.verifiedAt)}` : ""}
                    </span>
                    {answer.evidence.map((item) => (
                      <span key={`${item.source}-${item.section}`} className="citation">
                        <DocumentIcon />
                        {item.source} — {item.section}
                      </span>
                    ))}
                  </footer>

                  <div className="queue-actions">
                    <button
                      type="button"
                      className="btn btn-coral btn-sm"
                      onClick={() => verify(answer)}
                    >
                      {isEdited ? "Save & re-confirm" : "Still correct"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
