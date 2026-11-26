/** A hire's board milestones in one row (Day 15 · Settled in · 3 of 5 done ...), each opening the board at that milestone.
 *  Shown on the program page to the hire and their managers (the board's readers); renders nothing for anyone else.
 *  Reads GET /api/onboardings/{id}/board. */
import { Glyph } from "../../dataui";
import { useApi } from "../kit";
import { Bar } from "./parts";
import type { BoardData } from "./types";
import "./board.css";

export default function MilestoneStrip({ onboardingId }: { onboardingId: string }) {
  const b = useApi<BoardData>(`/onboardings/${encodeURIComponent(onboardingId)}/board`, ["lifecycle", "tasks"]).data;
  if (!b) return null;
  const href = (key?: string) => `#/board/${encodeURIComponent(onboardingId)}${key ? `?m=${encodeURIComponent(key)}` : ""}`;
  return (
    <nav className="bd-strip" aria-label="Milestones">
      <div className="bd-strip-head">
        <p className="bd-label">Milestones</p>
        <a className="bd-strip-open" href={href()}>Open board<Glyph name="arrow" size={13} /></a>
      </div>
      <ol>
        {b.milestones.map((m) => (
          <li key={m.key} className={`s-${m.state}`}>
            <a href={href(m.key)} aria-label={`Day ${m.due_day}, ${m.title}: ${m.done} of ${m.total} tasks done${m.overdue ? `, ${m.overdue} overdue` : ""}${m.state === "current" ? ", now" : ""}`}>
              <span className="bd-strip-day">Day {m.due_day}{m.state === "current" && <span className="bd-now">Now</span>}</span>
              <span className="bd-strip-title">{m.title}</span>
              <Bar done={m.done} total={m.total} awaiting={m.awaiting} label={m.title} />
              <span className="bd-strip-n">
                {m.total ? `${m.done} of ${m.total} done` : "No tasks"}
                {m.overdue > 0 && <span className="bd-late"> · {m.overdue} overdue</span>}
              </span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
