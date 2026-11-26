/** Program sections: the plan, milestones, the access checklist and pre-boarding. */
import { FormEvent, useState } from "react";
import { patch, post } from "../../api";
import { useSession } from "../../session";
import { Icon, toast } from "../../ui";
import { Glyph, kindOf } from "../../dataui";
import { Tick, STATE_LABEL } from "../Timeline";
import { copy, How, plural, Quiet, shortDate, titleCase, useAction, useApi } from "../kit";
import WhyThis from "../WhyThis";
import { explainOf } from "../agentModel";
import { Actions, SectionProps } from "./shared";
import type { AccessItem, Milestone } from "../types";

const TASK_STATUS: Record<string, string> = {
  approved: "Not started", in_progress: "In progress", submitted: "In review", changes_requested: "Changes requested", done: "Done",
};

/* ---------------------------------------------------------------- plan */

export function PlanSection({ p, rel }: SectionProps) {
  const plan = p.plan;
  return (
    <div className="lc-stack">
      <div className="lc-inline">
        <p className="lc-strong">{plan.status === "approved" ? "Plan approved" : plan.status === "awaiting_approval" ? "Plan awaiting approval"
          : plan.status === "drafting" ? "The plan is being drafted" : "No plan yet"}
          {plan.approved_at && plan.status === "approved" ? <span className="lc-dim"> · {shortDate(plan.approved_at)}</span> : null}</p>
        <WhyThis explain={explainOf(plan)} why={plan.why} />
      </div>
      {plan.pending_approval_id && (
        <div className="lc-callout">
          <p>{plan.proposed_tasks ? plural(plan.proposed_tasks, "proposed task") : "A proposal"} waiting for approval.</p>
          {rel === "manager"
            ? <a className="dp-btn small primary" href={`#/approvals/${encodeURIComponent(plan.pending_approval_id)}`}>Review in Approvals</a>
            : <span className="lc-dim">Your manager decides.</span>}
        </div>
      )}
      {p.plan_items.length === 0 ? <Quiet icon="clock">No approved tasks yet.</Quiet> : (
        <ul className="lc-list">
          {p.plan_items.map((t) => {
            const k = kindOf(t.kind);
            return (
              <li key={t.id} className="lc-li">
                <span className="lc-li-icon"><Glyph name={k.glyph} size={14} /></span>
                <span className="lc-li-main">{t.title}{t.issue ? <span className="lc-dim"> · #{t.issue}</span> : null}</span>
                <span className="lc-li-meta">{TASK_STATUS[t.status] ?? titleCase(t.status)}{t.due_date ? ` · ${shortDate(t.due_date)}` : ""}</span>
              </li>
            );
          })}
        </ul>
      )}
      <Actions><a className="lc-link" href="#/tasks">{rel === "hire" ? "Open my tasks" : "Open tasks"}</a></Actions>
    </div>
  );
}

/* ---------------------------------------------------------------- milestones */

function evidenceText(m: Milestone, who: SectionProps["who"]) {
  if (m.status === "pending") return null;
  const e = m.evidence as { auto?: boolean; by?: string; note?: string };
  if (e.by) return `${m.status === "done" ? "Marked done" : "Skipped"} by ${who(e.by)}${e.note && e.note !== "Seeded" ? `: ${e.note}` : ""}`;
  if (m.auto || e.auto) return "Checked automatically";
  return null;
}

export function MilestonesSection({ id, p, rel, reload, who }: SectionProps) {
  const act = useAction();
  const set = (m: Milestone, status: "pending" | "done" | "skipped") =>
    act.run(m.key + status, () => patch(`/onboardings/${encodeURIComponent(id)}/milestones/${encodeURIComponent(m.key)}`, { status })).then((r) => {
      if (r) { toast(status === "pending" ? "Milestone reopened" : status === "done" ? "Milestone done" : "Milestone skipped"); reload(); }
    });
  return (
    <div className="lc-stack">
      <ul className="lc-list lc-milestones">
        {[...p.milestones].sort((a, b) => a.due_day - b.due_day).map((m) => {
          const ev = evidenceText(m, who);
          return (
            <li key={m.key} className={`lc-li m-${m.state}`}>
              <span className="lc-li-icon"><Tick state={m.state} size={10} /></span>
              <span className="lc-li-main">
                {m.title}
                {ev && <span className="lc-li-sub">{ev}</span>}
              </span>
              <span className="lc-li-meta">
                <span className="lc-mono">Day {m.due_day}</span>
                <span className={`lc-state-text m-${m.state}`}>
                  {m.state === "done" && m.done_at ? `Done ${shortDate(m.done_at)}` : m.state === "overdue" ? `Overdue · ${shortDate(m.due_date)}`
                    : m.state === "skipped" ? "Skipped" : `${STATE_LABEL[m.state]} · ${shortDate(m.due_date)}`}
                </span>
              </span>
              {rel === "manager" && (
                <span className="lc-li-actions">
                  {m.status === "pending" ? <>
                    <button type="button" className="dp-btn small" disabled={!!act.busy} onClick={() => set(m, "done")}>Done</button>
                    <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => set(m, "skipped")}>Skip</button>
                  </> : <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => set(m, "pending")}>Reopen</button>}
                </span>
              )}
            </li>
          );
        })}
      </ul>
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </div>
  );
}

/* ---------------------------------------------------------------- access */

const ACCESS_LABEL: Record<AccessItem["status"], string> = { requested: "Waiting", granted: "Granted", not_needed: "Not needed" };

export function AccessSection({ id, p, rel, reload, meId }: SectionProps) {
  const act = useAction();
  const members = useApi<{ members: { id: string; name: string; role: string }[] }>(rel === "manager" ? `/projects/${encodeURIComponent(p.profile.project_id)}` : null, ["people"]);
  const [system, setSystem] = useState("");
  const [owner, setOwner] = useState("");
  const set = (a: AccessItem, status: AccessItem["status"]) =>
    act.run(a.id + status, () => patch(`/onboardings/${encodeURIComponent(id)}/access/${encodeURIComponent(a.id)}`, { status })).then((r) => {
      if (r) { toast(status === "granted" ? `${a.system} granted` : status === "not_needed" ? "Marked not needed" : "Reopened"); reload(); }
    });
  function add(e: FormEvent) {
    e.preventDefault();
    if (!system.trim()) return;
    act.run("add", () => post(`/onboardings/${encodeURIComponent(id)}/access`, { system: system.trim(), owner_id: owner || null })).then((r) => {
      if (r) { setSystem(""); setOwner(""); toast("Access requested"); reload(); }
    });
  }
  const owners = (members.data?.members ?? []).filter((m) => m.role !== "new_hire" && m.role !== "executive");
  return (
    <div className="lc-stack">
      {p.access.length === 0 ? <Quiet icon="shield">No access items.</Quiet> : (
        <ul className="lc-list">
          {p.access.map((a) => {
            const mine = a.owner?.id === meId;
            const can = rel === "manager" || mine;
            return (
              <li key={a.id} className={`lc-li a-${a.status}`}>
                <span className="lc-li-icon"><span className={`lc-check${a.status !== "requested" ? " on" : ""}`} aria-hidden="true" /></span>
                <span className="lc-li-main">{a.system}<span className="lc-li-sub">{mine ? "You grant this" : `Owner ${a.owner ? a.owner.name.replace(/\s*\(.*?\)/g, "") : "not set"}`}</span></span>
                <span className="lc-li-meta">{ACCESS_LABEL[a.status]}{a.status === "granted" && a.granted_at ? ` · ${shortDate(a.granted_at)}` : a.status === "requested" ? ` · since ${shortDate(a.requested_at)}` : ""}</span>
                {can && (
                  <span className="lc-li-actions">
                    {a.status === "requested" ? <>
                      <button type="button" className="dp-btn small" disabled={!!act.busy} onClick={() => set(a, "granted")}>Grant</button>
                      <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => set(a, "not_needed")}>Not needed</button>
                    </> : <button type="button" className="dp-btn small ghost" disabled={!!act.busy} onClick={() => set(a, "requested")}>Reopen</button>}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {rel === "manager" && (
        <form className="lc-addrow" onSubmit={add}>
          <label className="sr-only" htmlFor={`acc-sys-${id}`}>System</label>
          <input id={`acc-sys-${id}`} placeholder="Add a system, e.g. VPN" value={system} maxLength={120} onChange={(e) => setSystem(e.target.value)} />
          <label className="sr-only" htmlFor={`acc-own-${id}`}>Owner</label>
          <select id={`acc-own-${id}`} value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">Owner: you</option>
            {owners.filter((m) => m.id !== meId).map((m) => <option key={m.id} value={m.id}>{m.name.replace(/\s*\(.*?\)/g, "")}</option>)}
          </select>
          <button type="submit" className="dp-btn" disabled={!system.trim() || !!act.busy}>Request</button>
        </form>
      )}
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </div>
  );
}

/* ---------------------------------------------------------------- pre-boarding */

export function PreboardingSection({ id, p, rel, reload }: SectionProps) {
  const { config } = useSession();
  const act = useAction();
  const [link, setLink] = useState<{ url: string; expires_at: string } | null>(null);
  const checks = p.environment_checks;
  const last = checks[checks.length - 1];
  const live = p.preboarding.expires_at && new Date(p.preboarding.expires_at).getTime() > Date.now();
  function create() {
    act.run("link", () => post<{ token: string; url: string; expires_at: string }>(`/onboardings/${encodeURIComponent(id)}/preboarding-link`, {})).then((r) => {
      if (r) { setLink({ url: new URL(r.url, location.origin).href, expires_at: r.expires_at }); reload(); }
    });
  }
  return (
    <div className="lc-stack lc-pre">
      <div className="lc-pre-grid">
        <div>
          <p className="lc-label">Documents</p>
          {p.acknowledgements.length === 0 ? <Quiet icon="file">Nothing to acknowledge.</Quiet> : (
            <ul className="lc-list">
              {p.acknowledgements.map((a) => (
                <li key={a.doc_key} className="lc-li">
                  <span className="lc-li-icon"><span className={`lc-check${a.acknowledged_at ? " on" : ""}`} aria-hidden="true" /></span>
                  <span className="lc-li-main">{a.title}</span>
                  <span className="lc-li-meta">{a.acknowledged_at ? `Acknowledged ${shortDate(a.acknowledged_at)}` : "Waiting"}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <p className="lc-label">Environment check</p>
          {!last ? <Quiet icon="clock">Not run yet.</Quiet> : (
            <div className={`lc-env${last.ok ? " ok" : " fail"}`}>
              <p className="lc-env-head"><strong>{last.ok ? "Passing" : "Failing"}</strong><span className="lc-dim"> · {shortDate(last.ran_at)} · {plural(checks.length, "run")}</span></p>
              <ul className="lc-tools">
                {last.results.map((r) => <li key={r.name} className={r.ok ? "ok" : "fail"} title={r.detail}>{r.name}</li>)}
              </ul>
              {!last.ok && <p className="lc-env-why">{last.results.filter((r) => !r.ok).map((r) => `${r.name}: ${r.detail}`).join(" · ")}</p>}
            </div>
          )}
        </div>
      </div>
      <div className="lc-pre-link">
        <p className="lc-label">Pre-boarding link</p>
        {link ? (
          <div className="lc-once" role="status">
            <div className="lc-once-row">
              <input readOnly value={link.url} aria-label="Pre-boarding link" onFocus={(e) => e.currentTarget.select()} />
              <button type="button" className="dp-btn primary" onClick={() => copy(link.url, (ok) => toast(ok ? "Link copied" : "Could not copy the link", ok ? "ok" : "error"))}>
                <Icon name="link" size={14} />Copy
              </button>
            </div>
            <p className="lc-dim">Shown once. Valid until {shortDate(link.expires_at)}.</p>
          </div>
        ) : (
          <div className="lc-inline">
            <p className="lc-dim">{live ? `A link is live until ${shortDate(p.preboarding.expires_at)}.` : "No live link."}</p>
            {rel === "manager" && (
              <button type="button" className="dp-btn small" onClick={create} disabled={!!act.busy}>
                {act.busy ? "Creating…" : live ? "Create a new link" : "Create pre-boarding link"}
              </button>
            )}
            {rel === "manager" && live && <How label="Replaces" title="What a new link does" text="A new link replaces the live one; the old one stops working." />}
          </div>
        )}
        {config.mode === "demo" && rel !== "hire" && <p className="lc-dim lc-demo-note">Demo: Arjun’s page is at <a href="/welcome/demo-welcome-arjun">/welcome/demo-welcome-arjun</a>.</p>}
      </div>
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </div>
  );
}
