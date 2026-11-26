/* Links out of the site. Into the app (/login, /signup, /app): the app's chunk is prefetched as soon as a pointer, focus or touch
   lands on the link; the click keeps this tab (no reload, no second intro), remembers the scroll position for Back, and, where
   View Transitions exist and motion is welcome, morphs the finale's black panel into the sign-in page's black brand panel
   (both are named "ww-ink"; the rest cross-fades). Within the site (/, /about): a plain cross-fade. In-page links (#how, #top,
   the skip link) scroll here too, so Back and Forward return to the right place and focus follows the jump. */
import type { MouseEvent } from "react";
import { focusIn, navigate, navigateWithTransition, reducedMotion, waitFor } from "../surface";

let prefetched: Promise<unknown> | null = null;
let failed = false;

export function prefetchApp() {
  prefetched ??= import("../AppEntry").then(() => { failed = false; }, () => { prefetched = null; failed = true; });
  warmSignIn();
}

/** The sign-in page also needs the API's config and, outside demo mode, the sign-in SDK: start both with the app's chunk. */
let warmed = false;
function warmSignIn() {
  if (warmed) return;
  warmed = true;
  fetch("/api/config")
    .then((r) => (r.ok ? r.json() : null))
    .then((c: { mode?: string } | null) => { if (c && c.mode !== "demo") import("../auth/firebase").catch(() => {}); })
    .catch(() => {});
}

/** Spread on every link into the app. */
export const prefetch = { onPointerEnter: prefetchApp, onFocus: prefetchApp, onTouchStart: prefetchApp };

const plain = (e: MouseEvent | globalThis.MouseEvent) =>
  !(e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey);

/** Back (or a reload) comes back to where the visitor was. */
export function saveScroll() {
  history.replaceState({ ...(history.state ?? {}), y: Math.round(scrollY) }, "");
}

/** Into the app. `panel`: the black element that becomes the sign-in page's brand panel (the finale's), if any. */
export function toApp(e: MouseEvent<HTMLAnchorElement>, to: string, panel?: HTMLElement | null) {
  if (!plain(e)) return;
  e.preventDefault();
  saveScroll();
  // the app's chunk could not be fetched (a deploy replaced it, or the network): a plain page load gets the current build
  if (failed) { location.href = to; return; }
  // only the sign-in page answers the hand-over (its black panel, a terrain pulse); the workspace may be light
  const signIn = /^\/(login|signup)$/.test(to);
  if (signIn) window.__wwHandover = Date.now();
  prefetchApp();
  if (!document.startViewTransition || reducedMotion()) return navigate(to);
  const morph = signIn && panel ? panel : null;
  if (morph) morph.style.viewTransitionName = "ww-ink";
  // the old frame holds until the next page has drawn, but not long: on a slow load a quick fade beats a frozen page
  const t = document.startViewTransition(async () => {
    navigate(to);
    await waitFor(".en-brand, .shell, .splash [role=alert]", 900); // the sign-in panel, the workspace, or an error
  });
  t.finished.finally(() => { if (morph) morph.style.viewTransitionName = ""; });
}

/** Within the site. Same page: an anchor jumps (jumpTo), "Home" goes to the top. */
export function toSite(e: MouseEvent<HTMLAnchorElement>, to: string) {
  if (!plain(e)) return;
  const url = new URL(to, location.href);
  if (url.pathname === location.pathname) {
    e.preventDefault();
    if (url.hash) { jumpTo(url.hash); return; }
    if (location.hash) { saveScroll(); history.pushState(null, "", url.pathname); } // leave "#plan" behind (Back returns to it)
    scrollTo({ top: 0, behavior: reducedMotion() ? "instant" : "smooth" });
    const h1 = document.querySelector<HTMLElement>(".site h1");
    if (h1) focusIn(h1);
    return;
  }
  e.preventDefault();
  saveScroll();
  navigateWithTransition(url.pathname + url.hash, `.site[data-path="${url.pathname}"]`);
}

/** An in-page jump to `hash` ("#how"): keeps the position being left (Back returns to it), adds the entry, scrolls (smoothly
 *  unless the visitor prefers reduced motion; scroll-margin keeps the heading clear of the nav) and moves focus to the
 *  target's heading, so the next Tab continues from there. `push: false` when the entry already exists. */
/** A page may take over an in-page jump (the landing film cuts a long way under a dip through black): it returns true
 *  when it has moved the page, false to let the browser scroll there. */
let scroller: ((target: HTMLElement) => boolean) | null = null;
export function setScroller(fn: ((target: HTMLElement) => boolean) | null) { scroller = fn; }

export function jumpTo(hash: string, push = true): boolean {
  const target = hash.length > 1 ? document.getElementById(decodeURIComponent(hash.slice(1))) : null;
  if (!target) return false;
  if (push) {
    saveScroll();
    if (location.hash === hash) history.replaceState(null, "", hash); // the same address again adds no entry (as the browser does)
    else history.pushState(null, "", hash);
  }
  if (!scroller?.(target)) target.scrollIntoView({ behavior: reducedMotion() ? "instant" : "smooth", block: "start" });
  focusIn(target);
  return true;
}

/** Every same-page link on the site, whatever renders it (contents, chips, "Back to top", the skip link): one listener on the
 *  document, after React's, so a link with its own handler (the menu's) has already had its say. Returns the cleanup. */
export function interceptAnchors(root: HTMLElement): () => void {
  const on = (e: globalThis.MouseEvent) => {
    if (!plain(e)) return;
    const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
    if (!a || !root.contains(a) || a.target) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || url.pathname !== location.pathname || !url.hash) return;
    if (jumpTo(url.hash)) e.preventDefault();
  };
  document.addEventListener("click", on);
  return () => document.removeEventListener("click", on);
}
