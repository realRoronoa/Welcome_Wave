/* The product world's geometry: where each panel floats (x, y, z in CSS px around the screen's centre; ry its turn in
   degrees; w its width), when it emerges from the dark (F, film seconds), the camera's path through the space (keyframes
   in F, landing on the beats of film.ts), and the teal threads (each one a recorded KT moment tied to what it explains).
   Desktop is a 3D set of three stations the camera travels between; phones (narrow screens) fly through a single column of
   panels instead (2.5D). */

export type PanelId = "rec" | "code" | "answer" | "path" | "clip" | "steps" | "detail" | "team" | "report";
export type Place = { x: number; y: number; z: number; ry: number; w: number; in: [number, number] };
export type Cam = { u: number; x: number; y: number; z: number; ry: number; rx: number; hold?: boolean };
export type Layout = { perspective: number; originY: number; panels: Record<PanelId, Place>; cam: Cam[]; floorY: number };

export const DESKTOP: Layout = {
  perspective: 1200,
  originY: 0.57,
  floorY: 470,
  panels: {
    // 4 · knowledge: the code first, on the monitor's screen as the world opens out of it (film.ts ZOOM); the call beside
    // it (beat 5); the answer and the path (beat 6)
    code: { x: 110, y: 10, z: 0, ry: -7, w: 700, in: [21.0, 21.4] },
    rec: { x: -470, y: -130, z: -170, ry: 15, w: 440, in: [22.8, 23.4] },
    answer: { x: -440, y: 235, z: -70, ry: 11, w: 420, in: [24.1, 24.7] },
    path: { x: 420, y: 305, z: -260, ry: -11, w: 600, in: [24.5, 25.1] },
    // 5 · the first contribution, further on and to the right
    clip: { x: 1760, y: -20, z: -1500, ry: 11, w: 680, in: [26.3, 27] },
    steps: { x: 2240, y: -50, z: -1430, ry: -10, w: 400, in: [26.6, 27.2] },
    detail: { x: 2190, y: 265, z: -1330, ry: -8, w: 440, in: [27.1, 27.5] },
    // 6 · the team, and the report in front of it: the last stop
    team: { x: 920, y: -30, z: -3620, ry: 4, w: 1000, in: [30.9, 31.6] },
    report: { x: 1190, y: 150, z: -3280, ry: -8, w: 640, in: [31.7, 32.3] },
  },
  // the camera is already moving as the monitor's window opens (drifting across the code, which keeps growing on the
  // screen with the window), turns into a crane that draws back to reveal the call (never a stop), and creeps through
  // each station (hold)
  cam: [
    { u: 21.0, x: 110, y: 10, z: -540, ry: 0, rx: 0 },    // inside the monitor: the code fills the frame
    { u: 22.3, x: 75, y: 14, z: -420, ry: -1, rx: 0 },    // the window has opened (the code grew with it): the crane begins
    { u: 23.7, x: -40, y: 25, z: 120, ry: -2, rx: 0, hold: true }, // beat: the call and the code
    { u: 25.5, x: -20, y: 45, z: 300, ry: 2, rx: 1, hold: true },  // beat: the answer and the path too
    { u: 26.3, x: 900, y: 30, z: -380, ry: 5, rx: 0 },     // travelling on: the call and the code slide by
    { u: 27.5, x: 1980, y: 10, z: -1000, ry: 7, rx: 0, hold: true }, // beat: the first task
    { u: 30.5, x: 2030, y: 30, z: -1080, ry: 2, rx: 1, hold: true }, // beat: merged
    { u: 31.3, x: 1300, y: 25, z: -1900, ry: -2, rx: 0 },
    { u: 32.5, x: 660, y: 25, z: -2650, ry: -2, rx: 1 },   // beat: the team and the report, the last stop
  ],
};

/** phones: one column, the camera dollying through it; each panel has its moment, centred, then flies past */
const step = 720;
const col = (i: number, y: number, ry: number, inF: number): Place => ({ x: 0, y, z: -i * step, ry, w: 336, in: [inF, inF + 0.4] });
export const PHONE: Layout = {
  perspective: 900,
  originY: 0.62,
  floorY: 420,
  panels: {
    code: col(0, 10, -3, 21.0),
    rec: col(1, 20, 3, 23),
    answer: col(2, 20, -3, 24),
    path: col(3, 20, 3, 24.9),
    clip: col(4, 0, -3, 26),
    steps: col(5, 20, 3, 26.9),
    detail: col(6, 30, -3, 28),
    team: col(7, 0, 2, 30.8),
    report: col(8, 30, -2, 31.8),
  },
  cam: [
    { u: 21.0, x: 0, y: 0, z: 440, ry: 0, rx: 0 },
    { u: 22.3, x: 0, y: 0, z: 300, ry: 0, rx: 0 },
    ...([[22.9, 0], [23.7, 1], [24.6, 2], [25.5, 3], [26.7, 4], [27.5, 5], [30.5, 6], [31.5, 7], [32.5, 8]] as [number, number][])
      .map(([u, i]) => ({ u, x: 0, y: 0, z: -i * step + 70, ry: 0, rx: 0, hold: [23.7, 25.5, 27.5, 30.5].includes(u) })),
  ],
};

/** teal threads: from a recorded moment to what it explains (anchors are [data-anchor] inside the panels). A thread
 *  leaves its panel on one side and enters the other's on one side, so it runs through the gaps between panels; it is
 *  drawn under the panels (never over their words), with only its two end points glowing above them. */
export type Side = "down" | "up" | "left" | "right";
export const THREADS: { from: [PanelId, string]; to: [PanelId, string]; exit: Side; enter: Side; u: [number, number] }[] = [
  { from: ["rec", "moment"], to: ["code", "lines"], exit: "down", enter: "left", u: [23, 23.6] },
  { from: ["code", "lines"], to: ["answer", "source"], exit: "left", enter: "up", u: [24.5, 25.3] },
  { from: ["rec", "moment"], to: ["steps", "step-kt"], exit: "down", enter: "left", u: [25.7, 26.7] }, // across the space, as the camera travels on
];

/** the journey's six steps check off, and its details follow one another (F) */
export const STEP_U = [26.8, 27.05, 27.3, 28.1, 28.9, 29.7];
export const DETAIL_U: [number, number][] = [[27.3, 28.1], [28.1, 28.9], [28.9, 29.7], [29.7, 99]];
export const MERGED: [number, number] = [29.8, 30.1];

export const layout = () => (innerWidth < 900 ? PHONE : DESKTOP);
