/** Building blocks shared by the lifecycle pages (Home, Program, Team, Reports, ...). Styles: styles/lifecycle.css.
 *  The pages reuse the data pages' parts (dataui.tsx: Pill, Glyph, Avatar; fx.tsx: Skeleton, EmptyState; accordion.tsx);
 *  this file adds only what they lack: a loader bound to live updates, the "How" disclosure, API links, and a few atoms. */
import { createContext, ReactNode, useCallback, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage } from "../api";
import { useLive } from "../live";
import { Glyph, GlyphName } from "../dataui";
import { Skeleton } from "../fx";
import type { Person } from "./types";
import { forgetShared, getShared, KEEP_MS } from "./shared";
export { forgetShared, getShared, resetShared } from "./shared";
import "../styles/lifecycle.css";

/* ---------------------------------------------------------------- loading */

export type Loaded<T> = { data: T | null; error: unknown; reload: () => void; set: (fn: (d: T | null) => T | null) => void };

/* Shared reads (getShared, forgetShared, resetShared) live in ./shared.ts, so signing in and out can clear them without
   loading this file. */
/** GET `path` (null: wait), again whenever one of `topics` changes (live.ts) and when `path` changes. The data on screen
 *  stays while a reload runs, so a live update never flashes a skeleton. */
export function useApi<T>(path: string | null, topics: string[] = ["lifecycle"]): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const seq = useRef(0);
  const fetchIt = useCallback((maxAge: number) => {
    if (!path) return;
    const n = ++seq.current;
    getShared<T>(path, maxAge).then((d) => { if (n === seq.current) { setData(d); setError(null); } })
      .catch((e) => { if (n === seq.current) setError(e); });
  }, [path]);
  const reload = useCallback(() => fetchIt(0), [fetchIt]);
  useEffect(() => { setData(null); setError(null); fetchIt(KEEP_MS); }, [fetchIt]);
  useLive(topics, reload);
  return { data, error, reload, set: setData as Loaded<T>["set"] };
}

export const isStatus = (e: unknown, ...codes: number[]) => e instanceof ApiError && codes.includes(e.status);

/** Run one write, with its own busy and error state. `run` resolves to the response (or undefined when it failed). */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(key); setError(null);
    try { const r = await fn(); forgetShared(); return r; }
    catch (e) { setError(errorMessage(e)); return undefined; }
    finally { setBusy(null); }
  }, []);
  return { busy, error, setError, run };
}

/* ---------------------------------------------------------------- breadcrumbs */

/** The shell's breadcrumb names the section; a page with an item (a hire's program) adds its name with useCrumb. */
export const CrumbContext = createContext<(label: string | null) => void>(() => {});
export function useCrumb(label: string | null | undefined) {
  const set = useContext(CrumbContext);
  useEffect(() => { set(label ?? null); return () => set(null); }, [label, set]);
}

/* ---------------------------------------------------------------- links and names */

/** API links are paths ("/program/ob-1?tab=access&project=p"); the workspace routes on the hash. The expert inbox lives at
 *  /inbox in the web app (the API names it /experts). */
export function hashOf(link: string): string {
  if (!link) return "#/home";
  if (link.startsWith("#")) return link;
  const path = link.startsWith("/") ? link : `/${link}`;
  return "#" + path.replace(/^\/experts(?=[/?]|$)/, "/inbox");
}

/** "Vijay (New hire)" → "Vijay": the demo's names carry their role, which the page already shows. */
export const nameOf = (p: Person | string | null | undefined, fallback = "Someone") => {
  const n = typeof p === "string" ? p : p?.name;
  return n ? n.replace(/\s*\(.*?\)\s*/g, " ").trim() || n : fallback;
};
export const firstName = (p: Person | string | null | undefined) => nameOf(p).split(/\s+/)[0];
/** API sentences name people as the demo does ("Approve Vijay (New hire)'s plan"): drop the role in brackets. */
export const clean = (text: string) => text.replace(/\s*\((New hire|Senior|Manager|Admin|Executive|Expert)\)/g, "");

const parseDay = (iso: string) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(iso);
};
/** "12 Nov" (and the year when it is not this one). */
export function shortDate(iso: string | null | undefined) {
  if (!iso) return "";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? parseDay(iso) : new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }) });
}
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Where a hire is in their program, in words: "Day 12" of 90, or "Starts Nov 16" (in 2 days) before they have started. The day count
 *  is never negative on screen. */
export function dayFigure(p: { day: number; duration_days: number; start_date: string }): { label: string; value: string; of: string; main: string } {
  if (p.day < 0) return { label: "Starts", value: shortDate(p.start_date), of: `in ${plural(-p.day, "day")}`, main: `Starts ${shortDate(p.start_date)}` };
  return { label: "Day", value: String(p.day), of: `of ${p.duration_days}`, main: `Day ${p.day}` };
}
export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/* ---------------------------------------------------------------- the "How" disclosure */

/** A small button that opens a short explanation beside it: how a figure is worked out, why an item is listed. Escape or a
 *  click elsewhere closes it. Long reasons never sit on the page itself. */
export function How({ text, label = "How", title, align }: { text: ReactNode; label?: string; title?: string; align?: "start" | "end" }) {
  const [open, setOpen] = useState(false);
  const [side, setSide] = useState<"start" | "end">(align ?? "start");
  const id = useId();
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    if (!open || align) return;
    const r = root.current?.getBoundingClientRect();
    if (r) setSide(r.left + 300 > innerWidth - 16 ? "end" : "start");
  }, [open, align]);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); button.current?.focus(); } };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc, true); };
  }, [open]);
  return (
    <span className="lc-how" ref={root}>
      <button ref={button} type="button" className="lc-how-btn" aria-expanded={open} aria-controls={id}
              title={title ?? (label === "How" ? "How this is worked out" : label)} onClick={() => setOpen((o) => !o)}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <circle cx="12" cy="12" r="9.5" /><path d="M12 11v5.5" /><path d="M12 7.6v.01" />
        </svg>
        <span>{label}</span>
      </button>
      <span id={id} role="note" className={`lc-how-pop at-${side}`} hidden={!open}>{text}</span>
    </span>
  );
}

/** Reasons from the API (a list of short sentences) behind a "Why" disclosure. */
export function Why({ why, label = "Why" }: { why: string[] | string | null | undefined; label?: string }) {
  const lines = (Array.isArray(why) ? why : why ? [why] : []).filter(Boolean);
  if (!lines.length) return null;
  return <How label={label} title="Why this is shown" text={lines.length === 1 ? lines[0] : <ul>{lines.map((l, i) => <li key={i}>{l}</li>)}</ul>} />;
}

/* ---------------------------------------------------------------- page frame and states */

export function LcHead({ title, sub, actions, back, eyebrow }:
  { title: ReactNode; sub?: ReactNode; actions?: ReactNode; back?: { href: string; label: string }; eyebrow?: ReactNode }) {
  return (
    <header className="lc-head">
      <div className="lc-head-text">
        {back && <a className="lc-back" href={back.href}><Glyph name="arrow" size={13} />{back.label}</a>}
        {eyebrow && <p className="lc-eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {sub && <div className="lc-sub">{sub}</div>}
      </div>
      {actions && <div className="lc-head-actions">{actions}</div>}
    </header>
  );
}

/** A failed load: what happened, and a retry. */
export function LoadError({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="lc-error" role="alert">
      <Glyph name="alert" size={16} />
      <p>{errorMessage(error)}</p>
      {retry && <button type="button" className="dp-btn small" onClick={retry}>Try again</button>}
    </div>
  );
}

/** Placeholder rows while a page loads: the page's shape appears at once. */
export function LcSkeleton({ rows = 3, head = true }: { rows?: number; head?: boolean }) {
  return (
    <div className="lc-skel" aria-busy="true">
      <span className="sr-only" role="status">Loading…</span>
      {head && <Skeleton lines={2} height={16} style={{ maxWidth: 360, marginBottom: 32 }} />}
      {Array.from({ length: rows }, (_, i) => <div key={i} className="lc-card"><Skeleton lines={2} height={13} /></div>)}
    </div>
  );
}

/** A quiet section heading: a title, an optional count, and a link or action on the right. */
export function Section({ title, count, action, children, id }:
  { title: ReactNode; count?: number; action?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <section className="lc-section" aria-labelledby={id}>
      <div className="lc-section-head">
        <h2 id={id}>{title}{count !== undefined && <span className="dp-count">{count}</span>}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

/** An arrow link in a section header or at the end of a page. */
export function More({ href, children }: { href: string; children: ReactNode }) {
  return <a className="lc-more" href={href}>{children}<Glyph name="arrow" size={13} /></a>;
}

/** One quiet line for an empty list inside a section (EmptyState is for whole pages). */
export function Quiet({ children, icon = "check" }: { children: ReactNode; icon?: GlyphName }) {
  return <div className="lc-quiet"><Glyph name={icon} size={14} />{children}</div>;
}

/** The page's closing action: the one thing to do next. */
export function NextAction({ children }: { children: ReactNode }) {
  return <div className="lc-next">{children}</div>;
}

/** On track / needs attention / completed, as one pill. */
export function HireState({ onTrack, status }: { onTrack: boolean; status?: string }) {
  if (status === "completed") return <span className="lc-state done"><i aria-hidden="true" />Completed</span>;
  return onTrack ? <span className="lc-state ok"><i aria-hidden="true" />On track</span>
    : <span className="lc-state attn"><i aria-hidden="true" />Needs attention</span>;
}

/** A segmented progress bar, ink for done. */
export function Progress({ done, total, label }: { done: number; total: number; label: string }) {
  return (
    <span className="lc-progress" role="img" aria-label={label}>
      {Array.from({ length: Math.max(total, 1) }, (_, i) => <i key={i} className={i < done ? "on" : ""} />)}
    </span>
  );
}

/** The key figure: a label, a large value, and how it is worked out behind a disclosure. */
export function FigureCard({ label, value, of, how, foot }:
  { label: string; value: ReactNode; of?: ReactNode; how?: string; foot?: ReactNode }) {
  const thin = typeof value === "string" && /not enough data/i.test(value);
  return (
    <div className="lc-figure">
      <div className="lc-figure-top"><p className="lc-figure-label">{label}</p>{how && <How text={how} align="end" />}</div>
      <div className={`lc-figure-value${thin ? " thin" : ""}`}>{thin ? "Not enough data" : value}{of && <span className="lc-figure-of">{of}</span>}</div>
      {foot && <div className="lc-figure-foot">{foot}</div>}
    </div>
  );
}

/** Copy text, with a toast either way. */
export async function copy(text: string, done: (ok: boolean) => void) {
  try { await navigator.clipboard.writeText(text); done(true); }
  catch { done(false); }
}
