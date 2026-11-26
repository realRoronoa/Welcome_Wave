/** The full-screen intro lives in index.html (it paints before any JavaScript; public/boot-field.js adds its light, its
 *  field and its exit). The app calls hideBoot() once it has something real to show; the intro then plays out to MIN_MS of
 *  its own timeline, unless the viewer skips it (public/boot-words.js turns Esc, Enter, Space, Tab, a click, a tap or a
 *  scroll into "ww-boot-skip").
 *  The clock is the intro's CSS clock (its animations start at first paint), not navigation start: a slow document, font
 *  or stylesheet delays the first paint by seconds on a cold start, and the intro must still finish before it leaves.
 *  Adding .ww-boot--done starts the exit: boot-field.js opens the page through a W-shaped window that grows from the mark
 *  (or, without it, the lockup falls away and the field dissolves); FADE_MS must equal the end of that exit, when the node
 *  is removed. */
const MIN_MS = 4150; // the W draws by 1.51 s, settles by 2.52 s, the name has risen by 3.13 s and the words by 3.75 s
/** Reduced motion shows a static card: hold it long enough to read rather than flash it. */
const REDUCED_MS = 850; // + the .3 s fade: gone within ~1.2 s
const FADE_MS = 850; // boot-field.js REVEAL_MS (the W-shaped window), and the fallback dissolve
let hidden = false;

declare global {
  interface Window {
    __wwBootSkip?: boolean;
    /** performance.now() of the intro's first frame (set by boot-words.js); null until it has painted */
    __wwBootT0?: number | null;
    /** the app bundle has loaded (boot-words.js picks its slow-load message from this) */
    __wwBootApp?: boolean;
    /** hideBoot() has run: the intro will leave on its own, so boot-words.js's load failsafe stands down */
    __wwBootReady?: boolean;
  }
}

window.__wwBootApp = true;

export function bootVisible() {
  return !hidden && !!document.getElementById("ww-boot");
}

/** Milliseconds the intro has been on screen, by the CSS clock. */
function played(el: HTMLElement): number {
  const a = el.querySelector(".ww-art")?.getAnimations?.()[0]; // index.html's clock animation, started at first paint
  const now = document.timeline?.currentTime;
  if (a && typeof a.startTime === "number" && typeof now === "number") return now - a.startTime;
  const t0 = window.__wwBootT0;
  if (typeof t0 === "number") return performance.now() - t0;
  return t0 === null ? 0 : performance.now(); // null: not painted yet; undefined: boot-words.js never ran
}

/* While the intro is up the browser chrome (mobile status / address bar) keeps the intro's field colour; src/theme.ts
   writes the app's colour when it loads, so remember that and put it back as the intro dissolves. */
let appThemeColor: string | null = null;
let themeGuard: MutationObserver | null = null;

function holdThemeColor() {
  const el = document.getElementById("ww-boot");
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!el || !meta || typeof MutationObserver === "undefined") return;
  const field = () => getComputedStyle(el).getPropertyValue("--b-bg").trim();
  const enforce = () => {
    const want = field();
    if (want && meta.getAttribute("content") !== want) meta.setAttribute("content", want);
    themeGuard?.takeRecords(); // our own write is not the app's colour
  };
  const current = meta.getAttribute("content");
  if (current && current !== field()) appThemeColor = current;
  themeGuard = new MutationObserver((records) => {
    if (records.some((r) => r.target === meta)) appThemeColor = meta.getAttribute("content");
    enforce();
  });
  themeGuard.observe(meta, { attributes: true, attributeFilter: ["content"] });
  // the intro's field follows data-theme too (a theme change from another tab while it plays)
  themeGuard.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  enforce();
}

function releaseThemeColor() {
  themeGuard?.disconnect();
  themeGuard = null;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta && appThemeColor) meta.setAttribute("content", appThemeColor);
  dispatchEvent(new Event("ww-boot-done"));
}

if (typeof document !== "undefined") holdThemeColor();

export function hideBoot() {
  if (hidden) return;
  hidden = true;
  window.__wwBootReady = true;
  const el = document.getElementById("ww-boot");
  if (!el) return releaseThemeColor();
  let done = false;
  const fade = () => {
    if (done) return;
    done = true;
    removeEventListener("ww-boot-skip", fade);
    el.classList.add("ww-boot--done");
    releaseThemeColor();
    setTimeout(() => el.remove(), FADE_MS);
  };
  if (window.__wwBootSkip) return fade();
  addEventListener("ww-boot-skip", fade);
  const hold = matchMedia("(prefers-reduced-motion: reduce)").matches ? REDUCED_MS : MIN_MS;
  // measured in a frame callback: it runs only once the intro is on screen (a tab opened in the background waits)
  requestAnimationFrame(() => { if (!done) setTimeout(fade, Math.max(0, hold - played(el))); });
}
