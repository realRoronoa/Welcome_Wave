/** #/home: one page that adapts to the role. Every home has the same spine: a short focus list (what to do next, each a
 *  link), one key figure with how it is worked out behind a disclosure, then the role's own sections, and a closing action.
 *  GET /api/home (docs/specs/homes-and-setup.md). */
import { ReactNode } from "react";
import { isManager } from "../api";
import { useSession } from "../session";
import { EmptyState } from "../fx";
import { Avatar, Glyph } from "../dataui";
import Timeline from "./Timeline";
import HireList from "./HireList";
import { useHireRows, useProjectHires, HireRow } from "./hires";
import { FigureTile } from "./ReportParts";
import WhyThis, { AgentTag } from "./WhyThis";
import { AgentsSummary } from "./Agents";
import { explainOf } from "./agentModel";
import {
  clean, dayFigure, FigureCard, firstName, hashOf, HireState, LcHead, LcSkeleton, LoadError, More, nameOf, NextAction, plural, Progress, Quiet,
  Section, shortDate, useApi,
} from "./kit";
import type {
  AdminHome, Blocker, ExecHome, ExpertHome, FocusItem, Handover, Home as HomeData, HireHome, ManagerHome, Program,
} from "./types";

function greeting(name: string) {
  const h = new Date().getHours();
  return `${h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"}, ${name}`;
}

export default function Home() {
  const { me } = useSession();
  const home = useApi<HomeData>("/home", ["lifecycle", "approvals", "tasks", "inbox", "notifications"]);
  if (home.error && !home.data) return <section className="lc"><LcHead title="Home" /><LoadError error={home.error} retry={home.reload} /></section>;
  if (!home.data) return <section className="lc"><LcSkeleton rows={4} /></section>;
  const d = home.data;
  const hello = greeting(firstName(me.name));
  switch (d.role) {
    case "new_hire": return <HireHomeView d={d} hello={hello} />;
    case "manager": return <ManagerHomeView d={d} hello={hello} />;
    case "expert": return <ExpertHomeView d={d} hello={hello} />;
    case "admin": return <AdminHomeView d={d} hello={hello} />;
    case "executive": return <ExecHomeView d={d} hello={hello} />;
  }
  return null;
}

/* ---------------------------------------------------------------- the shared spine */

function Focus({ items, empty = "Nothing needs you right now." }: { items: FocusItem[]; empty?: string }) {
  return (
    <Section title="Next up" id="focus-h">
      {items.length === 0 ? <Quiet>{empty}</Quiet> : (
        <ol className="lc-focus">
          {items.map((f) => (
            <li key={f.key} className={`p${Math.min(f.priority, 4)}`}>
              <a href={hashOf(f.link)}>
                <span className="lc-focus-dot" aria-hidden="true" />
                <span className="lc-focus-text">{clean(f.text)}</span>
                {explainOf(f) && <AgentTag name={explainOf(f)!.agent_name} />}
                <Glyph name="arrow" size={14} className="lc-focus-go" />
              </a>
              <WhyThis explain={explainOf(f)} why={f.why} tag={false} />
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}

/** The home grid: the focus list and the role's sections on the left, the figure and small cards on the right. */
function Spine({ head, focus, aside, children, next }:
  { head: ReactNode; focus: ReactNode; aside: ReactNode; children?: ReactNode; next?: ReactNode }) {
  return (
    <section className="lc lc-home">
      {head}
      <div className="lc-home-grid">
        <div className="lc-home-main">{focus}{children}</div>
        <aside className="lc-home-aside">{aside}</aside>
      </div>
      {next && <NextAction>{next}</NextAction>}
    </section>
  );
}

/* ---------------------------------------------------------------- new hire */

function HireHomeView({ d, hello }: { d: HireHome; hello: string }) {
  const card = d.sections.program;
  const id = card?.profile.onboarding_id ?? null;
  const program = useApi<Program>(id ? `/onboardings/${encodeURIComponent(id)}/program` : null);
  const blockers = useApi<{ blockers: Blocker[] }>(id ? `/onboardings/${encodeURIComponent(id)}/blockers` : null);
  if (!card) {
    return (
      <section className="lc lc-home">
        <LcHead title={hello} />
        <EmptyState title="Your program starts soon" action={<a className="dp-btn primary" href="#/chat">Ask the project</a>}>
          Your manager enrols you. Your plan and milestones appear here.
        </EmptyState>
      </section>
    );
  }
  const p = card.profile;
  const open = (blockers.data?.blockers ?? []).filter((b) => !b.resolved_at);
  const brief = d.sections.first_task;
  const programHref = `#/program/${encodeURIComponent(p.onboarding_id)}`;
  return (
    <Spine
      head={<LcHead title={hello} sub={[p.role_title, p.team?.name].filter(Boolean).join(" · ")}
                    actions={<a className="dp-btn" href="#/me"><Glyph name="shield" size={15} />What your manager sees</a>} />}
      focus={
        <>
          <div className="lc-card lc-tl-card">
            {program.data ? <Timeline milestones={program.data.milestones} day={p.day} duration={p.duration_days} label="Your program" />
              : <div className="lc-tl-placeholder" aria-hidden="true" />}
          </div>
          <Focus items={d.focus} empty="You are up to date. Keep going on your tasks." />
        </>
      }
      aside={
        <>
          <FigureCard label={dayFigure(p).label} value={p.day < 0 ? dayFigure(p).value : d.figure?.value ?? p.day} of={dayFigure(p).of} how={d.figure?.how}
                      foot={<HireState onTrack={card.status.on_track} status={p.status} />} />
          <PersonCard role="Your mentor" person={p.mentor} action={<a href="#/chat" className="lc-link">Ask the project first</a>} />
          <PersonCard role="Your manager" person={p.manager} />
        </>
      }
      next={<><a className="dp-btn primary" href={programHref}>Open my program<Glyph name="arrow" size={14} /></a><a className="dp-btn" href="#/tasks">My tasks</a></>}
    >
      {open.length > 0 && (
        <Section title="Open blockers" count={open.length} id="blockers-h" action={<More href={`${programHref}?tab=blockers`}>All blockers</More>}>
          <BlockerRows rows={open.map((b) => ({ b }))} href={`${programHref}?tab=blockers`} />
        </Section>
      )}
      <Section title="First task" id="first-h">
        {brief ? (
          <a className="lc-card lc-brief-link" href={`${programHref}?tab=first-task`}>
            <span className="lc-brief-k">{brief.issue ? `Issue #${brief.issue}` : "Task"}</span>
            <span className="lc-brief-t">{brief.title}</span>
            <span className="lc-brief-f">{plural(brief.files.length, "file")}{brief.kt_clips?.length ? <> · <span className="lc-kt">{plural(brief.kt_clips.length, "KT clip")}</span></> : null}</span>
            <Glyph name="arrow" size={15} className="lc-brief-go" />
          </a>
        ) : <Quiet icon="clock">Your manager picks it with you from a shortlist.</Quiet>}
      </Section>
    </Spine>
  );
}

function PersonCard({ role, person, action }: { role: string; person: { id: string; name: string } | null; action?: ReactNode }) {
  return (
    <div className="lc-card lc-person">
      {person ? <Avatar name={nameOf(person)} size={34} /> : <span className="lc-person-none" aria-hidden="true" />}
      <div>
        <p className="lc-person-k">{role}</p>
        <p className="lc-person-n">{person ? nameOf(person) : "Not named yet"}</p>
        {action}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- manager */

function splitOf(v: string | number | null | undefined): [ReactNode, ReactNode | undefined] {
  const m = typeof v === "string" ? /^(\d+) of (\d+)$/.exec(v) : null;
  return m ? [m[1], `of ${m[2]}`] : [v ?? "–", undefined];
}

function ManagerHomeView({ d, hello }: { d: ManagerHome; hello: string }) {
  const { project } = useSession();
  const hires = useProjectHires(project.id);
  const rows = (hires.rows ?? []).filter((r) => r.status !== "completed");
  const [value, of] = splitOf(d.figure?.value);
  return (
    <Spine
      head={<LcHead title={hello} sub={d.sections.hires.length ? `${plural(d.sections.hires.length, "hire")} onboarding` : "No one onboarding yet"}
                    actions={<a className="dp-btn primary" href="#/enrol"><Glyph name="plus" size={15} />Enrol a new hire</a>} />}
      focus={<Focus items={d.focus} />}
      aside={
        <>
          {d.figure && <FigureCard label={d.figure.label} value={value} of={of} how={d.figure.how} />}
          <a className="lc-card lc-stat" href="#/approvals">
            <span className="lc-stat-n">{d.sections.plans_awaiting_approval}</span>
            <span className="lc-stat-l">{d.sections.plans_awaiting_approval === 1 ? "Plan awaiting approval" : "Plans awaiting approval"}</span>
            <Glyph name="arrow" size={14} />
          </a>
          <AgentsSummary />
        </>
      }
      next={<><a className="dp-btn primary" href="#/enrol"><Glyph name="plus" size={15} />Enrol a new hire</a><a className="dp-btn" href="#/team">Open Team</a></>}
    >
      <Section title="Hires" count={rows.length || undefined} id="hires-h" action={<More href="#/team">Team</More>}>
        {hires.error && !hires.rows ? <LoadError error={hires.error} retry={hires.reload} />
          : !hires.rows ? <LcSkeleton rows={2} head={false} />
          : rows.length === 0 ? <Quiet icon="users">Nobody is onboarding. Enrol a hire to start a program.</Quiet>
          : <HireList rows={rows} compact />}
      </Section>
      {hires.rows && <TeamBlockers rows={rows} />}
      {hires.rows && <UpcomingCheckins rows={rows} />}
    </Spine>
  );
}

type BlockerRow = { b: Blocker; who?: string; href?: string };

function BlockerRows({ rows, href }: { rows: BlockerRow[]; href?: string }) {
  return (
    <ul className="lc-rows">
      {rows.map(({ b, who, href: own }) => (
        <li key={b.id} className="lc-row">
          <a className="lc-row-main" href={own ?? href}>
            <span className={`lc-stage s-${b.stage}`}>{b.stage === "hire" ? "Hire" : b.stage === "mentor" ? "Mentor" : "Manager"}</span>
            <span className="lc-row-text">{who && <strong>{who}</strong>}{who ? " · " : ""}{clean(b.text)}</span>
            {explainOf(b) && <AgentTag name={explainOf(b)!.agent_name} />}
            <span className="lc-row-meta">{b.business_days_open ? `${plural(b.business_days_open, "business day")}` : "Today"}</span>
          </a>
          <WhyThis explain={explainOf(b)} why={b.why} tag={false} />
        </li>
      ))}
    </ul>
  );
}

function TeamBlockers({ rows }: { rows: HireRow[] }) {
  const list: BlockerRow[] = rows.flatMap((r) => r.blockers.map((b) => ({
    b, who: nameOf(r.program.profile.hire), href: `#/program/${encodeURIComponent(r.id)}?tab=blockers`,
  })));
  return (
    <Section title="Blockers" count={list.length || undefined} id="team-blockers-h">
      {list.length === 0 ? <Quiet>No open blockers.</Quiet> : <BlockerRows rows={list} />}
    </Section>
  );
}

function UpcomingCheckins({ rows }: { rows: HireRow[] }) {
  const soon = rows.flatMap((r) => r.program.milestones
    .filter((m) => m.key.startsWith("checkin_") && m.status === "pending" && m.due_day - r.program.profile.day <= 21)
    .map((m) => ({ r, m, inDays: m.due_day - r.program.profile.day })))
    .sort((a, b) => a.inDays - b.inDays);
  return (
    <Section title="Upcoming check-ins" count={soon.length || undefined} id="checkins-h">
      {soon.length === 0 ? <Quiet icon="calendar">None in the next three weeks.</Quiet> : (
        <ul className="lc-rows">
          {soon.map(({ r, m, inDays }) => (
            <li key={r.id + m.key} className="lc-row">
              <a className="lc-row-main" href={`#/program/${encodeURIComponent(r.id)}?tab=checkins`}>
                <span className="lc-row-day">Day {m.due_day}</span>
                <span className="lc-row-text"><strong>{nameOf(r.program.profile.hire)}</strong> · check-in</span>
                <span className={`lc-row-meta${inDays < 0 ? " late" : ""}`}>
                  {inDays < 0 ? `Overdue ${-inDays}d` : inDays === 0 ? "Today" : `${shortDate(m.due_date)} · in ${inDays}d`}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/* ---------------------------------------------------------------- expert */

type KtPlan = { sessions: { key: string; title: string; est_minutes: number; deadline: string | null; booked: unknown; items: unknown[] }[] };

function ExpertHomeView({ d, hello }: { d: ExpertHome; hello: string }) {
  const { project } = useSession();
  const ids = d.sections.mentees.map((m) => m.profile.onboarding_id);
  const mentees = useHireRows(ids);
  const plan = useApi<KtPlan>(`/kt-plan?project_id=${encodeURIComponent(project.id)}`, ["meetings", "knowledge"]);
  const sessions = plan.data?.sessions ?? [];
  return (
    <Spine
      head={<LcHead title={hello} sub={d.sections.mentees.length ? `Mentoring ${d.sections.mentees.map((m) => firstName(m.profile.hire)).join(" and ")}` : undefined}
                    actions={<a className="dp-btn" href="#/inbox"><Glyph name="message" size={15} />Expert inbox</a>} />}
      focus={<Focus items={d.focus} />}
      aside={d.figure ? <FigureCard label={d.figure.label} value={d.figure.value ?? 0} how={d.figure.how}
                                    foot={<a className="lc-link" href="#/inbox">Open the inbox</a>} /> : null}
      next={<><a className="dp-btn primary" href="#/inbox">Open expert inbox<Glyph name="arrow" size={14} /></a><a className="dp-btn" href="#/kt-planner">My KT queue</a></>}
    >
      <Section title="Digest" count={d.sections.digest.length || undefined} id="digest-h" action={d.sections.digest.length ? <More href="#/inbox">Inbox</More> : undefined}>
        {d.sections.digest.length === 0 ? <Quiet>Nothing in your digest.</Quiet> : (
          <ul className="lc-rows">
            {d.sections.digest.map((q) => (
              <li key={q.id} className="lc-row"><a className="lc-row-main" href="#/inbox">
                <Glyph name="message" size={14} /><span className="lc-row-text">{q.question}</span>
                <span className="lc-row-meta">{shortDate(q.created_at)}</span></a></li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Mentees" count={ids.length || undefined} id="mentees-h">
        {ids.length === 0 ? <Quiet icon="users">You are not mentoring anyone.</Quiet>
          : mentees.error && !mentees.rows ? <LoadError error={mentees.error} retry={mentees.reload} />
          : !mentees.rows ? <LcSkeleton rows={1} head={false} /> : <HireList rows={mentees.rows} compact />}
      </Section>
      <Section title="KT sessions to deliver" count={sessions.length || undefined} id="kt-h" action={<More href="#/kt-planner">KT queue</More>}>
        {plan.error && !plan.data ? <Quiet icon="alert">The KT queue could not be loaded.</Quiet>
          : !plan.data ? <LcSkeleton rows={1} head={false} />
          : sessions.length === 0 ? <Quiet>No sessions planned for you.</Quiet> : (
          <ul className="lc-rows">
            {sessions.map((s) => (
              <li key={s.key} className="lc-row"><a className="lc-row-main" href="#/kt-planner">
                <Glyph name="video" size={14} /><span className="lc-row-text">{s.title}</span>
                <span className="lc-row-meta">{Math.round(s.est_minutes)} min{s.deadline ? ` · by ${shortDate(s.deadline)}` : ""}{s.booked ? " · booked" : ""}</span>
              </a></li>
            ))}
          </ul>
        )}
      </Section>
    </Spine>
  );
}

/* ---------------------------------------------------------------- admin */

function AdminHomeView({ d, hello }: { d: AdminHome; hello: string }) {
  const { project, me } = useSession();
  const handovers = useApi<Handover[]>(isManager(me.role) ? `/handovers?project_id=${encodeURIComponent(project.id)}` : null);
  const people = useApi<{ members: { id: string; name: string }[] }>(`/projects/${encodeURIComponent(project.id)}`, ["people"]);
  const who = (id: string) => nameOf(people.data?.members.find((m) => m.id === id)?.name ?? "Someone");
  const steps = d.sections.setup;
  const settled = steps.filter((s) => s.status !== "todo").length;
  const open = (handovers.data ?? []).filter((h) => h.status !== "done");
  return (
    <Spine
      head={<LcHead title={hello} sub={settled === steps.length ? "Setup is complete" : `Setup: ${settled} of ${steps.length} steps`}
                    actions={<a className="dp-btn primary" href="#/enrol"><Glyph name="plus" size={15} />Enrol a new hire</a>} />}
      focus={<Focus items={d.focus} />}
      aside={<>
        {d.figure && <FigureCard label={d.figure.label} value={d.figure.value ?? "–"} how={d.figure.how}
                                 foot={<a className="lc-link" href="#/people">Manage people</a>} />}
        <AgentsSummary />
      </>}
      next={settled < steps.length
        ? <><a className="dp-btn primary" href="#/setup">Continue setup<Glyph name="arrow" size={14} /></a><a className="dp-btn" href="#/team">Open Team</a></>
        : <><a className="dp-btn primary" href="#/team">Open Team<Glyph name="arrow" size={14} /></a><a className="dp-btn" href="#/reports">Reports</a></>}
    >
      <Section title="Setup" id="setup-h" action={<More href="#/setup">Setup</More>}>
        <div className="lc-card lc-setup-mini">
          <div className="lc-setup-mini-top"><strong>{settled} of {steps.length}</strong><Progress done={settled} total={steps.length} label={`${settled} of ${steps.length} setup steps settled`} /></div>
          <ul>
            {steps.map((s) => (
              <li key={s.key} className={`st-${s.status}`}><span className="lc-step-mark" aria-hidden="true" />{s.title}
                <span className="lc-dim">{s.status === "done" ? "Done" : s.status === "skipped" ? "Skipped" : "To do"}</span></li>
            ))}
          </ul>
        </div>
      </Section>
      <Section title="Handovers" count={open.length || undefined} id="handovers-h" action={<More href="#/handovers">Handovers</More>}>
        {handovers.error && !handovers.data ? <LoadError error={handovers.error} retry={handovers.reload} />
          : !handovers.data ? <LcSkeleton rows={1} head={false} />
          : open.length === 0 ? <Quiet>No one is leaving.</Quiet> : (
          <ul className="lc-rows">
            {open.map((h) => {
              const left = h.plan.items.filter((i) => !i.done).length;
              return (
                <li key={h.id} className="lc-row"><a className="lc-row-main" href="#/handovers">
                  <Glyph name="users" size={14} /><span className="lc-row-text"><strong>{who(h.user_id)}</strong> · last day {shortDate(h.last_day)}</span>
                  <span className="lc-row-meta">{left ? `${plural(left, "item")} left` : "All items done"}</span>
                </a></li>
              );
            })}
          </ul>
        )}
      </Section>
    </Spine>
  );
}

/* ---------------------------------------------------------------- executive */

function ExecHomeView({ d, hello }: { d: ExecHome; hello: string }) {
  return (
    <Spine
      head={<LcHead title={hello} sub={`Onboarding across the organisation · as of ${shortDate(d.sections.as_of)}`}
                    actions={<a className="dp-btn" href="#/reports">Full report</a>} />}
      focus={<Focus items={d.focus} />}
      aside={d.figure ? <FigureCard label={d.figure.label} value={d.figure.value ?? "–"} of={typeof d.figure.value === "number" ? "days" : undefined} how={d.figure.how} /> : null}
      next={<a className="dp-btn primary" href="#/reports">Open the full report<Glyph name="arrow" size={14} /></a>}
    >
      <Section title="This cohort" id="cohort-h" action={<More href="#/reports">Reports</More>}>
        <div className="lc-tiles">{d.sections.report.map((f) => <FigureTile key={f.key} f={f} />)}</div>
      </Section>
    </Spine>
  );
}
