import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, errorMessage, isManager, post } from "../api";
import { useSession } from "../session";
import { replaceRoute } from "../route";
import { toast } from "../ui";
import { CountUp, EmptyState, Reveal, Skeleton } from "../fx";
import { Avatar, daysUntil, Glyph, GlyphName, Pill, SectionHead, Tone } from "../dataui";
import { Accordion, AccordionItem } from "../accordion";
import { calendlyUrl, ScheduleDialog, ScheduleValues, useScheduling } from "./schedule";
import "../styles/planner.css";
import { useLive } from "../live";

/* ---------------------------------------------------------------- API shapes (GET /api/kt-plan) */
type ReasonKind = "first_pr" | "task" | "action_item" | "question" | "named_not_shown" | "gap" | "stale";
type Reason = {
  path: string; symbol: string | null; kind: ReasonKind; text: string; weight: number; due: string | null; pending_approval: boolean;
  ref: { task_id?: string; meeting_id?: string; at_s?: number; message_id?: string; module?: string }; start_line: number | null;
};
type Source = "codeowners" | "speaker" | "manager" | "none";
type Item = {
  path: string; symbol: string; start_line: number; end_line: number; lines: number; est_minutes: number;
  state: "none" | "stale"; presenter_source: Source; reasons: Reason[];
};
type Person = { user_id: string; name: string; login: string | null };
type Session = {
  key: string; title: string; presenter: Person | null; presenter_source: Source; est_minutes: number; deadline: string | null;
  part: number; parts: number; items: Item[]; booked: { meeting_id: string; status: string; created_by?: string | null } | null;
  impact: { files_newly_covered: string[]; gaps_closed: string[]; tasks_unblocked: number; questions_addressed: number;
    recorded_share_before: number; recorded_share_after: number };
};
type Rerecord = {
  meeting_id: string; meeting_title: string; file_path: string; symbol: string | null; start_line: number | null; end_line: number | null;
  start_s: number; end_s: number; presenter: Person | null;
};
type PresenterShare = Person & { sessions: number; est_minutes: number };
type Plan = {
  indexed: boolean; pace_s_per_line: number; pace_is_default: boolean; scope: "team" | "mine"; presenter_filter: string | null;
  partial?: boolean; // some code searches failed or ran out of time: tasks and questions that name no file may be missing
  index_truncated?: boolean; // the repository is past the index's read limit: files late in path order are not planned
  sessions_total?: number; // sessions in this view; the response carries the first 50
  presenters?: PresenterShare[]; // each presenter's share of the whole team plan (team views only)
  totals: { sessions: number; est_minutes: number; items: number; recorded_share_before: number; recorded_share_after: number;
    gaps_before: string[]; gaps_after: string[] };
  sessions: Session[]; rerecord: Rerecord[]; backlog: { unrecorded_symbols: number; lines: number; symbols: string[] };
};
type Booked = { meeting: { id: string; status: string }; notified_user_id: string | null; session: Session };

/* ---------------------------------------------------------------- wording */
const REASON: Record<ReasonKind, { glyph: GlyphName; label: string }> = {
  first_pr: { glyph: "pr", label: "First PR" },
  task: { glyph: "task", label: "Task" },
  action_item: { glyph: "play", label: "Action item" },
  question: { glyph: "message", label: "Question" },
  named_not_shown: { glyph: "video", label: "Named, never shown" },
  gap: { glyph: "layers", label: "Coverage gap" },
  stale: { glyph: "clock", label: "Code changed" },
};
/** Why this person presents: the planner's presenter_source, in words, naming the presenter ("you" in an expert's queue). */
function sourceText(source: Source, who: string | null, mine: boolean): string {
  if (source === "none" || !who) return "No code owner or manager to present";
  if (mine) {
    return source === "codeowners" ? "You own this code in CODEOWNERS"
      : source === "speaker" ? "You explained this module in a KT session" : "No code owner, so you present as manager";
  }
  return source === "codeowners" ? `${who} owns this code in CODEOWNERS`
    : source === "speaker" ? `${who} explained this module in a KT session` : `No code owner, so ${who} presents as manager`;
}
const BOOKED_LABEL: Record<string, string> = { live: "Live now", processing: "Processing recording" };

const firstName = (name: string) => name.replace(/\(.*?\)/g, " ").trim().split(/\s+/)[0] || name;
const mins = (m: number) => (Number.isInteger(m) ? String(m) : m.toFixed(1));
const pct = (share: number) => Math.round(share * 100);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const lineRange = (a: number | null, b: number | null) => (a == null ? "" : b == null || a === b ? `L${a}` : `L${a}–${b}`);
const shortMeeting = (title: string) => { const h = title.split(":")[0].trim(); return title.includes(":") && h.length <= 24 ? h : title; };
const codeHref = (path: string, line?: number | null) => `#/code?path=${encodeURIComponent(path)}${line ? `&line=${line}` : ""}`;
const clipHref = (meetingId: string, at?: number | null) => `#/meetings/${encodeURIComponent(meetingId)}${at != null ? `?t=${Math.floor(at)}` : ""}`;
const listWords = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

function reasonHref(r: Reason): string | null {
  if (r.kind === "first_pr" || r.kind === "task") return "#/tasks";
  if (r.ref.meeting_id) return clipHref(r.ref.meeting_id, r.ref.at_s);
  return null;
}
/** One copy of each reason: a file-level reason rides on every unrecorded function of its file. */
function distinct(reasons: Reason[]) {
  const seen = new Set<string>();
  return reasons.filter((r) => {
    const k = `${r.kind}|${r.path}|${r.symbol}|${r.start_line}|${JSON.stringify(r.ref)}`;
    return seen.has(k) ? false : (seen.add(k), true);
  });
}
const paths = (s: Session) => [...new Set(s.items.map((i) => i.path))];

function impactLine(s: Session) {
  const i = s.impact, out: string[] = [];
  if (i.gaps_closed.length) out.push(`Closes the ${listWords(i.gaps_closed)} ${i.gaps_closed.length === 1 ? "gap" : "gaps"}`);
  if (i.files_newly_covered.length) out.push(`${plural(i.files_newly_covered.length, "file")} newly explained`);
  if (i.tasks_unblocked) out.push(`unblocks ${plural(i.tasks_unblocked, "task")}`);
  if (i.questions_addressed) out.push(`answers ${plural(i.questions_addressed, "question")}`);
  const stale = s.items.filter((x) => x.state === "stale").length;
  if (stale) out.push(`refreshes ${plural(stale, "outdated clip")}`);
  const text = out.join(" · ");
  return text ? text[0].toUpperCase() + text.slice(1) : `Explains ${plural(s.items.length, "function")}`;
}

/* ---------------------------------------------------------------- small pieces */
/** Recorded (solid teal), planned to record (teal wash, outlined), the rest neutral. Teal means "explained in a KT session". */
function CoverageBar({ before, after, label, size = "lg" }: { before: number; after: number; label: string; size?: "lg" | "sm" }) {
  const rec = Math.max(0, before), plan = Math.max(0, after - before), rest = Math.max(0, 1 - Math.max(before, after));
  return (
    <div className={`ktp-bar ktp-bar-${size}`} role="img" aria-label={label}>
      {rec > 0 && <span className="ktp-seg-rec" style={{ flexGrow: rec }} />}
      {plan > 0 && <span className="ktp-seg-plan" style={{ flexGrow: plan }} />}
      {rest > 0 && <span className="ktp-seg-rest" style={{ flexGrow: rest }} />}
    </div>
  );
}

/** '24 Oct', the same day format as the reason texts the server writes ('due 24 Oct'). */
function dayMonth(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** 'before 24 Oct': amber when it is close, red once it has passed. */
function Deadline({ date }: { date: string }) {
  const n = daysUntil(date);
  const tone: Tone = n < 0 ? "rec" : n <= 3 ? "warn" : "neutral";
  const text = n < 0 ? `${-n}d overdue` : `before ${dayMonth(date)}`;
  return (
    <span className={`dp-due t-${tone}`} title={`Needed before ${dayMonth(date)}${n >= 0 ? ` (in ${n} ${n === 1 ? "day" : "days"})` : ""}`}>
      <Glyph name={n < 0 ? "alert" : "calendar"} size={13} />{text}
    </span>
  );
}

function ReasonChip({ r, file }: { r: Reason; file?: string }) {
  const k = REASON[r.kind], href = reasonHref(r);
  const body = (
    <>
      <span className={`ktp-reason-icon k-${r.kind}`} aria-hidden="true"><Glyph name={k.glyph} size={13} /></span>
      <span className="ktp-reason-text">
        <span className="sr-only">{k.label}: </span>{r.text}
        {r.pending_approval && !/awaiting approval/i.test(r.text) && <span className="ktp-pending"> · awaiting approval</span>}
        {file && <code className="ktp-reason-file">{file}</code>}
      </span>
    </>
  );
  return href ? <a className={`ktp-reason k-${r.kind}`} href={href}>{body}</a> : <span className={`ktp-reason k-${r.kind}`}>{body}</span>;
}

/* ---------------------------------------------------------------- page */
export default function KtPlanner({ params }: { params: URLSearchParams }) {
  const { project, me } = useSession();
  const mine = me.role === "expert";
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [justBooked, setJustBooked] = useState<string | null>(null);
  const [focused, setFocused] = useState<Plan | null>(null); // one presenter's sessions, when the team plan is cut at 50
  const sched = useScheduling(project.id); // Google Calendar connected, Calendly links: how a session can be booked
  /** The presenter's Calendly link, prefilled and tagged with this session, when they have one (never your own). */
  const calendly = (s: Session) => {
    const link = s.presenter && s.presenter.user_id !== me.id && sched.links?.webhook ? sched.links.links[s.presenter.user_id] : null;
    return link && sched.links ? calendlyUrl(link, sched.links.prefill, s.key) : null;
  };
  const [booking, setBooking] = useState<Session | null>(null); // the session whose schedule form is open
  const urlPresenter = params.get("presenter");
  const [presenter, setPresenter] = useState<string | null>(urlPresenter);
  useEffect(() => setPresenter(urlPresenter), [urlPresenter]); // a link such as #/kt-planner?presenter=u-meera

  /** Resolves true once the latest plan is on screen, false when the request failed (the error is then shown). */
  const load = useCallback(() => api<Plan>(`/kt-plan?project_id=${encodeURIComponent(project.id)}`)
    .then((p) => { setPlan(p); setFocused(null); setError(null); return true; })
    .catch((e) => { setError(errorMessage(e)); return false; }), [project.id]);
  useEffect(() => { void load(); }, [load]);
  useLive(["meetings", "knowledge", "sources", "people"], () => { void load(); }, project.id); // booked, recorded, re-indexed elsewhere
  // The team plan lists its first 50 sessions. Filtered to one presenter, ask for that presenter's own first 50.
  const capped = !!plan && !mine && (plan.sessions_total ?? plan.sessions.length) > plan.sessions.length;
  useEffect(() => {
    if (!capped || !presenter) { setFocused(null); return; }
    let live = true;
    api<Plan>(`/kt-plan?project_id=${encodeURIComponent(project.id)}&presenter=${encodeURIComponent(presenter)}`)
      .then((p) => { if (live) setFocused(p); }).catch(() => { /* the team plan, filtered here, is still on screen */ });
    return () => { live = false; };
  }, [capped, presenter, project.id, plan]);
  // "Just booked" lasts as long as the card's settle animation, so a card shown again later never re-takes focus.
  useEffect(() => {
    if (!justBooked) return;
    const t = window.setTimeout(() => setJustBooked(null), 1800);
    return () => window.clearTimeout(t);
  }, [justBooked]);

  function pick(id: string | null) {
    setJustBooked(null);
    setPresenter(id);
    replaceRoute(`#/kt-planner${id ? `?presenter=${encodeURIComponent(id)}` : ""}`);
  }

  /** Book from the schedule form: on Google Calendar with a Meet link, or held elsewhere. (Calendly bookings happen on
   *  Calendly; the form opens it, and the session shows up booked once Calendly's webhook arrives.) A 409 closes the form
   *  and reloads the plan; any other error stays in the form. */
  async function book(s: Session, v: ScheduleValues) {
    setBusy(s.key);
    try {
      const r = await post<Booked>("/kt-plan/schedule", { project_id: project.id, key: s.key, starts_at: v.starts_at, duration_min: v.duration_min,
                                                          attendees: v.attendees, provider: v.provider, join_url: v.join_url });
      const mark = (p: Plan | null) => p && { ...p, sessions: p.sessions.map((x) => (x.key === s.key ? { ...x, booked: r.session.booked } : x)) };
      setBooking(null);
      setPlan(mark);
      setFocused(mark);
      setJustBooked(s.key);
      toast(r.notified_user_id && s.presenter ? `Booked. ${firstName(s.presenter.name)} has the agenda.` : "Booked. It is in KT sessions.");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && (e.code === "already_booked" || e.code === "plan_changed")) {
        setBooking(null);
        if (await load()) toast(e.code === "already_booked" ? "Already booked. Showing the latest." : "The plan changed. Showing the latest.");
        else toast(e.code === "already_booked" ? "Already booked, but the latest plan did not load." : "The plan changed, but the latest plan did not load.", "error");
      } else throw e;
    } finally { setBusy(null); }
  }

  async function cancel(s: Session) {
    if (!s.booked) return;
    setBusy(s.key);
    try {
      await post(`/meetings/${encodeURIComponent(s.booked.meeting_id)}/cancel`, {});
      toast("Cancelled. The session can be booked again.");
    } catch (e) {
      toast(errorMessage(e), "error");
    } finally {
      await load();
      setBusy(null);
    }
  }

  const title = mine ? "My KT queue" : "KT planner";
  const presenters = useMemo(() => {
    if (plan?.presenters?.length) return plan.presenters.map((p) => ({ id: p.user_id, name: firstName(p.name), n: p.sessions }));
    const seen = new Map<string, { id: string; name: string; n: number }>();
    for (const s of plan?.sessions ?? []) {
      if (!s.presenter) continue;
      const p = seen.get(s.presenter.user_id) ?? { id: s.presenter.user_id, name: firstName(s.presenter.name), n: 0 };
      p.n += 1;
      seen.set(p.id, p);
    }
    return [...seen.values()];
  }, [plan]);

  if (!plan) {
    return error ? (
      <section className="dp">
        <header className="dp-head"><div><p className="dp-eyebrow">{project.name}</p><h1>{title}</h1></div></header>
        <div className="error ktp-error" role="alert"><span>{error}</span><button className="dp-btn small" onClick={() => { setError(null); void load(); }}>Try again</button></div>
      </section>
    ) : <PlannerSkeleton title={title} project={project.name} />;
  }

  const filter = !mine && presenter && presenters.some((p) => p.id === presenter) ? presenter : null;
  const source = filter && focused?.presenter_filter === filter ? focused : plan;
  const sessions = filter ? source.sessions.filter((s) => s.presenter?.user_id === filter) : plan.sessions;
  const inView = filter ? (source === focused ? focused.sessions_total ?? sessions.length : presenters.find((p) => p.id === filter)?.n ?? sessions.length)
    : plan.sessions_total ?? sessions.length;
  const rerecord = filter ? plan.rerecord.filter((r) => r.presenter?.user_id === filter) : plan.rerecord;
  const scoped = mine || !!filter;
  const before = plan.totals.recorded_share_before;
  const after = scoped
    ? Math.min(1, before + sessions.reduce((sum, s) => sum + (s.impact.recorded_share_after - s.impact.recorded_share_before), 0))
    : plan.totals.recorded_share_after;
  const minutes = sessions.reduce((sum, s) => sum + s.est_minutes, 0);
  const items = sessions.reduce((sum, s) => sum + s.items.length, 0);
  const closed = scoped ? [...new Set(sessions.flatMap((s) => s.impact.gaps_closed))]
    : plan.totals.gaps_before.filter((g) => !plan.totals.gaps_after.includes(g));
  const pace = `Your team explains about ${Math.round(plan.pace_s_per_line)} s of code per line${plan.pace_is_default ? " (a default until the first KT session is linked to code)" : ""}.`;
  const sub = !plan.indexed ? "KT sessions planned from what new hires need."
    : mine ? (sessions.length ? `You present ${sessions.length} of ${plural(plan.totals.sessions, "session")}, about ${mins(minutes)} min.` : "Nothing needs you right now.")
    : plan.totals.sessions ? `${plural(plan.totals.sessions, "session")}, about ${mins(plan.totals.est_minutes)} min, cover what new hires need.` : "Nothing needs a KT session right now.";

  return (
    <section className="dp ktp">
      <header className="dp-head">
        <div>
          <p className="dp-eyebrow">{project.name}</p>
          <h1>{title}</h1>
          <p className="dp-sub">{sub}</p>
        </div>
      </header>

      {error && (
        <div className="error ktp-error ktp-error-inline" role="alert">
          <span>Could not refresh the plan. {error}</span>
          <button className="dp-btn small" onClick={() => { setError(null); void load(); }}>Try again</button>
        </div>
      )}

      {plan.indexed && plan.index_truncated && (
        <p className="ktp-note" role="note">
          <Glyph name="alert" size={14} />
          <span>Some files of this large repository are not in the plan.</span>
        </p>
      )}

      {plan.indexed && plan.partial && (
        <p className="ktp-note" role="note">
          <Glyph name="alert" size={14} />
          <span>Some tasks and questions are not in the plan yet. Reload to try again.</span>
        </p>
      )}

      {!plan.indexed ? (
        <EmptyState title="No code yet"
          action={isManager(me.role) ? <a className="dp-btn primary" href="#/settings?tab=repository">Connect a repository</a> : undefined}>
          {isManager(me.role) ? "Sessions appear once a repository is connected." : "Sessions appear once your manager connects the repository."}
        </EmptyState>
      ) : plan.totals.sessions === 0 || (mine && sessions.length === 0) ? (
        <>
          <div className="ktp-done">
            <span className="ktp-done-mark" aria-hidden="true"><Glyph name="check" size={22} /></span>
            <p className="ktp-done-title">{mine ? "Nothing in your queue" : "Everything new hires need is explained"}</p>
            <p className="ktp-done-text">New tasks, questions and action items add sessions here.</p>
          </div>
          <Rerecords rows={rerecord} />
          <Backlog b={plan.backlog} />
        </>
      ) : (
        <>
          <Reveal className="ktp-strip">
            <div className="ktp-strip-main">
              <p className="ktp-label"><Glyph name="video" size={14} />{!scoped ? "Owned code on video" : mine ? "Owned code on video, with your sessions" : `Owned code on video, with ${presenters.find((p) => p.id === filter)?.name}'s sessions`}</p>
              <p className="ktp-big">
                <span className="ktp-from"><CountUp value={pct(before)} format={(n) => `${Math.round(n)}%`} /></span>
                <Glyph name="arrow" size={22} className="ktp-arrow" />
                <span className="ktp-to"><CountUp value={pct(after)} format={(n) => `${Math.round(n)}%`} /></span>
                {pct(after) > pct(before) && <Pill tone="kt">+{pct(after) - pct(before)} pts</Pill>}
              </p>
              <CoverageBar before={before} after={after}
                label={`${pct(before)}% of the code is explained in a KT session; this plan takes it to ${pct(after)}%`} />
              <p className="ktp-legend">
                <span><i className="ktp-seg-rec" aria-hidden="true" />Recorded {pct(before)}%</span>
                <span><i className="ktp-seg-plan" aria-hidden="true" />Planned +{pct(after) - pct(before)} pts</span>
                <span><i className="ktp-seg-rest" aria-hidden="true" />Not planned {Math.max(0, 100 - pct(after))}%</span>
              </p>
            </div>
            <dl className="ktp-stats">
              <div><dt>Sessions</dt><dd><CountUp value={sessions.length} /></dd></div>
              <div title={pace}><dt>KT time</dt><dd>~{mins(minutes)}<small> min</small></dd></div>
              <div><dt>Functions</dt><dd><CountUp value={items} /></dd></div>
            </dl>
            {(closed.length > 0 || (!scoped && plan.totals.gaps_after.length > 0)) && (
              <div className="ktp-gaps">
                {closed.map((g) => <span key={g} className="ktp-gap closed"><Glyph name="check" size={13} /><span className="ktp-gap-label">Gap closed:</span><code>{g}</code></span>)}
                {!scoped && plan.totals.gaps_after.map((g) => <span key={g} className="ktp-gap open"><Glyph name="alert" size={13} /><span className="ktp-gap-label">Still never explained:</span><code>{g}</code></span>)}
              </div>
            )}
          </Reveal>

          <SectionHead title={mine ? "Your sessions" : "Next sessions"} count={sessions.length}>
            {!mine && presenters.length > 1 && (
              <div className="ktp-filter" role="group" aria-label="Show sessions by presenter">
                <button type="button" aria-pressed={!filter} onClick={() => pick(null)}>All</button>
                {presenters.map((p) => (
                  <button key={p.id} type="button" aria-pressed={filter === p.id} onClick={() => pick(p.id)}>
                    {p.name}<span className="ktp-filter-n">{p.n}</span>
                  </button>
                ))}
              </div>
            )}
          </SectionHead>

          {inView > sessions.length && (
            <p className="ktp-note ktp-more" role="note">
              <Glyph name="layers" size={14} />
              <span>The {sessions.length} most urgent of {plural(inView, "session")}.</span>
            </p>
          )}

          <SessionAccordion sessions={sessions} rerecord={rerecord} mine={mine} busy={busy} justBooked={justBooked}
            indexOf={(s, i) => (source === plan ? plan.sessions.indexOf(s) + 1 : i + 1)} onBook={setBooking} calendly={calendly}
            onCancel={(s) => (isManager(me.role) || s.booked?.created_by === me.id ? () => cancel(s) : undefined)} />

          <Backlog b={plan.backlog} />
        </>
      )}
      <ScheduleDialog open={booking !== null} onClose={() => setBooking(null)} data={sched} heading="Book KT session"
        title={booking?.title} duration={Math.max(15, Math.ceil((booking?.est_minutes ?? 30) / 15) * 15)} presenterId={booking?.presenter?.user_id}
        ktKey={booking?.key} submitLabel={mine ? "Book" : "Book and send agenda"} onSubmit={(v) => (booking ? book(booking, v) : Promise.resolve())} />
    </section>
  );
}

/* ---------------------------------------------------------------- sessions as an accordion */
/** One session open at a time (the most urgent first); its header says who, which files, how long and by when, so the
 *  rest of the plan reads at a glance. The re-record queue is the last section. */
function SessionAccordion({ sessions, rerecord, mine, busy, justBooked, indexOf, onBook, onCancel, calendly }: {
  sessions: Session[]; rerecord: Rerecord[]; mine: boolean; busy: string | null; justBooked: string | null;
  indexOf: (s: Session, i: number) => number; onBook: (s: Session) => void; onCancel: (s: Session) => (() => void) | undefined;
  calendly: (s: Session) => string | null;
}) {
  const keys = sessions.map((s) => s.key).join(",");
  const [open, setOpen] = useState<string | null>(sessions[0]?.key ?? (rerecord.length ? "rerecord" : null));
  useEffect(() => { // a filter or a reload that drops the open session opens the first one instead
    setOpen((o) => (o === null || o === "rerecord" || sessions.some((s) => s.key === o) ? o : sessions[0]?.key ?? null));
  }, [keys]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Accordion open={open} onChange={setOpen} className="ktp-acc">
      {sessions.map((s, i) => {
        const who = s.presenter ? firstName(s.presenter.name) : null;
        const n = indexOf(s, i);
        return (
          <AccordionItem key={s.key} id={s.key} domId={`ktp-${s.key}`} className={`ktp-enter${s.booked ? " is-booked" : ""}`}
            icon={s.presenter ? <Avatar name={s.presenter.name} size={32} /> : <Glyph name="users" size={16} />}
            eyebrow={<>Session {n}{s.parts > 1 && <> · part {s.part} of {s.parts}</>}</>}
            title={<>{mine ? "Your session" : `KT with ${who ?? "no presenter yet"}`}<span className="ktp-acc-files">{paths(s).map((p) => <code key={p}>{p}</code>)}</span></>}
            sub={impactLine(s)}
            meta={<>
              <span className="dp-due t-neutral"><Glyph name="clock" size={13} />~{mins(s.est_minutes)} min</span>
              {s.deadline && <Deadline date={s.deadline} />}
              {s.booked && <Pill tone="ok">{BOOKED_LABEL[s.booked.status] ?? "Booked"}</Pill>}
            </>}>
            <SessionCard bare s={s} index={n} mine={mine} busy={busy === s.key} anyBusy={busy !== null} fresh={justBooked === s.key}
              onBook={() => onBook(s)} onCancel={onCancel(s)} calendly={calendly(s)} />
          </AccordionItem>
        );
      })}
      {rerecord.length > 0 && (
        <AccordionItem id="rerecord" title="Re-record" count={rerecord.length} icon={<Glyph name="clock" size={16} />}
          sub="Explained before the code changed">
          <Rerecords rows={rerecord} bare />
        </AccordionItem>
      )}
    </Accordion>
  );
}

/* ---------------------------------------------------------------- one session */
function SessionCard({ s, index, mine, busy, anyBusy, fresh, onBook, onCancel, bare, calendly }:
  { s: Session; index: number; mine: boolean; busy: boolean; anyBusy: boolean; fresh: boolean; onBook: () => void; onCancel?: () => void; bare?: boolean;
    calendly?: string | null }) {
  const [confirming, setConfirming] = useState(false); // "Cancel" asks once more before it cancels
  useEffect(() => {
    if (!confirming) return;
    const t = window.setTimeout(() => setConfirming(false), 4000);
    return () => window.clearTimeout(t);
  }, [confirming]);
  const files = paths(s);
  const multi = files.length > 1;
  const why = distinct(s.items.flatMap((i) => i.reasons.filter((r) => r.symbol === null)));
  const lines = s.items.reduce((n, i) => n + i.lines, 0);
  const who = s.presenter ? firstName(s.presenter.name) : null;
  const headingId = `ktp-s-${s.key}`;
  const delta = pct(s.impact.recorded_share_after) - pct(s.impact.recorded_share_before);
  // Focus the new "Open KT sessions" link once, when this card has just been booked; never again on later renders.
  const openRef = useRef<HTMLAnchorElement>(null);
  useEffect(() => { if (fresh) openRef.current?.focus({ preventScroll: true }); }, [fresh]);

  return (
    <article className={`ktp-session${bare ? " bare" : ""}${s.booked ? " is-booked" : ""}${fresh ? " fresh" : ""}`} aria-labelledby={bare ? undefined : headingId}
             aria-label={bare ? `Session ${index}` : undefined}>
      {bare && <p className="ktp-src-line">{sourceText(s.presenter_source, who, mine)}</p>}
      {!bare && <header className="ktp-session-head">
        <div className="ktp-who">
          {s.presenter ? <Avatar name={s.presenter.name} size={40} /> : <span className="ktp-noone" aria-hidden="true"><Glyph name="users" size={18} /></span>}
          <div className="ktp-who-text">
            <p className="ktp-kicker">
              Session {index}{s.parts > 1 && <> · part {s.part} of {s.parts}</>}
              <span className="ktp-src"><span className="ktp-dot" aria-hidden="true"> · </span>{sourceText(s.presenter_source, who, mine)}</span>
            </p>
            <h3 id={headingId}>
              <span className="ktp-title-who">KT with {who ?? "no presenter yet"}</span>
              <span className="ktp-title-files">{files.map((p) => <code key={p}>{p}</code>)}</span>
            </h3>
          </div>
        </div>
        <div className="ktp-session-meta">
          <span className="dp-due t-neutral ktp-est" title={`${lines} lines at your team's pace`}><Glyph name="clock" size={13} />~{mins(s.est_minutes)} min</span>
          {s.deadline && <Deadline date={s.deadline} />}
        </div>
      </header>}

      {why.length > 0 && (
        <div className="ktp-block">
          <p className="ktp-block-label">Why this session</p>
          <ul className="ktp-why">
            {why.map((r) => <li key={`${r.kind}${r.path}${JSON.stringify(r.ref)}`}><ReasonChip r={r} file={multi && !r.text.includes(r.path) ? r.path : undefined} /></li>)}
          </ul>
        </div>
      )}

      <div className="ktp-block">
        <p className="ktp-block-label">Agenda <span>{plural(s.items.length, "function")} · {plural(lines, "line")}</span></p>
        <ol className="ktp-agenda">
          {s.items.map((it, n) => {
            const own = distinct(it.reasons.filter((r) => r.symbol !== null));
            return (
              <li key={`${it.path}::${it.symbol}::${it.start_line}`} className={it.state === "stale" ? "ktp-row-stale" : undefined}>
                <span className="ktp-n" aria-hidden="true">{n + 1}</span>
                <a className="ktp-row-link" href={codeHref(it.path, it.start_line)}>
                  <code className="ktp-sym">{it.symbol}</code>
                  <span className="ktp-where">{it.path}<span className="ktp-lines"><span className="ktp-sep" aria-hidden="true"> · </span>{lineRange(it.start_line, it.end_line)}</span></span>
                  <span className="sr-only">, about {mins(it.est_minutes)} minutes. Open in code.</span>
                </a>
                {it.state === "stale" && <span className="dp-tag t-warn ktp-stale"><Glyph name="clock" size={11} />Re-record</span>}
                <span className="ktp-min" aria-hidden="true">{mins(it.est_minutes)} min</span>
                <Glyph name="arrow" size={14} className="ktp-go" />
                {own.length > 0 && <div className="ktp-row-reasons">{own.map((r) => <ReasonChip key={`${r.kind}${JSON.stringify(r.ref)}`} r={r} />)}</div>}
              </li>
            );
          })}
        </ol>
      </div>

      <footer className="ktp-session-foot">
        <div className="ktp-impact">
          <p>{impactLine(s)}</p>
          <div className="ktp-impact-bar">
            <CoverageBar size="sm" before={s.impact.recorded_share_before} after={s.impact.recorded_share_after}
              label={`This session takes the code on video from ${pct(s.impact.recorded_share_before)}% to ${pct(s.impact.recorded_share_after)}%`} />
            <span className="dp-num">{pct(s.impact.recorded_share_before)}% → {pct(s.impact.recorded_share_after)}%{delta > 0 ? ` (+${delta} pts)` : ""}</span>
          </div>
        </div>
        <div className="ktp-action">
          {s.booked ? (
            <>
              <Pill tone="neutral">{BOOKED_LABEL[s.booked.status] ?? "Booked"}</Pill>
              <a className="dp-btn small ktp-open" href={`#/meetings?call=${encodeURIComponent(s.booked.meeting_id)}`} ref={openRef}>
                Open KT sessions<Glyph name="arrow" size={14} />
              </a>
              {onCancel && s.booked.status === "scheduled" && (
                <button type="button" className={`dp-btn small ghost ktp-cancel${confirming ? " confirm" : ""}`} disabled={anyBusy && !busy}
                  aria-describedby={bare ? undefined : headingId} onClick={() => { if (anyBusy) return; if (confirming) { setConfirming(false); onCancel(); } else setConfirming(true); }}>
                  {busy ? "Cancelling…" : confirming ? "Confirm cancel" : "Cancel"}
                </button>
              )}
            </>
          ) : s.presenter ? (
            <>
              {/* the presenter's own Calendly page, prefilled and tagged: the webhook marks the session booked */}
              {calendly && (
                <a className="dp-btn ghost" href={calendly} target="_blank" rel="noopener noreferrer" aria-describedby={bare ? undefined : headingId}>
                  Book with Calendly<Glyph name="external" size={13} />
                </a>
              )}
              {/* The button being booked stays focusable (aria-disabled), so a keyboard user keeps their place if it fails. */}
              <button type="button" className="dp-btn primary" onClick={() => { if (!anyBusy) onBook(); }} disabled={anyBusy && !busy}
                aria-disabled={busy || undefined} aria-describedby={bare ? undefined : headingId}>
                {busy ? <><span className="ktp-spin" aria-hidden="true" />Booking…</> : <><Glyph name="calendar" size={15} />{mine ? "Book my session" : "Book session"}</>}
              </button>
            </>
          ) : (
            <p className="ktp-nobook">Add a CODEOWNERS entry for {listWords(files)} to book this session.</p>
          )}
        </div>
      </footer>
    </article>
  );
}

/* ---------------------------------------------------------------- re-record queue and backlog */
function Rerecords({ rows, bare }: { rows: Rerecord[]; bare?: boolean }) {
  if (!rows.length) return null;
  return (
    <section className={bare ? undefined : "ktp-enter"} style={bare ? undefined : { animationDelay: "260ms" }} aria-labelledby={bare ? undefined : "ktp-rerecord"}>
      {!bare && <SectionHead id="ktp-rerecord" title="Re-record" count={rows.length} />}
      {!bare && <p className="dp-lede">Explained before the code changed.</p>}
      <ul className="ktp-rerecord">
        {rows.map((r) => (
          <li key={`${r.meeting_id}${r.file_path}${r.symbol}${r.start_s}`}>
            <span className="ktp-rr-icon" aria-hidden="true"><Glyph name="clock" size={15} /></span>
            <div className="ktp-rr-main">
              <a className="ktp-rr-code" href={codeHref(r.file_path, r.start_line)}>
                <code className="ktp-sym">{r.symbol ?? r.file_path}</code>
                {r.symbol != null ? (
                  <span className="ktp-where">{r.file_path}{r.start_line != null && <span className="ktp-lines"><span className="ktp-sep" aria-hidden="true"> · </span>{lineRange(r.start_line, r.end_line)}</span>}</span>
                ) : (
                  <span className="ktp-where">{r.start_line != null ? lineRange(r.start_line, r.end_line) : "whole file"}</span>
                )}
              </a>
              <p className="dp-meta">{shortMeeting(r.meeting_title) || "KT session"} at {fmtTs(r.start_s)}</p>
            </div>
            <span className="ktp-rr-who">
              {r.presenter ? <><Avatar name={r.presenter.name} size={24} /><span>{firstName(r.presenter.name)}</span></> : <span className="dp-dim">No presenter</span>}
            </span>
            <a className="dp-btn small ghost" href={clipHref(r.meeting_id, r.start_s)}><Glyph name="play" size={13} />Old clip</a>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Backlog({ b }: { b: Plan["backlog"] }) {
  if (!b.unrecorded_symbols) return null;
  const more = b.unrecorded_symbols - b.symbols.length;
  return (
    <p className="ktp-backlog">
      <Glyph name="layers" size={14} />
      <span>
        {plural(b.lines, "line")} not explained yet, and not needed now:{" "}
        {b.symbols.map((s, i) => <span key={s + i}>{i > 0 && ", "}<code>{s}</code></span>)}{more > 0 && ` and ${more} more`}.
      </span>
    </p>
  );
}

function fmtTs(s: number) {
  const t = Math.max(0, Math.floor(s)), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

function PlannerSkeleton({ title, project }: { title: string; project: string }) {
  return (
    <section className="dp ktp" aria-busy="true">
      <span className="sr-only" role="status">Loading the KT plan…</span>
      <header className="dp-head"><div><p className="dp-eyebrow">{project}</p><h1>{title}</h1><Skeleton lines={1} height={14} style={{ width: 420, maxWidth: "100%", marginTop: 8 }} /></div></header>
      <div className="ktp-strip"><div className="ktp-strip-main"><Skeleton lines={3} height={16} /></div><div className="ktp-stats"><Skeleton lines={3} height={14} /></div></div>
      <div className="ktp-sessions ktp-sk">{[0, 1].map((i) => <div key={i} className="ktp-session"><Skeleton lines={5} height={15} /></div>)}</div>
    </section>
  );
}
