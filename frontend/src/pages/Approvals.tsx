import { useCallback, useEffect, useState } from "react";
import { api, errorMessage, when } from "../api";
import { useSession } from "../session";
import { replaceRoute } from "../route";
import { EmptyState, Reveal, Skeleton } from "../fx";
import { DueChip, Glyph, kindOf, Pill } from "../dataui";
import { Accordion, AccordionItem } from "../accordion";
import { Findings, PRHeader, RecBadge, RECOMMENDS, ReviewDetail } from "./Review";
import { FollowUpList, Rich, TranscriptList, useFollowUps, Utterance } from "./kt-parts";
import { useLive } from "../live";
import { Decided, useDecide } from "./decide";
import { AGENT_KINDS, AgentApprovalBody, agentHeaderOf } from "./AgentApprovals";
import { AgentTag } from "../lifecycle/WhyThis";
import { agentName, explainOf, KIND_AGENT, KIND_LABEL } from "../lifecycle/agentModel";

type PlanTask = { id: string; title: string; description: string; kind: string; origin: string; due_date: string };
/** task_plan, review, kt_followup, and the agents' rerecord, doc_edit and template_change (pages/AgentApprovals.tsx) */
type Approval = { id: string; kind: string; created_at: string; payload: any };

const WHY: Record<string, string> = {
  coverage_gap: "Not yet explained", meeting: "From a KT session", first_pr: "First pull request", plan: "Onboarding plan",
};

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** What a folded approval says about itself: enough to pick which one to open. */
function headerOf(a: Approval) {
  if (AGENT_KINDS.has(a.kind)) return agentHeaderOf(a);
  if (a.kind === "kt_followup") {
    return { icon: "video" as const, eyebrow: "KT session review", title: a.payload.title ?? "KT session",
      sub: "Check the follow-ups, then book them" };
  }
  if (a.kind === "task_plan") {
    const tasks = (a.payload.plans ?? []).reduce((n: number, p: any) => n + (p.tasks?.length ?? 0), 0);
    const who = (a.payload.plans ?? []).map((p: any) => String(p.new_hire).replace(/\s*\(.*?\)/g, "")).join(", ");
    return { icon: "layers" as const, eyebrow: `Task plan · ${plural(tasks, "task")}`, title: `Task plan for ${who || "a new hire"}`,
      sub: `Proposed ${a.payload.source === "meeting.recording_ready" ? "after a KT session" : "when onboarding started"}${a.created_at ? ` · ${when(a.created_at)}` : ""}` };
  }
  const pr = /\/pull\/(\d+)/.exec(a.payload.pr_url ?? "")?.[1];
  return { icon: "pr" as const, eyebrow: "Pull request review", title: pr ? `Pull request #${pr}` : "A pull request",
    sub: `Recommends: ${RECOMMENDS[a.payload.recommendation] ?? a.payload.recommendation ?? "a decision"}` };
}

/** The agent that proposed an approval: its explain block's, else the one that proposes this kind. */
const agentOf = (a: Approval) => explainOf(a.payload)?.agent_name ?? (KIND_AGENT[a.kind] ? agentName(KIND_AGENT[a.kind]) : null);

/** kind: show one kind only (#/approvals?kind=rerecord); agent: the kinds one agent proposes (#/approvals?agent=keeper). */
export default function Approvals({ focus, kind, agent }: { focus?: string | null; kind?: string | null; agent?: string | null }) {
  const { project } = useSession();
  const [rows, setRows] = useState<Approval[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Approval[]>(`/approvals?project_id=${project.id}`).then(setRows).catch((e) => setError(errorMessage(e)));
  }, [project.id]);
  useEffect(load, [load]);
  useLive("approvals", load, project.id); // a review or a plan an agent proposes, or another manager's decision, shows up at once
  const wanted = (a: Approval) => (kind ? a.kind === kind : agent ? KIND_AGENT[a.kind] === agent : true);
  const shown = rows?.filter(wanted) ?? null;
  // One approval open at a time: the one a notification linked to, else the first; when it is decided, the next one.
  const ids = shown?.map((a) => a.id).join(",") ?? "";
  useEffect(() => {
    if (!shown) return;
    setOpen((o) => (o && shown.some((a) => a.id === o) ? o : focus && shown.some((a) => a.id === focus) ? focus : shown[0]?.id ?? null));
  }, [ids, focus]); // eslint-disable-line react-hooks/exhaustive-deps

  // the filter: every kind waiting, with its count; a filter from a link stays selectable even when nothing of it waits
  const kinds = [...new Set([...(rows ?? []).map((a) => a.kind), ...(kind ? [kind] : [])])];
  const filterTo = (k: string | null) => replaceRoute(k ? `#/approvals?kind=${encodeURIComponent(k)}` : "#/approvals");
  const filtered = !!(kind || agent);
  const none = kind ? `No ${(KIND_LABEL[kind] ?? kind).toLowerCase()} waiting` : agent ? `Nothing from the ${agentName(agent)} waiting` : "";

  return (
    <section className="dp">
      <header className="dp-head">
        <div>
          <h1>Approvals{rows && rows.length > 0 && <span className="dp-count dp-count-lg">{rows.length}</span>}</h1>
          <p className="dp-sub">Agents propose; you decide.</p>
        </div>
      </header>
      {rows && (kinds.length > 1 || filtered) && (
        <div className="ap-filter" role="group" aria-label="Show">
          <button type="button" className="ap-filter-b" aria-pressed={!filtered} onClick={() => filterTo(null)}>
            All<span className="ap-filter-n">{rows.length}</span>
          </button>
          {agent && !kind && (
            <button type="button" className="ap-filter-b" aria-pressed="true" onClick={() => filterTo(null)}>
              {agentName(agent)}<span className="ap-filter-n">{shown?.length ?? 0}</span>
            </button>
          )}
          {kinds.map((k) => (
            <button key={k} type="button" className="ap-filter-b" aria-pressed={kind === k} onClick={() => filterTo(kind === k ? null : k)}>
              {KIND_LABEL[k] ?? k}<span className="ap-filter-n">{rows.filter((a) => a.kind === k).length}</span>
            </button>
          ))}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {!shown ? (!error && (
        <div aria-busy="true"><span className="sr-only" role="status">Loading…</span>
          {[0, 1].map((i) => <div key={i} className="dp-approval"><Skeleton lines={4} height={14} /></div>)}</div>
      )) : shown.length === 0 && (filtered && rows!.length > 0 ? (
        <EmptyState title={none} action={<button type="button" className="dp-btn" onClick={() => filterTo(null)}>Show all</button>}>
          {plural(rows!.length, "other approval")} {rows!.length === 1 ? "waits" : "wait"} for you.
        </EmptyState>
      ) : (
        <EmptyState title="You are all caught up" action={<a className="dp-btn" href="#/dashboard">See KT coverage</a>}>
          Plans, reviews and recorded KT sessions wait here for you.
        </EmptyState>
      ))}
      {shown && shown.length > 0 && (
        <Reveal>
          <Accordion open={open} onChange={setOpen}>
            {shown.map((a) => {
              const h = headerOf(a);
              const by = agentOf(a);
              return (
                <AccordionItem key={a.id} id={a.id} level={2} icon={<Glyph name={h.icon} size={17} />} eyebrow={h.eyebrow} title={h.title} sub={h.sub}
                               meta={<>{a.kind === "kt_followup" && <Pill tone="neutral">KT</Pill>}{by && <AgentTag name={by} />}</>}>
                  {AGENT_KINDS.has(a.kind) ? <AgentApprovalBody a={a} done={load} />
                    : a.kind === "task_plan" ? <PlanApproval a={a} done={load} />
                    : a.kind === "kt_followup" ? <KtReviewApproval a={a} done={load} />
                    : a.kind === "review" ? <ReviewApproval a={a} done={load} />
                    : <p className="dp-meta">This kind of approval needs a newer version of the app.</p>}
                </AccordionItem>
              );
            })}
          </Accordion>
        </Reveal>
      )}
    </section>
  );
}

type Edit = { title: string; description: string; due_date: string };

/** A 30/60/90 plan's phases (Days 1–30, 31–60, 61–90), when the Planner's payload carries them: each with its milestones
 *  and the tasks that belong to it (listed by id, or marked on the task, or by due day). Read defensively: the shape is new. */
type Phase = { key: string; title: string; sub: string | null; milestones: string[]; ids: Set<string> | null; from: number | null; to: number | null };
type PlanRow = PlanTask & { who: string; phase?: unknown; due_day?: number | null };
function phasesOf(payload: any): Phase[] | null {
  const raw = payload?.phases ?? payload?.plan?.phases ?? payload?.plans?.find((p: any) => Array.isArray(p?.phases))?.phases;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const n = (v: unknown) => (typeof v === "number" ? v : null);
  return raw.map((p: any, i: number) => {
    const from = n(p?.from_day) ?? n(p?.start_day) ?? n(p?.days?.[0]), to = n(p?.to_day) ?? n(p?.end_day) ?? n(p?.days?.[1]);
    const tasks = Array.isArray(p?.task_ids) ? p.task_ids : Array.isArray(p?.tasks) ? p.tasks.map((t: any) => (typeof t === "string" ? t : t?.id)) : null;
    return {
      key: String(p?.key ?? p?.id ?? i),
      title: p?.title ?? p?.label ?? p?.name ?? (from !== null && to !== null ? `Days ${from}–${to}` : `Phase ${i + 1}`),
      sub: typeof (p?.goal ?? p?.summary ?? p?.focus) === "string" ? (p.goal ?? p.summary ?? p.focus) : null,
      milestones: (Array.isArray(p?.milestones) ? p.milestones : []).map((m: any) => (typeof m === "string" ? m : m?.title ?? m?.label ?? "")).filter(Boolean),
      ids: tasks ? new Set(tasks.filter(Boolean)) : null, from, to,
    };
  });
}
function inPhase(t: PlanRow, ph: Phase, i: number) {
  if (ph.ids) return ph.ids.has(t.id);
  if (t.phase !== undefined && t.phase !== null) return String(t.phase) === ph.key || t.phase === i || t.phase === ph.title;
  return typeof t.due_day === "number" && ph.from !== null && ph.to !== null && t.due_day >= ph.from && t.due_day <= ph.to;
}

function PlanApproval({ a, done }: { a: Approval; done: () => void }) {
  const tasks: PlanRow[] = (a.payload.plans ?? []).flatMap((p: any) => (p.tasks ?? []).map((t: PlanTask) => ({ ...t, who: p.new_hire ?? "" })));
  const phases = phasesOf(a.payload);
  const [keep, setKeep] = useState<Set<string>>(new Set(tasks.map((t) => t.id)));
  const [edits, setEdits] = useState<Record<string, Edit>>({}); // the manager's changes, per task, before anything is assigned
  const [editing, setEditing] = useState<string | null>(null);
  const toggle = (id: string) => setKeep((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const { busy, error, decide, outcome } = useDecide(a.id, done);
  const who = [...new Set(tasks.map((t) => t.who))];
  const all = keep.size === tasks.length;
  const names = who.map((w) => w.replace(/\s*\(.*?\)/g, "")).join(", ");
  const shown = (t: PlanTask): Edit => edits[t.id] ?? { title: t.title, description: t.description ?? "", due_date: t.due_date ?? "" };
  const changed = (t: PlanTask) => {
    const e = edits[t.id];
    return !!e && (e.title !== t.title || e.description !== (t.description ?? "") || e.due_date !== (t.due_date ?? ""));
  };
  const set = (t: PlanTask, k: keyof Edit, v: string) => setEdits((x) => ({ ...x, [t.id]: { ...shown(t), [k]: v } }));
  /** Only what changed, for the tasks being assigned: the API applies these when it approves them. */
  function body() {
    const out: Record<string, Partial<Edit>> = {};
    for (const t of tasks) {
      if (!keep.has(t.id) || !changed(t)) continue;
      const e = edits[t.id], diff: Partial<Edit> = {};
      if (e.title.trim() && e.title.trim() !== t.title) diff.title = e.title.trim();
      if (e.description !== (t.description ?? "")) diff.description = e.description;
      if (e.due_date && e.due_date !== t.due_date) diff.due_date = e.due_date;
      if (Object.keys(diff).length) out[t.id] = diff;
    }
    return { approve: [...keep], edits: out };
  }
  const nEdited = tasks.filter((t) => keep.has(t.id) && changed(t)).length;
  const invalid = tasks.some((t) => keep.has(t.id) && !shown(t).title.trim());

  const row = (t: PlanRow) => {
    const kind = kindOf(t.kind);
    const on = keep.has(t.id);
    const v = shown(t);
    const open = editing === t.id;
    return (
      <li key={t.id} className={`dp-plan-row${on ? " on" : ""}${open ? " editing" : ""}`}>
        <label>
          <input type="checkbox" className="dp-check-input" checked={on} onChange={() => toggle(t.id)} />
          <span className="dp-check" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" /></svg></span>
          <span className="dp-plan-body">
            <strong>{v.title || t.title}</strong>
            <span className="dp-plan-meta">
              <span className="dp-tag"><Glyph name={kind.glyph} size={12} />{kind.label}</span>
              {v.due_date && <DueChip date={v.due_date} />}
              <span className="dp-meta">{WHY[t.origin] ?? t.origin}</span>
              {who.length > 1 && <span className="dp-meta">· {t.who}</span>}
              {changed(t) && <span className="dp-tag t-warn">Edited</span>}
            </span>
            {v.description && <span className="dp-plan-desc">{v.description}</span>}
          </span>
        </label>
        <button type="button" className="dp-btn ghost small dp-plan-edit" aria-expanded={open} aria-controls={`edit-${t.id}`}
                aria-label={`${open ? "Close editing" : "Edit"} ${v.title || t.title}`} onClick={() => setEditing(open ? null : t.id)} disabled={busy}>
          {open ? "Done" : "Edit"}
        </button>
        {open && (
          <div className="dp-plan-form" id={`edit-${t.id}`}>
            <label className="field"><span>Title</span>
              <input value={v.title} maxLength={200} required autoFocus onChange={(e) => set(t, "title", e.target.value)} /></label>
            <label className="field"><span>Description</span>
              <textarea value={v.description} maxLength={4000} rows={3} onChange={(e) => set(t, "description", e.target.value)} /></label>
            <div className="dp-plan-form-row">
              <label className="field"><span>Due date</span>
                <input type="date" value={v.due_date} onChange={(e) => set(t, "due_date", e.target.value)} /></label>
              {changed(t) && <button type="button" className="dp-btn ghost small" onClick={() => setEdits((x) => { const n = { ...x }; delete n[t.id]; return n; })}>Undo changes</button>}
            </div>
          </div>
        )}
      </li>
    );
  };

  if (outcome) return <article className="dp-approval done"><Decided o={outcome} /></article>;
  return (
    <article className="dp-approval">
      <p className="dp-meta dp-plan-lede">For {who.map((w) => w.replace(/\s*\(.*?\)/g, "")).join(", ")}. Untick a task to leave it out.</p>
      {phases ? (() => {
        const placed = new Set<string>();
        const groups = phases.map((ph, i) => {
          const list = tasks.filter((t) => !placed.has(t.id) && inPhase(t, ph, i));
          list.forEach((t) => placed.add(t.id));
          return { ph, list };
        });
        const rest = tasks.filter((t) => !placed.has(t.id));
        return (
          <div className="dp-phases">
            {groups.map(({ ph, list }) => (
              <section key={ph.key} className="dp-phase" aria-label={ph.title}>
                <header className="dp-phase-head">
                  <span className="dp-phase-t">{ph.title}</span>
                  {ph.sub && <span className="dp-phase-sub">{ph.sub}</span>}
                </header>
                {ph.milestones.length > 0 && (
                  <ul className="dp-phase-ms" aria-label="Milestones">{ph.milestones.map((m, i) => <li key={i}>{m}</li>)}</ul>
                )}
                {list.length > 0 ? <ul className="dp-plan">{list.map(row)}</ul> : <p className="dp-meta dp-phase-none">No tasks in this phase.</p>}
              </section>
            ))}
            {rest.length > 0 && (
              <section className="dp-phase" aria-label="Other tasks">
                <header className="dp-phase-head"><span className="dp-phase-t">Other tasks</span></header>
                <ul className="dp-plan">{rest.map(row)}</ul>
              </section>
            )}
          </div>
        );
      })() : <ul className="dp-plan">{tasks.map(row)}</ul>}
      <footer className="dp-approval-foot">
        <button type="button" className="dp-btn ghost" onClick={() => setKeep(all ? new Set() : new Set(tasks.map((t) => t.id)))} disabled={busy}>
          {all ? "Clear all" : "Select all"}
        </button>
        <button className="dp-btn primary" disabled={busy || invalid}
          onClick={() => { setEditing(null); decide(body(), keep.size
            ? { tone: "ok", title: "Plan approved", text: `${keep.size} task${keep.size === 1 ? "" : "s"} assigned to ${names}${nEdited ? `, ${nEdited} with your edits` : ""}.` }
            : { tone: "warn", title: "Plan declined", text: `No tasks were assigned to ${names}.` }); }}>
          {busy ? "Assigning…" : `Assign ${keep.size} of ${tasks.length} tasks`}
        </button>
      </footer>
      {invalid && <p className="error" role="alert">Every task you assign needs a title.</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}

function ReviewApproval({ a, done }: { a: Approval; done: () => void }) {
  const [r, setR] = useState<ReviewDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const { busy, error, decide, outcome } = useDecide(a.id, done);
  useEffect(() => { api<ReviewDetail>(`/reviews/${a.payload.review_id}`).then(setR).catch((e) => setLoadError(errorMessage(e))); }, [a.payload.review_id]);
  if (loadError) return <article className="dp-approval"><p className="error" role="alert">Could not load this review: {loadError}</p></article>;
  if (!r) return <article className="dp-approval" aria-busy="true"><Skeleton lines={4} /></article>;
  if (outcome) return <article className="dp-approval done"><Decided o={outcome} /></article>;

  return (
    <article className="dp-approval">
      <PRHeader r={r} as="h2" />
      <RecBadge rec={r.result.recommendation} />
      <p className="dp-summary">{r.result.summary}</p>
      <Findings findings={r.result.findings} prUrl={r.pr_url} rec={r.result.recommendation} />
      <footer className="dp-approval-foot">
        <a className="dp-foot-link" href={`#/reviews/${r.id}`}>Full review<Glyph name="arrow" size={14} /></a>
        <button className="dp-btn" onClick={() => decide({ decision: "changes_requested" }, { tone: "warn", title: "Changes requested", text: `Your request for changes to pull request #${r.pr_number} is recorded.` })} disabled={busy}>Request changes</button>
        <button className="dp-btn primary" onClick={() => decide({ decision: "approved" }, { tone: "ok", title: "Work approved", text: `Your approval of pull request #${r.pr_number} is recorded.` })} disabled={busy}>Approve the work</button>
      </footer>
      {error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}

type CallNotes = { meeting: { id: string; title: string; duration_s: number | null };
  notes: { summary: string; utterances: Utterance[]; action_items: { text: string }[] } | null; transcript?: string | null };

/** A recorded KT call, for the manager: what it covered, the follow-up sessions it calls for (all ticked), and the transcript
 *  behind them. Approving books the ticked sessions and sends each presenter the agenda. */
function KtReviewApproval({ a, done }: { a: Approval; done: () => void }) {
  const meetingId: string = a.payload.meeting_id;
  const [call, setCall] = useState<CallNotes | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const follow = useFollowUps(meetingId);
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const { busy, error, decide, outcome } = useDecide(a.id, done);
  useEffect(() => {
    api<CallNotes>(`/meetings/${encodeURIComponent(meetingId)}/notes`).then(setCall).catch((e) => setLoadError(errorMessage(e)));
  }, [meetingId]);
  useEffect(() => { // every follow-up that can be booked starts ticked
    if (follow.rows && picked === null) setPicked(new Set(follow.rows.filter((f) => !f.booked && f.presenter).map((f) => f.key)));
  }, [follow.rows, picked]);
  const toggle = (k: string) => setPicked((s) => { const n = new Set(s ?? []); n.has(k) ? n.delete(k) : n.add(k); return n; });

  if (outcome) return <div className="dp-approval done"><Decided o={outcome} /></div>;
  if (loadError) return <p className="error" role="alert">Could not load this session: {loadError}</p>;
  const n = call?.notes;
  const utterances = n?.utterances ?? [];
  const speakers = [...new Set(utterances.map((u) => u.speaker))];
  const chosen = picked?.size ?? 0;
  const title = a.payload.title ?? "this call";
  const approve = (keys: string[]) => decide({ approve: keys }, (r: any) => {
    const booked = r?.result?.booked?.length ?? 0, skipped = r?.result?.skipped?.length ?? 0;
    return booked
      ? { tone: "ok", title: `Approved, ${plural(booked, "session")} booked`, text: `Each presenter has the agenda.${skipped ? ` ${plural(skipped, "session")} had changed and ${skipped === 1 ? "was" : "were"} skipped.` : ""}` }
      : { tone: "ok", title: "Approved", text: `Nothing was booked for ${title}.` };
  });

  return (
    <div className="kt-review">
      {!call ? <Skeleton lines={3} /> : n && <p className="kt-review-summary"><Rich text={n.summary} /></p>}
      <div>
        <p className="kt-review-h"><span>Follow-up sessions</span>{follow.rows && follow.rows.length > 0 && <span>{chosen} of {follow.rows.filter((f) => !f.booked && f.presenter).length} ticked</span>}</p>
        {follow.error && <p className="error" role="alert">{follow.error}</p>}
        <FollowUpList meetingId={meetingId} rows={follow.rows} mode="select" selected={picked ?? new Set()} onToggle={toggle} />
      </div>
      {utterances.length > 0 && (
        <Accordion>
          <AccordionItem id="transcript" title="Transcript" count={utterances.length} sub={speakers.join(", ")}
                         meta={<Pill tone="neutral">Managers only</Pill>}>
            <TranscriptList utterances={utterances} idPrefix={`rv-${a.id}-`} />
          </AccordionItem>
        </Accordion>
      )}
      <footer className="dp-approval-foot">
        <a className="dp-foot-link" href={`#/meetings/${encodeURIComponent(meetingId)}`}>Open the session<Glyph name="arrow" size={14} /></a>
        <button className="dp-btn" onClick={() => approve([])} disabled={busy}>Approve without booking</button>
        <button className="dp-btn primary" onClick={() => approve([...(picked ?? [])])} disabled={busy || chosen === 0}>
          {busy ? "Booking…" : <><Glyph name="calendar" size={15} />Approve and book {plural(chosen, "session")}</>}
        </button>
      </footer>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
