/** The project's teams: add one, rename one. Reports, the anonymous survey (floored per team) and the Team page group hires by
 *  team, and Enrol puts a hire in one. Used by Settings > Onboarding and the Setup page's "Add teams" step.
 *  GET /api/teams?project_id, POST /api/teams, PATCH /api/teams/{id} (managers and admins). */
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, patch, post } from "../../api";
import { Msg, Note, toast } from "../../ui";
import "./settings-more.css";

type Team = { id: string; name: string; project_id: string };

export default function Teams({ projectId, onChange }: { projectId: string; onChange?: (teams: Team[]) => void }) {
  const [teams, setTeams] = useState<Team[] | null>(null);
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const changed = useRef(onChange);
  changed.current = onChange;

  const load = useCallback(() => {
    api<Team[]>(`/teams?project_id=${encodeURIComponent(projectId)}`)
      .then((t) => { setTeams(t); changed.current?.(t); })
      .catch((e) => { setTeams([]); setMsg({ ok: false, text: errorMessage(e) }); });
  }, [projectId]);
  useEffect(load, [load]);

  const taken = (n: string, except?: string) => (teams ?? []).some((t) => t.id !== except && t.name.trim().toLowerCase() === n.trim().toLowerCase());

  async function add(e: FormEvent) {
    e.preventDefault();
    const n = name.trim();
    if (!n || busy) return;
    if (taken(n)) return setMsg({ ok: false, text: `There is already a team called ${n}.` });
    setBusy(true); setMsg(null);
    try {
      await post<Team>("/teams", { project_id: projectId, name: n });
      setName(""); toast(`Team ${n} added`); load();
    } catch (err) { setMsg({ ok: false, text: errorMessage(err) }); } finally { setBusy(false); }
  }

  async function rename(e: FormEvent) {
    e.preventDefault();
    if (!editing || busy) return;
    const n = editing.name.trim();
    const before = teams?.find((t) => t.id === editing.id);
    if (!n || n === before?.name) return setEditing(null);
    if (taken(n, editing.id)) return setMsg({ ok: false, text: `There is already a team called ${n}.` });
    setBusy(true); setMsg(null);
    try {
      await patch<Team>(`/teams/${encodeURIComponent(editing.id)}`, { name: n });
      setEditing(null); toast("Team renamed"); load();
    } catch (err) { setMsg({ ok: false, text: errorMessage(err) }); } finally { setBusy(false); }
  }

  return (
    <div className="set-teams">
      {teams === null ? <p className="set-inset-text">Loading teams…</p>
        : teams.length === 0 ? <p className="set-inset-text">No teams yet. Reports and the anonymous survey group hires by team.</p>
        : (
          <ul className="set-team-list" aria-label="Teams">
            {teams.map((t) => (
              <li key={t.id}>
                {editing?.id === t.id ? (
                  <form className="set-team-edit" onSubmit={rename}>
                    <label className="sr-only" htmlFor={`team-${t.id}`}>New name for {t.name}</label>
                    <input id={`team-${t.id}`} value={editing.name} maxLength={120} autoFocus required
                           onChange={(e) => setEditing({ id: t.id, name: e.target.value })}
                           onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }} />
                    <button type="submit" className="set-primary" disabled={busy || !editing.name.trim()}>Save</button>
                    <button type="button" className="secondary" onClick={() => setEditing(null)} disabled={busy}>Cancel</button>
                  </form>
                ) : (
                  <>
                    <span className="set-team-name">{t.name}</span>
                    <button type="button" className="set-ghost" onClick={() => { setMsg(null); setEditing({ id: t.id, name: t.name }); }}
                            aria-label={`Rename ${t.name}`}>Rename</button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      <form className="set-team-add" onSubmit={add} aria-label="Add a team">
        <label className="field"><span>New team</span>
          <input value={name} maxLength={120} placeholder="Platform" onChange={(e) => setName(e.target.value)} />
        </label>
        <button type="submit" className="secondary" disabled={busy || !name.trim()}>Add team</button>
      </form>
      <Note msg={msg} />
    </div>
  );
}
