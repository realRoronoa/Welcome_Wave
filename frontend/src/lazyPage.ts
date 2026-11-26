/** Pages loaded on demand, so the sign-in screen and the first page never wait for every other page's code. Once a page's
 *  chunk has arrived it renders synchronously (no Suspense fallback, not even for a frame), and preloadPages() fetches all
 *  of them while the browser is idle after the first page is up, so moving around the app never waits on the network. */
import { ComponentType, createElement, lazy } from "react";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyComponent = ComponentType<any>;
export type LazyPage<C extends AnyComponent> = C & { preload: () => Promise<unknown> };

const all: (() => Promise<unknown>)[] = [];

export function lazyPage<C extends AnyComponent>(load: () => Promise<{ default: C }>): LazyPage<C> {
  let loaded: C | null = null;
  let pending: Promise<{ default: C }> | null = null;
  // a failed fetch is forgotten, and so is the lazy() that saw it (React keeps a rejected lazy rejected), so "Try again" refetches
  const fetchIt = () => (pending ??= load().then((m) => { loaded = m.default; return m; },
    (e) => { pending = null; Lazy = lazy(fetchIt); throw e; }));
  let Lazy = lazy(fetchIt);
  const Page = ((props: object) => createElement((loaded ?? Lazy) as AnyComponent, props)) as unknown as LazyPage<C>;
  Page.preload = () => fetchIt().catch(() => { /* fetched again when the page is opened */ });
  all.push(Page.preload);
  return Page;
}

let preloading = false;
let lastInput = 0;
/** Fetch every page's chunk, one at a time, only while the browser is idle and the person is not scrolling or typing: a
 *  chunk's code runs and its stylesheet is added when it arrives, and that work must never land in the middle of a scroll. */
export function preloadPages() {
  if (preloading) return;
  preloading = true;
  const mark = () => { lastInput = performance.now(); };
  for (const e of ["scroll", "wheel", "touchmove", "keydown", "pointerdown"]) addEventListener(e, mark, { passive: true, capture: true });
  const queue = [...all];
  const idle = (fn: (d?: IdleDeadline) => void) => ("requestIdleCallback" in window ? requestIdleCallback(fn, { timeout: 8000 }) : setTimeout(fn, 1500));
  const next = (d?: IdleDeadline) => {
    if (!queue.length) {
      for (const e of ["scroll", "wheel", "touchmove", "keydown", "pointerdown"]) removeEventListener(e, mark, { capture: true });
      return;
    }
    const quiet = performance.now() - lastInput > 1000 && (!d || d.didTimeout || d.timeRemaining() > 20);
    if (!quiet) { window.setTimeout(() => idle(next), 700); return; }
    void queue.shift()!().then(() => idle(next));
  };
  idle(next);
}
