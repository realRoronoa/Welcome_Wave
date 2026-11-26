/* The film, placed by the scroll. The page scrolls natively (wheel, trackpad, keys and touch are the browser's own: no
   interception, no snapping, and the page itself is never smoothed or held back); the film's position is where the page
   is, F = scroll / SCROLL_PER_SECOND, read once per frame in one rAF loop that runs only while something moves.
   While a wheel or a trackpad moves the page, what is drawn follows that position through a short critically damped spring
   (`follow` below: a glide of about 150 ms), so the film runs like footage rather than stepping with each notch or each
   uneven scroll event. The spring only shapes the picture, and only for the wheel: a finger (whose own momentum is
   already smooth), the keys, the scrollbar, a link's own smooth scroll and reduced motion all draw the page's own position,
   and a jump (a cut, a restored position) lands at once.
   Each frame places:
   - the footage: which shots show and their dissolves (a light leak blooming over each), each shot's keyframed camera
     (every move carried on through the cut, so the film reads as one camera), and whether each clip plays (each clip runs
     at its own speed and loops while visible, flick or not, and pauses off screen; the scroll never sets its time);
   - the zoom-through: the camera pushes into the mentor's monitor, the product world appears on its screen and grows out
     of that window until it fills the view (film.ts ZOOM_IN, ZOOM_OPEN), its own camera already moving;
   - the words: the cover leaving, each caption coming up around its beats, drifting slowly against the footage while it
     is up (a plane of its own), and leaving after them;
   - the product world: the camera's path through it, the panels emerging, the teal threads, the journey and the report,
     and past the film's end the world receding into the dark as the view is carried away;
   - the chapter rail (its marker glides from tick to tick), the scroll cue, and the nav over black.
   Before they are needed, the clips just ahead are prepared while the page is still, and
   the product world is drawn once, all but invisible, at the first quiet moment (its layers, images and shaders warmed).
   Long jumps (the rail, anchors far away) cut there under a short dip through black; short ones scroll natively.
   No layout is read per frame. Each frame writes transform, opacity and clip-path on the elements that move (and a few
   classes); no inherited custom property is written on a container, so a frame restyles only what moved.
   Layers follow the film: a caption's words are promoted a little before it comes up and released after it has gone
   (.is-near). Every curve is chosen: the camera creeps at a beat and travels between beats, quickest at the cuts so a
   move is carried across them; dissolves and fades are smooth at both ends, and nothing is a straight ramp.
   Reduced motion: posters only, the words and stills cross-fading. */
import { setScroller } from "../handover";
import { setNight } from "../clock";
import { BEATS, CAPTION_RANGES, COVER_OUT, FILM_END, LEAKS, posters, reducedMotion, saveData, SECTIONS, SHOTS, Shot, STAGE_OUT, variant, WORLD_IN, ZOOM_IN, ZOOM_OPEN } from "./film";
import { SceneVideo } from "./SceneVideo";
import { DETAIL_U, layout, Layout, MERGED, PanelId, Side, STEP_U, THREADS } from "./geometry";

const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const smooth = (t: number) => t * t * (3 - 2 * t);
const ramp = (x: number, a: number, b: number) => smooth(clamp((x - a) / (b - a)));
/** the push into the monitor: it enters already moving and keeps accelerating */
const pushE = (t: number) => 0.25 * t + 0.75 * Math.pow(t, 2.4);
/** a move that begins with the hand: quickest at the first touch of the scroll, settling as it ends */
const lead = (t: number, power = 2) => 1 - Math.pow(1 - t, power);
/** a fade that is gentle at both ends (1 to 0 as q goes 0 to 1) */
const fade = (q: number) => 1 - smooth(clamp(q));
const D = Math.PI / 180;
const N = BEATS.length;
/** screens: a jump further than this cuts under a dip through black instead of scrolling there */
const JUMP = 2.5;
/** the spring the picture follows the page through: critically damped (no overshoot, no oscillation), its rate in 1/s.
 *  A wheel's notch (a step) is carried out as one glide: 50 % in 1.68 / RATE = 65 ms, 90 % in 3.89 / RATE = 150 ms; under
 *  a steady scroll it sits 2 / RATE = 77 ms behind. Only the film's picture glides; the page itself is the browser's. */
const RATE = 26;
/** film seconds: the picture this far from the page is not eased there, it cuts (the scrollbar, Home and End, a restore) */
const LEAP = 3.2;
/** film seconds per second: above this a real flick is going through (several screens a second), so a caption stays down
 *  (an ordinary brisk scroll still raises every caption it passes) */
const FLICK_WORDS = 16;
/** film seconds: a caption's layers exist from this long before it comes up until this long after it has gone */
const NEAR = 1.4;
/** a caption is not raised when the film will have left it within this long (s, at its speed: it would only flash by),
 *  and one that leaves within QUICK ms of coming up goes back the way it came instead of sweeping on through the frame */
const STAY = 0.18, QUICK = 350;
/** ms: the picture eases only while the page moves under a wheel or a trackpad (its last event at most this old) */
const WHEEL_HOLD = 400;
/** film seconds: resting this close before the push into the monitor, the product world is drawn once more, unseen, so its
 *  layers are fresh when it first shows on the screen (a reader who came by a link or a fast scroll missed the first warm) */
const WARM_NEAR: [number, number] = [16.8, ZOOM_IN[0] - 0.05];

/** a smooth (C1, no overshoot) curve through keyframes: monotone cubic Hermite. It is already moving at its first key and
 *  still moving at its last (the one-sided slope), unless `restEnd` (the product world's last stop); a key in `holds` is a
 *  beat, crept through at about a third of the speed around it. */
function curve(xs: number[], ys: number[], x: number, holds?: boolean[], restEnd = false): number {
  const n = xs.length;
  if (n === 1 || x <= xs[0]) return ys[0];
  if (x >= xs[n - 1]) return ys[n - 1];
  let i = 0;
  while (x > xs[i + 1]) i++;
  const h = xs[i + 1] - xs[i], t = (x - xs[i]) / h;
  const d = (k: number) => (ys[k + 1] - ys[k]) / (xs[k + 1] - xs[k]);
  const m = (k: number) => {
    let v: number;
    if (k === 0) v = d(0);
    else if (k === n - 1) v = restEnd ? 0 : d(n - 2);
    else { const a = d(k - 1), b = d(k); v = a * b <= 0 ? 0 : (2 * a * b) / (a + b); }
    return holds?.[k] ? v * 0.3 : v;
  };
  const m0 = m(i) * h, m1 = m(i + 1) * h;
  const t2 = t * t, t3 = t2 * t;
  return (2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * m0 + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * m1;
}

type Vid = { shot: Shot; el: HTMLElement; img: HTMLImageElement; video: SceneVideo | null; posterSet: boolean; shown: boolean; media?: HTMLElement; last: string;
  /** its keys as curves (per channel), and its frame on the view: object-fit cover at its focus (the CSS's maths) */
  ks: number[]; ksc: number[]; kdx: number[]; kdy: number[]; kh: boolean[]; fit: { k: number; offX: number; offY: number; rw: number; rh: number; ox: number; oy: number } };
type Panel = { id: PanelId; el: HTMLElement; w: number; h: number; anchors: Map<string, [number, number]>; last: string; op: string; vis: boolean; o: number };
/** an element the film fades and raises (a reveal in the product world), with what was last written to it */
type Rise = { el: HTMLElement; by: number; last: string };
/** a caption: whether it is up, near (its layers exist) and resting above (it comes down into place when the film runs
 *  backwards), and the parts the film's velocity moves, written only while it is on screen (or on its way out) */
type Cap = { el: HTMLElement; range: [number, number]; on: boolean; near: boolean; above: boolean; timer: number; h: HTMLElement | null;
  inn: HTMLElement | null; lw: string; onAt: number };

export type StoryParts = { track: HTMLElement; stage: HTMLElement; world: HTMLElement; words: HTMLElement };

export function runStory({ track, stage, world, words }: StoryParts): () => void {
  const stepped = reducedMotion();
  const stills = stepped || saveData();
  if (stepped) track.classList.add("is-stepped");

  const vid = (shot: Shot, el: HTMLElement): Vid => ({
    shot, el, img: el.querySelector("img")!, video: stills ? null : new SceneVideo(shot, el.querySelector("video")!, el),
    posterSet: false, shown: false, media: el.querySelector<HTMLElement>(".s-shot-media") ?? undefined, last: "",
    ks: [], ksc: [], kdx: [], kdy: [], kh: [], fit: { k: 1, offX: 0, offY: 0, rw: 1, rh: 1, ox: 0, oy: 0 },
  });
  const shots = SHOTS.filter((s) => s.place === "stage").map((s) => vid(s, stage.querySelector<HTMLElement>(`[data-shot="${s.name}"]`)!));
  const clips = SHOTS.filter((s) => s.place === "world").map((s) => vid(s, world.querySelector<HTMLElement>(`.u-clip[data-shot="${s.name}"]`)!));
  const all = [...shots, ...clips];
  const leak = stage.querySelector<HTMLElement>(".s-leak")!;
  const dip = track.querySelector<HTMLElement>(".s-dip")!;
  const cue = track.querySelector<HTMLElement>(".s-cue");
  const glide = track.querySelector<HTMLElement>(".s-rail-glide");
  const grain = stage.querySelector<HTMLElement>(".s-grain");
  const rail = [...track.querySelectorAll<HTMLButtonElement>(".s-rail button")];
  const cam = world.querySelector<HTMLElement>(".w-world")!;
  const camHost = world.querySelector<HTMLElement>(".w-camera")!;
  const floor = world.querySelector<HTMLElement>(".w-floor")!;
  const canvas = world.querySelector<HTMLCanvasElement>(".w-threads")!;        // the threads' end points, above the panels
  const under = world.querySelector<HTMLCanvasElement>(".w-threads-under")!;  // the threads themselves, under the panels
  const g = canvas.getContext("2d"), gu = under.getContext("2d");
  const panels: Panel[] = [...world.querySelectorAll<HTMLElement>(".w-panel")].map((el) => ({
    id: el.dataset.panel as PanelId, el, w: 1, h: 1, anchors: new Map(), last: "", op: "", vis: true, o: 0 }));
  const byId = new Map(panels.map((p) => [p.id, p]));
  const stepsEl = [...world.querySelectorAll<HTMLElement>("[data-step]")].map((el) => ({ el, last: "" }));
  const rise = (el: HTMLElement, by: number): Rise => ({ el, by, last: "" });
  const details = [...world.querySelectorAll<HTMLElement>("[data-detail]")].map((el) => rise(el, 14));
  const mergedEl = world.querySelector<HTMLElement>("[data-reveal=merged]");
  const merged = mergedEl ? rise(mergedEl, 8) : null;
  const tiles = [...world.querySelectorAll<HTMLElement>(".u-tile-host")].map((el) => rise(el, 12));
  // the cover: its shade and the four parts that leave at their own speeds (each headline word by its line, --l)
  const cover = words.querySelector<HTMLElement>(".s-cover");
  const shade = words.querySelector<HTMLElement>(".s-cover-shade");
  const kicker = cover?.querySelector<HTMLElement>(".s-kicker") ?? null;
  const host = cover?.querySelector<HTMLElement>(".s-h1-host") ?? null;
  const side = cover?.querySelector<HTMLElement>(".s-cover-side") ?? null;
  const hws = [...(cover?.querySelectorAll<HTMLElement>(".s-h1 .s-hw") ?? [])].map((el) => ({ el, l: 0 }));
  // the headline's full stop sits beside its last word, not inside it: it rides with that word's line (as `translate`, which
  // adds to the transform its entrance animates, so it follows even while the entrance is still playing)
  const stop = cover?.querySelector<HTMLElement>(".s-h1-stop") ?? null;
  const caps: Cap[] = [...words.querySelectorAll<HTMLElement>("[data-cap]")].map((el) => ({
    el, range: CAPTION_RANGES[el.dataset.cap!], on: false, near: false, above: false, timer: 0, h: el.querySelector<HTMLElement>(".s-cap-h"),
    inn: el.querySelector<HTMLElement>(".s-cap-in"), lw: "", onAt: 0 }));
  // the shot whose monitor the product world grows out of (film.ts ZOOM)
  const portal = shots.find((v) => v.shot.monitor) ?? null;
  const drift = world.querySelector<HTMLElement>(".w-drift");
  const onScreen = (s: Shot, F: number) => F >= s.u0 && F <= s.u1;

  function poster(v: Vid) {
    if (v.posterSet) return;
    v.posterSet = true;
    const p = posters(v.shot);
    v.el.querySelectorAll<HTMLSourceElement>("source[data-srcset]").forEach((s) => { s.srcset = s.dataset.srcset!; });
    if (v.shot.u0 === 0) v.img.setAttribute("fetchpriority", "high");
    if (v.img.dataset.src) { v.img.srcset = v.img.dataset.srcset!; v.img.src = v.img.dataset.src; }
    else v.img.src = innerWidth < 900 ? p.jpgSmall : p.jpg;
    v.img.addEventListener("load", () => v.el.classList.add("has-poster"), { once: true });
    v.img.decode?.().catch(() => {}); // decoded off the main thread now, not in the frame it first shows
  }

  /* ---------------- measure: only on resize (and once fonts have settled) */
  let vh = 1, vw = 1, top = 0, len = 1, S = 1, L: Layout = layout(), phone = false;
  let camU: number[] = [], camH: boolean[] = [], camK: Record<"x" | "y" | "z" | "ry" | "rx", number[]> = { x: [], y: [], z: [], ry: [], rx: [] };
  const yOf = (F: number) => top + F * S;
  function measure() {
    vh = innerHeight; vw = innerWidth;
    top = track.getBoundingClientRect().top + scrollY;
    len = track.offsetHeight;
    S = track.querySelector<HTMLElement>(".s-spacer")!.offsetHeight / SECTIONS[1].from; // px of scroll per film second
    phone = vw < 900;
    L = layout();
    camU = L.cam.map((c) => c.u);
    camH = L.cam.map((c) => !!c.hold);
    (["x", "y", "z", "ry", "rx"] as const).forEach((k) => { camK[k] = L.cam.map((c) => c[k]); });
    camHost.style.perspective = `${L.perspective}px`;
    camHost.style.perspectiveOrigin = `50% ${L.originY * 100}%`;
    cam.style.top = `${L.originY * 100}%`;
    // the floor is set at 1/--k of its size (cinematic.css) and scaled back up here
    const fk = parseFloat(getComputedStyle(floor).getPropertyValue("--k")) || 1;
    floor.style.transform = `translate3d(0, ${L.floorY}px, -1600px) rotateX(90deg) scale(${fk}) translate(-50%, -50%)`;
    for (const p of panels) {
      p.el.style.width = `${L.panels[p.id].w}px`;
      p.w = p.el.offsetWidth; p.h = p.el.offsetHeight;
      p.anchors.clear();
      p.el.querySelectorAll<HTMLElement>("[data-anchor]").forEach((a) => {
        let x = a.offsetWidth / 2, y = a.offsetHeight / 2, n: HTMLElement | null = a;
        while (n && n !== p.el) { x += n.offsetLeft; y += n.offsetTop; n = n.offsetParent as HTMLElement | null; }
        p.anchors.set(a.dataset.anchor!, [x - p.w / 2, y - p.h / 2]);
      });
      p.last = "";
    }
    const dpr = Math.min(devicePixelRatio || 1, 2);
    for (const [c, x] of [[canvas, g], [under, gu]] as const) {
      c.width = Math.round(vw * dpr); c.height = Math.round(vh * dpr);
      x?.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    drewA = drewU = false; threadsAt = NaN;
    shots.forEach((s) => (s.last = ""));
    // each headline word's line (site.css .s-hw --l: phone or tablet-up), and the monitor's place in the portal shot's frame
    for (const w of hws) w.l = parseFloat(getComputedStyle(w.el).getPropertyValue("--l")) || 0;
    if (host) lineStep = (parseFloat(getComputedStyle(host).fontSize) || 106) * 0.11;
    // each caption's headline as it wraps here: its words come up a line at a time (cinematic.css reads --ln, the word's
    // line, and --wi, its place in that line). All read first, then all written.
    // --lns, its number of lines, lets the stagger run from the bottom line when the film runs backwards.
    const marks: [HTMLElement, number, number][] = [], lines: [HTMLElement, number][] = [];
    for (const c of caps) {
      let at = NaN, ln = -1, wi = 0;
      c.el.querySelectorAll<HTMLElement>(".s-cap-h .s-w").forEach((w) => {
        const y = w.offsetTop;
        if (y !== at) { at = y; ln++; wi = 0; }
        if (w.firstElementChild) marks.push([w.firstElementChild as HTMLElement, ln, wi++]);
      });
      if (c.h) lines.push([c.h, ln + 1]);
    }
    for (const [el, ln, wi] of marks) { el.style.setProperty("--ln", String(ln)); el.style.setProperty("--wi", String(wi)); }
    for (const [el, n] of lines) el.style.setProperty("--lns", String(Math.max(1, n)));
    lastCo = "";
    portrait = vw < 700 && vh > vw;
    for (const v of shots) measureShot(v);
    if (portal) mon = rectOn(portal, portal.shot.monitor!, -1);
    lastZoom = "";
  }

  /* ---------------- each shot's frame and camera (the same maths as its CSS: object-fit cover at its focus, then translate
     and scale about its origin) */
  let portrait = false;
  const pc = (t: string) => t.split(/\s+/).map((x) => parseFloat(x) / 100);
  function measureShot(v: Vid) {
    const s = v.shot, va = variant(s);
    const [sw, sh] = va === "portrait" ? [540, 960] : [1920, 1080];
    const k = Math.max(vw / sw, vh / sh), rw = sw * k, rh = sh * k;
    const [fx, fy] = pc(s.focus), [ox, oy] = pc(s.origin);
    v.fit = { k, offX: (vw - rw) * fx, offY: (vh - rh) * fy, rw, rh, ox: ox * vw, oy: oy * vh };
    // phones held upright: the same move, gentler (their 9:16 cuts are already enlarged on the screen; the push into the
    // monitor is the exception, the window the world grows out of is locked to it)
    const soft = portrait && !s.push;
    v.ks = s.cam.map((c) => c[0]);
    v.ksc = s.cam.map((c) => (soft ? 1 + (c[1] - 1) * 0.62 : c[1]));
    v.kdx = s.cam.map((c) => (soft ? c[2] * 0.5 : c[2]));
    v.kdy = s.cam.map((c) => c[3]);
    v.kh = s.cam.map((c) => !!c[4]);
  }
  /** a shot's camera at F: scale and drift (% of the frame). The drift never uncovers an edge of the view. */
  function camera(v: Vid, F: number) {
    if (v.shot.push) {
      const n = v.ks.length - 1, e = pushE(clamp((F - v.ks[0]) / (v.ks[n] - v.ks[0])));
      return { sc: v.ksc[0] + (v.ksc[n] - v.ksc[0]) * e, dx: v.kdx[0] + (v.kdx[n] - v.kdx[0]) * e, dy: v.kdy[0] + (v.kdy[n] - v.kdy[0]) * e };
    }
    const sc = curve(v.ks, v.ksc, F, v.kh), f = v.fit;
    const mx = Math.max(0, (sc - 1) * Math.min(f.ox, vw - f.ox)) / vw * 100, my = Math.max(0, (sc - 1) * Math.min(f.oy, vh - f.oy)) / vh * 100;
    return { sc, dx: clamp(curve(v.ks, v.kdx, F, v.kh), -mx, mx), dy: clamp(curve(v.ks, v.kdy, F, v.kh), -my, my) };
  }
  /** a rectangle of a shot's source frame (fractions) on the view: as framed (F < 0) or carried by its camera at F */
  type Box = { x0: number; y0: number; x1: number; y1: number };
  function rectOn(v: Vid, r: { wide: [number, number, number, number]; portrait: [number, number, number, number] }, F: number): Box {
    const q = variant(v.shot) === "portrait" ? r.portrait : r.wide, f = v.fit;
    const b = { x0: f.offX + q[0] * f.rw, y0: f.offY + q[1] * f.rh, x1: f.offX + q[2] * f.rw, y1: f.offY + q[3] * f.rh };
    return F < 0 ? b : carry(v, b, F);
  }
  function carry(v: Vid, b: Box, F: number): Box {
    const { sc, dx, dy } = camera(v, F), f = v.fit;
    const X = (x: number) => f.ox + (dx / 100) * vw + sc * (x - f.ox), Y = (y: number) => f.oy + (dy / 100) * vh + sc * (y - f.oy);
    return { x0: X(b.x0), y0: Y(b.y0), x1: X(b.x1), y1: Y(b.y1) };
  }
  /** px: where a window's edge runs out past the view's, it meets it through a smooth corner this wide (shrinking to nothing
   *  as the window finishes opening) instead of stopping dead there: a hard clamp jolted the world's growth (its speed fell
   *  to a third from one frame to the next) just as the push into the monitor peaks. Exact further than this from an edge,
   *  so the window stays locked to the screen it grows out of. */
  const EDGE = 72;
  const inView = (a: number, s: number) => (a >= s ? a : a <= -s ? 0 : ((a + s) * (a + s)) / (4 * s)); // max(0, a), C1
  /** a view-sized layer shown as a window (b, on the view) opening out to the whole view by t: its transform (scaled to
   *  cover the window, centred on it) and its clip (in its own coordinates: clip-path applies before the transform) */
  function windowOf(b: Box, t: number) {
    const u = 1 - t, s = EDGE * u * Math.sqrt(u); // (1 - t)^1.5: still wide when an edge crosses late in the opening
    const x0 = inView(b.x0 * (1 - t), s), y0 = inView(b.y0 * (1 - t), s);
    const x1 = vw - inView(vw - (b.x1 + (vw - b.x1) * t), s), y1 = vh - inView(vh - (b.y1 + (vh - b.y1) * t), s);
    const w = Math.max(1, x1 - x0), h = Math.max(1, y1 - y0);
    const k = Math.max(w / vw, h / vh);
    const ix = Math.max(0, (vw - w / k) / 2), iy = Math.max(0, (vh - h / k) / 2);
    return { tf: `translate3d(${((x0 + x1 - vw) / 2).toFixed(2)}px, ${((y0 + y1 - vh) / 2).toFixed(2)}px, 0) scale(${k.toFixed(5)})`,
      clip: `inset(${iy.toFixed(2)}px ${ix.toFixed(2)}px)` };
  }
  let mon: Box = { x0: 0, y0: 0, x1: 0, y1: 0 };
  let lastZoom = "";
  /** the world as a window on the monitor's screen, opening out to the whole view (t: 0 on the screen, 1 the view) */
  function zoomWorld(F: number) {
    let tf = "", clip = "";
    if (portal && F < ZOOM_OPEN[1]) {
      // before the window shows, it waits where it will appear (no writes while it is hidden); the screen's rectangle
      // carried by the push, opening out to the view's (only the part of it inside the view: a phone's monitor is wider
      // than the screen)
      const w = windowOf(carry(portal, mon, Math.max(F, ZOOM_IN[0])), ramp(F, ZOOM_OPEN[0], ZOOM_OPEN[1]));
      tf = w.tf; clip = w.clip;
    }
    const key = tf + clip;
    if (key === lastZoom) return;
    lastZoom = key;
    world.style.transform = tf;
    world.style.clipPath = clip;
  }

  /* ---------------- the camera and its projection (the same maths as the CSS transforms) */
  let C = { x: 0, y: 0, z: 0, ry: 0, rx: 0 };
  function toCam(x: number, y: number, z: number): [number, number, number] {
    let X = x - C.x, Y = y - C.y, Z = z - C.z;
    const cy = Math.cos(C.ry * D), sy = Math.sin(C.ry * D);
    [X, Z] = [X * cy + Z * sy, -X * sy + Z * cy];
    const cx = Math.cos(C.rx * D), sx = Math.sin(C.rx * D);
    [Y, Z] = [Y * cx - Z * sx, Y * sx + Z * cx];
    return [X, Y, Z];
  }
  const project = ([X, Y, Z]: [number, number, number]): [number, number] => {
    const k = L.perspective / Math.max(1, L.perspective - Z);
    return [vw / 2 + X * k, vh * L.originY + Y * k];
  };
  function place(id: PanelId, F: number) {
    const pl = L.panels[id];
    const e = ramp(F, pl.in[0], pl.in[1]);
    const z = pl.z - (1 - e) * (phone ? 160 : 280);
    const ry = pl.ry + (1 - e) * (pl.ry >= 0 ? 10 : -10);
    return { e, x: pl.x, y: pl.y + (1 - e) * 40, z, ry };
  }
  function onPanel(p: Panel, F: number, lx: number, ly: number): [number, number, number] {
    const q = place(p.id, F);
    const c = Math.cos(q.ry * D), s = Math.sin(q.ry * D);
    return [q.x + lx * c, q.y + ly, q.z - lx * s];
  }
  function outside(p: Panel, a: [number, number], side: Side): [number, number] {
    const Gp = 26;
    return side === "down" ? [a[0], p.h / 2 + Gp] : side === "up" ? [a[0], -p.h / 2 - Gp] : side === "left" ? [-p.w / 2 - Gp, a[1]] : [p.w / 2 + Gp, a[1]];
  }

  /* ---------------- one frame of the film at F */
  let night = false, lastWorldVis = true, lastStageVis = true, lastLeak = "", leakLit = false, lastCam = "", lastStageOp = "", lastWorldOp = "";
  let px = 0, py = 0, tx = 0, ty = 0; // pointer tilt (current, target)
  let driftOn = false;
  /** a clip plays whenever its shot is on screen, at any speed of scroll (a flick passes through living footage) */
  function run(v: Vid, on: boolean) {
    v.video?.play(on && !document.hidden);
  }
  function apply(F: number) {
    // footage: gone once the world has grown out of the monitor over the whole view (reduced motion: a cross-fade)
    const stageOut = stepped ? 1 - ramp(F, STAGE_OUT[0], STAGE_OUT[1]) : F < ZOOM_OPEN[1] ? 1 : 0;
    const stageVis = stageOut > 0;
    if (stageVis !== lastStageVis) { lastStageVis = stageVis; stage.style.visibility = stageVis ? "visible" : "hidden"; }
    const so = stageOut >= 1 ? "" : stageOut.toFixed(3);
    if (so !== lastStageOp) { lastStageOp = so; stage.style.opacity = so; }
    let covered = false;
    for (let i = shots.length - 1; i >= 0; i--) {
      const v = shots[i], s = v.shot;
      const vis = onScreen(s, F) && stageVis;
      const op = vis ? (s.fade ? ramp(F, s.u0, s.u0 + s.fade) : 1) : 0;
      const on = op > 0 && !covered;
      if (op >= 1) covered = true;
      if (on !== v.shown) {
        v.shown = on; v.el.classList.toggle("is-on", on);
      }
      run(v, on);
      if (!on) continue;
      const { sc, dx, dy } = camera(v, F);
      const tf = stepped ? "" : `translate3d(${dx.toFixed(3)}%, ${dy.toFixed(3)}%, 0) scale(${sc.toFixed(4)})`;
      const key = `${tf}|${op.toFixed(3)}`;
      if (key !== v.last) { v.last = key; v.media!.style.transform = tf; v.el.style.opacity = op >= 1 ? "" : op.toFixed(3); }
    }
    // the cover's words leave as the first movement leans in
    coverOut(clamp((F - COVER_OUT[0]) / (COVER_OUT[1] - COVER_OUT[0])));

    // the product world: on the monitor's screen first, then the whole view (reduced motion: it fades in)
    if (!stepped) zoomWorld(F);
    applyWorld(F, stepped ? ramp(F, WORLD_IN[0], WORLD_IN[1]) : ramp(F, ZOOM_IN[0], ZOOM_IN[1]));
  }

  /* the light leak: a soft bloom where two shots overlap and as a window opens. It grows and dies away without an edge (a
     bell that is flat where it starts and where it ends), at any speed of scroll. Its layer exists only around a dissolve
     (a full-view blended layer is not kept for the rest of the film). */
  function leaks(F: number) {
    let lk = 0;
    if (!stepped) for (const [a, b] of LEAKS) { const t = (F - a) / (b - a); if (t > 0 && t < 1) lk = Math.max(lk, Math.sin(Math.PI * smooth(t)) * 0.34); }
    const lks = lk.toFixed(3);
    if (lks !== lastLeak) { lastLeak = lks; leak.style.opacity = lks; }
    const lit = !stepped && LEAKS.some(([a, b]) => F > a - 0.8 && F < b + 0.4);
    if (lit !== leakLit) { leakLit = lit; leak.classList.toggle("is-lit", lit); }
  }

  /* the cover leaves in one gesture, from the first pixel of scroll: every part rides one curve that is quickest under the
     first touch and settles as it goes (so the page answers at once), the lower lines travelling a little further than
     the upper ones (the lower, the faster: depth) with the full stop on its line, and each part fades on a curve that is
     gentle at both ends, the small type first, the headline last. p: 0 the cover as set, 1 gone. Written on each part. */
  let lastCo = "";
  /** how far a headline line travels (px): each line a little further than the one above (a ninth of the type size a line,
   *  measured), so the lines draw together a touch as they go and never close on each other, at any size */
  let lineStep = 12;
  const LINE = (l: number) => -70 - l * lineStep;
  function coverOut(p: number) {
    const cs = p.toFixed(4);
    if (!cover || cs === lastCo) return;
    lastCo = cs;
    const e = lead(p);
    const at = (n: number) => (p <= 0 ? "" : n.toFixed(3));
    const up = (px: number) => (p <= 0 || stepped ? "" : `translate3d(0, ${(e * px).toFixed(2)}px, 0)`); // reduced motion: it only fades
    if (kicker) { kicker.style.transform = up(-50); kicker.style.opacity = at(fade(p * 1.9)); }
    for (const w of hws) w.el.style.transform = up(LINE(w.l));
    if (stop && hws.length) stop.style.translate = p <= 0 || stepped ? "" : `0 ${(e * LINE(hws[hws.length - 1].l)).toFixed(2)}px`;
    if (host) host.style.opacity = at(fade(p * 1.2));
    if (side) { side.style.transform = up(-150); side.style.opacity = at(fade(p * 2.3)); }
    if (shade) shade.style.opacity = at(fade(p));
    // gone: out of the picture altogether (hidden content has no layers), and back as it was at the first step upwards
    const gone = p >= 1;
    if (gone !== coverGone) { coverGone = gone; cover.classList.toggle("is-gone", gone); shade?.classList.toggle("is-gone", gone); }
  }
  let coverGone = false;

  function applyWorld(F: number, wo: number) {
    const worldVis = wo > 0;
    if (worldVis !== lastWorldVis) { lastWorldVis = worldVis; world.style.visibility = worldVis ? "visible" : "hidden"; }
    const live = worldVis && !stepped && !warming;
    if (live !== driftOn) { driftOn = live; drift?.classList.toggle("is-live", live); }
    if (!warming) for (const v of clips) run(v, worldVis && onScreen(v.shot, F)); // a warm starts no clip
    if (worldVis) {
      const wos = wo >= 1 ? "" : wo.toFixed(3);
      if (wos !== lastWorldOp) { lastWorldOp = wos; world.style.opacity = wos; }
      // the camera never stops as the monitor's window opens (its path is already moving there) and creeps at each
      // station; past the film's end it pulls back and up as the view is carried away (exitE)
      const cu = clamp(F, camU[0], camU[camU.length - 1]), h = (k: number[]) => curve(camU, k, cu, camH, true);
      const xe = smooth(exitE);
      C = { x: h(camK.x), y: h(camK.y) - (phone ? 40 : 90) * exitE, z: h(camK.z) + (phone ? 300 : 520) * xe,
            ry: h(camK.ry) + px * 2.4, rx: h(camK.rx) - py * 1.6 + 3 * exitE };
      const ct = `rotateX(${C.rx.toFixed(3)}deg) rotateY(${C.ry.toFixed(3)}deg) translate3d(${(-C.x).toFixed(2)}px, ${(-C.y).toFixed(2)}px, ${(-C.z).toFixed(2)}px)`;
      if (ct !== lastCam) { lastCam = ct; cam.style.transform = ct; }
      const P = L.perspective;
      for (const p of panels) {
        const q = place(p.id, F);
        const [, , Z] = toCam(q.x, q.y, q.z);
        // near panels fade out before they grow past 110 % of the screen's width; far ones sink into the dark
        const zMax = P * (1 - p.w / (1.1 * vw));
        const fog = clamp((zMax - Z) / (0.14 * P)) * clamp((Z + 3400) / 900) * (1 - ramp(exitE, 0.35, 0.95));
        const o = q.e * fog;
        p.o = o;
        const vis = o > 0.004;
        if (vis !== p.vis) { p.vis = vis; p.el.style.visibility = vis ? "" : "hidden"; }
        if (!vis) continue;
        const tf = `translate3d(${q.x}px, ${q.y.toFixed(2)}px, ${q.z.toFixed(2)}px) rotateY(${q.ry.toFixed(3)}deg) translate(-50%, -50%)`;
        if (tf !== p.last) { p.last = tf; p.el.style.transform = tf; }
        const os = o >= 0.999 ? "" : o.toFixed(3);
        if (os !== p.op) { p.op = os; p.el.style.opacity = os; }
      }
      // a step's check (--s feeds its colours: a small leaf subtree, written only while it changes)
      stepsEl.forEach((st, i) => {
        const v = ramp(F, STEP_U[i], STEP_U[i] + 0.22).toFixed(3);
        if (v !== st.last) { st.last = v; st.el.style.setProperty("--s", v); }
      });
      details.forEach((d, i) => {
        const [a, b] = DETAIL_U[i];
        reveal(d, ramp(F, a, a + 0.15) * (1 - ramp(F, b - 0.08, b + 0.08)));
      });
      if (merged) reveal(merged, ramp(F, MERGED[0], MERGED[1]));
      const rIn = L.panels.report.in[0];
      tiles.forEach((t, i) => reveal(t, ramp(F, rIn + 0.2 + i * 0.07, rIn + 0.5 + i * 0.07)));
      // the threads are redrawn (two full-view canvases, cleared and uploaded) for a visible change only: in the spring's
      // last sub-pixel steps they wait, and they are drawn exactly where the picture comes to rest
      if (!(Fs !== Ft && Math.abs(F - threadsAt) < 0.002 && px === threadsPx && py === threadsPy)) drawThreads(F);
    }
  }

  function reveal(r: Rise, o: number) {
    const os = o.toFixed(3);
    if (os === r.last) return;
    r.last = os;
    r.el.style.opacity = os;
    r.el.style.transform = `translate3d(0, ${((1 - o) * r.by).toFixed(2)}px, 0)`;
  }

  /* ---------------- the teal threads: light from a recorded moment to what it explains, routed through the gaps between
     panels and drawn under them (a dark halo beneath it), with only the two end points glowing above */
  let drewA = false, drewU = false; // whether either canvas holds anything: an empty canvas is left alone (no clear, no upload)
  let threadsAt = NaN, threadsPx = 0, threadsPy = 0; // what the threads were last drawn for
  function drawThreads(F: number) {
    if (!g || !gu) return;
    threadsAt = F; threadsPx = px; threadsPy = py;
    if (drewA) g.clearRect(0, 0, vw, vh);
    if (drewU) gu.clearRect(0, 0, vw, vh);
    drewA = drewU = false;
    const P = L.perspective;
    for (const t of THREADS) {
      const d = stepped ? (F >= t.u[1] ? 1 : 0) : ramp(F, t.u[0], t.u[1]);
      if (d <= 0) continue;
      const pa = byId.get(t.from[0]), pb = byId.get(t.to[0]);
      if (!pa || !pb) continue;
      const alpha = pb.o * Math.max(0.35, pa.o);
      if (alpha < 0.02) continue;
      const a = pa.anchors.get(t.from[1]), b = pb.anchors.get(t.to[1]);
      if (!a || !b) continue;
      const pts3 = [onPanel(pa, F, a[0], a[1]), onPanel(pa, F, ...outside(pa, a, t.exit)), onPanel(pb, F, ...outside(pb, b, t.enter)), onPanel(pb, F, b[0], b[1])];
      const camPts = pts3.map((w) => toCam(...w));
      const zMax = Math.max(...camPts.map((c) => c[2]));
      if (zMax > P * 0.8) continue;
      const fade = alpha * clamp((P * 0.8 - zMax) / (P * 0.35));
      if (fade <= 0.01) continue;
      const q = camPts.map(project);
      const ctl = [q[0], ...q, q[3]];
      const pts: [number, number][] = [];
      const per = 18;
      for (let seg = 0; seg < 3; seg++) {
        const [p0, p1, p2, p3] = [ctl[seg], ctl[seg + 1], ctl[seg + 2], ctl[seg + 3]];
        for (let i = seg ? 1 : 0; i <= per; i++) {
          const s2 = i / per, s3 = s2 * s2, s4 = s3 * s2;
          const f = (k: 0 | 1) => 0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * s2 + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * s3 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * s4);
          pts.push([f(0), f(1)]);
        }
      }
      const k = Math.max(2, Math.round(pts.length * d));
      const stroke = (w: number, color: string) => {
        gu.beginPath(); gu.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < k; i++) gu.lineTo(pts[i][0], pts[i][1]);
        gu.lineWidth = w; gu.strokeStyle = color; gu.stroke();
      };
      gu.lineCap = "round"; gu.lineJoin = "round";
      drewU = true; drewA = true;
      stroke(12, `rgba(0, 0, 0, ${(0.28 * fade).toFixed(3)})`);
      stroke(6, `rgba(0, 0, 0, ${(0.42 * fade).toFixed(3)})`);
      stroke(5, `rgba(45, 212, 191, ${(0.14 * fade).toFixed(3)})`);
      stroke(1.5, `rgba(45, 212, 191, ${(0.9 * fade).toFixed(3)})`);
      if (d < 1) dot(gu, pts[k - 1], 3, fade);
      dot(g, q[0], 2.6, fade);
      if (d >= 0.99) dot(g, q[3], 2.6, fade);
    }
  }
  function dot(c: CanvasRenderingContext2D, [x, y]: [number, number], r: number, a: number) {
    const grad = c.createRadialGradient(x, y, 0, x, y, r * 3.2);
    grad.addColorStop(0, `rgba(153, 246, 228, ${(0.95 * a).toFixed(3)})`);
    grad.addColorStop(0.35, `rgba(45, 212, 191, ${(0.55 * a).toFixed(3)})`);
    grad.addColorStop(1, "rgba(45, 212, 191, 0)");
    c.fillStyle = grad; c.beginPath(); c.arc(x, y, r * 3.2, 0, Math.PI * 2); c.fill();
  }

  /* ---------------- loading: what the next stretch of film needs, nothing more */
  let loadedAt = -99;
  function load(F: number) {
    if (Math.abs(F - loadedAt) < 0.5) return;
    loadedAt = F;
    for (const v of all) {
      const s = v.shot;
      if (s.u0 === 0 || (s.u0 <= F + 9 && s.u1 >= F - 3)) poster(v);
      if (!v.video) continue;
      if (s.u0 === 0 || (s.u0 <= F + 5 && s.u1 >= F - 2)) v.video.prepare(onScreen(s, F));
      else if (s.u0 <= F + 9 && s.u1 >= F - 3) v.video.prepare();
    }
    // The next two clips get a source before they appear; clips behind may rewind after scrolling has settled.
    const ahead = all.filter((v) => v.video && v.shot.u0 > F).slice(0, 2);
    const behind = all.filter((v) => v.video && v.shot.u0 > 0 && v.shot.u1 < F).sort((a, b) => b.shot.u1 - a.shot.u1).slice(0, 1);
    for (const v of ahead) { poster(v); v.video!.prepare(); }
    if (performance.now() >= SceneVideo.quietAt) for (const v of behind) v.video!.rewind();
  }

  /* ---------------- warm the product world: drawn for two frames at its first station, at opacity 0.001, so its layers,
     images, text and shaders are ready before the push into the monitor. Once at the first quiet moment on the cover, and
     again at a quiet moment just before the push (WARM_NEAR), once per approach: a reader who arrived by a link, a reload
     or a fast scroll never rested on the cover, and layers drawn long ago may have been let go. */
  let warmed = stills, warmedNear = stills, warmTimer = 0, warming = false;
  function warmWorld() {
    warmTimer = 0;
    if (warmed) return;
    if (lastF > 2) { warmed = true; warmAhead(); return; } // the reader is already on the way: primed as it nears
    if (performance.now() < SceneVideo.quietAt) { warmTimer = window.setTimeout(warmWorld, 600); return; }
    warmed = true;
    warm();
  }
  function warm() {
    // at full scale (not in the monitor's window), so its layers are rastered as sharp as the whole view needs
    world.style.transform = world.style.clipPath = "";
    lastZoom = "";
    warming = true;
    applyWorld(BEATS[5].F, 0.001);
    warming = false;
    requestAnimationFrame(() => requestAnimationFrame(() => { drawThreads(-1); lastF = -1; frameNow(); }));
  }
  /** called when the film comes to rest: warm the world if the push into the monitor is just ahead */
  function warmAhead() {
    if (stills) return;
    const F = Fs;
    if (F < WARM_NEAR[0] - 1 || F > ZOOM_OPEN[1]) warmedNear = false; // away from the push: the next approach warms again
    if (warmedNear || F < WARM_NEAR[0] || F > WARM_NEAR[1] || warmTimer) return;
    warmTimer = window.setTimeout(function wait() {
      warmTimer = 0;
      if (warmedNear || raf || Fs < WARM_NEAR[0] || Fs > WARM_NEAR[1]) return; // moved on (the next rest asks again)
      if (performance.now() < SceneVideo.quietAt) { warmTimer = window.setTimeout(wait, 300); return; }
      warmedNear = true;
      warm();
    }, 500);
  }

  /* ---------------- the words: each caption up around its beats, away after them. It comes from the side the film is
     running from: forwards it rises out of its masks and leaves upwards, backwards it comes down into place and leaves
     downwards, so running the film back reads as the same move reversed. Its layers exist only while it is near. A flick
     that is only passing through leaves it down; it comes up when the film slows. */
  let back = false; // the film is running backwards (set from its velocity, with a little hysteresis)
  const rest = (c: Cap) => { if (c.above !== back) { c.above = back; c.el.classList.toggle("is-above", back); } };
  /** the film is about to leave this caption: the page is already past its edge (the picture is only catching up), or at
   *  the film's speed the edge is less than STAY away. A caption raised now would only flash through the frame. */
  const leaving = (c: Cap, F: number) => {
    if (Ft < c.range[0] || Ft > c.range[1]) return true;
    if (Math.abs(vel) < 1.5) return false; // all but still: it stays
    return ((vel > 0 ? c.range[1] : c.range[0]) - F) / vel < STAY;
  };
  function words_(F: number) {
    const fast = Math.abs(vel) > FLICK_WORDS;
    for (const c of caps) {
      const on = F >= c.range[0] && F <= c.range[1];
      const near = on || c.on || !!c.timer || (F >= c.range[0] - NEAR && F <= c.range[1] + NEAR);
      if (near !== c.near) { c.near = near; c.el.classList.toggle("is-near", near); }
      if (near && !c.on && !c.timer) rest(c); // near and unseen: it waits on the side it will come in from
      if (on === c.on || (on && (fast || leaving(c, F)))) continue;
      c.on = on;
      clearTimeout(c.timer);
      c.timer = 0;
      c.el.classList.remove("is-out", "is-down", "is-back");
      if (on) { c.el.classList.add("is-on"); c.onAt = performance.now(); }
      else if (!stepped && performance.now() - c.onAt < QUICK) {
        // it had barely begun to rise: its words go back into the masks they came from, quickly, rather than carrying on
        // through the frame and out of the other side
        c.el.classList.remove("is-on"); c.el.classList.add("is-back");
        c.timer = window.setTimeout(() => { c.timer = 0; c.el.classList.remove("is-back"); drifts(c, ""); rest(c); }, 420);
      } else {
        c.el.classList.remove("is-on"); c.el.classList.add("is-out"); c.el.classList.toggle("is-down", back);
        c.timer = window.setTimeout(() => { c.timer = 0; c.el.classList.remove("is-out", "is-down"); drifts(c, ""); rest(c); }, 650);
      }
    }
  }
  let railOn = -2, cueGone: boolean | null = null, told: boolean | null = null;
  function chrome(F: number) {
    let sec = 0;
    SECTIONS.forEach((s, i) => { if (F >= s.from - 0.6) sec = i; });
    const t = F > 1.2; // the rail shows once the story has begun
    if (t !== told) { told = t; track.classList.toggle("is-told", t); }
    const r = sec - 1;
    if (r !== railOn) {
      railOn = r;
      rail.forEach((b, i) => { b.classList.toggle("is-on", i === r); b.setAttribute("aria-current", i === r ? "step" : "false"); });
      if (glide) { glide.style.setProperty("--at", String(Math.max(0, r))); glide.classList.toggle("is-off", r < 0); }
    }
    const gone = F > 0.04;
    if (gone !== cueGone) { cueGone = gone; cue?.classList.toggle("is-gone", gone); }
  }

  /* ---------------- long jumps cut under a short dip through black */
  let dipping = false;
  function cut(y: number) {
    if (dipping) return;
    dipping = true;
    dip.style.transition = "opacity .22s ease";
    dip.style.opacity = "1";
    setTimeout(() => {
      scrollTo(0, y);
      land();
      frameNow();
      dip.style.transition = "opacity .45s ease";
      dip.style.opacity = "0";
      setTimeout(() => { dipping = false; }, 460);
    }, 230);
  }
  /** to y (document px): a short way scrolls there natively, a long way cuts under the dip */
  function go(y: number) {
    const to = Math.max(0, Math.min(document.documentElement.scrollHeight - innerHeight, y));
    if (Math.abs(to - scrollY) > JUMP * innerHeight && !stepped) cut(to);
    else scrollTo({ top: to, behavior: stepped ? "instant" : "smooth" });
  }
  // in-page links (the nav, the footer, the skip link) that go a long way cut there; a short way is the browser's own
  // smooth scroll (handover.ts jumpTo)
  setScroller((el) => {
    const y = el.getBoundingClientRect().top + scrollY - 88;
    if (stepped || Math.abs(y - scrollY) <= JUMP * innerHeight) return false;
    cut(Math.max(0, y));
    return true;
  });

  /** the last beat at or before F */
  function nearBeat(F: number) {
    let k = 0;
    for (let i = 1; i < N; i++) if (BEATS[i].F <= F) k = i;
    return k;
  }

  /* ---------------- the picture follows the page: Ft is where the page is, Fs what is drawn. While a wheel or a trackpad
     moves the page (its events at most WHEEL_HOLD old), Fs follows Ft through a critically damped spring, stepped exactly
     for the frame's real length (the same curve at 60, 90 or 120 Hz, and after a dropped frame); it lands on Ft once the
     rest is too small to see. A gesture from rest starts from rest too (seeding the spring's velocity answers a frame
     sooner, but it doubled the unevenness of a wheel's first notch when measured). Anything else that moves the page (a
     finger, the keys, the scrollbar, a link's smooth scroll, find in page) draws it as it is: Fs is Ft (a wheel's last
     sub-pixel tail may finish while the page is still). So do reduced motion, the frame after a cut or a resize, and a page
     suddenly far away (LEAP). */
  let Ft = 0, Fs = 0, Fv = 0, FtWas = 0;
  let hard = true; // the next frame lands on the page's position
  let wheelAt = -1e9; // when the last wheel event came (a passive listener: it only notes the time)
  function follow(dt: number) {
    const d = Fs - Ft, moved = Ft !== FtWas;
    FtWas = Ft;
    const wheeled = performance.now() - wheelAt < WHEEL_HOLD;
    if (hard || stepped || Math.abs(d) > LEAP || (moved && !wheeled)) {
      if (hard || Math.abs(d) > LEAP) velF = -1; // a cut is not a movement: it lifts nothing
      hard = false; Fs = Ft; Fv = 0;
      return;
    }
    if (dt <= 0 || (d === 0 && Fv === 0)) return;
    const e = Math.exp(-RATE * dt), k = (Fv + RATE * d) * dt;
    Fs = Ft + (d + k) * e;
    Fv = (Fv - RATE * k) * e;
    if (Math.abs(Fs - Ft) < 0.0006 && Math.abs(Fv) < 0.02) { Fs = Ft; Fv = 0; } // under half a pixel of scroll, and still
  }

  /* ---------------- the frame: the film where the picture is. dt: the frame's length in seconds (0 when called outside
     the loop: nothing moves by itself then) */
  let raf = 0, lastF = -1, lastE = -1;
  /** past the film's end: 0 to 1 over the screen in which the view is carried away (the world recedes into the dark) */
  let exitE = 0;
  function frameNow(dt = 0): number {
    const raw = scrollY;
    Ft = clamp((raw - top) / S, 0, FILM_END);
    load(Ft);
    follow(dt);
    const F = Fs;
    if (dt > 0) speed(F, dt);
    exitE = stepped ? 0 : clamp((raw - top - FILM_END * S) / vh);
    if (F !== lastF || exitE !== lastE || px !== tx || py !== ty) { lastF = F; lastE = exitE; apply(F); chrome(F); }
    words_(F);
    leaks(F);
    touches(F, dt);
    const inFilm = raw >= top - 32 && raw + 32 < top + len;
    if (inFilm !== night) {
      night = inFilm;
      setNight("story", inFilm, inFilm);
      document.documentElement.classList.toggle("s-film-on", inFilm); // site.css: the nav over the film is plain ink (no blur over video)
    }
    const k = nearBeat(F), b = String(k + (F - BEATS[k].F > 0.02 ? 0.5 : 0));
    if (track.dataset.beat !== b) track.dataset.beat = b; // a beat, or half way to the next (for tests)
    return F;
  }

  /* ---------------- the captions' plane: a caption that is up drifts slowly upwards through its stretch of film (a few
     px from where it came in to where it leaves: a layer of its own, nearer than the footage, scrolling past more slowly
     than the page), by the film's position, so it is the same both ways and never floats on by itself. Written on the
     captions that are up (or leaving), nothing else. The film's velocity (film seconds per second, smoothed) says which way
     it is running (the captions' side), whether a flick is passing through, and lets the grain breathe. */
  let vel = 0, velF = -1, lastGrain = "";
  function drifts(c: Cap, ls: string) {
    if (ls === c.lw) return;
    c.lw = ls;
    if (c.inn) c.inn.style.translate = ls;
  }
  function speed(F: number, dt: number) {
    if (stepped) return;
    const inst = velF < 0 ? 0 : (F - velF) / dt;
    velF = F;
    vel += (clamp(inst, -12, 12) - vel) * (1 - Math.exp(-dt / 0.14));
    if (Math.abs(vel) < 0.03) vel = 0; // a lift of under a tenth of a pixel: at rest
    if (vel > 0.05) back = false; else if (vel < -0.05) back = true;
  }
  /** the grain's four positions (cinematic.css s-grain), stepped here while the film moves */
  const GRAIN = ["", "translate(-18px, 12px)", "translate(14px, -20px)", "translate(-8px, -10px)"];
  let grainT = 0, grainAt = 0;
  function touches(F: number, dt: number) {
    if (stepped) return;
    const D = phone ? 10 : 20, a = Math.abs(vel);
    for (const c of caps) {
      if (!c.on && !c.timer) continue;
      const b = Math.min(c.range[1], FILM_END), q = clamp((F - c.range[0]) / Math.max(0.5, b - c.range[0]));
      drifts(c, `0 ${(D * (1 - 2 * q)).toFixed(1)}px`);
    }
    const gr = vel === 0 ? "" : (0.82 + 0.18 * (1 - Math.exp(-a * 0.5))).toFixed(2); // at rest: the CSS's .82
    if (grain && gr !== lastGrain) { lastGrain = gr; grain.style.opacity = gr; }
    if (grain && moving) {
      grainT += dt;
      const at = Math.floor(grainT / 0.125) % 4;
      if (at !== grainAt) { grainAt = at; grain.style.transform = GRAIN[at]; }
    }
  }
  /* while the film moves (.is-moving) the grain is stepped here, in the frame already being written, instead of by its CSS
     animation (cinematic.css); the footage's breath runs on regardless */
  let moving = false;
  function setMoving(m: boolean) {
    if (m === moving) return;
    moving = m;
    track.classList.toggle("is-moving", m);
    if (!m && grain && grainAt) { grainAt = 0; grainT = 0; grain.style.transform = ""; } // back to the CSS animation's own steps
  }
  let lastT = 0;
  function frame(now: number) {
    raf = 0;
    const dt = Math.min(0.064, lastT ? Math.max(0.001, (now - lastT) / 1000) : 1 / 60);
    lastT = now;
    if (px !== tx || py !== ty) {
      const k = 1 - Math.exp(-dt * 6);
      px += (tx - px) * k; py += (ty - py) * k;
      if (Math.abs(tx - px) < 0.001) px = tx;
      if (Math.abs(ty - py) < 0.001) py = ty;
      lastF = -1;
    }
    frameNow(dt);
    // the loop runs on a moment after the last scroll event, so a finger's scroll has a velocity too (the captions' side)
    const film = vel !== 0 || Fs !== Ft || now - scrolledAt < 140;
    setMoving(film && !stepped); // reduced motion: nothing travels, so nothing is "moving"
    if (film || px !== tx || py !== ty) raf = requestAnimationFrame(frame);
    else { lastT = 0; velF = -1; warmAhead(); }
  }
  const kick = () => { if (!raf) raf = requestAnimationFrame(frame); };
  /** land on the page's position now (after a cut, a resize, keyboard focus): no easing towards it */
  const land = () => { hard = true; lastF = -1; };
  let scrolledAt = -1e9;
  const onScroll = () => { scrolledAt = performance.now(); SceneVideo.quietAt = scrolledAt + 450; kick(); };
  const onResize = () => { measure(); land(); kick(); };
  // passive, and they only note the time: what moves the page is never touched. A key or a press (the scrollbar, a
  // middle-click scroll) means the next movement is not the wheel's.
  const onWheel = () => { wheelAt = performance.now(); };
  const notWheel = () => { wheelAt = -1e9; };
  const onPointer = (e: PointerEvent) => {
    if (e.pointerType !== "mouse") return;
    if (stepped || phone) return;
    tx = (e.clientX / vw) * 2 - 1; ty = (e.clientY / vh) * 2 - 1;
    if (lastWorldVis) kick();
  };
  // the chapter rail: to a chapter's first beat (a long way: the cut)
  const onRail = (e: Event) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>(".s-rail button");
    if (!b) return;
    const s = SECTIONS.find((x) => x.id === b.dataset.section);
    if (s) go(yOf(s.from));
  };
  // keyboard focus moving into a caption (the last stop's buttons) brings the film to that caption's beat
  const onFocus = (ev: FocusEvent) => {
    const el = (ev.target as HTMLElement).closest?.("[data-cap]") as HTMLElement | null;
    if (!el) return;
    const k = BEATS.findIndex((x) => x.cap === el.dataset.cap);
    if (k >= 0 && Math.abs(scrollY - yOf(BEATS[k].F)) > 4) { scrollTo(0, yOf(BEATS[k].F)); land(); frameNow(); }
  };
  const onVis = () => all.forEach((v) => v.video?.suspend(document.hidden));

  measure();
  frameNow();
  warmTimer = window.setTimeout(warmWorld, 1800); // on the cover, or (a link into the film) just before the push
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", onResize, { passive: true });
  addEventListener("pointermove", onPointer, { passive: true });
  addEventListener("wheel", onWheel, { passive: true });
  addEventListener("keydown", notWheel, { passive: true });
  addEventListener("pointerdown", notWheel, { passive: true });
  track.addEventListener("click", onRail);
  words.addEventListener("focusin", onFocus);
  document.addEventListener("visibilitychange", onVis);
  document.fonts?.ready.then(() => { measure(); land(); kick(); });

  return () => {
    cancelAnimationFrame(raf);
    clearTimeout(warmTimer);
    caps.forEach((c) => clearTimeout(c.timer));
    setScroller(null);
    removeEventListener("scroll", onScroll);
    removeEventListener("resize", onResize);
    removeEventListener("pointermove", onPointer);
    removeEventListener("wheel", onWheel);
    removeEventListener("keydown", notWheel);
    removeEventListener("pointerdown", notWheel);
    track.removeEventListener("click", onRail);
    words.removeEventListener("focusin", onFocus);
    document.removeEventListener("visibilitychange", onVis);
    all.forEach((v) => v.video?.dispose());
    setNight("story", false, false);
    document.documentElement.classList.remove("s-film-on");
  };
}
