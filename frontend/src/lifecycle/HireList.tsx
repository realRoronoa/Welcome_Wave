/** A list of hires, one row each: who, a compact timeline, the day, state, open blockers and mentor. The whole row opens
 *  the hire's program. Team, the manager's home and the mentor's home share it. */
import { Avatar } from "../dataui";
import Timeline from "./Timeline";
import { dayFigure, HireState, nameOf, plural } from "./kit";
import type { HireRow } from "./hires";

export default function HireList({ rows, compact }: { rows: HireRow[]; compact?: boolean }) {
  return (
    <ul className={`lc-hires${compact ? " is-compact" : ""}`}>
      {rows.map((r) => {
        const p = r.program.profile;
        const name = nameOf(p.hire);
        const href = `#/program/${encodeURIComponent(r.id)}`;
        const sub = [p.role_title, p.team?.name].filter(Boolean).join(" · ");
        const at = dayFigure(p);
        return (
          <li key={r.id} className="lc-hire">
            <a className="lc-hire-link" href={href} aria-label={`${name}'s program`} />
            <div className="lc-hire-who">
              <Avatar name={name} size={32} />
              <div>
                <p className="lc-hire-name">{name}</p>
                <p className="lc-hire-sub">{sub}</p>
              </div>
            </div>
            <div className="lc-hire-tl">
              <Timeline variant="compact" milestones={r.program.milestones} day={p.day} duration={p.duration_days} label={`${name}'s program`} />
            </div>
            <p className="lc-hire-day"><strong>{at.main}</strong><span>{at.of}</span></p>
            <div className="lc-hire-state">
              <HireState onTrack={r.onTrack} status={r.status} />
            </div>
            {!compact && (
              <>
                <p className={`lc-hire-blockers${r.blockers.length ? " on" : ""}`}>
                  {r.blockers.length ? plural(r.blockers.length, "blocker") : <span className="lc-dim">No blockers</span>}
                </p>
                <p className="lc-hire-mentor">{p.mentor ? <><span className="lc-dim">Mentor</span> {nameOf(p.mentor)}</> : <span className="lc-dim">No mentor</span>}</p>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}
