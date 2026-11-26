/** #/enrol (managers, admins): one calm form that starts a draft program from a template; the Planner drafts one 30/60/90
 *  plan into Approvals, and approving it creates the milestones, the tasks and the invitation (a link the manager shares,
 *  and emailed too when the server sends email). POST /api/onboardings/enrol (docs/specs/enrolment-and-plan.md). */
import { FormEvent, useMemo, useState } from "react";
import { post, today } from "../api";
import { useSession } from "../session";
import { Glyph } from "../dataui";
import { toast } from "../ui";
import { copy, LcHead, LcSkeleton, LoadError, nameOf, shortDate, useAction, useApi } from "./kit";
import type { Enrolled, Program, Team, Template } from "./types";

type Detail = { members: { id: string; name: string; role: string; email: string }[] };

function nextMonday() {
  const d = new Date(`${today()}T00:00:00`);
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

/** #/enrol?email=…&name=…&github=…: a person already in the project (Settings > Onboarding links here), whose onboarding the
 *  enrolment starts, or adopts when one was started without a program. */
function prefill() {
  const q = new URLSearchParams(location.hash.split("?")[1] ?? "");
  return { name: q.get("name") ?? "", email: q.get("email") ?? "", github_login: q.get("github") ?? "" };
}

export default function Enrol() {
  const { project, me } = useSession();
  const templates = useApi<Template[]>("/programs/templates");
  const teams = useApi<Team[]>(`/teams?project_id=${encodeURIComponent(project.id)}`);
  const detail = useApi<Detail>(`/projects/${encodeURIComponent(project.id)}`, ["people"]);
  const modules = useApi<{ modules: { module: string }[] }>(`/coverage?project_id=${encodeURIComponent(project.id)}`, ["sources"]);
  const act = useAction();
  const [done, setDone] = useState<{ r: Enrolled; name: string; email: string; template: Template | null } | null>(null);
  const [f, setF] = useState(() => ({ ...prefill(), start_date: nextMonday(), template_id: "", team_id: "", mentor_id: "", focus_area: "", role_title: "" }));
  const set = (k: keyof typeof f) => (v: string) => setF((x) => ({ ...x, [k]: v }));

  const byTrack = useMemo(() => {
    const m = new Map<string, Template[]>();
    for (const t of (templates.data ?? []).filter((t) => t.active)) {
      if (!m.has(t.role_track)) m.set(t.role_track, []);
      m.get(t.role_track)!.push(t);
    }
    return [...m.entries()];
  }, [templates.data]);
  const tpl = templates.data?.find((t) => t.id === f.template_id) ?? null;
  const mentors = (detail.data?.members ?? []).filter((m) => (m.role === "expert" || m.role === "manager") && m.email.toLowerCase() !== f.email.trim().toLowerCase());
  const areas = (modules.data?.modules ?? []).map((m) => m.module).filter((m) => !/^\(|^docs$|^tests$/.test(m));
  const valid = f.name.trim() && /\S+@\S+\.\S+/.test(f.email) && f.start_date && f.template_id;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    const body = {
      project_id: project.id, name: f.name.trim(), email: f.email.trim(), github_login: f.github_login.trim() || null, start_date: f.start_date,
      template_id: f.template_id, team_id: f.team_id || null, mentor_id: f.mentor_id || null, focus_area: f.focus_area.trim() || null,
      role_title: f.role_title.trim() || tpl?.title || null,
    };
    act.run("enrol", () => post<Enrolled>("/onboardings/enrol", body)).then((r) => {
      if (r) { setDone({ r, name: f.name.trim(), email: f.email.trim(), template: tpl }); scrollTo({ top: 0 }); }
    });
  }

  if (done) return <Created d={done} again={() => { setDone(null); setF((x) => ({ ...x, name: "", email: "", github_login: "", role_title: "" })); }} />;
  const loading = !templates.data || !detail.data;
  return (
    <section className="lc lc-narrow">
      <LcHead title="Enrol a new hire" sub="The Planner drafts a 30/60/90 plan. Approving it starts the program." back={{ href: "#/team", label: "Team" }} />
      {templates.error ? <LoadError error={templates.error} retry={templates.reload} /> : loading ? <LcSkeleton rows={3} head={false} /> : (
        <form className="lc-form lc-enrol" onSubmit={submit} noValidate>
          <fieldset>
            <legend>Who</legend>
            <div className="lc-grid2">
              <label className="field"><span>Name</span><input value={f.name} onChange={(e) => set("name")(e.target.value)} maxLength={120} autoComplete="off" required autoFocus /></label>
              <label className="field"><span>Email</span><input type="email" value={f.email} onChange={(e) => set("email")(e.target.value)} maxLength={320} autoComplete="off" required /></label>
              <label className="field"><span>GitHub login <em>optional</em></span><input value={f.github_login} onChange={(e) => set("github_login")(e.target.value.replace(/[^A-Za-z0-9-]/g, ""))} maxLength={39} autoComplete="off" /></label>
              <label className="field"><span>Role title <em>optional</em></span><input value={f.role_title} placeholder={tpl?.title ?? ""} onChange={(e) => set("role_title")(e.target.value)} maxLength={120} /></label>
            </div>
          </fieldset>
          <fieldset>
            <legend>Program</legend>
            <div className="lc-grid2">
              <label className="field lc-span2"><span>Template</span>
                <select value={f.template_id} onChange={(e) => set("template_id")(e.target.value)} required>
                  <option value="">Choose a template</option>
                  {byTrack.map(([track, list]) => (
                    <optgroup key={track} label={track}>
                      {list.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
                    </optgroup>
                  ))}
                </select>
                {tpl && <span className="lc-field-note">{tpl.duration_days} days · {tpl.role_track} · {tpl.level} · {tpl.milestones.length} milestones</span>}
              </label>
              <label className="field"><span>Start date</span><input type="date" value={f.start_date} onChange={(e) => set("start_date")(e.target.value)} required /></label>
              <label className="field"><span>Team</span>
                <select value={f.team_id} onChange={(e) => set("team_id")(e.target.value)}>
                  <option value="">No team</option>
                  {(teams.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </label>
              <label className="field"><span>Mentor</span>
                <select value={f.mentor_id} onChange={(e) => set("mentor_id")(e.target.value)}>
                  <option value="">No mentor yet</option>
                  {mentors.map((m) => <option key={m.id} value={m.id}>{nameOf(m.name)}{m.id === me.id ? " (you)" : ""}</option>)}
                </select>
              </label>
              <label className="field"><span>Focus area <em>optional</em></span>
                <input list="enrol-areas" value={f.focus_area} onChange={(e) => set("focus_area")(e.target.value)} maxLength={200} placeholder={areas[0] ?? ""} />
                <datalist id="enrol-areas">{areas.map((a) => <option key={a} value={a} />)}</datalist>
              </label>
            </div>
          </fieldset>
          <div className="lc-form-foot">
            <a className="dp-btn ghost" href="#/team">Cancel</a>
            <button type="submit" className="dp-btn primary" disabled={!valid || !!act.busy}>{act.busy ? "Enrolling…" : "Enrol"}</button>
          </div>
          {act.error && <p className="error" role="alert">{act.error}</p>}
        </form>
      )}
    </section>
  );
}

/** Where an invitation is accepted: the link the API gives, else the sign-in page (the hire signs in with the invited email
 *  and accepts it there). Nothing is emailed: the manager shares the link. */
function inviteLink(inv: NonNullable<Enrolled["invitation"]>) {
  const u = inv.url ?? inv.link ?? inv.invite_url ?? "/login";
  return /^https?:/.test(u) ? u : `${location.origin}${u.startsWith("/") ? u : `/${u}`}`;
}

function Created({ d, again }: { d: { r: Enrolled; name: string; email: string; template: Template | null }; again: () => void }) {
  const { r } = d;
  const { config } = useSession(); // config.email: this server emails invitations
  // the Planner drafts the 30/60/90 plan in the background: follow the program until the proposal waits in Approvals, and
  // until it is approved (approving starts the program and creates the invitation)
  const live = useApi<Program>(`/onboardings/${encodeURIComponent(r.onboarding.id)}/program`, ["lifecycle", "approvals"]);
  const plan = live.data?.plan ?? r.plan;
  const approved = plan?.status === "approved";
  const status = live.data?.profile.status ?? (r.program as { status?: string } | null)?.status ?? r.onboarding.status;
  const draft = status === "draft" && !approved;
  const inv = r.invitation;
  const link = inv ? inviteLink(inv) : null;
  const first = d.name.split(/\s+/)[0];
  const programHref = `#/program/${encodeURIComponent(r.onboarding.id)}`;
  return (
    <section className="lc lc-narrow">
      <LcHead title={`${d.name} is enrolled`} sub={`Starts ${shortDate(r.onboarding.start_date)}${d.template ? ` · ${d.template.title} · ${d.template.duration_days} days` : ""}`} />
      <ol className="lc-created">
        <li className={approved || plan?.pending_approval_id ? "on" : "wait"}>
          <span className="lc-created-mark" aria-hidden="true"><Glyph name={approved || plan?.pending_approval_id ? "check" : "clock"} size={13} /></span>
          <div>
            <p className="lc-strong">{approved ? "Plan approved" : plan?.pending_approval_id ? "Plan drafted" : "Plan being drafted"}</p>
            <p className="lc-dim">{approved ? "Days 1–30, 31–60 and 61–90, with milestones and tasks." : plan?.pending_approval_id ? "A 30/60/90 plan waits for your approval." : "The Planner drafts it. It appears in Approvals in a moment."}</p>
          </div>
          {!approved && <a className="dp-btn small" href={plan?.pending_approval_id ? `#/approvals/${encodeURIComponent(plan.pending_approval_id)}` : "#/approvals"}>Review</a>}
        </li>
        <li className={draft ? "wait" : "on"}>
          <span className="lc-created-mark" aria-hidden="true"><Glyph name={draft ? "clock" : "check"} size={13} /></span>
          <div>
            <p className="lc-strong">{draft ? "Program drafted" : "Program started"}</p>
            <p className="lc-dim">{draft ? "It starts when you approve the plan." : "Milestones, tasks and access requests are in place."}</p>
          </div>
        </li>
        <li className={inv ? "on" : draft ? "wait" : "on"}>
          <span className="lc-created-mark" aria-hidden="true"><Glyph name={inv || !draft ? "check" : "clock"} size={13} /></span>
          <div className="lc-created-body">
            <p className="lc-strong">{inv ? "Invitation created" : draft ? "Invitation" : "Already a member"}</p>
            {inv && link ? (
              <>
                <p className="lc-dim">{config.email ? <>Emailed to {d.email}. You can also share this link with {first}.</>
                  : <>No email is sent. Share this link with {first}; they sign in as {d.email}.</>}</p>
                <div className="lc-invite">
                  <code className="lc-invite-url" title={link}>{link}</code>
                  <button type="button" className="dp-btn small" onClick={() => copy(link, (ok) => toast(ok ? "Link copied" : "Could not copy the link", ok ? "ok" : "error"))}>Copy link</button>
                </div>
              </>
            ) : <p className="lc-dim">{draft ? (config.email ? `Created when you approve the plan, then emailed to ${d.email}.` : "Created when you approve the plan. You share the link; no email is sent.") : d.email}</p>}
          </div>
        </li>
      </ol>
      <div className="lc-next">
        <a className="dp-btn primary" href={programHref}>Open program<Glyph name="arrow" size={14} /></a>
        <button type="button" className="dp-btn" onClick={again}>Enrol another</button>
      </div>
    </section>
  );
}
