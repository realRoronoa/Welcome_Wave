/** "Why this?": one small disclosure for every agent output. It shows the agent that produced the item, its own rationale
 *  (or the rule lines that fired), how it got there (its trace: each step with its sources, from the payload or loaded from
 *  GET /api/agents/runs/{run_id} when opened), the sources as links (code at its line, a KT session at its moment, a
 *  document), the confidence when there is one, whether AI wrote the wording, and the trace id for admins. Payloads without
 *  an `explain` block fall back to their `why` lines, so this replaces the plain "Why" disclosure wherever an agent is behind
 *  an item.
 *  AgentTag is the quiet attribution beside an agent-produced item ("Coach", "Scout"). Styles: styles/agents.css. */
import { ReactNode, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { mmss } from "../api";
import { SessionContext } from "../session";
import { Glyph } from "../dataui";
import { Explain, ktLabel, sourceHref, sourceTime } from "./agentModel";
import { RunTraceView, TraceList } from "./Trace";
import "../styles/agents.css";

/** The agent's name in ink, mono, quiet. */
export function AgentTag({ name, title }: { name: string; title?: string }) {
  return <span className="ag-tag" title={title ?? `Prepared by the ${name} agent`}><span className="sr-only">By </span>{name}</span>;
}

const SOURCE_GLYPH = { code: "code", kt: "play", doc: "file", event: "clock", metric: "layers" } as const;

type Props = {
  explain?: Explain | null;
  /** the payload's `why` lines: shown when there is no explain block (older payloads), else ignored */
  why?: string[] | string | null;
  /** extra facts under the rule lines (a blocker's evidence) */
  detail?: ReactNode;
  label?: string;
  /** show the agent's name beside the button (off where the name is already on screen) */
  tag?: boolean;
  /** who wrote an answer's words, when the item says: "extractive" is the offline writer quoting its sources (no model) */
  writer?: "model" | "extractive" | null;
};

export default function WhyThis({ explain, why, detail, label = "Why this?", tag = true, writer }: Props) {
  const session = useContext(SessionContext);
  const admin = session?.me.role === "admin";
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<{ x: number; up: boolean }>({ x: 0, up: false });
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLSpanElement>(null);

  const lines = explain?.logic.length ? explain.logic : (Array.isArray(why) ? why : why ? [why] : []).filter(Boolean);
  const steps = explain?.steps ?? [];
  const runId = !steps.length ? explain?.run_id ?? null : null; // a trace to load when opened
  const rationale = explain?.rationale && !lines.includes(explain.rationale) ? explain.rationale : null;

  // placed once it is open: below the button (above when there is no room), and kept 12 px inside the viewport
  useLayoutEffect(() => {
    if (!open || !root.current || !pop.current || !button.current) return;
    const at = root.current.getBoundingClientRect().left;
    const b = button.current.getBoundingClientRect();
    const w = pop.current.offsetWidth, h = pop.current.offsetHeight;
    let left = at;
    if (left + w > innerWidth - 12) left = innerWidth - 12 - w;
    if (left < 12) left = 12;
    setPlace({ x: Math.round(left - at), up: b.bottom + h + 16 > innerHeight && b.top - h - 16 > 72 });
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); button.current?.focus(); } };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc, true); };
  }, [open]);

  if (!explain && !lines.length && !detail) return null;
  const traced = steps.length > 0 || !!runId;
  const quoted = writer === "extractive";
  // a quoted answer has no measured confidence (the offline writer reports a fixed one): none is shown
  const pct = quoted || explain?.confidence == null ? null : Math.round(explain.confidence <= 1 ? explain.confidence * 100 : explain.confidence);
  const mode = quoted ? "Quoted from the sources, no AI" : writer === "model" || explain?.ai ? "AI wrote the wording" : "Rules only";
  return (
    <span className="ag-why" ref={root}>
      {explain && tag && <AgentTag name={explain.agent_name} />}
      <span className="ag-why-anchor">
        <button ref={button} type="button" className="ag-why-btn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9.5" /><path d="M12 11v5.5" /><path d="M12 7.6v.01" />
          </svg>
          <span>{label}</span>
        </button>
        {/* phrasing elements only (spans with list roles), so "Why this?" may sit inside a paragraph or a link's row */}
        <span id={id} ref={pop} className={`ag-why-pop${place.up ? " up" : ""}${traced ? " has-trace" : ""}`} hidden={!open} style={{ left: place.x }}
              role="group" aria-label={explain ? `Why ${explain.agent_name} shows this` : "Why this is shown"}>
          {explain && (
            <span className="ag-why-head">
              <span className="ag-why-agent">{explain.agent_name}</span>
              <span className="ag-why-mode">{mode}</span>
            </span>
          )}
          {rationale && <span className="ag-why-why">{rationale}</span>}
          {lines.length === 1 && <span className="ag-why-one">{lines[0]}</span>}
          {lines.length > 1 && <span className="ag-why-logic" role="list">{lines.map((l, i) => <span key={i} role="listitem" className="ag-why-li">{l}</span>)}</span>}
          {detail && <span className="ag-why-detail">{detail}</span>}
          {steps.length > 0 && (
            <span className="ag-why-block ag-why-trace-block">
              <span className="ag-why-k">How it got here · {steps.length} {steps.length === 1 ? "step" : "steps"}</span>
              <TraceList steps={steps} onNavigate={() => setOpen(false)} maxSources={2} />
            </span>
          )}
          {open && runId && <RunTraceView runId={runId} onNavigate={() => setOpen(false)} label="How it got here" maxSources={2} />}
          {explain && explain.sources.length > 0 && (
            <span className="ag-why-block">
              <span className="ag-why-k">Sources</span>
              <span className="ag-why-src" role="list">
                {explain.sources.map((s, i) => {
                  const href = sourceHref(s), t = sourceTime(s);
                  const inner = (
                    <>
                      <Glyph name={(SOURCE_GLYPH as Record<string, "code" | "play" | "file" | "clock" | "layers">)[s.kind] ?? "file"} size={12} />
                      {t !== null && <span className="ag-why-t">{mmss(t)}</span>}
                      <span className="ag-why-label" title={s.label}>{ktLabel(s.label, t)}</span>
                    </>
                  );
                  return (
                    <span key={i} role="listitem" className={`ag-why-s${s.kind === "kt" ? " is-kt" : ""}`}>
                      {href ? <a href={href} onClick={() => setOpen(false)}>{inner}</a> : <span className="ag-why-s-in">{inner}</span>}
                    </span>
                  );
                })}
              </span>
            </span>
          )}
          {explain && (pct !== null || (admin && explain.trace_id)) && (
            <span className="ag-why-foot">
              {pct !== null && <span>Confidence {pct}%</span>}
              {admin && explain.trace_id && <span className="ag-why-trace" title={explain.trace_id}>Trace {explain.trace_id.slice(0, 12)}</span>}
            </span>
          )}
        </span>
      </span>
    </span>
  );
}
