/** #/people (admins): everyone in the organisation, with role changes, deactivation and reactivation. Invitations stay
 *  in Settings → People. GET/PATCH /api/org/members (Track G); until that API exists the page says so. */
import { useState } from "react";
import { patch, post, ROLE_LABEL } from "../api";
import { useSession } from "../session";
import { toast } from "../ui";
import { EmptyState } from "../fx";
import { Avatar, Glyph } from "../dataui";
import { isStatus, LcHead, LcSkeleton, LoadError, nameOf, NextAction, plural, shortDate, useAction, useApi } from "./kit";
import type { Deactivated, Member } from "./types";

const ROLES = ["admin", "manager", "expert", "new_hire", "executive"];

export default function People() {
  const { me } = useSession();
  const list = useApi<Member[]>("/org/members", ["people", "projects"]);
  const act = useAction();
  const [q, setQ] = useState("");
  const [left, setLeft] = useState<Deactivated | null>(null); // the last deactivation, with what was waiting on them
  const invite = <a className="dp-btn" href="#/settings?tab=people"><Glyph name="plus" size={15} />Invite people</a>;

  if (list.error && !list.data) {
    if (isStatus(list.error, 404, 405)) {
      return (
        <section className="lc">
          <LcHead title="People" actions={invite} />
          <EmptyState title="Member management is on its way" action={<a className="dp-btn primary" href="#/settings?tab=people">Open Settings → People</a>}>
            Invite and review project members in Settings for now.
          </EmptyState>
        </section>
      );
    }
    return <section className="lc"><LcHead title="People" actions={invite} /><LoadError error={list.error} retry={list.reload} /></section>;
  }
  if (!list.data) return <section className="lc"><LcSkeleton rows={5} /></section>;
  const rows = list.data.filter((m) => !q || `${m.name} ${m.email}`.toLowerCase().includes(q.toLowerCase()));
  const active = list.data.filter((m) => m.status === "active").length;

  const update = (m: Member, fn: () => Promise<Member>, msg: string) => act.run(m.id, fn).then((r) => { if (r) { toast(msg); list.reload(); } });
  return (
    <section className="lc">
      <LcHead title="People" sub={`${active} active · ${list.data.length - active} deactivated`} actions={invite} />
      {left && <Reassign d={left} close={() => setLeft(null)} />}
      <div className="lc-filters">
        <label className="lc-filter lc-filter-grow"><span className="sr-only">Find a person</span>
          <input type="search" placeholder="Find by name or email" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
      </div>
      <div className="dp-table-wrap">
        <table className="dp-table lc-people">
          <thead><tr><th scope="col">Person</th><th scope="col">Role</th><th scope="col">Projects</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {rows.map((m) => {
              const self = m.id === me.id;
              return (
                <tr key={m.id} className={m.status !== "active" ? "is-off" : ""}>
                  <th scope="row">
                    <span className="lc-person-cell"><Avatar name={nameOf(m.name)} size={28} /><span><span className="lc-strong">{nameOf(m.name)}{self ? <span className="lc-dim"> · you</span> : null}</span><span className="lc-dim lc-email">{m.email}</span></span></span>
                  </th>
                  <td>
                    <label className="sr-only" htmlFor={`role-${m.id}`}>Role of {m.name}</label>
                    <select id={`role-${m.id}`} className="lc-select-sm" value={m.role} disabled={self || m.status !== "active" || !!act.busy}
                            onChange={(e) => update(m, () => patch<Member>(`/org/members/${encodeURIComponent(m.id)}`, { role: e.target.value }), `${nameOf(m.name)} is now ${ROLE_LABEL[e.target.value] ?? e.target.value}`)}>
                      {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                    </select>
                  </td>
                  <td className="lc-wrap">{m.projects.length ? m.projects.map((p) => p.name).join(", ") : <span className="lc-dim">None</span>}</td>
                  <td><span className={`lc-state ${m.status === "active" ? "ok" : "off"}`}><i aria-hidden="true" />{m.status === "active" ? "Active" : "Deactivated"}</span></td>
                  <td className="lc-right">
                    {!self && (m.status === "active"
                      ? <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => act.run(m.id, () => post<Deactivated>(`/org/members/${encodeURIComponent(m.id)}/deactivate`, {})).then((r) => {
                          if (r) { toast(`${nameOf(m.name)} deactivated`); setLeft(r); list.reload(); }
                        })}>Deactivate</button>
                      : <button type="button" className="dp-btn small" disabled={!!act.busy} onClick={() => update(m, () => post<Member>(`/org/members/${encodeURIComponent(m.id)}/reactivate`, {}), `${nameOf(m.name)} reactivated`)}>Reactivate</button>)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && <p className="lc-quiet">Nobody matches “{q}”.</p>}
      {act.error && <p className="error" role="alert">{act.error}</p>}
      <NextAction>{invite}</NextAction>
    </section>
  );
}

/** After a deactivation: the hires they mentored and the questions routed to them, so someone picks them up. There is no
 *  reassignment endpoint yet: a mentee's program and the expert inbox are where the work continues. */
function Reassign({ d, close }: { d: Deactivated; close: () => void }) {
  const n = d.needs_reassignment;
  const mentees = n?.mentoring ?? [], asks = n?.expert_requests ?? [];
  const name = nameOf(d.name);
  return (
    <div className="lc-card lc-reassign" role="status">
      <div className="lc-reassign-head">
        <p className="lc-strong">{name} is deactivated</p>
        <button type="button" className="dp-btn ghost small" onClick={close}>Dismiss</button>
      </div>
      {!mentees.length && !asks.length ? <p className="lc-dim">Nothing was waiting on {name.split(" ")[0]}.</p> : (
        <>
          <p className="lc-dim">Waiting on {name.split(" ")[0]}: {[mentees.length && plural(mentees.length, "mentee"), asks.length && plural(asks.length, "open question")].filter(Boolean).join(" and ")}.</p>
          <ul className="lc-list">
            {mentees.map((m) => (
              <li key={m.onboarding_id} className="lc-li">
                <span className="lc-li-icon"><Glyph name="users" size={14} /></span>
                <span className="lc-li-main">{nameOf(m.hire?.name ?? "A hire")}<span className="lc-li-sub">Needs a new mentor</span></span>
                <a className="dp-btn small" href={`#/program/${encodeURIComponent(m.onboarding_id)}?project=${encodeURIComponent(m.project_id)}`}>Open program</a>
              </li>
            ))}
            {asks.map((a) => (
              <li key={a.id} className="lc-li">
                <span className="lc-li-icon"><Glyph name="message" size={14} /></span>
                <span className="lc-li-main">Question routed to them<span className="lc-li-sub">Asked {shortDate(a.created_at)}</span></span>
                <a className="dp-btn small" href={`#/inbox?project=${encodeURIComponent(a.project_id)}`}>Expert inbox</a>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
