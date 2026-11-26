/** #/team (managers, admins): every hire in the project with a compact timeline, state, open blockers and mentor.
 *  Filters by team, state and day range. "KT coverage" (modules, not people) is one link away. */
import { useMemo, useState } from "react";
import { useSession } from "../session";
import { EmptyState } from "../fx";
import { Glyph } from "../dataui";
import HireList from "./HireList";
import { useProjectHires } from "./hires";
import { LcHead, LcSkeleton, LoadError, NextAction, plural, useApi } from "./kit";
import type { Team as TeamT } from "./types";

const RANGES: { id: string; label: string; test: (d: number, len: number) => boolean }[] = [
  { id: "all", label: "Any day", test: () => true },
  { id: "first30", label: "Days 0–30", test: (d) => d <= 30 },
  { id: "mid", label: "Days 31–60", test: (d) => d > 30 && d <= 60 },
  { id: "late", label: "Days 61–90", test: (d) => d > 60 && d <= 90 },
  { id: "over", label: "Past the end", test: (d, len) => d > len },
];

export default function Team() {
  const { project } = useSession();
  const hires = useProjectHires(project.id);
  const teams = useApi<TeamT[]>(`/teams?project_id=${encodeURIComponent(project.id)}`);
  const [team, setTeam] = useState("all");
  const [state, setState] = useState("active");
  const [range, setRange] = useState("all");

  const rows = useMemo(() => (hires.rows ?? []).filter((r) => {
    const p = r.program.profile;
    if (team !== "all" && p.team?.id !== team) return false;
    if (state === "active" && r.status === "completed") return false;
    if (state === "attention" && (r.onTrack || r.status === "completed")) return false;
    if (state === "on_track" && (!r.onTrack || r.status === "completed")) return false;
    if (state === "completed" && r.status !== "completed") return false;
    return (RANGES.find((x) => x.id === range) ?? RANGES[0]).test(p.day, p.duration_days);
  }).sort((a, b) => Number(a.onTrack) - Number(b.onTrack) || a.program.profile.day - b.program.profile.day), [hires.rows, team, state, range]);

  const active = (hires.rows ?? []).filter((r) => r.status !== "completed");
  const attention = active.filter((r) => !r.onTrack).length;
  const filtered = team !== "all" || state !== "active" || range !== "all";

  return (
    <section className="lc">
      <LcHead title="Team"
              sub={hires.rows ? (active.length ? `${plural(active.length, "hire")} onboarding${attention ? ` · ${attention} need${attention === 1 ? "s" : ""} attention` : ""}` : "No one onboarding") : " "}
              actions={<>
                <a className="dp-btn" href="#/dashboard"><Glyph name="layers" size={15} />KT coverage</a>
                <a className="dp-btn primary" href="#/enrol"><Glyph name="plus" size={15} />Enrol</a>
              </>} />

      <div className="lc-filters" role="group" aria-label="Filters">
        <label className="lc-filter"><span>Team</span>
          <select value={team} onChange={(e) => setTeam(e.target.value)}>
            <option value="all">All teams</option>
            {(teams.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
        <label className="lc-filter"><span>State</span>
          <select value={state} onChange={(e) => setState(e.target.value)}>
            <option value="active">Active</option>
            <option value="attention">Needs attention</option>
            <option value="on_track">On track</option>
            <option value="completed">Completed</option>
            <option value="all">All</option>
          </select>
        </label>
        <label className="lc-filter"><span>Day</span>
          <select value={range} onChange={(e) => setRange(e.target.value)}>
            {RANGES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        </label>
        {filtered && <button type="button" className="dp-btn ghost small" onClick={() => { setTeam("all"); setState("active"); setRange("all"); }}>Clear</button>}
      </div>

      {hires.error && !hires.rows ? <LoadError error={hires.error} retry={hires.reload} />
        : !hires.rows ? <LcSkeleton rows={4} head={false} />
        : hires.rows.length === 0 ? (
          <EmptyState title="No one is onboarding yet" action={<a className="dp-btn primary" href="#/enrol">Enrol a new hire</a>}>
            Enrol a hire: invitation, program and a drafted plan in one step.
          </EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState title="No hires match" action={<button type="button" className="dp-btn" onClick={() => { setTeam("all"); setState("active"); setRange("all"); }}>Clear filters</button>} />
        ) : (
          <>
            <div className="lc-hires-head" aria-hidden="true"><span>Hire</span><span>Timeline</span><span>Day</span><span>State</span><span>Blockers</span><span>Mentor</span></div>
            <HireList rows={rows} />
          </>
        )}

      <NextAction>
        <a className="dp-btn primary" href="#/enrol"><Glyph name="plus" size={15} />Enrol a new hire</a>
        <a className="dp-btn" href="#/approvals">Approvals</a>
      </NextAction>
    </section>
  );
}
