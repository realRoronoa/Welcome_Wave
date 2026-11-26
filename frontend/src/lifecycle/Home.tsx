/** #/home: the signed-in person's workspace, by role. Every home reads the same way, top to bottom:
 *    1. the workspace header: organisation, project and your role, then a greeting and one line of status;
 *    2. a row of key figures, each a link to where it is worked on;
 *    3. what matters for this person (left) and the small cards beside it (right);
 *    4. "How we work here": the team, the documents everyone reads first, and what was explained on record.
 *  A new hire sees their 30/60/90 milestones, the current milestone's tasks (the board, #/board), KT sessions coming up and
 *  their questions. A manager or admin sees the hires and their stage, decisions waiting for them, and recent activity,
 *  newest first. Data: GET /api/home (backend/app/lifecycle/home.py), plus the project, its KT sessions and its files. */
import { ReactNode } from "react";
import { isManager, ROLE_LABEL } from "../api";
import { useSession } from "../session";
import { EmptyState, Ring } from "../fx";
import { KtBadge } from "../ui";
import { Avatar, Glyph, GlyphName } from "../dataui";
import { useProjectHires, useHireRows, HireRow } from "./hires";
import HireList from "./HireList";
import { FigureTile } from "./ReportParts";
import WhyThis, { AgentTag } from "./WhyThis";
import { AgentsSummary } from "./Agents";
import { explainOf, KIND_LABEL } from "./agentModel";
import {
  clean, firstName, hashOf, HireState, LcSkeleton, LoadError, More, nameOf, plural, Progress, Quiet, Section, shortDate, useApi,
} from "./kit";
import type {
  AdminHome, Blocker, ExecHome, ExpertHome, FocusItem, Handover, HireCard, Home as HomeData, HireHome, ManagerHome,
} from "./types";
import "../styles/home.css";

/* ---------------------------------------------------------------- the API's product sections (home.py) */

type PhaseState = "done" | "current" | "upcoming";
type Phase = { key: string; title: string; due_day: number; due_date: string; state: PhaseState; tasks_done: number; tasks_total: number };
type PhaseTask = { id: string; title: string; kind: string; status: string; due_date: string | null; pr_number?: number | null };
type Questions = { asked: number; answered: number; answered_by_expert: number; unanswered: number };
type Activity = { id: string; at: string; action: string; text: string; link: string | null; actor: { id: string; name: string } };
type Approvals = { count: number; items: { id: string; kind: string; title: string; created_at: string; link: string }[] };
type HireExtra = { phases?: Phase[]; current?: (Phase & { tasks: PhaseTask[] }) | null; questions?: Questions };
type LeadExtra = { approvals?: Approvals; activity?: Activity[]; hires?: HireCard[] };

/* the project, its KT sessions and its files: for KT coming up and "How we work here" */
type ProjectDetail = {
  members: { id: string; name: string; role: string }[];
  documents: { id: string; title: string; status: string }[];
  repositories: { full_name: string }[];
};
type Session = { id: string; title: string; status: string; starts_at?: string | null; started_at?: string | null; provider?: string; join_url?: string | null };
type CodeFile = { path: string; kt_clips: number };

function greeting(name: string) {
  const h = new Date().getHours();
  return `${h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening"}, ${name}`;
}

export default function Home() {
  const { me } = useSession();
  const home = useApi<HomeData>("/home", ["lifecycle", "approvals", "tasks", "inbox", "notifications", "audit"]);
  if (home.error && !home.data) return <section className="lc hm"><Head hello="Home" /><LoadError error={home.error} retry={home.reload} /></section>;
  if (!home.data) return <section className="lc hm"><HomeSkeleton /></section>;
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

/* ---------------------------------------------------------------- the frame every home shares */

/** The workspace header: where you are (organisation, project) and who you are here (your role), then the greeting. */
function Head({ hello, sub, actions, meta }: { hello: string; sub?: ReactNode; actions?: ReactNode; meta?: ReactNode }) {
  const { me, project } = useSession();
  return (
    <header className="hm-head">
      <div className="hm-ws">
        <span className="hm-ws-tile" aria-hidden="true">{(project.name.trim()[0] ?? "W").toUpperCase()}</span>
        <span className="hm-ws-path">
          {me.org_name && me.org_name !== project.name && <><span className="hm-ws-org">{me.org_name}</span><span className="hm-ws-sep" aria-hidden="true">/</span></>}
          <strong>{project.name}</strong>
        </span>
        <span className="hm-role"><span className="sr-only">Your role: </span>{ROLE_LABEL[me.role] ?? me.role}</span>
        {meta}
      </div>
      <div className="hm-head-row">
        <div className="hm-head-text">
          <h1>{hello}</h1>
          {sub && <p className="hm-sub">{sub}</p>}
        </div>
        {actions && <div className="hm-actions">{actions}</div>}
      </div>
    </header>
  );
}

/** One key figure: a label, the number, a quiet line under it; a link to where it is worked on. */
function Kpi({ label, value, of, foot, href, visual, tone }:
  { label: string; value: ReactNode; of?: ReactNode; foot?: ReactNode; href?: string; visual?: ReactNode; tone?: "attn" }) {
  const body = (
    <>
      <span className="hm-kpi-top"><span className="hm-kpi-label">{label}</span>{href && <Glyph name="arrow" size={13} className="hm-kpi-go" />}</span>
      <span className="hm-kpi-row">
        <span className="hm-kpi-value">{value}{of !== undefined && <span className="hm-kpi-of">{of}</span>}</span>
        {visual}
      </span>
      {foot && <span className="hm-kpi-foot">{foot}</span>}
    </>
  );
  const cls = `hm-kpi${tone ? ` is-${tone}` : ""}`;
  return href ? <a className={cls} href={href}>{body}</a> : <div className={cls}>{body}</div>;
}

function Kpis({ children }: { children: ReactNode }) {
  return <div className="hm-kpis">{children}</div>;
}

/** The two columns: what matters (left) and the small cards beside it (right). One column on narrow screens. */
function Grid({ main, aside }: { main: ReactNode; aside: ReactNode }) {
  return (
    <div className="hm-grid">
      <div className="hm-main">{main}</div>
      <aside className="hm-aside" aria-label="At a glance">{aside}</aside>
    </div>
  );
}

function HomeSkeleton() {
  return (
    <div aria-busy="true">
      <span className="sr-only" role="status">Loading your home…</span>
      <div className="hm-skel-head"><i /><i /><i /></div>
      <div className="hm-kpis">{[0, 1, 2, 3].map((i) => <div key={i} className="hm-kpi hm-skel" />)}</div>
      <LcSkeleton rows={3} head={false} />
    </div>
  );
}

function Focus({ items, empty = "Nothing needs you right now." }: { items: FocusItem[]; empty?: string }) {
  return (
    <Section title="Next up" id="focus-h" count={items.length || undefined}>
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

/** A small titled card in the right-hand column. */
function Card({ title, action, children, id }: { title: string; action?: ReactNode; children: ReactNode; id: string }) {
  return (
    <section className="hm-card" aria-labelledby={id}>
      <div className="hm-card-head"><h2 id={id}>{title}</h2>{action}</div>
      {children}
    </section>
  );
}

/* ---------------------------------------------------------------- dates */

const DAY = 86_400_000;

function relative(iso: string) {
  const t = new Date(iso).getTime();
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`;
  const days = Math.floor(s / 86_400);
  return days === 1 ? "yesterday" : days < 7 ? `${days} days ago` : shortDate(iso);
}

function when(iso: string) {
  const d = new Date(iso);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.floor((new Date(d).setHours(0, 0, 0, 0) - today.getTime()) / DAY);
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return diff === 0 ? `Today, ${time}` : diff === 1 ? `Tomorrow, ${time}` : `${d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}, ${time}`;
}

/* ---------------------------------------------------------------- new hire */

function HireHomeView({ d, hello }: { d: HireHome; hello: string }) {
  const { project } = useSession();
  const s = d.sections as HireHome["sections"] & HireExtra;
  const card = s.program;
  const blockers = useApi<{ blockers: Blocker[] }>(card ? `/onboardings/${encodeURIComponent(card.profile.onboarding_id)}/blockers` : null);
  const sessions = useApi<Session[]>(`/meetings?project_id=${encodeURIComponent(project.id)}`, ["meetings"]);
  if (!card) {
    return (
      <section className="lc hm">
        <Head hello={hello} sub="Your program has not started yet." />
        <EmptyState title="Your program starts soon" action={<a className="dp-btn primary" href="#/chat">Ask the project</a>}>
          Your manager enrols you. Your milestones and tasks appear here.
        </EmptyState>
        <Culture />
      </section>
    );
  }
  const p = card.profile;
  const phases = s.phases ?? [];
  const current = s.current ?? null;
  const q = s.questions ?? { asked: 0, answered: 0, answered_by_expert: 0, unanswered: 0 };
  const done = phases.reduce((n, x) => n + x.tasks_done, 0), total = phases.reduce((n, x) => n + x.tasks_total, 0);
  const open = (blockers.data?.blockers ?? []).filter((b) => !b.resolved_at);
  const programHref = `#/program/${encodeURIComponent(p.onboarding_id)}`;
  const brief = s.first_task;
  const started = p.day < 0;
  return (
    <section className="lc hm">
      <Head hello={hello}
            sub={[p.role_title, p.team?.name, started ? `starts ${shortDate(p.start_date)}` : `started ${shortDate(p.start_date)}`].filter(Boolean).join(" · ")}
            meta={<HireState onTrack={card.status.on_track} status={p.status} />}
            actions={<><a className="dp-btn" href="#/me"><Glyph name="shield" size={15} />What your manager sees</a>
                       <a className="dp-btn primary" href="#/board"><Glyph name="layers" size={15} />Open my board</a></>} />
      <Kpis>
        <Kpi label="Program" value={started ? `${-p.day}` : `Day ${p.day}`} of={started ? "days to go" : `of ${p.duration_days}`}
             visual={<Ring value={started ? 0 : p.day / Math.max(1, p.duration_days)} size={40} stroke={4} tone="primary" />}
             foot={started ? "Pre-boarding" : `${Math.round((Math.max(0, p.day) / Math.max(1, p.duration_days)) * 100)}% of the way`} href={programHref} />
        <Kpi label="Tasks done" value={done} of={`of ${total}`} foot={total ? <Progress done={Math.round((done / total) * 10)} total={10} label={`${done} of ${total} tasks done`} /> : "No tasks yet"}
             href="#/tasks" />
        <Kpi label="Current milestone" value={<span className="hm-kpi-text">{current?.title ?? "All done"}</span>}
             foot={current ? `Day ${current.due_day} · due ${shortDate(current.due_date)}` : "Every milestone is done"} href="#/board" />
        <Kpi label="Questions answered" value={q.answered} of={`of ${q.asked}`}
             foot={q.answered_by_expert ? `${plural(q.answered_by_expert, "answer")} from a person` : "With sources you can open"} href="#/chat" />
      </Kpis>
      <Grid
        main={<>
          {phases.length > 0 && (
            <Section title="Your 30/60/90" id="phases-h" action={<More href={programHref}>Program</More>}>
              <Phases phases={phases} />
            </Section>
          )}
          <Section title={current ? `Now: ${current.title}` : "Now"} id="now-h" count={current?.tasks.length || undefined}
                   action={<More href="#/board">Board</More>}>
            {current && current.tasks.length > 0 ? <CurrentTasks tasks={current.tasks} /> : (
              <Quiet>{current ? "No tasks in this milestone yet. Your manager adds them." : "Every milestone is done."}</Quiet>
            )}
          </Section>
          {/* a blocker is said once: under Open blockers when that section shows, else here */}
          <Focus items={open.length ? d.focus.filter((f) => !f.key.startsWith("blocker:")) : d.focus} empty="You are up to date. Keep going on your tasks." />
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
                <span className="lc-brief-f">{plural(brief.files.length, "file")}{brief.kt_clips?.length ? <> · <KtBadge /> <span className="lc-kt">{plural(brief.kt_clips.length, "clip")}</span></> : null}</span>
                <Glyph name="arrow" size={15} className="lc-brief-go" />
              </a>
            ) : <Quiet icon="clock">Your manager picks it with you from a shortlist.</Quiet>}
          </Section>
        </>}
        aside={<>
          <UpcomingKt sessions={sessions.data} error={!!sessions.error && !sessions.data} />
          <Card title="Your people" id="people-h">
            <ul className="hm-people">
              <PersonRow role="Mentor" person={p.mentor} />
              <PersonRow role="Manager" person={p.manager} />
            </ul>
            <a className="hm-card-link" href="#/chat"><Glyph name="message" size={14} />Ask the project first</a>
          </Card>
          <Card title="Your questions" id="questions-h" action={<More href="#/chat">Ask</More>}>
            <dl className="hm-q">
              <div><dt>Asked</dt><dd>{q.asked}</dd></div>
              <div><dt>Answered</dt><dd>{q.answered}</dd></div>
              <div><dt>By a person</dt><dd>{q.answered_by_expert}</dd></div>
            </dl>
          </Card>
        </>}
      />
      <Culture />
    </section>
  );
}

/** The program's milestones as one row of steps (a column on phones): done, now, or still ahead, and the tasks done in each. */
function Phases({ phases }: { phases: Phase[] }) {
  return (
    <ol className="hm-phases">
      {phases.map((p) => (
        <li key={p.key} className={`hm-phase is-${p.state}`} aria-current={p.state === "current" ? "step" : undefined}>
          <span className="hm-phase-node" aria-hidden="true">{p.state === "done" && <Glyph name="check" size={11} />}</span>
          <span className="hm-phase-day">Day {p.due_day}{p.state === "current" && <span className="hm-now">Now</span>}</span>
          <span className="hm-phase-title">{p.title}</span>
          <span className="hm-phase-meta">
            <span className="hm-bar" role="img" aria-label={`${p.tasks_done} of ${p.tasks_total} tasks done`}>
              <i style={{ transform: `scaleX(${p.tasks_total ? p.tasks_done / p.tasks_total : p.state === "done" ? 1 : 0})` }} />
            </span>
            <span className="hm-phase-n">{p.tasks_total ? `${p.tasks_done}/${p.tasks_total}` : "–"}</span>
          </span>
          <span className="sr-only">{p.state === "done" ? "Done." : p.state === "current" ? "Current milestone." : "Upcoming."} Due {shortDate(p.due_date)}.</span>
        </li>
      ))}
    </ol>
  );
}

/** A task's status in the board's words (board/Board.tsx): a finished task waits for sign-off, or is in review with a PR. */
const TASK_STATE: Record<string, { label: string; cls: string }> = {
  done: { label: "Done", cls: "done" }, in_progress: { label: "In progress", cls: "doing" }, submitted: { label: "Waiting for sign-off", cls: "review" },
  changes_requested: { label: "Changes requested", cls: "attn" }, approved: { label: "To do", cls: "todo" },
};
const KIND_GLYPH: Record<string, GlyphName> = { code: "code", learn: "learn", meet: "meet" };

function CurrentTasks({ tasks }: { tasks: PhaseTask[] }) {
  return (
    <ul className="hm-tasks">
      {tasks.map((t) => {
        const st = t.status === "submitted" && t.pr_number ? { label: "In review", cls: "review" }
          : TASK_STATE[t.status] ?? { label: t.status.replace(/_/g, " "), cls: "todo" };
        return (
          <li key={t.id} className={`hm-task is-${st.cls}`}>
            <a href="#/tasks">
              <span className="hm-task-mark" aria-hidden="true">{st.cls === "done" && <Glyph name="check" size={11} />}</span>
              <span className="hm-task-title">{t.title}</span>
              <span className="hm-task-kind" title={t.kind}><Glyph name={KIND_GLYPH[t.kind] ?? "task"} size={13} /></span>
              <span className={`hm-task-state s-${st.cls}`}>{st.label}</span>
              <span className="hm-task-due">{t.due_date ? shortDate(t.due_date) : ""}</span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}

/** KT sessions booked from now on: the date, the title and the time; a KT badge, because each is a recorded session. */
function UpcomingKt({ sessions, error }: { sessions: Session[] | null; error: boolean }) {
  const now = Date.now() - 3_600_000;
  const soon = (sessions ?? []).filter((m) => m.status === "scheduled" && m.starts_at && new Date(m.starts_at).getTime() >= now)
    .sort((a, b) => new Date(a.starts_at!).getTime() - new Date(b.starts_at!).getTime()).slice(0, 3);
  const recorded = (sessions ?? []).filter((m) => m.status === "ready").length;
  return (
    <Card title="KT sessions" id="kt-h" action={<More href="#/meetings">All</More>}>
      {error ? <Quiet icon="alert">Sessions could not be loaded.</Quiet>
        : !sessions ? <div className="hm-skel-line" aria-hidden="true" />
        : soon.length === 0 ? <Quiet icon="calendar">Nothing booked yet. Your mentor books them.</Quiet> : (
          <ul className="hm-kt">
            {soon.map((m) => {
              const at = new Date(m.starts_at!);
              return (
                <li key={m.id}>
                  <a href={`#/meetings?call=${encodeURIComponent(m.id)}`}>
                    <span className="hm-date" aria-hidden="true"><b>{at.getDate()}</b>{at.toLocaleDateString("en-GB", { month: "short" })}</span>
                    <span className="hm-kt-text"><span className="hm-kt-title">{m.title}</span><span className="hm-kt-when">{when(m.starts_at!)}</span></span>
                    <KtBadge />
                  </a>
                </li>
              );
            })}
          </ul>
        )}
      {sessions && <p className="hm-card-foot">{recorded ? <><KtBadge /> {plural(recorded, "session")} recorded, linked to the code</> : "No session recorded yet"}</p>}
    </Card>
  );
}

function PersonRow({ role, person }: { role: string; person: { id: string; name: string } | null }) {
  return (
    <li className="hm-person">
      {person ? <Avatar name={nameOf(person)} size={30} /> : <span className="lc-person-none" aria-hidden="true" />}
      <span className="hm-person-text"><span className="hm-person-n">{person ? nameOf(person) : "Not named yet"}</span><span className="hm-person-k">{role}</span></span>
    </li>
  );
}

/* ---------------------------------------------------------------- managers and admins */

function onTrackOf(cards: HireCard[]) {
  const active = cards.filter((c) => c.profile.status !== "completed");
  return { active, onTrack: active.filter((c) => c.status.on_track).length, blockers: active.reduce((n, c) => n + c.status.open_blockers, 0) };
}

function LeadKpis({ cards, approvals, extra }: { cards: HireCard[]; approvals?: Approvals; extra?: ReactNode }) {
  const { active, onTrack, blockers } = onTrackOf(cards);
  return (
    <Kpis>
      <Kpi label="Onboarding" value={active.length} of={active.length === 1 ? "hire" : "hires"} foot={active.length ? "In a 30/60/90 program" : "Enrol someone to start"} href="#/team" />
      <Kpi label="On track" value={onTrack} of={`of ${active.length}`} tone={onTrack < active.length ? "attn" : undefined}
           visual={<Ring value={active.length ? onTrack / active.length : 0} size={40} stroke={4} tone="primary" />}
           foot={active.length - onTrack ? `${plural(active.length - onTrack, "hire")} need${active.length - onTrack === 1 ? "s" : ""} a look` : "Nothing overdue"} href="#/team" />
      <Kpi label="Waiting for you" value={approvals?.count ?? 0} of={approvals?.count === 1 ? "decision" : "decisions"}
           foot={approvals?.count ? "Agents propose, you decide" : "Nothing to decide"} href="#/approvals" tone={approvals?.count ? "attn" : undefined} />
      {extra ?? <Kpi label="Open blockers" value={blockers} foot={blockers ? "Across your hires" : "None open"} href="#/team" tone={blockers ? "attn" : undefined} />}
    </Kpis>
  );
}

/** Each hire with where they are: day of the program on a bar, the next checkpoint, and on track or not. */
function HireStages({ cards }: { cards: HireCard[] }) {
  const rows = cards.filter((c) => c.profile.status !== "completed").sort((a, b) => Number(a.status.on_track) - Number(b.status.on_track) || b.profile.day - a.profile.day);
  if (!rows.length) return <Quiet icon="users">Nobody is onboarding. Enrol a hire to start a 30/60/90 program.</Quiet>;
  return (
    <ul className="hm-hires">
      {rows.map((c) => {
        const p = c.profile;
        const share = Math.max(0, Math.min(1, p.day / Math.max(1, p.duration_days)));
        return (
          <li key={p.onboarding_id}>
            <a href={`#/program/${encodeURIComponent(p.onboarding_id)}`}>
              <Avatar name={nameOf(p.hire)} size={32} />
              <span className="hm-hire-who">
                <span className="hm-hire-n">{nameOf(p.hire)}</span>
                <span className="hm-hire-r">{[p.role_title, p.team?.name].filter(Boolean).join(" · ")}</span>
              </span>
              <span className="hm-hire-stage">
                <span className="hm-hire-day">{p.day < 0 ? `Starts in ${-p.day}d` : `Day ${p.day}`}<span className="hm-hire-of"> of {p.duration_days}</span></span>
                <span className="hm-bar wide" role="img" aria-label={`Day ${Math.max(0, p.day)} of ${p.duration_days}`}><i style={{ transform: `scaleX(${share})` }} /></span>
                <span className="hm-hire-next">{c.status.next_milestone ? `Next checkpoint: ${c.status.next_milestone.title}` : "Every checkpoint done"}</span>
              </span>
              <HireState onTrack={c.status.on_track} status={p.status} />
            </a>
          </li>
        );
      })}
    </ul>
  );
}

/** Recent activity, newest first: who did what, when, and a link to it. */
function ActivityFeed({ rows }: { rows: Activity[] }) {
  if (!rows.length) return <Quiet icon="clock">Nothing has happened yet.</Quiet>;
  return (
    <ol className="hm-activity">
      {rows.map((r) => {
        const inner = (
          <>
            <Avatar name={r.actor.name} size={26} />
            <span className="hm-act-text"><strong>{r.actor.name}</strong> {clean(r.text)}</span>
            <time className="hm-act-when" dateTime={r.at} title={new Date(r.at).toLocaleString("en-GB")}>{relative(r.at)}</time>
          </>
        );
        return <li key={r.id}>{r.link ? <a href={hashOf(r.link)}>{inner}</a> : <div>{inner}</div>}</li>;
      })}
    </ol>
  );
}

function Decisions({ approvals }: { approvals?: Approvals }) {
  const items = approvals?.items ?? [];
  return (
    <Card title="Waiting for your decision" id="decide-h" action={approvals?.count ? <More href="#/approvals">All {approvals.count}</More> : undefined}>
      {items.length === 0 ? <Quiet>Nothing waits for you.</Quiet> : (
        <ul className="hm-decide">
          {items.map((a) => (
            <li key={a.id}>
              <a href={hashOf(a.link)}>
                <span className="hm-decide-k">{KIND_LABEL[a.kind] ?? a.kind.replace(/_/g, " ")}{a.kind === "kt_followup" && <> <KtBadge /></>}</span>
                <span className="hm-decide-t">{clean(a.title)}</span>
                <span className="hm-decide-when">{relative(a.created_at)}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function ManagerHomeView({ d, hello }: { d: ManagerHome; hello: string }) {
  const { project } = useSession();
  const s = d.sections as ManagerHome["sections"] & LeadExtra;
  const hires = useProjectHires(project.id);
  const rows = (hires.rows ?? []).filter((r) => r.status !== "completed");
  const { active, onTrack } = onTrackOf(s.hires);
  return (
    <section className="lc hm">
      <Head hello={hello}
            sub={active.length ? `${plural(active.length, "hire")} onboarding · ${onTrack} on track · ${plural(s.approvals?.count ?? 0, "decision")} waiting` : "No one onboarding yet"}
            actions={<><a className="dp-btn" href="#/team">Open Team</a><a className="dp-btn primary" href="#/enrol"><Glyph name="plus" size={15} />Enrol a new hire</a></>} />
      <LeadKpis cards={s.hires} approvals={s.approvals} />
      <Grid
        main={<>
          <Focus items={d.focus} />
          <Section title="Hires and their stage" count={active.length || undefined} id="hires-h" action={<More href="#/team">Team</More>}>
            <HireStages cards={s.hires} />
          </Section>
          {hires.error && !hires.rows ? <LoadError error={hires.error} retry={hires.reload} /> : hires.rows && <><TeamBlockers rows={rows} /><UpcomingCheckins rows={rows} /></>}
          <Section title="Recent activity" id="activity-h">
            <ActivityFeed rows={s.activity ?? []} />
          </Section>
        </>}
        aside={<>
          <Decisions approvals={s.approvals} />
          <AgentsSummary />
        </>}
      />
      <Culture />
    </section>
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
  if (!list.length) return null; // the "On track" figure already says nothing is blocked
  return (
    <Section title="Blockers" count={list.length} id="team-blockers-h">
      <BlockerRows rows={list} />
    </Section>
  );
}

function UpcomingCheckins({ rows }: { rows: HireRow[] }) {
  const soon = rows.flatMap((r) => r.program.milestones
    .filter((m) => m.key.startsWith("checkin_") && m.status === "pending" && m.due_day - r.program.profile.day <= 21)
    .map((m) => ({ r, m, inDays: m.due_day - r.program.profile.day })))
    .sort((a, b) => a.inDays - b.inDays);
  if (!soon.length) return null;
  return (
    <Section title="Upcoming check-ins" count={soon.length} id="checkins-h">
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
    </Section>
  );
}

function AdminHomeView({ d, hello }: { d: AdminHome; hello: string }) {
  const { project, me } = useSession();
  const s = d.sections as AdminHome["sections"] & LeadExtra;
  const handovers = useApi<Handover[]>(isManager(me.role) ? `/handovers?project_id=${encodeURIComponent(project.id)}` : null);
  const people = useApi<{ members: { id: string; name: string }[] }>(`/projects/${encodeURIComponent(project.id)}`, ["people"]);
  const who = (id: string) => nameOf(people.data?.members.find((m) => m.id === id)?.name ?? "Someone");
  const steps = s.setup;
  const settled = steps.filter((x) => x.status !== "todo").length;
  const open = (handovers.data ?? []).filter((h) => h.status !== "done");
  const cards = s.hires ?? [];
  const { active } = onTrackOf(cards);
  return (
    <section className="lc hm">
      <Head hello={hello}
            sub={[settled === steps.length ? "Setup is complete" : `Setup: ${settled} of ${steps.length} steps`,
                  plural(active.length, "hire") + " onboarding", `${plural(s.approvals?.count ?? 0, "decision")} waiting`].join(" · ")}
            actions={<>{settled < steps.length && <a className="dp-btn" href="#/setup">Continue setup</a>}
                       <a className="dp-btn primary" href="#/enrol"><Glyph name="plus" size={15} />Enrol a new hire</a></>} />
      <LeadKpis cards={cards} approvals={s.approvals}
                extra={d.figure ? <Kpi label={d.figure.label} value={d.figure.value ?? "–"} foot="On at least one project" href="#/people" /> : undefined} />
      <Grid
        main={<>
          <Focus items={d.focus} />
          <Section title="Hires and their stage" count={active.length || undefined} id="hires-h" action={<More href="#/team">Team</More>}>
            <HireStages cards={cards} />
          </Section>
          <Section title="Recent activity" id="activity-h" action={<More href="#/audit">Audit log</More>}>
            <ActivityFeed rows={s.activity ?? []} />
          </Section>
        </>}
        aside={<>
          <Decisions approvals={s.approvals} />
          {settled < steps.length && (
            <Card title="Setup" id="setup-h" action={<More href="#/setup">Setup</More>}>
              <div className="lc-setup-mini-top"><strong>{settled} of {steps.length}</strong><Progress done={settled} total={steps.length} label={`${settled} of ${steps.length} setup steps settled`} /></div>
              <ul className="hm-steps">
                {steps.filter((x) => x.status === "todo").map((x) => <li key={x.key}><span className="lc-step-mark" aria-hidden="true" />{x.title}</li>)}
              </ul>
            </Card>
          )}
          <Card title="Handovers" id="handovers-h" action={<More href="#/handovers">All</More>}>
            {handovers.error && !handovers.data ? <LoadError error={handovers.error} retry={handovers.reload} />
              : !handovers.data ? <div className="hm-skel-line" aria-hidden="true" />
              : open.length === 0 ? <Quiet>No one is leaving.</Quiet> : (
                <ul className="hm-decide">
                  {open.map((h) => {
                    const left = h.plan.items.filter((i) => !i.done).length;
                    return (
                      <li key={h.id}><a href="#/handovers">
                        <span className="hm-decide-t"><strong>{who(h.user_id)}</strong> · last day {shortDate(h.last_day)}</span>
                        <span className="hm-decide-when">{left ? `${plural(left, "item")} left` : "All items done"}</span>
                      </a></li>
                    );
                  })}
                </ul>
              )}
          </Card>
          <AgentsSummary />
        </>}
      />
      <Culture />
    </section>
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
    <section className="lc hm">
      <Head hello={hello} sub={d.sections.mentees.length ? `Mentoring ${d.sections.mentees.map((m) => firstName(m.profile.hire)).join(" and ")}` : "Answering questions and recording KT"}
            actions={<><a className="dp-btn" href="#/kt-planner">My KT queue</a><a className="dp-btn primary" href="#/inbox"><Glyph name="message" size={15} />Expert inbox</a></>} />
      <Kpis>
        <Kpi label={d.figure?.label ?? "Questions waiting"} value={d.figure?.value ?? 0} foot="Response target: 24 hours" href="#/inbox"
             tone={Number(d.figure?.value) ? "attn" : undefined} />
        <Kpi label="Mentees" value={ids.length} foot={ids.length ? "Their blockers reach you first" : "No one yet"} />
        <Kpi label="KT to deliver" value={sessions.length} foot={sessions.length ? "Sessions planned for you" : "Nothing planned"} href="#/kt-planner" />
      </Kpis>
      <Grid
        main={<>
          <Focus items={d.focus} />
          <Section title="Mentees" count={ids.length || undefined} id="mentees-h">
            {ids.length === 0 ? <Quiet icon="users">You are not mentoring anyone.</Quiet>
              : mentees.error && !mentees.rows ? <LoadError error={mentees.error} retry={mentees.reload} />
              : !mentees.rows ? <LcSkeleton rows={1} head={false} /> : <HireList rows={mentees.rows} compact />}
          </Section>
        </>}
        aside={<>
          <Card title="KT sessions to deliver" id="kt-h" action={<More href="#/kt-planner">Queue</More>}>
            {plan.error && !plan.data ? <Quiet icon="alert">The KT queue could not be loaded.</Quiet>
              : !plan.data ? <div className="hm-skel-line" aria-hidden="true" />
              : sessions.length === 0 ? <Quiet>No sessions planned for you.</Quiet> : (
                <ul className="hm-decide">
                  {sessions.map((x) => (
                    <li key={x.key}><a href="#/kt-planner">
                      <span className="hm-decide-k"><KtBadge /> {Math.round(x.est_minutes)} min{x.booked ? " · booked" : ""}</span>
                      <span className="hm-decide-t">{x.title}</span>
                      {x.deadline && <span className="hm-decide-when">by {shortDate(x.deadline)}</span>}
                    </a></li>
                  ))}
                </ul>
              )}
          </Card>
          <Card title="Digest" id="digest-h" action={d.sections.digest.length ? <More href="#/inbox">Inbox</More> : undefined}>
            {d.sections.digest.length === 0 ? <Quiet>Nothing in your digest.</Quiet> : (
              <ul className="hm-decide">
                {d.sections.digest.map((q) => (
                  <li key={q.id}><a href="#/inbox"><span className="hm-decide-t">{q.question}</span><span className="hm-decide-when">{shortDate(q.created_at)}</span></a></li>
                ))}
              </ul>
            )}
          </Card>
        </>}
      />
      <Culture />
    </section>
  );
}

/* ---------------------------------------------------------------- executive */

function ExecHomeView({ d, hello }: { d: ExecHome; hello: string }) {
  return (
    <section className="lc hm">
      <Head hello={hello} sub={`Onboarding across the organisation · as of ${shortDate(d.sections.as_of)}`}
            actions={<a className="dp-btn primary" href="#/reports">Open the full report<Glyph name="arrow" size={14} /></a>} />
      <Grid
        main={<>
          <Section title="This cohort" id="cohort-h" action={<More href="#/reports">Reports</More>}>
            <div className="lc-tiles">{d.sections.report.map((f) => <FigureTile key={f.key} f={f} />)}</div>
          </Section>
          <Focus items={d.focus} />
        </>}
        aside={d.figure ? (
          <Card title={d.figure.label} id="exec-fig-h">
            <p className="hm-big">{d.figure.value ?? "–"}{typeof d.figure.value === "number" && <span className="hm-kpi-of">days</span>}</p>
            <p className="hm-card-foot">{d.figure.how}</p>
          </Card>
        ) : <span />}
      />
    </section>
  );
}

/* ---------------------------------------------------------------- how we work here */

const READ_FIRST = [/^readme(\.md)?$/i, /(^|\/)contributing(\.md)?$/i, /(^|\/)codeowners$/i, /(^|\/)architecture(\.md)?$/i];

/** The organisation's way of working, from what the project holds: its people, the documents everyone reads first, and how much
 *  of the code was explained in a recorded session. Compact and secondary: the product comes first. */
function Culture() {
  const { me, project } = useSession();
  const pid = encodeURIComponent(project.id);
  const detail = useApi<ProjectDetail>(`/projects/${pid}`, ["people", "sources"]);
  const files = useApi<CodeFile[]>(`/code/files?project_id=${pid}`, ["sources", "knowledge"]);
  const sessions = useApi<Session[]>(`/meetings?project_id=${pid}`, ["meetings"]);
  if (!detail.data) return null; // secondary: it appears when ready, and stays out of the way when it cannot load
  const members = detail.data.members.filter((m) => m.role !== "executive");
  const docs = [
    ...(files.data ?? []).filter((f) => READ_FIRST.some((r) => r.test(f.path))).slice(0, 3)
      .map((f) => ({ key: f.path, label: f.path, href: `#/code?path=${encodeURIComponent(f.path)}`, code: true })),
    ...detail.data.documents.filter((x) => x.status === "ready").slice(0, 2)
      .map((x) => ({ key: x.id, label: x.title, href: `#/documents/${encodeURIComponent(x.id)}`, code: false })),
  ];
  const explained = (files.data ?? []).filter((f) => f.kt_clips > 0).length;
  const recorded = (sessions.data ?? []).filter((m) => m.status === "ready").length;
  return (
    <section className="hm-culture" aria-labelledby="culture-h">
      <div className="hm-culture-head">
        <h2 id="culture-h">How {me.org_name} works</h2>
        <p>The people on {project.name}, what everyone reads first, and what was explained on record.</p>
      </div>
      <div className="hm-culture-grid">
        <div className="hm-culture-col">
          <h3>The team</h3>
          <div className="hm-faces" aria-hidden="true">{members.slice(0, 7).map((m) => <Avatar key={m.id} name={nameOf(m.name)} size={28} />)}</div>
          <p>{plural(members.length, "person", "people")} · {members.filter((m) => m.role === "expert").length} who answer questions</p>
        </div>
        <div className="hm-culture-col">
          <h3>Read first</h3>
          {docs.length === 0 ? <p>No guides yet. Add a README or a document in Settings.</p> : (
            <ul className="hm-docs">
              {docs.map((x) => <li key={x.key}><a href={x.href}><Glyph name="file" size={13} /><span className={x.code ? "hm-path" : undefined}>{x.label}</span></a></li>)}
            </ul>
          )}
        </div>
        <div className="hm-culture-col">
          <h3>On record <KtBadge /></h3>
          <p className="hm-record"><strong>{recorded}</strong> {recorded === 1 ? "session" : "sessions"} recorded</p>
          <p className="hm-record"><strong>{explained}</strong> of {files.data?.length ?? 0} files explained</p>
          <a className="hm-card-link" href="#/code">See what was explained<Glyph name="arrow" size={13} /></a>
        </div>
      </div>
    </section>
  );
}
