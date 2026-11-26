/* The landing film's edit. One shoot (cottonbro studio's tech-office series on Pexels, the same actors throughout;
   public/media/CREDITS.md), cut into shots on a single timeline measured in film seconds ("F"), which is where the page is
   scrolled to: F = scroll / SCROLL_PER_SECOND (in screens). The page scrolls natively.
   The scroll places the composition: which shots show, their dissolves, each shot's camera (keyframes), the captions, the
   push into the monitor and the product world's camera. Every transition is the camera's: a push carried on through a
   dissolve, a pan carried on through a dissolve, and the push into the monitor whose screen becomes the product. The
   footage itself is never driven by the scroll: each clip plays at its own speed while its shot is on screen (from c0) and
   loops (from loopAt, through a dissolve), so a shot never freezes; it pauses once it is off screen. The story's BEATS are its rest points
   (the hero, each scene's held composition, the product world's stations, the last stop): there the camera creeps,
   between them it travels. */
/** screens of scroll per film second */
export const SCROLL_PER_SECOND = 0.7;

/** a camera key: [F, scale, drift x, drift y (% of the frame), hold]. A hold is a beat: the camera creeps through it (about
 *  a third of its speed either side), never stops. Keys keep the drift inside what the scale allows (no edge of the frame
 *  ever shows), so the curve is never clipped. */
export type Key = [number, number, number, number, 1?];
type Rect = [number, number, number, number];

export type Shot = {
  /** file stem: media/welcome-wave/<dir>/<name>-1920.mp4 etc. */
  name: string;
  dir: string;
  /** clip length in seconds */
  dur: number;
  /** "stage": full-bleed footage; "world": a screen inside the 3D product world */
  place: "stage" | "world";
  /** on screen from F u0 to F u1, dissolving in over [u0, u0 + fade] */
  u0: number;
  u1: number;
  fade: number;
  /** the clip's own time it starts from when the shot comes on (it then plays at 1x by itself) */
  c0: number;
  /** where the clip goes back to when it ends (its own time, s; from 0 when absent), through a dissolve (SceneVideo.ts) */
  loopAt?: number;
  /** the camera: keyframes (scale and drift about `origin`), a smooth monotone curve through them that is already moving
   *  at the shot's first key and still moving at its last. Phones held upright take the same move, gentler (camera()). */
  cam: Key[];
  origin: string;
  /** the push into the monitor: one move from its first key to its last that enters moving and keeps accelerating */
  push?: boolean;
  /** where the subject sits (object-position), so every crop keeps them in frame */
  focus: string;
  /** the monitor's screen in the frame (left, top, right, bottom, as fractions of the source), for each cut: the window
   *  the next scene grows out of (ZOOM) */
  monitor?: { wide: Rect; portrait: Rect };
  what: string;
};

export const SHOTS: Shot[] = [
  // the cover: the office, alive (its own loop). The first movement leans in on the new engineer at the right end of the
  // desk, from the first pixel of scroll, gathering speed...
  { name: "hero", dir: "hero", dur: 9, place: "stage", u0: 0, u1: 1.95, fade: 0, c0: 0,
    cam: [[0, 1.02, 0, 0], [1.0, 1.2, -2.5, -1], [1.6, 1.44, -5.5, -2.2], [1.95, 1.66, -8, -3.2]],
    origin: "84% 58%", focus: "62% 50%",
    what: "A wide shot of a tech office: four engineers at a long desk under pendant lamps." },
  // ...and, the push still going, dissolves into him at his desk: one camera move through the cut. The dissolve waits until
  // the push has done its work (he fills much of the wide by then), and is quick, so the two faces are never long over each
  // other. The new shot comes in moving the same way: his face starts to the right, where he sat in the wide, and the
  // camera settles onto him (its drift easing out) into the beat; from there a punch-in onto his face
  { name: "first-day", dir: "first-day", dur: 5.6, place: "stage", u0: 1.5, u1: 6.2, fade: 0.45, c0: 0,
    cam: [[1.5, 1.09, 4, 0.8], [2.0, 1.17, 1.2, 0.2], [2.7, 1.215, -0.9, -0.4], [4.4, 1.245, -2.4, -0.8, 1], [5.2, 1.28, -2.8, -1], [6.2, 1.52, -3.4, -1.7]],
    origin: "50% 34%", focus: "55% 45%",
    what: "The new engineer at his desk, code on the monitor beside him." },
  // the push carries on across the dissolve, close on his face (a cut-in on the same line: the face stays where the eye
  // already is); the slow drift to the left that began in the wide goes on through all three shots
  { name: "first-day-close", dir: "first-day", dur: 8, place: "stage", u0: 5.2, u1: 9.4, fade: 0.65, c0: 0.4, loopAt: 0.4,
    cam: [[5.2, 1.1, -1, 1], [5.85, 1.19, -1.5, 0.6], [8.0, 1.24, -2.2, 0, 1], [8.7, 1.27, -3.2, 0], [9.4, 1.33, -7.5, 0]],
    origin: "54% 38%", focus: "56% 40%",
    what: "Close on his face: he reads the screen, unsure." },
  // he gets up: the close-up pans away (quickening), and the walk dissolves in carrying the same pan, then a long lateral
  // move with him to the planning wall (held there, it loops only the part where he stands at the wall, after his hoodie
  // has swept past the lens (6.4 to 7.4 s): from 8 s, through a dissolve, so he stays at the wall)
  { name: "walking", dir: "walking", dur: 10.2, place: "stage", u0: 8.7, u1: 15.2, fade: 0.6, c0: 3.4, loopAt: 8.0,
    cam: [[8.7, 1.14, 6.5, 0], [9.3, 1.16, 2.5, 0], [11.5, 1.17, -1, 0], [14.0, 1.19, -4, -0.5, 1], [15.2, 1.23, -8, -0.8]],
    origin: "50% 45%", focus: "50% 45%",
    what: "He gets up and walks away from the desk to the planning wall." },
  // the pan carries on into the seniors noticing him, and becomes a push towards the monitor...
  { name: "mentor-notice", dir: "mentor", dur: 9, place: "stage", u0: 14.4, u1: 17.3, fade: 0.6, c0: 1.0,
    cam: [[14.4, 1.1, 3.8, 0], [15.0, 1.115, 2.5, 0], [16.2, 1.14, 0, 0], [17.3, 1.27, -1.5, -1.2]],
    origin: "40% 62%", focus: "66% 45%",
    what: "Two senior engineers stop behind his desk and watch." },
  // ...carried across the cut: one accelerating dolly from here, through the next shot, into the product. The clip is one
  // slow push of its own, from the group to the code on the monitor; held, it loops only its closer half (from 5 s, the
  // senior's hand at the screen), through a dissolve, so it never jumps back out to the group
  { name: "mentor", dir: "mentor", dur: 9, place: "stage", u0: 16.5, u1: 21.5, fade: 0.6, c0: 0, loopAt: 5.0,
    cam: [[16.5, 1.02, 0.7, 0.3], [17.1, 1.07, 0.3, 0.1], [18.3, 1.1, -0.5, 0, 1], [20.0, 1.17, -1.5, 0.3], [21.5, 1.32, -3, 1]],
    origin: "40% 45%", focus: "52% 45%",
    what: "The senior points at the code on the monitor and explains." },
  // the match cut: over his shoulder, the camera pushes into the monitor until the screen becomes the product
  // (the screen holds still in the frame while the clip plays: about 16-72 % across and 19-77 % down; 21-75 % down in the
  // portrait cut, which the screen fills from side to side)
  { name: "product-transition", dir: "product-transition", dur: 6.6, place: "stage", u0: 20.5, u1: 23.3, fade: 0.4, c0: 0.4, push: true,
    cam: [[20.5, 1.16, -2, 0], [23.3, 3.4, 4, 2]], origin: "33% 40%", focus: "38% 45%",
    monitor: { wide: [0.165, 0.19, 0.715, 0.775], portrait: [0, 0.21, 1, 0.75] },
    what: "Over his shoulder: the code on the monitor, closer and closer." },
  // screens inside the product world. First, the recorded explanation itself, playing in the KT panel at the first station
  // (from the senior's hand at the code, where the push into the monitor left it), so the station is never a still
  { name: "mentor", dir: "mentor", dur: 9, place: "world", u0: 22.3, u1: 26.6, fade: 0, c0: 5.0, loopAt: 5.0,
    cam: [], origin: "50% 50%", focus: "50% 45%",
    what: "The recorded KT explanation playing in its panel: the senior's hand at the code on the monitor." },
  { name: "first-contribution", dir: "first-contribution", dur: 10, place: "world", u0: 25.7, u1: 31.7, fade: 0, c0: 0.5,
    cam: [], origin: "50% 50%", focus: "60% 42%",
    what: "Back at the desk, he types with confidence." },
  { name: "team-impact", dir: "team-impact", dur: 10, place: "world", u0: 30.9, u1: 99, fade: 0, c0: 1.0,
    cam: [], origin: "50% 50%", focus: "45% 45%",
    what: "The team gathers round his desk, the senior with notes in hand." },
];

/** the story's sections (anchors, the nav's folio, the chapter rail), each from the film second it starts at */
export const SECTIONS = [
  { id: "home", n: "", title: "", from: 0 },
  { id: "product", n: "01", title: "The first day", from: 4.4 },
  { id: "struggle", n: "02", title: "The struggle", from: 14.0 },
  { id: "mentor", n: "03", title: "The mentor", from: 18.3 },
  { id: "knowledge", n: "04", title: "Knowledge, captured", from: 23.7 },
  { id: "contribution", n: "05", title: "The first contribution", from: 27.5 },
  { id: "impact", n: "06", title: "Business impact", from: 32.5 },
] as const;
/** the film's end (F): a little past the last beat, then the page scrolls on, and over that last screen the product world
 *  recedes into the dark as the view is carried away (signature 4) */
export const FILM_END = 33.7;

/** the beats: where the film rests (F) and whose words are up there */
export const BEATS: { F: number; cap: string | null; section: string }[] = [
  { F: 0, cap: null, section: "home" },              // the cover, over the office (its loop plays)
  { F: 4.4, cap: "product", section: "product" },    // at his desk
  { F: 8.0, cap: "product", section: "product" },    // close on his face
  { F: 14.0, cap: "struggle", section: "struggle" }, // at the planning wall, having walked away
  { F: 18.3, cap: "mentor", section: "mentor" },     // the senior explains at the screen
  { F: 23.7, cap: "knowledge", section: "knowledge" }, // inside the product: the call and the code it explains
  { F: 25.5, cap: "knowledge", section: "knowledge" }, // the answer and the learning path
  { F: 27.5, cap: "contribution", section: "contribution" }, // his first task
  { F: 30.5, cap: "contribution", section: "contribution" }, // reviewed, approved, merged
  { F: 32.5, cap: "impact", section: "impact" },     // the team and the report: the last stop
];

/** each caption is up from shortly before its first beat to shortly after its last (F). None comes up before its chapter is
 *  named: the rail turns at a section's start − 0.6 and the nav's folio at about − 0.66 (a line 46 % down a 70 %-a-second
 *  view), so a caption starts no earlier than its section's start − 0.6 */
export const CAPTION_RANGES: Record<string, [number, number]> = {
  product: [3.6, 8.45], struggle: [13.4, 15.0], mentor: [17.7, 19.5], knowledge: [23.1, 26.2],
  contribution: [27.0, 31.2], impact: [32.0, 99],
};

/** the cover's words leave as the first movement leans in: from the first pixel of scroll (no dead zone), in one gesture */
export const COVER_OUT: [number, number] = [0, 1.0];
/** the light leak that blooms where two shots overlap, and as the monitor's window opens (F) */
export const LEAKS: [number, number][] = [[1.45, 2.0], [5.2, 5.9], [8.7, 9.3], [14.4, 15.0], [16.5, 17.1], [21.35, 22.3]];
/** the zoom-through: while the camera pushes in on the monitor, the product world fades in on its screen (IN), then grows out
 *  of that window until it fills the view (OPEN); the footage under it is gone once it does */
export const ZOOM_IN: [number, number] = [21.0, 21.35];
export const ZOOM_OPEN: [number, number] = [21.35, 22.3];
/** reduced motion: the footage and the world cross-fade instead */
export const WORLD_IN: [number, number] = [22.1, 22.7];
export const STAGE_OUT: [number, number] = [22.5, 23.1];

const ROOT = "/media/welcome-wave";

export type Variant = "1920" | "960" | "portrait";

/** One file per screen: phones held upright get the 9:16 cut for full-bleed footage (540 x 960, framed on the subject),
 *  small or slow screens the 960 px cut, everything else 1920. Screens inside the product world are never full-bleed
 *  (about 700 px at most): they always take the 960 cut. */
export function variant(s: Shot): Variant {
  const w = innerWidth, h = innerHeight;
  if (s.place === "world") return "960"; // a screen in the 3D world is never wider than about 700 px
  if (w < 700 && h > w) return "portrait";
  const c = (navigator as Navigator & { connection?: { effectiveType?: string } }).connection;
  if (c?.effectiveType && /(^|-)(2g|3g)$/.test(c.effectiveType)) return "960";
  return w * Math.min(devicePixelRatio || 1, 2) <= 1100 ? "960" : "1920";
}

export function sources(s: Shot, v: Variant): { src: string; type: string }[] {
  if (v === "portrait") return [{ src: `${ROOT}/mobile/${s.name}-portrait.mp4`, type: "video/mp4" }];
  if (v === "960") return [{ src: `${ROOT}/${s.dir}/${s.name}-960.mp4`, type: "video/mp4" }];
  return [
    { src: `${ROOT}/${s.dir}/${s.name}-1920.mp4`, type: "video/mp4" },
    { src: `${ROOT}/${s.dir}/${s.name}-1920.webm`, type: "video/webm" },
  ];
}

export function posters(s: Shot) {
  return {
    avif: `${ROOT}/posters/${s.name}-1920.avif`,
    avifPortrait: `${ROOT}/posters/${s.name}-portrait.avif`,
    jpg: `${ROOT}/posters/${s.name}-1920.jpg`,
    jpgSmall: `${ROOT}/posters/${s.name}-960.jpg`,
    jpgPortrait: `${ROOT}/posters/${s.name}-portrait.jpg`,
  };
}

/** Posters only: the visitor asked for less data, or for less motion (the film then crossfades between stills). */
export const saveData = () => !!(navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData;
export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
