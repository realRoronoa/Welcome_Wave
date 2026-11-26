/** Small motion and state primitives shared by every page. All of them respect prefers-reduced-motion. */
import { CSSProperties, ReactNode, useEffect, useRef, useState } from "react";
import { Logo } from "./ui";
import "./styles/fx.css";

export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Shimmering placeholder blocks while data loads: the layout appears at once instead of a spinner. */
export function Skeleton({ lines = 3, height = 14, style }: { lines?: number; height?: number; style?: CSSProperties }) {
  return (
    <div className="skeleton" aria-hidden="true" style={style}>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} style={{ height, width: i === lines - 1 && lines > 1 ? "62%" : undefined }} />
      ))}
    </div>
  );
}

/** A number that counts up from zero the first time it is shown. */
export function CountUp({ value, duration = 900, format = (n: number) => String(Math.round(n)) }:
  { value: number; duration?: number; format?: (n: number) => string }) {
  const [shown, setShown] = useState(() => (reducedMotion() ? value : 0));
  useEffect(() => {
    if (reducedMotion()) return setShown(value);
    let raf = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      setShown(value * (1 - Math.pow(1 - t, 3)));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  return <span className="count-up">{format(shown)}</span>;
}

/** A progress ring, 0..1, the label in the middle. Ink by default; `tone="kt"` is teal and only for a share that a KT session explained. */
export function Ring({ value, size = 44, stroke = 4, label, tone = "ink" }: { value: number; size?: number; stroke?: number; label?: ReactNode; tone?: "ink" | "kt" }) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, v = Math.max(0, Math.min(1, value || 0));
  return (
    <span className={`ring${tone === "kt" ? " ring-kt" : ""}`} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} className="ring-track" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} className="ring-value" strokeWidth={stroke}
          strokeDasharray={c} style={{ ["--ring-off" as string]: c * (1 - v), ["--ring-c" as string]: c } as CSSProperties} />
      </svg>
      {label !== undefined && <span className="ring-label">{label}</span>}
    </span>
  );
}

/** An empty state: the mark, quiet, laid down once; a title, one line of guidance, and an optional action. */
export function EmptyState({ title, children, action }: { title: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <Logo size={30} motion="arrive" className="empty-mark" />
      <p className="empty-title">{title}</p>
      {children && <p className="empty-text">{children}</p>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

/** A keyboard key, e.g. <Kbd>⌘</Kbd><Kbd>K</Kbd>. */
export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const modKey = isMac ? "⌘" : "Ctrl";

/** Fades and lifts its children in when they scroll into view (once). */
export function Reveal({ children, delay = 0, as: Tag = "div", className = "", id }:
  { children: ReactNode; delay?: number; as?: "div" | "section" | "li"; className?: string; id?: string }) {
  const ref = useRef<HTMLElement>(null);
  const [seen, setSeen] = useState(() => reducedMotion());
  useEffect(() => {
    if (seen || !ref.current) return;
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); io.disconnect(); } }, { rootMargin: "0px 0px -8% 0px" });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [seen]);
  return <Tag ref={ref as never} id={id} className={`reveal ${seen ? "in" : ""} ${className}`} style={{ transitionDelay: `${delay}ms` }}>{children}</Tag>;
}

/* Cursor spotlight: any element with class "spot" gets --mx/--my (cursor position inside it) for a soft radial highlight. */
if (typeof window !== "undefined" && !(window as { __wwSpot?: boolean }).__wwSpot) {
  (window as { __wwSpot?: boolean }).__wwSpot = true;
  addEventListener("pointermove", (e) => {
    const el = (e.target as Element | null)?.closest?.(".spot") as HTMLElement | null;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  }, { passive: true });
}

/** Keep a page's data current without a reload: run `load` when the window regains focus, when the tab becomes visible,
 *  and every `every` ms while it is visible. Agents and other people change tasks, approvals and questions meanwhile. */
export function useRefresh(load: () => void, every = 30_000) {
  const latest = useRef(load);
  latest.current = load;
  useEffect(() => {
    const run = () => { if (!document.hidden) latest.current(); };
    const timer = window.setInterval(run, every);
    addEventListener("focus", run);
    document.addEventListener("visibilitychange", run);
    return () => { clearInterval(timer); removeEventListener("focus", run); document.removeEventListener("visibilitychange", run); };
  }, [every]);
}
