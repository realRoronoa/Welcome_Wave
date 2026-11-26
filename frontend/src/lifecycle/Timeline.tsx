/** The onboarding timeline: day 0 to the program's last day, milestones as ticks with their state, and a today marker.
 *  `full` (a hire's home, the program page) carries an axis and a caption; `compact` (team rows) is one thin line.
 *  Monochrome: ink for done and due soon, hairline for upcoming, a dashed ring for skipped; only overdue uses the
 *  app's danger token. */
import { CSSProperties, useMemo } from "react";
import type { Milestone, MilestoneState } from "./types";
import { shortDate } from "./kit";

export const STATE_LABEL: Record<MilestoneState, string> = {
  done: "Done", skipped: "Skipped", overdue: "Overdue", due_soon: "Due soon", upcoming: "Upcoming",
};

/** One milestone's mark, shared by the timeline, the milestone list and the legend. */
export function Tick({ state, size = 10 }: { state: MilestoneState; size?: number }) {
  return <span className={`tl-mark m-${state}`} style={{ width: size, height: size }} aria-hidden="true" />;
}

type Props = {
  milestones: Milestone[]; day: number; duration: number; variant?: "full" | "compact";
  label?: string; // what the timeline belongs to, for screen readers ("Vijay's program")
};

export default function Timeline({ milestones, day, duration, variant = "full", label = "Onboarding timeline" }: Props) {
  const span = Math.max(duration, 1);
  const pos = (d: number) => Math.max(0, Math.min(100, (d / span) * 100));
  const today = pos(day);
  const sorted = useMemo(() => [...milestones].sort((a, b) => a.due_day - b.due_day), [milestones]);
  // crowded ticks (days 2, 3, 5 of 90) step up into a second and third lane instead of hiding each other
  const lanes = useMemo(() => {
    const out: number[] = [];
    const last = [-99, -99, -99];
    for (const m of sorted) {
      const x = pos(m.due_day);
      const lane = variant === "compact" ? 0 : [0, 1, 2].find((l) => x - last[l] >= 2.6) ?? 0;
      last[lane] = x;
      out.push(lane);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sorted, span, variant]);
  const done = milestones.filter((m) => m.state === "done" || m.state === "skipped").length;
  const overdue = milestones.filter((m) => m.state === "overdue").length;
  const next = sorted.find((m) => m.status === "pending");
  const before = day < 0; // not started: the marker waits at day 0
  const summary = `${label}: ${before ? `starts in ${-day} ${day === -1 ? "day" : "days"}` : `day ${day} of ${duration}`}. ${done} of ${milestones.length} checkpoints done${overdue ? `, ${overdue} overdue` : ""}.`;
  const axis = variant === "full" ? [0, ...[30, 60, 90].map((d) => Math.round((d * duration) / 90))].filter((d, i, a) => a.indexOf(d) === i && d <= duration) : [];

  return (
    <div className={`tl tl-${variant}`} role="group" aria-label={summary}>
      <div className="tl-track">
        <span className="tl-line" aria-hidden="true" />
        <span className="tl-fill" style={{ width: `${today}%` }} aria-hidden="true" />
        {sorted.map((m, i) => (
          <span key={m.key} className={`tl-tick lane-${lanes[i]}`} style={{ left: `${pos(m.due_day)}%` } as CSSProperties}
                data-tip={variant === "full" ? `${m.title} · day ${m.due_day} · ${STATE_LABEL[m.state].toLowerCase()}` : undefined}
                tabIndex={variant === "full" ? 0 : undefined} role={variant === "full" ? "img" : undefined}
                aria-label={variant === "full" ? `${m.title}, day ${m.due_day}, ${STATE_LABEL[m.state]}` : undefined}>
            <Tick state={m.state} size={variant === "full" ? 11 : 7} />
          </span>
        ))}
        <span className={`tl-today${today > 82 ? " flip" : ""}`} style={{ left: `${today}%` }} aria-hidden="true">
          {variant === "full" && <span className="tl-today-label">{before ? `Starts in ${-day} ${day === -1 ? "day" : "days"}` : `Today · day ${day}`}</span>}
        </span>
      </div>
      {variant === "full" && (
        <div className="tl-axis" aria-hidden="true">
          {axis.map((d) => <span key={d} style={{ left: `${pos(d)}%` }}>{d === 0 ? "Day 0" : d}</span>)}
        </div>
      )}
      {variant === "full" && (
        <div className="tl-caption">
          {next ? (
            <p><span className="tl-cap-k">Next</span><Tick state={next.state} size={9} /><span className="tl-cap-t">{next.title}</span>
              <span className="tl-cap-d">{next.state === "overdue" ? `overdue since ${shortDate(next.due_date)}` : `due ${shortDate(next.due_date)}`}</span></p>
          ) : <p><span className="tl-cap-k">Next</span><span className="tl-cap-t">Every milestone is settled</span></p>}
          <ul className="tl-legend" aria-hidden="true">
            {(["done", "due_soon", "upcoming", "overdue", "skipped"] as MilestoneState[])
              .filter((s) => s === "done" || s === "upcoming" || milestones.some((m) => m.state === s))
              .map((s) => <li key={s}><Tick state={s} size={8} />{STATE_LABEL[s]}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
