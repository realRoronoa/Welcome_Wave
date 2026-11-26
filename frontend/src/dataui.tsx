/** Small building blocks shared by the data pages (dashboard, tasks, approvals, reviews, KT calls). Styles: styles/data.css. */
import { ReactNode } from "react";
import { today } from "./api";
import "./styles/data.css";

// Stroke glyphs on a 24px grid (after Lucide, ISC licence). Local to the data pages so ui.tsx stays the shell's.
const GLYPHS = {
  code: ["M16 18l6-6-6-6", "M8 6l-6 6 6 6"],
  learn: ["M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z", "M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"],
  meet: ["M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2", "M9 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8z", "M23 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  task: ["M9 11l3 3L22 4", "M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"],
  calendar: ["M8 2v4", "M16 2v4", "M3 10h18", "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"],
  pr: ["M6 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M18 15a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M6 9v12", "M13 6h3a2 2 0 0 1 2 2v7"],
  branch: ["M6 3v12", "M18 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M6 15a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M18 9a9 9 0 0 1-9 9"],
  commit: ["M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8z", "M2 12h6", "M16 12h6"],
  check: ["M20 6L9 17l-5-5"],
  alert: ["M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z", "M12 9v4", "M12 17h.01"],
  clock: ["M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z", "M12 6v6l4 2"],
  play: ["M6 4l14 8-14 8z"],
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6"],
  arrow: ["M5 12h14", "M12 5l7 7-7 7"],
  external: ["M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6", "M15 3h6v6", "M10 14L21 3"],
  message: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
  users: ["M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2", "M9 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8z", "M23 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "M9 12l2 2 4-4"],
  layers: ["M12 2L2 7l10 5 10-5-10-5z", "M2 17l10 5 10-5", "M2 12l10 5 10-5"],
  video: ["M23 7l-7 5 7 5V7z", "M3 5h11a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z"],
  plus: ["M12 5v14", "M5 12h14"],
  sparkle: ["M12 3l1.9 5.8L20 10.7l-5.8 1.9L12 18.4l-1.9-5.8L4.3 10.7l5.8-1.9z"],
};
export type GlyphName = keyof typeof GLYPHS;

export function Glyph({ name, size = 16, className }: { name: GlyphName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {GLYPHS[name].map((d) => <path key={d} d={d} />)}
    </svg>
  );
}

export const KIND: Record<string, { glyph: GlyphName; label: string }> = {
  code: { glyph: "code", label: "Code" }, learn: { glyph: "learn", label: "Learn" }, meet: { glyph: "meet", label: "Meet" },
};
export const kindOf = (k: string) => KIND[k] ?? { glyph: "task" as GlyphName, label: k };

/** kt: explained in a KT session (nothing else). rec: the one red (overdue, critical, failed). ok: done, in ink. warn: needs a look, an ink
 *  outline. info: in motion, a hollow dot. */
export type Tone = "kt" | "rec" | "ok" | "warn" | "info" | "neutral";

/** A status pill. `live` pulses its dot for states that are still moving. */
export function Pill({ tone = "neutral", live, children, title }: { tone?: Tone; live?: boolean; children: ReactNode; title?: string }) {
  return <span className={`dp-pill t-${tone}${live ? " live" : ""}`} title={title}><i aria-hidden="true" />{children}</span>;
}

/** Status of a task or meeting, as a tone and whether it is in motion. */
export const STATUS_TONE: Record<string, { tone: Tone; live?: boolean }> = {
  proposed: { tone: "neutral" }, approved: { tone: "neutral" }, in_progress: { tone: "info", live: true }, submitted: { tone: "info" },
  changes_requested: { tone: "warn" }, done: { tone: "ok" }, rejected: { tone: "neutral" },
  scheduled: { tone: "neutral" }, live: { tone: "info", live: true }, processing: { tone: "info", live: true }, ready: { tone: "ok" }, failed: { tone: "rec" },
  cancelled: { tone: "neutral" }, ended: { tone: "neutral" },
};
export function StatusPill({ status, label }: { status: string; label: string }) {
  const s = STATUS_TONE[status] ?? { tone: "neutral" as Tone };
  return <Pill tone={s.tone} live={s.live}>{label}</Pill>;
}

function parseDay(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
/** Whole days from today to a YYYY-MM-DD date (negative when it has passed). */
export function daysUntil(iso: string) {
  return Math.round((parseDay(iso).getTime() - parseDay(today()).getTime()) / 86400000);
}
export const shortDay = (iso: string) => parseDay(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** A due-date chip that turns amber when the date is close and red once it has passed. */
export function DueChip({ date, done }: { date: string; done?: boolean }) {
  const n = daysUntil(date);
  let tone: Tone = "neutral", text = `Due ${shortDay(date)}`;
  if (!done) {
    if (n < 0) { tone = "rec"; text = `Overdue ${-n}d`; }
    else if (n === 0) { tone = "warn"; text = "Due today"; }
    else if (n === 1) { tone = "warn"; text = "Due tomorrow"; }
    else if (n <= 3) { tone = "warn"; text = `Due in ${n}d`; }
  }
  return (
    <span className={`dp-due t-${tone}`} title={`Due ${shortDay(date)}`}>
      <Glyph name={tone === "rec" ? "alert" : "calendar"} size={13} />{text}
    </span>
  );
}

export const initials = (name: string) =>
  name.replace(/\(.*?\)/g, "").trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase() || "?";

export function Avatar({ name, size = 34 }: { name: string; size?: number }) {
  return <span className="dp-avatar" style={{ width: size, height: size, fontSize: Math.max(9.5, size * 0.36) }} aria-hidden="true">{initials(name)}</span>;
}

/** A dark code block with line numbers. `start` is the first line's number, when known. */
export function CodeBlock({ code, start, label }: { code: string; start?: number | null; label?: ReactNode }) {
  const lines = code.replace(/\n+$/, "").split("\n");
  return (
    <figure className="dp-code">
      {label && <figcaption>{label}</figcaption>}
      <pre><code>{lines.map((l, i) => (
        <span key={i} className="dp-code-line"><span className="dp-ln" aria-hidden="true">{start ? start + i : i + 1}</span><span>{l || " "}</span></span>
      ))}</code></pre>
    </figure>
  );
}

/** Section heading with an optional count and trailing content. */
export function SectionHead({ title, count, children, id }: { title: ReactNode; count?: number; children?: ReactNode; id?: string }) {
  return (
    <div className="dp-section-head">
      <h2 id={id}>{title}{count !== undefined && <span className="dp-count">{count}</span>}</h2>
      {children}
    </div>
  );
}
