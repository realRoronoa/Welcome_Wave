/** The hires of a project, each with their program (milestones for the timeline) and open blockers. Used by Team, the
 *  manager's and mentor's homes, and the demo clock. One GET per hire for each part: a team is a handful of people. */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import { getShared } from "./kit";
import { useLive } from "../live";
import type { Blocker, Program } from "./types";

export type HireRow = {
  id: string; program: Program; blockers: Blocker[]; // open blockers only
  onTrack: boolean; status: string; // active | completed | paused
};

type ProjectDetail = { onboardings: { id: string; new_hire_id: string; status: string; start_date: string }[] };

const onTrackOf = (p: Program, open: Blocker[]) =>
  !p.milestones.some((m) => m.state === "overdue") && !open.some((b) => b.stage === "manager");

async function rowOf(id: string, maxAge: number): Promise<HireRow | null> {
  try {
    const [program, blockers] = await Promise.all([
      getShared<Program>(`/onboardings/${encodeURIComponent(id)}/program`, maxAge),
      getShared<{ blockers: Blocker[] }>(`/onboardings/${encodeURIComponent(id)}/blockers`, maxAge).catch(() => ({ blockers: [] as Blocker[] })),
    ]);
    const open = blockers.blockers.filter((b) => !b.resolved_at);
    return { id, program, blockers: open, onTrack: onTrackOf(program, open), status: program.profile.status };
  } catch (e) {
    if (e instanceof ApiError && (e.status === 403 || e.status === 404)) return null; // not related to this person
    throw e;
  }
}

/** Rows for the given onboarding ids (null: wait). Reloads on lifecycle, task and approval changes. */
export function useHireRows(ids: string[] | null) {
  const [rows, setRows] = useState<HireRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const seq = useRef(0);
  const key = ids?.join(",") ?? null;
  const load = useCallback((maxAge = 0) => {
    if (key === null) return;
    const n = ++seq.current;
    Promise.all(key ? key.split(",").map((id) => rowOf(id, maxAge)) : [])
      .then((r) => { if (n === seq.current) { setRows(r.filter((x): x is HireRow => !!x)); setError(null); } })
      .catch((e) => { if (n === seq.current) setError(e); });
  }, [key]);
  useEffect(() => load(4000), [load]);
  useLive(["lifecycle", "tasks", "approvals"], () => load(0));
  return { rows, error, reload: () => load(0) };
}

/** Every onboarding of the project (managers and admins). */
export function useProjectHires(projectId: string) {
  const [ids, setIds] = useState<string[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const load = useCallback((maxAge = 0) => {
    getShared<ProjectDetail>(`/projects/${encodeURIComponent(projectId)}`, maxAge)
      .then((d) => { setIds(d.onboardings.map((o) => o.id)); setError(null); })
      .catch(setError);
  }, [projectId]);
  useEffect(() => load(4000), [load]);
  useLive(["lifecycle", "people", "tasks"], () => load(0));
  const rows = useHireRows(ids);
  return { rows: rows.rows, error: error ?? rows.error, reload: () => { load(); rows.reload(); } };
}
