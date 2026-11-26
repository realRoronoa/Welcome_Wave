import { Component, ReactNode, useEffect, useState } from "react";
import { bootVisible, hideBoot } from "./boot";
import { handedOver } from "./surface";

/* The Welcome Wave mark (docs/brand/BRAND.md): four identical strokes on a W. Each stroke is laid over the one before it,
   and the hairline seam it cuts shows where one person handed on to the next. Generated from one geometry
   (scratch build: mark.mjs), as are public/brand/*.svg and public/favicon.svg; change them together.
   MARK is the 64-unit drawing (centre-line vertices 8,11 20,53 32,11 44,53 56,11; stroke 7.6; seam 1.3).
   MARK_SMALL is the 16-unit drawing for 16-23 px: no seams (they would only blur), a heavier stroke on the pixel grid. */
const MARK = {
  view: 64,
  d: [
    "M4.346 12.044L15.372 50.634L19.324 36.802L11.654 9.956A3.8 3.8 0 0 0 4.346 12.044Z",
    "M23.654 54.044L31.324 27.198L27.372 13.366L16.346 51.956A3.8 3.8 0 0 0 23.654 54.044Z",
    "M28.346 12.044L39.372 50.634L43.324 36.802L35.654 9.956A3.8 3.8 0 0 0 28.346 12.044Z",
    "M47.654 54.044L59.654 12.044A3.8 3.8 0 0 0 52.346 9.956L40.346 51.956A3.8 3.8 0 0 0 47.654 54.044Z",
  ],
};
const MARK_SMALL = {
  view: 16,
  d: [
    "M1.196 3.421L4.046 13.221A1.15 1.15 0 0 0 6.254 12.579L3.404 2.779A1.15 1.15 0 0 0 1.196 3.421Z",
    "M6.254 13.221L9.104 3.421A1.15 1.15 0 0 0 6.896 2.779L4.046 12.579A1.15 1.15 0 0 0 6.254 13.221Z",
    "M6.896 3.421L9.746 13.221A1.15 1.15 0 0 0 11.954 12.579L9.104 2.779A1.15 1.15 0 0 0 6.896 3.421Z",
    "M11.954 13.221L14.804 3.421A1.15 1.15 0 0 0 12.596 2.779L9.746 12.579A1.15 1.15 0 0 0 11.954 13.221Z",
  ],
};
/** The mark's centre-line on its 64-unit grid: the W the four strokes run along (for line drawings of the mark). */
export const MARK_LINE = "M8 11L20 53L32 11L44 53L56 11";

/** The Welcome Wave mark in the current text colour, so it follows the surface it sits on (white in the landing page's dark
 *  nav, ink on paper) whatever data-theme says. The colour is a presentation attribute, not a stylesheet rule: the public
 *  site does not load the app's styles.css. tone="color" paints the first stroke (the explanation the other three reuse)
 *  teal: var(--logo-first) if the surface sets it, else the theme's --kt (#2dd4bf on dark grounds, #0c7c72 on light).
 *  `motion` (the app's styles.css; nothing under reduced motion): "arrive" lays the four strokes down in order once, as the
 *  pen draws them; "busy" runs a wave through them, left to right, for as long as it is shown (a loading indicator). */
export function Logo({ size = 28, tone = "mono", motion, className = "" }:
  { size?: number; tone?: "mono" | "color"; motion?: "arrive" | "busy"; className?: string }) {
  const m = size < 24 ? MARK_SMALL : MARK;
  const cls = ["logo", tone === "color" && "logo--color", motion && `logo--${motion}`, className].filter(Boolean).join(" ");
  return (
    <svg className={cls} width={size} height={size} viewBox={`0 0 ${m.view} ${m.view}`} aria-hidden="true" focusable="false">
      {m.d.map((d, i) => (
        <path key={d} d={d} className="logo-s" fill="currentColor"
          style={tone === "color" && i === 0 ? { fill: "var(--logo-first, var(--kt, #2dd4bf))" } : undefined} />
      ))}
    </svg>
  );
}

/** The mark's centre-line as a line drawing (MARK_LINE on its 64-unit grid): the watermark on a recorded call's tile. The
 *  stroke is set in CSS (.mark-line), in px, so it keeps its weight at any size. */
export function MarkLine({ className = "" }: { className?: string }) {
  return (
    <svg className={`mark-line ${className}`} viewBox="0 0 64 64" aria-hidden="true" focusable="false"><path d={MARK_LINE} /></svg>
  );
}

/** A centred card for the screens shown before the app itself: sign-in, sign-up, invitations, errors. */
export function Splash({ children }: { children: ReactNode }) {
  useEffect(hideBoot, []);
  return (
    <main className="splash">
      <div className="card">
        <p className="brand-mark"><Logo size={30} /> Welcome Wave</p>
        {children}
      </div>
    </main>
  );
}

/** While the intro still covers the page, render nothing under it; afterwards, a quiet loading card. Just after "Log in" on the
 *  landing page (which ends in black), the first moments stay black instead, so the sign-in page's black panel follows on. */
export function Booting() {
  if (bootVisible()) return null;
  if (handedOver(3000)) return <div className="handover-hold" role="status" aria-label="Loading Welcome Wave" />;
  return <Splash><Loading /></Splash>;
}

export type Msg = { ok: boolean; text: ReactNode } | null;

/** A success notice or an error, announced to screen readers. */
export function Note({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return msg.ok ? <p className="notice" role="status">{msg.text}</p> : <p className="error" role="alert">{msg.text}</p>;
}

/** The page header every page shares: a title, one line of description, and actions on the right (they wrap below on
 *  narrow screens). Markup is the plain <header class="page-head"> the pages already use, plus a .page-actions slot. */
export function PageHead({ title, children, actions, back }:
  { title: ReactNode; children?: ReactNode; actions?: ReactNode; back?: { href: string; label: string } }) {
  return (
    <header className="page-head">
      {back && <a href={back.href} className="back">{back.label}</a>}
      <h1>{title}</h1>
      {children && <p>{children}</p>}
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

type ToastItem = { id: number; text: ReactNode; tone: "ok" | "error" };
let toastSeq = 0;

/** A short-lived confirmation ("Link copied") in the corner. Errors that need reading stay in <Note/>. */
export function toast(text: ReactNode, tone: "ok" | "error" = "ok") {
  dispatchEvent(new CustomEvent<ToastItem>("ww-toast", { detail: { id: ++toastSeq, text, tone } }));
}

/** Mounted once by the workspace; shows what toast() sends, announced politely to screen readers. */
export function Toaster() {
  const [items, setItems] = useState<(ToastItem & { leaving?: boolean })[]>([]);
  useEffect(() => {
    const timers = new Set<number>();
    const later = (fn: () => void, ms: number) => { const t = window.setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };
    const on = (e: Event) => {
      const t = (e as CustomEvent<ToastItem>).detail;
      setItems((xs) => [...xs.slice(-2), t]);
      later(() => setItems((xs) => xs.map((x) => (x.id === t.id ? { ...x, leaving: true } : x))), 2800);
      later(() => setItems((xs) => xs.filter((x) => x.id !== t.id)), 3100);
    };
    addEventListener("ww-toast", on);
    return () => { removeEventListener("ww-toast", on); timers.forEach(clearTimeout); };
  }, []);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}${t.leaving ? " leaving" : ""}`}>
          <span className="toast-icon" aria-hidden="true"><Icon name={t.tone === "ok" ? "check" : "alert"} size={14} /></span>
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function Loading({ text = "Loading…" }: { text?: string }) {
  return <p className="loading" role="status"><span className="spinner" aria-hidden="true" />{text}</p>;
}

// Stroke icons on a 24px grid (paths after Lucide, ISC licence).
const ICONS = {
  bell: ["M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9", "M13.73 21a2 2 0 0 1-3.46 0"],
  up: ["M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3z", "M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"],
  down: ["M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3z", "M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"],
  chat: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
  code: ["M16 18l6-6-6-6", "M8 6l-6 6 6 6"],
  video: ["M23 7l-7 5 7 5V7z", "M3 5h11a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z"],
  tasks: ["M9 11l3 3L22 4", "M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"],
  inbox: ["M22 12h-6l-2 3h-4l-2-3H2", "M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"],
  approve: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "M9 12l2 2 4-4"],
  pulse: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  settings: ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1 14h6", "M9 8h6", "M17 16h6"],
  plus: ["M12 5v14", "M5 12h14"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  search: ["M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z", "M21 21l-4.35-4.35"],
  sun: ["M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M12 2v2", "M12 20v2", "M4.93 4.93l1.41 1.41", "M17.66 17.66l1.41 1.41", "M2 12h2", "M20 12h2", "M6.34 17.66l-1.41 1.41", "M19.07 4.93l-1.41 1.41"],
  moon: ["M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"],
  monitor: ["M4 3h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M8 21h8", "M12 17v4"],
  menu: ["M4 6h16", "M4 12h16", "M4 18h16"],
  close: ["M18 6L6 18", "M6 6l12 12"],
  link: ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
  folder: ["M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"],
  check: ["M20 6L9 17l-5-5"],
  alert: ["M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z", "M12 9v4", "M12 17h.01"],
  updown: ["M7 15l5 5 5-5", "M7 9l5-5 5 5"],
  route: ["M6 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", "M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15", "M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  radar: ["M19.07 4.93A10 10 0 1 1 12 2", "M16.24 7.76A6 6 0 1 0 18 12", "M12 12l7-7", "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z"],
  calendar: ["M8 2v4", "M16 2v4", "M3 10h18", "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M9 16l2 2 4-4"],
};
export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name].map((d) => <path key={d} d={d} />)}
    </svg>
  );
}

/** Keeps one broken page from blanking the whole app. Keyed by route, so navigating away resets it. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section role="alert">
        <h1>This page ran into a problem</h1>
        <p className="muted">Reload the page or try again. If it keeps happening, tell your administrator what you were doing.</p>
        <button onClick={() => this.setState({ error: null })}>Try again</button>
      </section>
    );
  }
}
