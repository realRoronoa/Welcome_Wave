/** How the app's page scrolls. Scrolling itself is the browser's own everywhere: nothing here takes a wheel or touch event,
 *  smooths, delays or snaps. This module does three small things around it, all from one passive scroll listener:
 *
 *  - Route changes: a new page starts at the top, and back/forward returns to where that page was left. The browser's own
 *    restoration (history.scrollRestoration "auto") runs before the page's data has loaded, so it lands short; the app
 *    restores instead, once the page is tall enough. Each history entry gets an id in its state; positions are kept per id
 *    (and in sessionStorage, so a reload returns to the same place).
 *  - html.ww-app while the app is on screen (styles/shell.css: the scrollbar's gutter).
 *  - The top bar's hairline: CSS drives it from the scroll position where it can (a scroll-driven animation); where it
 *    cannot, or motion is reduced, html.ww-scrolled is toggled, only when the page crosses its top. */
import { useLayoutEffect } from "react";

const KEY = "wwScroll"; // our id in each history entry's state (other fields are kept)
const STORE = "ww-scroll";
const INPUT = ["wheel", "touchstart", "keydown", "pointerdown"] as const;

let lastY = 0; // where the page was at the last scroll event: read when a route change has already replaced the page
let current: { key: string; route: string } | null = null;
let pending: (() => void) | null = null; // a restore waiting for the page to grow
const saved = new Map<string, number>();
let seq = 0;

function load() {
  try { for (const [k, y] of JSON.parse(sessionStorage.getItem(STORE) ?? "[]") as [string, number][]) saved.set(k, y); } catch { /* storage blocked or bad */ }
}
function persist() {
  if (current) saved.set(current.key, Math.round(lastY));
  while (saved.size > 60) saved.delete(saved.keys().next().value!);
  try { sessionStorage.setItem(STORE, JSON.stringify([...saved])); } catch { /* storage blocked or full */ }
}

/** This history entry's id, given one if it has none yet (a new entry: `fresh`). */
function entry(): { key: string; fresh: boolean } {
  const st: unknown = history.state;
  const obj = st && typeof st === "object" ? (st as Record<string, unknown>) : null;
  if (typeof obj?.[KEY] === "string") return { key: obj[KEY] as string, fresh: false };
  const key = `${Date.now().toString(36)}.${(seq++).toString(36)}`;
  try { history.replaceState({ ...(obj ?? {}), [KEY]: key }, ""); } catch { /* a state we cannot copy: no memory for this entry */ }
  return { key, fresh: true };
}

const jump = (top: number) => scrollTo({ top, behavior: "instant" });
const room = () => document.documentElement.scrollHeight - innerHeight;

/** Scroll to y now if the page is tall enough, else as far as it goes and the rest once its content has loaded. Given up
 *  when the reader scrolls, types or taps first, or after 3 s. */
function restore(y: number) {
  pending?.();
  jump(Math.min(y, Math.max(0, room())));
  if (y <= room() + 1) return;
  const ro = new ResizeObserver(() => { if (room() + 1 >= y) { jump(y); stop(); } });
  const t = window.setTimeout(() => stop(), 3000);
  const stop = () => {
    ro.disconnect(); clearTimeout(t);
    for (const e of INPUT) removeEventListener(e, stop, true);
    if (pending === stop) pending = null;
  };
  for (const e of INPUT) addEventListener(e, stop, { capture: true, passive: true });
  ro.observe(document.body);
  pending = stop;
}

/** Call on every render with the current route (page and project). Runs before paint, so a new page never shows a frame at
 *  the old page's position. Pages that bring something into view themselves (a deep link) do so later, and win. */
export function useRouteScroll(route: string) {
  useLayoutEffect(() => {
    const { key, fresh } = entry();
    const prev = current;
    if (prev && prev.key === key && prev.route === route) return; // the same page (an effect run twice)
    if (prev) saved.set(prev.key, Math.round(lastY));
    current = { key, route };
    if (prev && !fresh && prev.key !== key) restore(saved.get(key) ?? 0); // back or forward
    else if (!prev && !fresh) restore(saved.get(key) ?? 0);               // the app opened on an entry it has seen (a reload)
    else { pending?.(); jump(0); }                                         // a new page, or the same entry showing another page
  }, [route]);
}

/** Mount once, in the app's shell, before useRouteScroll (layout effects run in order): the scroll listener, manual
 *  restoration, html.ww-app, and the hairline's fallback. */
export function useAppScroll() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const was = history.scrollRestoration;
    history.scrollRestoration = "manual";
    root.classList.add("ww-app");
    load();
    lastY = scrollY;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)");
    const cssEdge = typeof CSS !== "undefined" && CSS.supports?.("animation-timeline: scroll()");
    let marked: boolean | null = null;
    const mark = () => {
      const on = !(cssEdge && !reduce.matches) && lastY > 0; // only when CSS is not drawing it
      if (on !== marked) { marked = on; root.classList.toggle("ww-scrolled", on); }
    };
    const onScroll = () => { lastY = scrollY; mark(); };
    mark();
    addEventListener("scroll", onScroll, { passive: true });
    reduce.addEventListener("change", mark);
    addEventListener("pagehide", persist);
    return () => {
      removeEventListener("scroll", onScroll);
      reduce.removeEventListener("change", mark);
      removeEventListener("pagehide", persist);
      persist();
      pending?.();
      current = null;
      history.scrollRestoration = was;
      root.classList.remove("ww-app", "ww-scrolled");
    };
  }, []);
}
