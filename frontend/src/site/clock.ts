/* The site's one animation clock. Scroll-driven scenes (the KT scrubber, the finale) register here: an IntersectionObserver
   turns a scene on while it is near the viewport, and a single requestAnimationFrame loop runs only while at least one is on
   and the page is moving. Each frame reads every scene's layout first, then lets every scene write (CSS variables and
   attributes), so a frame never forces a second layout. The loop stops a few frames after the last scroll or resize and a
   passive scroll listener (which only schedules a frame) wakes it, so a visitor resting on the page costs nothing. No
   per-frame React state. Reveals ("plays once" entrances) share one observer. */

export type Scene = {
  el: Element;
  /** measure (getBoundingClientRect and friends); no writes */
  read(): void;
  /** apply what read() found; no reads */
  write(): void;
};

const active = new Set<Scene>();
const byEl = new Map<Element, Scene>();
let raf = 0;
let sceneIO: IntersectionObserver | null = null;
/** where the page was at the last frame, and how many frames it has stayed there */
let last = "", still = 0;

function frame() {
  raf = 0;
  if (!active.size) return;
  for (const s of active) s.read();
  for (const s of active) s.write();
  const now = `${scrollY} ${innerWidth} ${innerHeight}`;
  still = now === last ? still + 1 : 0;
  last = now;
  if (still < 3) raf = requestAnimationFrame(frame); // a few settled frames after the page stops, then idle
}

function kick() {
  if (active.size && !raf) raf = requestAnimationFrame(frame);
}

function wake() {
  still = 0;
  kick();
}

function observer() {
  return (sceneIO ??= new IntersectionObserver((entries) => {
    for (const e of entries) {
      const s = byEl.get(e.target);
      if (!s) continue;
      if (e.isIntersecting) active.add(s);
      else { active.delete(s); s.read(); s.write(); } // settle it where it left (fast scrolls skip frames)
    }
    wake();
  }, { rootMargin: "25% 0px" }));
}

/** Run `scene` on the clock while its element is near the viewport. Returns the unregister function. */
export function addScene(scene: Scene): () => void {
  if (!byEl.size) { addEventListener("scroll", wake, { passive: true }); addEventListener("resize", wake, { passive: true }); }
  byEl.set(scene.el, scene);
  observer().observe(scene.el);
  scene.read(); scene.write(); // the first frame is right before any scroll
  return () => {
    sceneIO?.unobserve(scene.el);
    byEl.delete(scene.el);
    active.delete(scene);
    if (!byEl.size) { removeEventListener("scroll", wake); removeEventListener("resize", wake); }
  };
}

/* ---------- reveals: .s-rv elements get .is-in once, when 15 % of them is on screen (hidden states exist only under html.motion) */
let revealIO: IntersectionObserver | null = null;
/** what the observer is watching: a page change within the site (/ to /about) drops the old page's unrevealed elements here */
const watched = new Set<Element>();

export function reveal(root: ParentNode) {
  revealIO ??= new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add("is-in");
      revealIO!.unobserve(e.target);
      watched.delete(e.target);
    }
  }, { threshold: 0.15 });
  for (const el of watched) if (!el.isConnected) { revealIO.unobserve(el); watched.delete(el); }
  root.querySelectorAll(".s-rv:not(.is-in)").forEach((el) => { if (!watched.has(el)) { watched.add(el); revealIO!.observe(el); } });
}

export function stopReveals() {
  revealIO?.disconnect();
  revealIO = null;
  watched.clear();
}

/* ---------- night: the nav inverts while black is under it; the browser chrome turns black while the finale or footer fills the view */
const night = new Set<string>();
const chrome = new Set<string>();

function sync() {
  document.documentElement.classList.toggle("s-night", night.size > 0);
  const meta = document.querySelector('meta[name="theme-color"]');
  meta?.setAttribute("content", chrome.size ? "#0a0a0a" : "#f2f2f2");
}

export function setNight(reason: string, under: boolean, filling: boolean) {
  const was = night.has(reason) === under && chrome.has(reason) === filling;
  if (was) return;
  if (under) night.add(reason); else night.delete(reason);
  if (filling) chrome.add(reason); else chrome.delete(reason);
  sync();
}

export function resetNight() {
  night.clear(); chrome.clear();
  document.documentElement.classList.remove("s-night");
}

/** Motion is on for this visit (html.motion is set by the site unless the visitor prefers reduced motion). */
export const moving = () => document.documentElement.classList.contains("motion");
