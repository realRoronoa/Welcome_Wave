/** "Watch it think": an agent run's steps (agentic contract §2 and §5).
 *
 *  Thinking   Ask's live view. While the agent works, one quiet line per step appears as it happens; the newest shimmers.
 *             When the answer lands, the list folds into "Thought for 6 steps · 4.2 s", a button that opens the trace.
 *  TraceList  the trace itself: each step with its sources as links. Spans only (with list roles), so it may sit inside a
 *             paragraph, as "Why this?" does.
 *  RunTrace   a run's trace loaded on demand from GET /api/agents/runs/{run_id} (the Agents page, "Why this?").
 *
 *  Monochrome; teal only on steps about a KT session. Motion is opacity and transform only, and none at all under
 *  prefers-reduced-motion. Styles: styles/trace.css. */
import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError, mmss } from "../api";
import { reducedMotion } from "../fx";
import { Logo } from "../ui";
import {
  ExplainSource, ktLabel, normaliseRun, RunTrace as Trace, secs, sourceHref, sourceTime, StepLook, stepLook, thoughtLabel, TraceStep,
} from "./agentModel";
import "../styles/trace.css";

const LOOK_PATHS: Record<Exclude<StepLook, "think">, string[]> = {
  search: ["M11 18.5a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15z", "M20.5 20.5l-4.2-4.2"],
  read: ["M14 3H6.5A2.5 2.5 0 0 0 4 5.5v13A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V9z", "M14 3v6h6", "M8.5 13.5h7", "M8.5 17h4.5"],
  kt: ["M12 2.75a9.25 9.25 0 1 1 0 18.5 9.25 9.25 0 0 1 0-18.5z", "M10.2 8.7v6.6l5.3-3.3z"],
  verify: ["M12 21.5s7.5-3.6 7.5-9.4V5.4L12 2.6 4.5 5.4v6.7c0 5.8 7.5 9.4 7.5 9.4z", "M8.8 12.2l2.2 2.2 4.2-4.4"],
  route: ["M19 21v-1.6a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4V21", "M12 11.4a3.9 3.9 0 1 0 0-7.8 3.9 3.9 0 0 0 0 7.8z"],
  done: ["M20 6.5L9.5 17 4 11.5"],
};

/** One glyph per kind of step. "think" is the brand mark (its small drawing): the assistant's own. */
export function StepIcon({ look, size = 15 }: { look: StepLook; size?: number }) {
  if (look === "think") return <Logo size={size} className="tk-svg" />;
  return (
    <svg className="tk-svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {LOOK_PATHS[look].map((d) => <path key={d} d={d} />)}
    </svg>
  );
}

function Chevron() {
  return (
    <svg className="tk-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
  );
}

/** The least time between two steps appearing: a burst of steps still reads as a sequence. */
export const STEP_GAP = 200;

/** Reveals `count` items one at a time, at least `gap` ms apart. */
function usePaced(count: number, pace: boolean, gap = STEP_GAP) {
  const [n, setN] = useState(pace ? 0 : count);
  const last = useRef(0);
  useEffect(() => {
    if (!pace) { setN(count); return; }
    if (n >= count) return;
    const wait = Math.max(0, last.current + gap - performance.now());
    const t = window.setTimeout(() => { last.current = performance.now(); setN((x) => Math.min(count, x + 1)); }, wait);
    return () => window.clearTimeout(t);
  }, [count, pace, n, gap]);
  return Math.min(n, count);
}

type ThinkingProps = {
  steps: TraceStep[];
  /** the agent is still working */
  live: boolean;
  /** how long it took, when known (else the steps' own times add up) */
  ms?: number | null;
  /** the line shown before the first step arrives (or all along, when the server streams none) */
  waiting?: string;
};

/** Ask's thinking: live steps, then the one-line summary that opens the trace. Renders nothing for an answer without steps. */
export function Thinking({ steps, live, ms, waiting = "Checking the code, documents and KT sessions…" }: ThinkingProps) {
  const still = reducedMotion();
  const shown = usePaced(steps.length, live && !still);
  const [phase, setPhase] = useState<"live" | "folding" | "done">(live ? "live" : "done");
  const [open, setOpen] = useState(false);
  const traceId = useId();
  const onScreen = useRef<TraceStep[]>([]);

  useEffect(() => {
    if (live) { setPhase("live"); return; }
    if (phase !== "live") return;
    if (still || onScreen.current.length === 0) { setPhase("done"); return; }
    setPhase("folding"); // the steps fade up and out, then the summary takes their place
    const t = window.setTimeout(() => setPhase("done"), 210);
    return () => window.clearTimeout(t);
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

  // folding keeps exactly the lines that were on screen (the answer's own steps may differ): they fade out together
  const folding = phase === "folding" || (phase === "live" && !live);
  const rows = phase === "done" ? [] : folding ? onScreen.current : steps.slice(0, shown);
  if (phase !== "done") onScreen.current = rows;
  if (phase !== "done" && (live || rows.length)) {
    if (!rows.length) {
      return (
        <div className="tk tk-wait">
          <span className="tk-ico"><Logo size={15} motion="busy" className="tk-svg" /></span>
          <span className="tk-text is-sheen">{waiting}</span>
        </div>
      );
    }
    return (
      <ol className={`tk tk-live${folding ? " is-folding" : ""}`} aria-label="What the assistant is doing">
        {rows.map((s, i) => {
          const look = stepLook(s);
          const current = !folding && i === rows.length - 1;
          return (
            <li key={s.n} className={`tk-row is-${look}${current ? " is-current" : ""}`} style={{ ["--i" as string]: rows.length - 1 - i }}>
              <span className="tk-ico"><StepIcon look={look} /></span>
              <span className={`tk-text${current ? " is-sheen" : ""}`}>{s.text}</span>
            </li>
          );
        })}
      </ol>
    );
  }

  if (!steps.length) return null;
  return (
    <div className={`tk tk-done${open ? " is-open" : ""}`}>
      <button type="button" className="tk-sum" aria-expanded={open} aria-controls={traceId} onClick={() => setOpen((o) => !o)}>
        <span className="tk-ico"><StepIcon look="think" /></span>
        <span>{thoughtLabel(steps, ms)}</span>
        <Chevron />
      </button>
      <div id={traceId} className="tk-trace" hidden={!open}>
        {open && <TraceList steps={steps} />}
      </div>
    </div>
  );
}

/** A source in a trace: the code at its line, the KT session at its moment (teal), the document at its page. */
function SourceLink({ s, onNavigate }: { s: ExplainSource; onNavigate?: () => void }) {
  const href = sourceHref(s), t = sourceTime(s);
  const inner = <>{t !== null && <span className="tk-src-t">{mmss(t)}</span>}<span className="tk-src-l">{ktLabel(s.label, t)}</span></>;
  const cls = `tk-src${s.kind === "kt" ? " is-kt" : ""}`;
  return href
    ? <a className={cls} href={href} onClick={onNavigate} title={s.label}>{inner}</a>
    : <span className={cls} title={s.label}>{inner}</span>;
}

/** The trace: one line per step, its sources under it, its time on the right. */
export function TraceList({ steps, onNavigate, maxSources = 3 }: { steps: TraceStep[]; onNavigate?: () => void; maxSources?: number }) {
  const [all, setAll] = useState<Set<number>>(() => new Set());
  return (
    <span className="tk-list" role="list">
      {steps.map((s, i) => {
        const look = stepLook(s);
        const t = secs(s.ms);
        const srcs = all.has(s.n) ? s.sources : s.sources.slice(0, maxSources);
        const more = s.sources.length - srcs.length;
        return (
          <span key={s.n} role="listitem" className={`tk-row is-${look}`} style={{ ["--i" as string]: i }}>
            <span className="tk-ico"><StepIcon look={look} /></span>
            <span className="tk-body">
              <span className="tk-line"><span className="tk-text">{s.text}</span>{t && <span className="tk-ms">{t}</span>}</span>
              {srcs.length > 0 && (
                <span className="tk-srcs">
                  {srcs.map((x, j) => <SourceLink key={j} s={x} onNavigate={onNavigate} />)}
                  {more > 0 && (
                    <button type="button" className="tk-more" onClick={() => setAll((a) => new Set(a).add(s.n))}>+{more} more</button>
                  )}
                </span>
              )}
            </span>
          </span>
        );
      })}
    </span>
  );
}

/** Loads a run's trace once (GET /api/agents/runs/{run_id}). `null` while loading; a server without traces gives `missing`. */
export function useRunTrace(runId: string | null | undefined, on = true) {
  const [state, setState] = useState<{ trace: Trace | null; missing: boolean; failed: boolean; denied: boolean }>({ trace: null, missing: false, failed: false, denied: false });
  useEffect(() => {
    if (!runId || !on) return;
    let live = true;
    api(`/agents/runs/${encodeURIComponent(runId)}`).then(
      (d) => { if (live) setState({ trace: normaliseRun(d), missing: false, failed: false, denied: false }); },
      (e) => {
        if (!live) return;
        const status = e instanceof ApiError ? e.status : 0;
        setState({ trace: null, missing: status === 404 || status === 405, failed: status !== 404 && status !== 403 && status !== 405, denied: status === 403 });
      },
    );
    return () => { live = false; };
  }, [runId, on]);
  return state;
}

/** A run's trace, loaded when shown: the Agents page's open run, "Why this?" when the payload names its run only.
 *  `label` adds a heading; with one, a run the server keeps no steps for shows nothing at all (a quiet "Why this?"). */
export function RunTraceView({ runId, onNavigate, label, empty = "No steps were kept for this run.", maxSources }:
  { runId: string; onNavigate?: () => void; label?: string; empty?: string; maxSources?: number }) {
  const { trace, missing, failed, denied } = useRunTrace(runId);
  if (label && (missing || (trace && !trace.private && !trace.steps.length))) return null;
  const body = denied || trace?.private ? <span className="tk-note">Private run. Only the person it was for can see its steps.</span>
    : missing ? <span className="tk-note">Steps aren't kept for this run.</span>
    : failed ? <span className="tk-note">The steps could not be loaded.</span>
    : !trace ? <span className="tk-note tk-loading" aria-busy="true">Loading steps…</span>
    : !trace.steps.length ? <span className="tk-note">{empty}</span>
    : (
      <span className="tk-run">
        <span className="tk-run-sum">{thoughtLabel(trace.steps, trace.run?.ms)}</span>
        <TraceList steps={trace.steps} onNavigate={onNavigate} maxSources={maxSources} />
      </span>
    );
  if (!label) return body;
  return <span className="ag-why-block ag-why-trace-block"><span className="ag-why-k">{label}</span>{body}</span>;
}
