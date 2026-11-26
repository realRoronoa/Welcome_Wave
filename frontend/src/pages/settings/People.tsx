import { FormEvent, useState } from "react";
import { ApiError, day, del, errorMessage, patch, post, ROLE_LABEL, User } from "../../api";
import { useSession } from "../../session";
import { Msg, Note } from "../../ui";
import type { Invitation, SectionProps } from "../Settings";
import { Avatar, GitHubMark, Glyph, RolePill, SectionCard } from "./kit";
import "./settings-more.css";

type InviteResult = { added: User; role_unchanged?: boolean } | { invited: Invitation; sign_in_url: string | null };
const EMPTY = { email: "", role: "new_hire", name: "", github_login: "" };

export default function People({ d, reload }: SectionProps) {
  const { project, me, demo } = useSession();
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const set = (k: keyof typeof EMPTY) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function invite(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      const r = await post<InviteResult>(`/projects/${project.id}/invitations`, {
        email: form.email.trim(), role: form.role, name: form.name.trim() || null, github_login: form.github_login.trim() || null,
      });
      setMsg({ ok: true, text: "added" in r
        ? `${r.added.name} is now a member of this project.`
          + (r.role_unchanged ? ` Their role stays ${ROLE_LABEL[r.added.role] ?? r.added.role}.` : "")
        : demo ? <>Invited. To see Welcome Wave as {r.invited.name || r.invited.email}, switch account and accept the invitation.</>
        : <>Invited. Ask them to sign in at <code>{r.sign_in_url || `${location.origin}/login`}</code> with {r.invited.email}. Share this link with them.</> });
      setForm({ ...EMPTY, role: form.role });
      reload();
    } catch (err) { setMsg({ ok: false, text: errorMessage(err) }); } finally { setBusy(false); }
  }

  async function revoke(inv: Invitation) {
    setMsg(null);
    try { await del(`/projects/${project.id}/invitations/${inv.id}`); reload(); } catch (err) { setMsg({ ok: false, text: errorMessage(err) }); }
  }

  const pending = d.invitations ?? [];
  const count = `${d.members.length} ${d.members.length === 1 ? "member" : "members"}${pending.length ? ` · ${pending.length} invited` : ""}`;
  return (
    <SectionCard id="people-h" title="People" actions={<span className="set-count">{count}</span>}>
      <form className="set-invite" onSubmit={invite} aria-label="Invite someone">
        <p className="set-invite-title"><Glyph name="mail" /> Invite someone</p>
        <div className="set-invite-grid">
          <label className="field f-email"><span>Email</span><input type="email" required placeholder="e.g. name@company.com" value={form.email} onChange={set("email")} /></label>
          <label className="field"><span>Role</span>
            <select value={form.role} onChange={set("role")}>
              <option value="new_hire">New hire</option>
              <option value="expert">Expert (answers questions)</option>
              <option value="manager">Manager</option>
              {me.role === "admin" && <option value="executive">Executive (reads reports)</option>}
              {me.role === "admin" && <option value="admin">Admin</option>}
            </select>
          </label>
          <label className="field"><span>Name <em>optional</em></span><input maxLength={120} value={form.name} onChange={set("name")} /></label>
          <label className="field"><span>GitHub username <em>optional</em></span>
            <input maxLength={39} pattern="[A-Za-z0-9\-]*" title="Letters, numbers and dashes" placeholder="e.g. octocat" value={form.github_login} onChange={set("github_login")} /></label>
          <button type="submit" className="set-primary" disabled={busy}>{busy ? <><span className="en-spin" aria-hidden="true" />Saving…</> : "Invite"}</button>
        </div>
        <p className="hint">The GitHub username links their pull requests to their tasks.</p>
        <Note msg={msg} />
      </form>

      <div className="set-table-wrap">
        <table className="set-table">
          <thead><tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col" className="c-gh">GitHub</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {d.members.map((m) => (
              <tr key={m.id}>
                <th scope="row">
                  <span className="set-person">
                    <Avatar name={m.name} you={m.id === me.id} />
                    <span className="set-person-text">
                      <span className="set-person-name">{m.name}{m.id === me.id && <span className="set-you">You</span>}</span>
                      <span className="set-person-sub">{m.email}</span>
                    </span>
                  </span>
                </th>
                <td><RolePill role={m.role} /></td>
                <td className="c-gh"><GitHubCell member={m} projectId={project.id} onSaved={reload} /></td>
                <td />
              </tr>
            ))}
          </tbody>
          {pending.length > 0 && (
            <tbody className="set-pending">
              <tr className="set-group"><th scope="colgroup" colSpan={4}>Waiting to sign in</th></tr>
              {pending.map((i) => (
                <tr key={i.id}>
                  <th scope="row">
                    <span className="set-person">
                      <Avatar name={i.name || i.email} pending />
                      <span className="set-person-text">
                        <span className="set-person-name">{i.name || i.email}</span>
                        <span className="set-person-sub">{i.name ? `${i.email} · ` : ""}expires {day(i.expires_at)}</span>
                      </span>
                    </span>
                  </th>
                  <td><RolePill role={i.role} /></td>
                  <td className="c-gh">{i.github_login ? <span className="set-gh"><GitHubMark size={13} />{i.github_login}</span> : <span className="set-none">Not linked</span>}</td>
                  <td className="c-act">
                    <button className="set-ghost-danger" onClick={() => revoke(i)} aria-label={`Revoke the invitation for ${i.email}`}>Revoke</button>
                  </td>
                </tr>
              ))}
            </tbody>
          )}
        </table>
      </div>
    </SectionCard>
  );
}

/** A member's GitHub username, which links their pull requests to their tasks: shown, and set or corrected in place by the
 *  manager (a hire enrolled without one, or with a typo). PATCH /api/projects/{id}/members/{user_id}; they are told. */
function GitHubCell({ member: m, projectId, onSaved }: { member: User; projectId: string; onSaved: () => void }) {
  const [value, setValue] = useState<string | null>(null); // null: not editing
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save(e: FormEvent) {
    e.preventDefault();
    if (value === null) return;
    setBusy(true); setError(null);
    try {
      await patch(`/projects/${projectId}/members/${encodeURIComponent(m.id)}`, { github_login: value.trim() || null });
      setValue(null); onSaved();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 422 ? "Letters, numbers and dashes only." : errorMessage(err));
    } finally { setBusy(false); }
  }
  if (value !== null) {
    return (
      <form className="set-gh-edit" onSubmit={save}>
        <label className="sr-only" htmlFor={`gh-${m.id}`}>GitHub username for {m.name}</label>
        <input id={`gh-${m.id}`} value={value} maxLength={39} pattern="[A-Za-z0-9\-]*" placeholder="e.g. octocat" autoFocus
               onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setValue(null); }}
               aria-invalid={!!error} aria-describedby={error ? `gh-${m.id}-err` : undefined} />
        <button type="submit" className="set-primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" className="set-ghost" onClick={() => { setValue(null); setError(null); }} disabled={busy}>Cancel</button>
        {error && <span className="error" id={`gh-${m.id}-err`} role="alert">{error}</span>}
      </form>
    );
  }
  return (
    <span className="set-gh-cell">
      {m.github_login ? <span className="set-gh"><GitHubMark size={13} />{m.github_login}</span> : <span className="set-none">Not linked</span>}
      <button type="button" className="set-ghost" onClick={() => setValue(m.github_login ?? "")}
              aria-label={m.github_login ? `Change ${m.name}'s GitHub username` : `Add ${m.name}'s GitHub username`}>
        {m.github_login ? "Change" : "Add"}
      </button>
    </span>
  );
}
