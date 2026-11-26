/* The product as the app draws it, rebuilt for the site from the same data shapes and the same tokens (monochrome, fine
   borders, Geist, teal only for recorded explanations). The app's own Timeline and FigureTile (lifecycle/) import
   lifecycle/kit.tsx, which pulls the API client and the app's stylesheets into whatever imports it, so the site keeps these
   faithful copies instead: same markup roles, same states, same labels.
   Elements carrying data-at reveal once their scene's progress passes that value (ScrollStoryController toggles .is-on). */
import { CSSProperties, ReactNode } from "react";
import type { Milestone, MilestoneState, ReportFigure } from "../../../lifecycle/types";

/* ------------------------------------------------------------------------------------------------- code (Python) */
const KW = new Set(["def", "return", "for", "in", "if", "not", "continue", "class", "from", "import", "pass", "raise", "and", "or", "is", "None", "True", "False"]);
type Tok = { k: "" | "kw" | "str" | "com" | "def" | "num" | "fn"; s: string };

/** A small Python tokenizer, enough for the demo file: keywords, strings (f-strings included), comments, names after def. */
export function tokens(line: string): Tok[] {
  const out: Tok[] = [];
  const re = /("""[^]*?(?:"""|$)|[rbf]?"(?:\\.|[^"\\])*"|[rbf]?'(?:\\.|[^'\\])*'|#.*$|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z0-9_]*\b|\s+|.)/g;
  let m: RegExpExecArray | null, prevDef = false;
  while ((m = re.exec(line))) {
    const s = m[0];
    let k: Tok["k"] = "";
    if (/^[rbf]?["']/.test(s) || s.startsWith('"""')) k = "str";
    else if (s.startsWith("#")) k = "com";
    else if (/^\d/.test(s)) k = "num";
    else if (KW.has(s)) k = "kw";
    else if (prevDef && /^[A-Za-z_]/.test(s)) k = "def";
    else if (/^[A-Za-z_]/.test(s) && line[re.lastIndex] === "(") k = "fn";
    if (/\S/.test(s)) prevDef = s === "def" || s === "class";
    out.push({ k, s });
  }
  return out;
}

export function CodeLine({ n, text, marked, anchor, children }: { n: number; text: string; marked?: boolean; anchor?: string; children?: ReactNode }) {
  return (
    <div className={`u-code-line${marked ? " is-kt" : ""}`}>
      {anchor && <span className="u-anchor u-anchor-gutter" data-anchor={anchor} aria-hidden="true" />}
      <span className="u-code-n" aria-hidden="true">{n}</span>
      <code>{tokens(text).map((t, i) => t.k ? <span key={i} className={`t-${t.k}`}>{t.s}</span> : t.s)}{text === "" ? " " : null}</code>
      {children}
    </div>
  );
}

/* --------------------------------------------------------------------------------------------- KT recording bits */
/** The KT call's waveform as the app's player draws it: one bar per 2 seconds, speech tall, silence a hairline; the moment
 *  being shown is teal (a recorded explanation). Seeded, so every visit draws the same call. */
export function Waveform({ speech, length, moment, bars = 96 }: { speech: [number, number][]; length: number; moment: [number, number]; bars?: number }) {
  let a = 11;
  const rand = () => { a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const step = length / bars;
  return (
    <svg className="u-wave" viewBox={`0 0 ${bars} 24`} preserveAspectRatio="none" aria-hidden="true" focusable="false">
      {Array.from({ length: bars }, (_, i) => {
        const s = i * step;
        const on = speech.some(([f, t]) => s >= f && s <= t);
        const h = on ? 6 + rand() * 18 : 1.5;
        const kt = s >= moment[0] && s <= moment[1];
        return <rect key={i} x={i + 0.2} y={12 - h / 2} width={0.6} height={h} rx={0.3} className={kt ? "is-kt" : undefined} />;
      })}
    </svg>
  );
}

/** The teal mark the app puts on code a KT call explains */
export function KtMark({ children }: { children: ReactNode }) {
  return (
    <span className="u-ktmark">
      <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M3.5 2.2v7.6L10 6z" /></svg>
      {children}
    </span>
  );
}

/* ----------------------------------------------------------------------------------- the onboarding timeline (app) */
const STATE_LABEL: Record<MilestoneState, string> = { done: "Done", skipped: "Skipped", overdue: "Overdue", due_soon: "Due soon", upcoming: "Upcoming" };

export function Tick({ state, size = 10 }: { state: MilestoneState; size?: number }) {
  return <span className={`u-mark m-${state}`} style={{ width: size, height: size }} aria-hidden="true" />;
}

/** lifecycle/Timeline.tsx's "full" variant: day 0 to the last day, milestones as ticks, the today marker, the next milestone */
export function Timeline({ milestones, day, duration, label }: { milestones: Milestone[]; day: number; duration: number; label: string }) {
  const pos = (d: number) => Math.max(0, Math.min(100, (d / duration) * 100));
  const sorted = [...milestones].sort((a, b) => a.due_day - b.due_day);
  const lanes: number[] = [];
  const last = [-99, -99, -99];
  for (const ms of sorted) {
    const x = pos(ms.due_day);
    const lane = [0, 1, 2].find((l) => x - last[l] >= 4) ?? 0;
    last[lane] = x;
    lanes.push(lane);
  }
  const done = milestones.filter((x) => x.state === "done").length;
  const next = sorted.find((x) => x.status === "pending");
  return (
    <div className="u-tl" role="group" aria-label={`${label}: day ${day} of ${duration}. ${done} of ${milestones.length} milestones done.`}>
      <div className="u-tl-track">
        <span className="u-tl-line" aria-hidden="true" />
        <span className="u-tl-fill" style={{ width: `${pos(day)}%` }} aria-hidden="true" />
        {sorted.map((ms, i) => (
          <span key={ms.key} className={`u-tl-tick lane-${lanes[i]}`} style={{ left: `${pos(ms.due_day)}%` } as CSSProperties} aria-hidden="true">
            <Tick state={ms.state} size={10} />
          </span>
        ))}
        <span className="u-tl-today" style={{ left: `${pos(day)}%` }} aria-hidden="true"><span>Today · day {day}</span></span>
      </div>
      <div className="u-tl-axis" aria-hidden="true">
        {[0, 30, 60, 90].map((d) => <span key={d} style={{ left: `${pos(d)}%` }}>{d === 0 ? "Day 0" : d}</span>)}
      </div>
      {next && (
        <p className="u-tl-next"><span className="u-k">Next</span><Tick state={next.state} size={9} /><b>{next.title}</b>
          <span className="u-soft">day {next.due_day} · {STATE_LABEL[next.state].toLowerCase()}</span></p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------------------ a report figure (app's tile) */
function delta(f: ReportFigure) {
  const a = f.current.value, b = f.previous.value;
  if (a === null || b === null || a === b) return null;
  const d = a - b;
  const unit = f.unit === "%" ? " pts" : f.unit === "days" ? " d" : f.unit === "hours" ? " h" : "";
  return { up: d > 0, text: `${Math.round(Math.abs(d))}${unit}` };
}
const parts = (display: string): [string, string] => { const x = /^(-?[\d.,]+)\s*(.*)$/.exec(display.trim()); return x ? [x[1], x[2]] : [display, ""]; };

/** lifecycle/ReportParts.tsx FigureTile: the value large, its unit small, the previous cohort beside it, n, and how it is
 *  worked out (here as the tile's description; the app puts it behind a disclosure) */
export function FigureTile({ f, good }: { f: ReportFigure; good: "up" | "down" }) {
  const dl = delta(f);
  const [num, unit] = parts(f.current.display);
  const thin = (s: string) => /not enough data/i.test(s);
  return (
    <div className="u-tile" title={f.how}>
      <p className="u-tile-label">{f.label}</p>
      <p className="u-tile-value s-tnum">{num}{unit && <span>{unit}</span>}</p>
      <p className="u-tile-foot">
        <span>Previous <b>{thin(f.previous.display) ? "not enough data" : f.previous.display}</b></span>
        {dl && (
          <span className={`u-delta ${dl.up === (good === "up") ? "good" : "bad"}`} aria-label={`${dl.up ? "up" : "down"} ${dl.text}`}>
            <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden="true"><path d={dl.up ? "M5 1.5 9 8H1z" : "M5 8.5 9 2H1z"} /></svg>{dl.text}
          </span>
        )}
        <span className="u-soft">n = {f.current.n}</span>
      </p>
    </div>
  );
}

export function Avatar({ name, tone = "" }: { name: string; tone?: "" | "ink" }) {
  return <span className={`u-av${tone ? ` is-${tone}` : ""}`} aria-hidden="true">{name.slice(0, 1)}</span>;
}

export function Check() {
  return <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M2.5 6.4 5 8.8l4.6-5.6" /></svg>;
}
