/** One single-page app, three surfaces: the public site (the landing page, About, and a 404 for any other path), the app
 *  (sign-in at /login and /signup, the workspace at /app#/..., GitHub's /github/callback) and a new hire's pre-boarding page
 *  (/welcome/<link token>: no sign-in, the link is the permission). main.tsx renders one of them and loads each in its own
 *  chunk. public/boot-theme.js repeats surfaceFor() before any script loads, and frontend/nginx.conf.template serves the
 *  shell for these paths: keep all three in sync. */
export type Surface = "site" | "app" | "portal";

/** The pre-boarding page's address: /welcome/<token>. */
export const PORTAL_PATH = /^\/welcome\/([^/]+)\/?$/;

export function surfaceFor(path: string, hash: string): Surface {
  if (PORTAL_PATH.test(path)) return "portal";
  if (/^\/(login|signup|github\/callback)\/?$/.test(path) || /^\/app(\/|$)/.test(path)) return "app";
  if (path === "/" && /^#\//.test(hash)) return "app"; // an old link into the app, "/#/chat": main.tsx moves it to /app#/chat
  return "site";
}

export const NAVIGATE = "ww:navigate";

/** Go to another address in this tab without a reload. main.tsx and the site re-read the address on this event (and on popstate).
 *  history.state.y is the scroll position to come back to: the site writes it before leaving, so Back restores it. */
export function navigate(to: string, replace = false) {
  const state = { y: 0 };
  if (replace) history.replaceState(state, "", to);
  else history.pushState(state, "", to);
  arrival = Date.now();
  dispatchEvent(new Event(NAVIGATE));
}

/** A page changed in place by a link (navigate()), not by a reload or Back: the page that renders next moves keyboard and
 *  screen-reader focus to its heading, as a full page load would start at the top. True once, shortly after navigate(). */
let arrival = 0;
export function takeArrival(): boolean {
  const fresh = Date.now() - arrival < 6000; // the app may first wait for its config
  arrival = 0;
  return fresh;
}

/** Focus `el` for keyboard and screen readers without scrolling: itself if it already takes focus (main#content), otherwise
 *  its first heading. A heading gets tabindex="-1", so it takes focus but stays out of the Tab order. */
export function focusIn(el: HTMLElement) {
  const f = el.hasAttribute("tabindex") ? el : el.querySelector<HTMLElement>("h1, h2, h3") ?? el;
  if (!f.hasAttribute("tabindex")) f.tabIndex = -1;
  f.focus({ preventScroll: true });
}

/** The app sets this after a successful sign-in (in demo mode too) and clears it on sign-out; the landing page then offers
 *  "Open Welcome Wave" instead of "Log in". A hint only: the app still checks the session itself. */
const SIGNED_IN = "ww-signed-in";

export function setSignedIn(on: boolean) {
  try {
    if (on) localStorage.setItem(SIGNED_IN, "1");
    else localStorage.removeItem(SIGNED_IN);
  } catch { /* storage blocked: the landing just offers Log in */ }
}

export function seemsSignedIn(): boolean {
  try { return localStorage.getItem(SIGNED_IN) === "1"; } catch { return false; }
}

declare global {
  interface Window {
    /** Date.now() of a click from the site into the app ("Log in", "Get started"): the sign-in page answers it once (a terrain
     *  pulse), and the app's first loading frame stays black so the hand-over never flashes a light card. */
    __wwHandover?: number;
  }
}

/** A hand-over from the site happened in the last `ms` milliseconds. */
export function handedOver(ms: number) {
  const t = window.__wwHandover;
  return typeof t === "number" && Date.now() - t < ms;
}

/** Resolves once `selector` matches (or after `ms`): how long a view transition holds the old frame while the next one renders.
 *  A MutationObserver, not requestAnimationFrame: frames are not produced while a view transition waits for its update. */
export function waitFor(selector: string, ms: number): Promise<void> {
  return new Promise((done) => {
    if (document.querySelector(selector)) return done();
    const mo = new MutationObserver(() => { if (document.querySelector(selector)) finish(); });
    const timer = setTimeout(finish, ms);
    function finish() { mo.disconnect(); clearTimeout(timer); done(); }
    mo.observe(document.body, { childList: true, subtree: true });
  });
}

export const reducedMotion = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** navigate() inside a view transition (a cross-fade, plus whatever names the page sets), held until `until` renders.
 *  Without View Transitions, or under reduced motion, it simply navigates. */
export function navigateWithTransition(to: string, until: string) {
  if (!document.startViewTransition || reducedMotion()) return navigate(to);
  document.startViewTransition(async () => { navigate(to); await waitFor(until, 1500); });
}
