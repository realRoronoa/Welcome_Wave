import { useEffect, useState } from "react";
import { api, ApiError, day, errorMessage, post, Task, User } from "../../api";
import { useSession } from "../../session";
import { Msg, Note } from "../../ui";
import type { OnboardingRow, SectionProps } from "../Settings";
import { Avatar, daysSince, Glyph, SectionCard } from "./kit";
import Teams from "./Teams";
import "./settings-more.css";

/** #/enrol, filled in for someone already in the project. Enrol chooses the program template, mentor and team, and adopts an
 *  onboarding that was started without a program (an older client), so the lifecycle sees the hire. */
export function enrolHref(m?: Pick<User, "email" | "name" | "github_login">) {
  if (!m) return "#/enrol";
  const q = new URLSearchParams({ email: m.email, name: m.name });
  if (m.github_login) q.set("github", m.github_login);
  return `#/enrol?${q}`;
}

export default function Onboarding({ d, reload }: SectionProps) {
  const { project, demo } = useSession();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  // per onboarding: "ready" once a task was approved, "review" while its proposals wait in Approvals. Neither (no tasks, or
  // every one rejected) is what the API accepts a new plan request for.
  const [planned, setPlanned] = useState<Map<string, "ready" | "review"> | null>(null);
  const [asked, setAsked] = useState<Set<string>>(new Set()); // onboardings whose plan is on its way: no button to ask again

  useEffect(() => {
    api<Task[]>(`/tasks?project_id=${project.id}`).then((ts) => {
      const m = new Map<string, "ready" | "review">();
      for (const t of ts) {
        const id = t.onboarding_id ?? "";
        if (t.status === "proposed") { if (!m.has(id)) m.set(id, "review"); }
        else if (t.status !== "rejected") m.set(id, "ready");
      }
      setPlanned(m);
    }).catch(() => setPlanned(null));
  }, [project.id, d]);

  const onboarding = new Set(d.onboardings.map((o) => o.new_hire_id));
  const eligible = d.members.filter((m) => m.role === "new_hire" && !onboarding.has(m.id));
  const name = (id: string) => d.members.find((m) => m.id === id)?.name ?? "A new hire";
  const member = (id: string) => d.members.find((m) => m.id === id);

  const approvals = <a href="#/approvals">Approvals</a>;
  // ask again when no plan arrived: the API re-runs the plan for this onboarding, or answers 409 while the first run still works
  async function askAgain(hireId: string, startDate: string, title: string) {
    setBusy(true); setMsg(null);
    try {
      const ob = await post<OnboardingRow>(`/projects/${project.id}/onboardings`, { new_hire_id: hireId, start_date: startDate, role_title: title.trim() || "Software Engineer" });
      setAsked((s) => new Set(s).add(ob.id));
      setMsg({ ok: true, text: <>A new plan for {name(hireId)} is on its way to {approvals}.</> });
      reload();
    } catch (err) {
      if (err instanceof ApiError && err.code === "plan_in_progress") {
        const o = d.onboardings.find((x) => x.new_hire_id === hireId);
        if (o) setAsked((s) => new Set(s).add(o.id));
        setMsg({ ok: true, text: <>The plan for {name(hireId)} is still being drafted. It will wait in {approvals}.</> });
      } else setMsg({ ok: false, text: errorMessage(err) });
    } finally { setBusy(false); }
  }
  return (
    <>
    <SectionCard id="ob-h" title="Onboarding"
                 description={<>You approve each plan in {approvals}.</>}
                 actions={d.onboardings.length > 0 ? <span className="set-count">{d.onboardings.length} active</span> : undefined}>
      {d.onboardings.length > 0 && (
        <ul className="set-obs">
          {d.onboardings.map((o) => {
            const has = planned?.get(o.id);
            const waiting = !!planned && !has;
            const n = daysSince(o.start_date);
            const plan = !planned ? null : has ?? (asked.has(o.id) ? "preparing" : "missing");
            const orphan = (o as OnboardingRow & { has_program?: boolean }).has_program === false;
            return (
              <li key={o.id}>
                <Avatar name={name(o.new_hire_id)} size={36} />
                <span className="set-ob-text">
                  <strong>{name(o.new_hire_id)}</strong>
                  <span>{o.role_title} · {day(o.start_date)}</span>
                </span>
                {n !== null && <span className="set-day" title={`Started ${day(o.start_date)}`}>{n < 0 ? `Starts in ${-n} ${-n === 1 ? "day" : "days"}` : <>Day <b>{n + 1}</b></>}</span>}
                {orphan ? (
                  <a className="set-link-btn primary" href={enrolHref(member(o.new_hire_id))}
                     title="This onboarding has no program yet: no milestones, check-ins or completion review">Set up program</a>
                ) : <>
                  {plan === "review" ? <a className="set-plan p-review" href="#/approvals">Review plan</a>
                    : plan && <span className={`set-plan p-${plan}`}>{plan === "ready" ? "Plan in place" : plan === "preparing" ? "Drafting plan" : "No plan yet"}</span>}
                  {waiting && !asked.has(o.id) && (
                    <button className="secondary" onClick={() => askAgain(o.new_hire_id, o.start_date, o.role_title)} disabled={busy}>Ask for the plan again</button>
                  )}
                  <a className="set-ghost" href={`#/program/${encodeURIComponent(o.id)}`} aria-label={`Open ${name(o.new_hire_id)}'s program`}>Program</a>
                </>}
              </li>
            );
          })}
        </ul>
      )}
      <div className="set-inset">
        <p className="set-invite-title"><Glyph name="sparkle" /> Start onboarding</p>
        <p className="set-inset-text">Enrol picks the program template, mentor and team. The drafted 30/60/90 plan waits in {approvals}.</p>
        {eligible.length > 0 ? (
          <ul className="set-enrol-list" aria-label="New hires not enrolled yet">
            {eligible.map((m) => (
              <li key={m.id}>
                <Avatar name={m.name} size={30} />
                <span className="set-ob-text"><strong>{m.name}</strong><span>{m.email}</span></span>
                <a className="set-link-btn" href={enrolHref(m)} aria-label={`Enrol ${m.name}`}>Enrol</a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="set-inset-text">Every new hire in this project is enrolled. Invite one in <a href="#/settings?tab=people">People</a>{demo ? "; they appear here once they accept" : "; they appear here once they sign in"}, or enrol someone by email.</p>
        )}
        <div className="set-inset-actions">
          <a className="set-link-btn primary" href={enrolHref()}>Enrol a new hire</a>
        </div>
        <Note msg={msg} />
      </div>
    </SectionCard>

    <SectionCard id="teams-h" title="Teams" description="Hires are enrolled into a team. Reports and the anonymous survey are grouped by team.">
      <Teams projectId={project.id} />
    </SectionCard>
    </>
  );
}
