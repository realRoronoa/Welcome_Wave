import { FormEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, isManager, KTClip, mmss, patch, post, safeHref, Task } from "../api";
import { useSession } from "../session";
import { toast } from "../ui";
import { EmptyState, Reveal, Ring, Skeleton } from "../fx";
import { CodeBlock, DueChip, Glyph, kindOf, Pill, SectionHead, STATUS_TONE, Tone } from "../dataui";
import { Accordion, AccordionItem } from "../accordion";
import { Findings, ReviewResult } from "./Review";
import { useLive } from "../live";

const LABEL: Record<string, string> = {
  proposed: "Waiting for manager", approved: "To do", in_progress: "In progress", submitted: "In review",
  changes_requested: "Changes requested", done: "Done", rejected: "Not assigned",
};
const NEXT: Record<string, { to: string; label: string }> = {
  approved: { to: "in_progress", label: "Start task" },
  in_progress: { to: "submitted", label: "Mark as submitted" },
  changes_requested: { to: "in_progress", label: "Resume work" },
};
/** A manager's decision on submitted work (code with a pull request is decided through its review in Approvals). */
const DECIDE: { to: string; label: string; primary?: boolean }[] = [
  { to: "done", label: "Mark done", primary: true },
  { to: "changes_requested", label: "Ask for changes" },
];
const ORDER = ["changes_requested", "in_progress", "approved", "submitted", "proposed", "done"];
/** Lanes, in the order a person works through them. Done folds away: it is history, not work. */
const LANES: { title: string; statuses: string[] }[] = [
  { title: "Needs attention", statuses: ["changes_requested"] },
  { title: "In progress", statuses: ["in_progress"] },
  { title: "To do", statuses: ["approved"] },
  { title: "In review", statuses: ["submitted"] },
  { title: "Waiting for manager", statuses: ["proposed"] },
];
/** Where a task came from, when it is not the ordinary onboarding plan. */
const ORIGIN: Record<string, string> = { coverage_gap: "Not yet explained", meeting: "From a KT session", first_pr: "First pull request" };
const prLabel = (t: Task) => (t.pr_number ? `#${t.pr_number}` : t.pr_url?.match(/\/pull\/(\d+)/)?.[1] ? `#${t.pr_url.match(/\/pull\/(\d+)/)![1]}` : "PR");

/** What the agent looks for to match a pull request to this task: task-<first 8 hex digits of the id>. */
const branchRef = (id: string) => `task-${id.replace(/-/g, "").slice(0, 8).toLowerCase()}`;
const OPEN = new Set(["approved", "in_progress", "changes_requested"]);
/** A hire can review their own change privately while the task is open or waiting for review. */
const REVIEWABLE = new Set(["approved", "in_progress", "changes_requested", "submitted"]);

/** related_paths are often folders: link a folder to the first indexed file under it, or to nothing. */
function fileFor(path: string, files: string[]) {
  const dir = path.replace(/^\.\//, "").replace(/\/+$/, "");
  return files.includes(dir) ? dir : files.find((f) => f.startsWith(dir + "/")) ?? null;
}
const fileHref = (p: string, line?: number | null) => `#/code?path=${encodeURIComponent(p)}${line ? `&line=${line}` : ""}`;
/** Done is not teal: teal means "explained in a KT session". */
const toneOf = (status: string): { tone: Tone; live?: boolean } => (status === "done" ? { tone: "neutral" } : STATUS_TONE[status] ?? { tone: "neutral" });

/** The first-task brief (GET /onboardings/{id}/program → first_task), written when the manager chooses the task. */
type Brief = {
  candidate_id: string; title: string; issue: number | null; files: string[]; why: string[]; owners: string[]; tests: string[];
  kt_clips: KTClip[]; acceptance_criteria: string[]; next: string; task_id: string | null; selected_at?: string;
};

export default function Tasks() {
  const { project, me, demo } = useSession();
  const hire = me.role === "new_hire";
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [moved, setMoved] = useState<string | null>(null); // the task that just changed lane, for a brief highlight
  const [brief, setBrief] = useState<Brief | null>(null);
  const [reviewing, setReviewing] = useState<Task | null>(null); // the task whose private review is open

  const load = useCallback(() => {
    api<Task[]>(`/tasks?project_id=${project.id}`).then(setTasks).catch((e) => setError(errorMessage(e)));
  }, [project.id]);
  useEffect(() => {
    load();
    api<{ path: string }[]>(`/code/files?project_id=${project.id}`).then((r) => setFiles(r.map((f) => f.path))).catch(() => { /* paths show as plain text */ });
  }, [project.id, load]);

  // the hire's first-task brief: their onboarding comes from a task, or from their home page when no task is visible yet
  const onboardingId = tasks?.find((t) => t.onboarding_id)?.onboarding_id ?? null;
  const loadBrief = useCallback(async () => {
    if (!hire || tasks === null) return;
    try {
      const id = onboardingId ?? (await api<{ sections?: { program?: { profile?: { onboarding_id?: string } } } }>("/home"))
        .sections?.program?.profile?.onboarding_id;
      if (!id) return setBrief(null);
      const program = await api<{ first_task: Brief | null }>(`/onboardings/${encodeURIComponent(id)}/program`);
      setBrief(program.first_task ?? null);
    } catch { setBrief(null); /* the brief is extra: the task list still works */ }
  }, [hire, tasks === null, onboardingId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadBrief(); }, [loadBrief]);
  // a review decision, a merged pull request, the other person's move or a newly chosen first task shows up at once
  useLive("tasks", () => { load(); loadBrief(); }, project.id);

  async function move(t: Task, to: string) {
    setBusy(t.id); setError(null);
    try {
      const updated = await patch<Task>(`/tasks/${t.id}`, { status: to });
      setTasks((xs) => (xs ?? []).map((x) => (x.id === t.id ? { ...x, ...updated } : x)));
      setMoved(t.id);
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(null); }
  }

  /** Demo only (no GitHub App): open a pull request for this code task, or push a new commit to the open one. The same webhook
   *  path GitHub drives matches it to the task, and the Review agent reviews the demo diff. */
  async function simulate(t: Task) {
    setBusy(t.id); setError(null);
    try {
      const r = await post<{ task: Task; review_id: string | null }>("/demo/pull-request", { task_id: t.id });
      toast(t.pr_number ? "New commit pushed and reviewed." : "Pull request opened and reviewed.");
      setMoved(t.id);
      if (r.review_id) location.hash = `#/reviews/${r.review_id}`;
      else load();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(null); }
  }

  const title = hire ? "My tasks" : "Team tasks";
  const manager = isManager(me.role); // a proposed task waits on this person: say so, and point to where they decide
  if (!tasks) {
    return error ? (
      <section className="dp"><header className="dp-head"><div><h1>{title}</h1></div></header><p className="error" role="alert">{error}</p></section>
    ) : (
      <section className="dp" aria-busy="true">
        <span className="sr-only" role="status">Loading…</span>
        <header className="dp-head"><div><h1>{title}</h1></div></header>
        <div className="dp-tasks">{[0, 1, 2].map((i) => <div key={i} className="dp-task"><Skeleton lines={3} height={14} /></div>)}</div>
      </section>
    );
  }

  const sorted = [...tasks].filter((t) => t.status !== "rejected").sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
  const finished = sorted.filter((t) => t.status === "done");
  const done = finished.length;
  const active = tasks.filter((t) => !["rejected", "proposed"].includes(t.status)).length;
  const briefTask = hire && brief?.task_id ? tasks.find((t) => t.id === brief.task_id) ?? null : null;
  // the first task is shown once, as its brief, while it is open; once done it joins the others under Done
  const listed = sorted.filter((t) => !(briefTask && t.id === briefTask.id && t.status !== "done"));
  const lanes = LANES.map((l) => ({ ...l, tasks: listed.filter((t) => l.statuses.includes(t.status)) })).filter((l) => l.tasks.length > 0);
  const canReview = (t: Task) => hire && t.kind === "code" && REVIEWABLE.has(t.status);

  /** What the hire can do with a task: move it on, (demo) open its pull request, review their change privately. */
  const hireActions = (t: Task, big = false) => (
    <>
      {canReview(t) && (
        <button type="button" className={`dp-btn dp-review-btn${big ? (t.status === "in_progress" && !demo ? " primary" : "") : " ghost small"}`} onClick={() => setReviewing(t)}>
          <LockGlyph size={big ? 14 : 13} />{big ? "Run private review" : "Private review"}
        </button>
      )}
      {demo && t.kind === "code" && t.status !== "approved" && (OPEN.has(t.status) || t.status === "submitted") && (
        <button className={`dp-btn${t.status === "in_progress" ? " primary" : ""}`} onClick={() => simulate(t)} disabled={busy === t.id}
                title="Demo: stands in for opening a pull request on GitHub">
          <Glyph name="pr" size={14} />{busy === t.id ? "Opening…" : t.pr_number ? "Push a new commit" : "Open a pull request"}
        </button>
      )}
      {NEXT[t.status] && !(t.kind === "code" && demo && t.status === "in_progress") && (
        <button className={`dp-btn${t.status === "in_progress" ? "" : " primary"}`} onClick={() => move(t, NEXT[t.status].to)} disabled={busy === t.id}>
          {busy === t.id ? "Saving…" : NEXT[t.status].label}
        </button>
      )}
    </>
  );

  const card = (t: Task) => {
    const kind = kindOf(t.kind);
    const tone = toneOf(t.status);
    const label = manager && t.status === "proposed" ? "Needs your approval" : LABEL[t.status] ?? t.status;
    return (
      <li key={t.id} className={`dp-task spot k-${t.kind} s-${t.status}${moved === t.id ? " moved" : ""}`} onAnimationEnd={() => moved === t.id && setMoved(null)}>
        <span className="dp-kind" title={kind.label}><Glyph name={kind.glyph} size={17} /></span>
        <div className="dp-task-main">
          <p className="dp-meta dp-task-kicker">{kind.label}{ORIGIN[t.origin] ? ` · ${ORIGIN[t.origin]}` : ""}</p>
          <h3>{t.title}</h3>
          {t.description && t.description.trim() !== t.title.trim() && <p className="dp-task-desc">{t.description}</p>}
          {t.acceptance_criteria.length > 0 && t.status !== "done" && (
            <ul className="dp-criteria" aria-label="Done when">
              {t.acceptance_criteria.map((c, i) => <li key={i}><Glyph name="check" size={12} />{c}</li>)}
            </ul>
          )}
          {t.related_paths.length > 0 && (
            <p className="dp-chips">
              {t.related_paths.map((p) => {
                const file = fileFor(p, files);
                return file ? <a key={p} className="dp-chip" href={fileHref(file)}><Glyph name="file" size={12} />{p}</a>
                  : <span key={p} className="dp-chip"><Glyph name="file" size={12} />{p}</span>;
              })}
            </p>
          )}
          {t.kind === "code" && hire && OPEN.has(t.status) && (
            <p className="dp-hint dp-branch">
              <Glyph name="branch" size={14} />
              <span>Branch <code>{branchRef(t.id)}</code>{t.github_issue_number ? <>, or <code>Closes #{t.github_issue_number}</code> in the pull request</> : null}</span>
            </p>
          )}
        </div>
        <div className="dp-task-side">
          <Pill tone={tone.tone} live={tone.live}>{label}</Pill>
          {t.due_date && <DueChip date={t.due_date} done={t.status === "done"} />}
          {!hire && t.assignee && <span className="dp-assignee"><Glyph name="users" size={12} />{t.assignee.replace(/\s*\(.*?\)/g, "")}</span>}
          {t.review_id && (
            <a className="dp-chip" href={`#/reviews/${t.review_id}`} aria-label={`Review of pull request ${prLabel(t)}`}>
              <Glyph name="shield" size={12} />Review
            </a>
          )}
          {safeHref(t.pr_url) && (
            <a className="dp-chip dp-pr" href={safeHref(t.pr_url)!} target="_blank" rel="noreferrer" aria-label={`Open pull request ${prLabel(t)}`}>
              <Glyph name="pr" size={13} />{prLabel(t)}<Glyph name="external" size={11} />
            </a>
          )}
          {hire && hireActions(t)}
          {manager && t.status === "submitted" && (t.review_pending ? (
            <a className="dp-btn small" href="#/approvals">Decide in Approvals</a>
          ) : (
            <span className="dp-task-actions">
              {DECIDE.map((d) => (
                <button key={d.to} className={`dp-btn small${d.primary ? " primary" : ""}`} onClick={() => move(t, d.to)} disabled={busy === t.id}>
                  {busy === t.id ? "Saving…" : d.label}
                </button>
              ))}
            </span>
          ))}
        </div>
      </li>
    );
  };

  return (
    <section className="dp dp-tasks-page">
      <header className="dp-head">
        <div>
          <h1>{title}</h1>
          <p className="dp-sub">{active ? `${done} of ${active} done`
            : sorted.length ? (manager ? "A proposed plan is waiting for your approval." : "Your plan is waiting for your manager.")
            : hire ? "Tasks appear once your manager approves your plan." : "Tasks appear once a plan is approved."}</p>
        </div>
        {active > 0 && (
          <div className="dp-progress-card">
            <Ring value={done / active} size={52} stroke={5} label={`${Math.round((done / active) * 100)}%`} />
            <p><strong>{done} of {active}</strong><span className="dp-meta">tasks done</span></p>
          </div>
        )}
      </header>
      {error && <p className="error" role="alert">{error}</p>}

      {hire && brief && <FirstTaskBrief brief={brief} task={briefTask} files={files} actions={briefTask ? hireActions(briefTask, true) : null} />}

      {sorted.length === 0 && !brief && (
        <EmptyState title="No tasks yet">{hire ? "Your manager approves your plan; the tasks show up here." : "Approved onboarding tasks show up here."}</EmptyState>
      )}
      {lanes.map((lane, li) => (
        <Reveal key={lane.title} delay={li * 60} className="dp-lane">
          {manager && lane.statuses.includes("proposed") ? (
            <SectionHead title="Waiting for your approval" count={lane.tasks.length}>
              <a className="dp-section-link" href="#/approvals">Open Approvals<Glyph name="arrow" size={14} /></a>
            </SectionHead>
          ) : <SectionHead title={lane.title} count={lane.tasks.length} />}
          <ul className="dp-tasks">{lane.tasks.map(card)}</ul>
        </Reveal>
      ))}
      {finished.length > 0 && (
        <Reveal delay={lanes.length * 60} className="dp-lane dp-done-lane">
          <Accordion initial={lanes.length === 0 ? "done" : null}>
            <AccordionItem id="done" title="Done" count={finished.length} icon={<Glyph name="check" size={16} />}
                           sub={finished.slice(0, 2).map((t) => t.title).join(" · ") + (finished.length > 2 ? ` · and ${finished.length - 2} more` : "")}>
              <ul className="dp-tasks">{finished.map(card)}</ul>
            </AccordionItem>
          </Accordion>
        </Reveal>
      )}
      <PrivateReview task={reviewing} onClose={() => setReviewing(null)} />
    </section>
  );
}

function LockGlyph({ size = 13 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 11h14v10H5z" /><path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

/** How to run a test file, from its language. A suggestion: the repository's README has the final word. */
function testCommand(path: string) {
  if (/\.py$/.test(path)) return `python -m pytest ${path}`;
  if (/\.(ts|tsx|js|jsx|mjs)$/.test(path)) return `npm test -- ${path}`;
  if (/_test\.go$/.test(path)) return `go test ./${path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "."}`;
  if (/\.rs$/.test(path)) return "cargo test";
  return null;
}

/** The hire's first task, as their manager chose it: the next step first, the rest folded. */
function FirstTaskBrief({ brief, task, files, actions }: { brief: Brief; task: Task | null; files: string[]; actions: ReactNode }) {
  const commands = brief.tests.map(testCommand).filter((c): c is string => !!c);
  const meetings = [...new Set(brief.kt_clips.map((c) => c.meeting_title))];
  const step = !task ? "Waiting for your manager to approve it."
    : task.status === "approved" ? "Start it, then run a private review on your branch before you open the pull request."
    : task.status === "done" ? "Done."
    : task.pr_url ? "Your pull request is open. Its review is in your tasks."
    : "Run a private review on your branch, then open the pull request.";
  return (
    <Reveal as="section" className="dp-card dp-brief" id="first-task">
      <div className="dp-brief-head">
        <div>
          <p className="dp-eyebrow">First task{brief.issue ? ` · Issue #${brief.issue}` : ""}</p>
          <h2>{brief.title.replace(/^First PR:\s*/i, "")}</h2>
          {task && (
            <p className="dp-chips dp-brief-state">
              <Pill tone={toneOf(task.status).tone} live={toneOf(task.status).live}>{LABEL[task.status] ?? task.status}</Pill>
              {task.due_date && <DueChip date={task.due_date} done={task.status === "done"} />}
              {task.review_id && <a className="dp-chip" href={`#/reviews/${task.review_id}`}><Glyph name="shield" size={12} />Review</a>}
            </p>
          )}
          <p className="dp-brief-next"><Glyph name="arrow" size={14} /><span>{step}</span></p>
          {task && task.kind === "code" && OPEN.has(task.status) && (
            <p className="dp-hint dp-branch">
              <Glyph name="branch" size={14} />
              <span>Branch <code>{branchRef(task.id)}</code>{task.github_issue_number ? <>, or <code>Closes #{task.github_issue_number}</code> in the pull request</> : null}</span>
            </p>
          )}
        </div>
        {actions && <div className="dp-brief-actions">{actions}</div>}
      </div>
      <Accordion className="dp-brief-acc">
        <AccordionItem id="why" title="Why this task" icon={<Glyph name="sparkle" size={15} />} sub={brief.why[0]}>
          <ul className="dp-ticks">{brief.why.map((w, i) => <li key={i}><Glyph name="check" size={13} /><span>{w}</span></li>)}</ul>
        </AccordionItem>
        <AccordionItem id="files" title="Files and tests" count={brief.files.length} icon={<Glyph name="file" size={15} />} sub={brief.files.join(" · ")}>
          <p className="dp-chips dp-brief-files">
            {brief.files.map((f) => (files.length === 0 || files.includes(f))
              ? <a key={f} className="dp-chip" href={fileHref(f)}><Glyph name="file" size={12} />{f}</a>
              : <span key={f} className="dp-chip"><Glyph name="file" size={12} />{f}</span>)}
          </p>
          {commands.length > 0 && <CodeBlock code={commands.join("\n")} label="Run the tests" />}
        </AccordionItem>
        {brief.kt_clips.length > 0 && (
          <AccordionItem id="kt" title="Explained in KT sessions" count={brief.kt_clips.length} icon={<Glyph name="play" size={15} />} sub={meetings.join(" · ")}>
            <ul className="dp-brief-clips">
              {brief.kt_clips.map((c, i) => (
                <li key={i}>
                  <a className="dp-brief-clip" href={`#/meetings/${encodeURIComponent(c.meeting_id)}?t=${Math.floor(c.start_s)}`}>
                    <span className="dp-brief-time">{mmss(c.start_s)}</span>
                    <span className="dp-brief-clip-text"><strong>{c.symbol ?? c.file_path}</strong><span>{c.meeting_title}</span></span>
                    <Glyph name="play" size={13} />
                  </a>
                </li>
              ))}
            </ul>
          </AccordionItem>
        )}
        <AccordionItem id="done-when" title="Done when" count={brief.acceptance_criteria.length} icon={<Glyph name="check" size={15} />}
                       sub={brief.acceptance_criteria[0]}>
          <ul className="dp-criteria">{brief.acceptance_criteria.map((c, i) => <li key={i}><Glyph name="check" size={12} />{c}</li>)}</ul>
        </AccordionItem>
        {brief.owners.length > 0 && (
          <AccordionItem id="owners" title="Who reviews it" icon={<Glyph name="users" size={15} />} sub={brief.owners.map((o) => `@${o}`).join(", ")}>
            <p className="dp-chips">{brief.owners.map((o) => <span key={o} className="dp-chip">@{o}</span>)}</p>
            <p className="dp-meta dp-brief-note">Code owners of these files.</p>
          </AccordionItem>
        )}
      </Accordion>
    </Reveal>
  );
}

type PrivateResult = ReviewResult & { files: string[] };
const VERDICT: Record<string, { label: string; tone: Tone }> = {
  looks_good: { label: "Looks ready", tone: "neutral" }, needs_work: { label: "Needs work", tone: "warn" },
  not_enough_information: { label: "Not enough to judge", tone: "warn" },
};

/** The hire's private review: their change (a pasted diff, or a draft pull request's number) through the same Review agent a
 *  pull request gets. The result lives in this dialog only: not stored, never shown to anyone else. */
function PrivateReview({ task, onClose }: { task: Task | null; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [mode, setMode] = useState<"diff" | "pr">("diff");
  const [diff, setDiff] = useState("");
  const [pr, setPr] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PrivateResult | null>(null);

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (task && !d.open) { setResult(null); setError(null); d.showModal(); }
    if (!task && d.open) d.close();
  }, [task]);

  async function run(e: FormEvent) {
    e.preventDefault();
    if (!task || busy) return;
    setBusy(true); setError(null); setResult(null);
    try {
      const body = mode === "diff" ? { diff } : { pr_number: Number(pr) };
      setResult(await post<PrivateResult>(`/tasks/${task.id}/presubmit-review`, body));
    } catch (err) { setError(errorMessage(err)); } finally { setBusy(false); }
  }

  const ready = mode === "diff" ? diff.trim().length > 0 : /^\d+$/.test(pr.trim()) && Number(pr) > 0;
  const verdict = result ? VERDICT[result.recommendation] ?? { label: result.recommendation, tone: "neutral" as Tone } : null;
  return (
    <dialog ref={dialog} className="dp-dialog" aria-labelledby="pr-h" onClose={onClose}
            onClick={(e) => { if (e.target === dialog.current) dialog.current?.close(); }}>
      <div className="dp-dialog-inner">
        <header className="dp-dialog-head">
          <div>
            <h2 id="pr-h">Private review</h2>
            <p className="dp-private"><LockGlyph />Only you can see this. Nothing is stored.</p>
          </div>
          <button type="button" className="dp-btn ghost small dp-dialog-x" onClick={() => dialog.current?.close()} aria-label="Close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </header>
        {task && <p className="dp-meta dp-dialog-task">{task.title}</p>}
        <form className="dp-review-form" onSubmit={run}>
          <div className="dp-toggle" role="radiogroup" aria-label="What to review">
            {(["diff", "pr"] as const).map((m) => (
              <button key={m} type="button" role="radio" aria-checked={mode === m} onClick={() => setMode(m)}>
                {m === "diff" ? "Paste a diff" : "Pull request number"}
              </button>
            ))}
          </div>
          {mode === "diff" ? (
            <label className="dp-field">
              <span className="sr-only">Your diff</span>
              <textarea className="dp-diff" rows={8} value={diff} onChange={(e) => setDiff(e.target.value)} spellCheck={false}
                        placeholder={"git diff main...HEAD"} />
            </label>
          ) : (
            <label className="dp-field dp-pr-field">
              <span>Draft pull request</span>
              <span className="dp-pr-input"><span aria-hidden="true">#</span>
                <input inputMode="numeric" value={pr} onChange={(e) => setPr(e.target.value.replace(/[^\d]/g, ""))} placeholder="12" /></span>
            </label>
          )}
          <div className="dp-review-run">
            <button type="submit" className="dp-btn primary" disabled={busy || !ready}>{busy ? "Reviewing…" : "Run review"}</button>
          </div>
        </form>
        {error && <p className="error" role="alert">{error}</p>}
        {result && verdict && (
          <div className="dp-private-result" role="region" aria-label="Review result">
            <div className="dp-verdict">
              <Pill tone={verdict.tone}>{verdict.label}</Pill>
              <span className="dp-meta">{result.files.length} {result.files.length === 1 ? "file" : "files"} read</span>
            </div>
            <p className="dp-summary">{result.summary}</p>
            <Findings findings={result.findings} rec={result.recommendation} />
            {result.questions.length > 0 && (
              <>
                <h3 className="dp-sub-h">Be ready to answer</h3>
                <ol className="dp-qa">{result.questions.map((q, i) => <li key={i}><span className="dp-qa-n" aria-hidden="true">Q{i + 1}</span><span>{q}</span></li>)}</ol>
              </>
            )}
          </div>
        )}
      </div>
    </dialog>
  );
}
