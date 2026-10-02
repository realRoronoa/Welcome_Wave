import { useState } from "react";
import type { RoadmapProgress, User } from "../types";
import { ROADMAPS } from "../mockData";
import { DEPARTMENT_LABEL, formatDate } from "../shared/labels";
import { CheckIcon, DocumentIcon } from "../shared/icons";

interface Props {
  user: User;
}

/**
 * The new hire's landing screen — the "proactive" half of the product. The
 * roadmap is looked up by department rather than asked for, which is the whole
 * point: nobody has to know what to prompt.
 *
 * `ROADMAPS` is imported directly because it is fixed reference data that never
 * changes at runtime. Mutable state (the answer list) is only ever passed down
 * as props from App.
 */
export default function RoadmapView({ user }: Props) {
  const roadmap = ROADMAPS[user.department];

  const [progress, setProgress] = useState<RoadmapProgress>({
    completed: false,
    completedAt: null,
  });

  // Ticking off reading is local presentation state — it is not part of the
  // RoadmapProgress contract the backend will own, so it stays here.
  const [read, setRead] = useState<Record<number, boolean>>({});
  const readCount = roadmap.readFirst.filter((_, index) => read[index]).length;
  const readPercent = roadmap.readFirst.length
    ? Math.round((readCount / roadmap.readFirst.length) * 100)
    : 0;

  function completeTask() {
    setProgress({ completed: true, completedAt: new Date().toISOString() });
  }

  return (
    <div className="roadmap-layout">
      <section className="card accent-violet">
        <span className="accent-bar" aria-hidden="true" />
        <header className="card-head">
          <h2>{DEPARTMENT_LABEL[user.department]} · {roadmap.readFirst.length} things to read</h2>
        </header>
        <p className="lede">{roadmap.overview}</p>

        <div className="progress">
          <div className="progress-head">
            <span>Reading progress</span>
            <strong aria-live="polite">
              {readCount} of {roadmap.readFirst.length} done
            </strong>
          </div>
          <div
            className="progress-track"
            role="progressbar"
            aria-valuenow={readPercent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Reading progress"
          >
            <span className="progress-fill" style={{ width: `${readPercent}%` }} />
          </div>
        </div>

        <ul className="checklist">
          {roadmap.readFirst.map((item, index) => (
            <li key={item} className={read[index] ? "done" : undefined}>
              <input
                type="checkbox"
                id={`read-${index}`}
                checked={Boolean(read[index])}
                onChange={(event) =>
                  setRead((prev) => ({ ...prev, [index]: event.target.checked }))
                }
              />
              <label htmlFor={`read-${index}`}>
                <span className="checklist-icon" aria-hidden="true">
                  <DocumentIcon />
                </span>
                {item}
              </label>
            </li>
          ))}
        </ul>
      </section>

      {/* Violet, like the card above it. Both are roadmap content, and amber
          here used to collide with the one thing amber means everywhere else
          in the console — that a person has verified something. */}
      <section className="card accent-violet">
        <span className="accent-bar" aria-hidden="true" />
        <header className="card-head">
          <h2>Start here</h2>
        </header>

        <div className="task">
          <p className="task-text">{roadmap.firstTask}</p>

          {progress.completed ? (
            <p className="task-done" role="status">
              <span className="task-done-icon" aria-hidden="true">
                <CheckIcon />
              </span>
              Completed{progress.completedAt ? ` on ${formatDate(progress.completedAt)}` : ""}. Your
              manager can see this.
            </p>
          ) : (
            <button type="button" className="btn btn-primary" onClick={completeTask}>
              Mark complete
            </button>
          )}
        </div>

        <p className="hint">
          One task, not a backlog. The roadmap is a starting point — ask anything else on the Ask
          screen.
        </p>
      </section>
    </div>
  );
}
