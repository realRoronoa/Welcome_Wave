/* TerrainScene: the "knowledge terrain" behind the sign-in brand panel (BrandPanel in ./Entry.tsx mounts it).

   A wide swell of contour lines in perspective: one long ridge (the codebase) under slow travelling waves, drawn as a dark
   solid surface with hidden lines, so crests hide what lies behind them. Rows are spaced wider with distance and fade into fog.

   Colour keeps its one meaning. Every line is neutral silver except one fixed patch on the ridge, which is teal: "explained
   in a KT call". The pointer tilts the camera a little and lifts the lines where it rests (brighter and heavier, never a new
   hue); with no pointer that light wanders the open ground. A press on an empty part of the panel sends a silver ring.
   pulseTerrain() (a "ww-terrain-pulse" event on window) sends teal rings out of the explained patch: the form calls it when a
   demo account is chosen.

   Calm next to the form: the waves and the line contrast fade over the ~160 px nearest the form. While a field has focus or a
   value, time runs at ~30 % and the swell at ~50 %, with no idle rings. At most two idle rings per page session, and none after
   the first real interaction. After ~30 s without input it settles into a near-still breath (drawn at ~30 fps).

   Raw WebGL 1, no dependencies, no eval, no network. Lifecycle: new TerrainScene(canvas) / start() / stop() / destroy().
   It runs only while started, the tab is visible and the canvas is on screen; it caps devicePixelRatio at 2 and steps it down
   (then the grid density) when frames run long; the geometry is static, so a frame only updates uniforms; prefers-reduced-motion
   gets one composed still frame. No WebGL, or a lost context: onSupport(false), and the page shows its CSS fallback. */

/** The window event that sends teal rings out of the explained patch. */
export const TERRAIN_PULSE = "ww-terrain-pulse";

/** Show an explanation on the terrain: teal rings leave the explained patch (call it when a demo account is chosen). */
export function pulseTerrain(): void {
  window.dispatchEvent(new Event(TERRAIN_PULSE));
}

export type TerrainOptions = {
  /** Where the pointer is followed (the canvas itself takes no pointer events). Default: the canvas's parent. */
  pointerTarget?: HTMLElement;
  /** Over these the light dims, and a press selects text instead of sending a ring. */
  quiet?: string;
  maxDpr?: number;
  /** false: no WebGL, or the context was lost (show the CSS fallback); true: drawing again. */
  onSupport?: (ok: boolean) => void;
};

type Vec2 = [number, number];
type Vec3 = [number, number, number];
type Ring = { x: number; z: number; age: number; s: number; kt: boolean };
type Part = { vbo: WebGLBuffer; ibo: WebGLBuffer; n: number; quad: boolean; mode: number; shader: number; gain: number };
type View = {
  fov: number; eye: Vec3; look: Vec3;
  /** pointer parallax: eye x, eye y, look x, look y */
  par: [number, number, number, number];
  fog: Vec2;
  /** the explained patch: a fixed point (x, z) on the ridge's near flank, beside the copy, clear of the calm edge */
  kt: Vec2;
  /** the idle light's path over the open ground, in NDC, by drift clock */
  drift: (w: number) => Vec2;
  /** the calm edge toward the form: NDC direction, width in CSS px, depth (1 = full calm) */
  edge: [number, number, number, number];
};

const RINGS = 5;
const RING_LIFE = 4.2;
const SETTLE_AFTER = 30; // seconds without pointer or keys
const IDLE_RINGS = 2; // per page session
// the land: one long ridge from the near right into the far left (keep in step with land() in the vertex shader)
const RIDGE_A: Vec2 = [3.4, -2.5];
const RIDGE_AB: Vec2 = [-9, -22];

/** Tall panel (beside the form): a high three-quarter view of the ridge, the form to the right. */
const TALL: View = {
  fov: 40, eye: [0.4, 2.2, 6.0], look: [0.6, -0.3, -6], par: [0.5, 0.18, 0.3, 0.1], fog: [4, 27],
  kt: [1.55, -4.35],
  drift: (w) => [0.3 + 0.15 * Math.sin(w * 0.1), -0.68 + 0.14 * Math.sin(w * 0.13 + 1)],
  edge: [1, 0, 160, 1],
};
/** Wide band (phones, above the form): low and long, the terrain reads as a horizon strip; the form is below. */
const WIDE: View = {
  fov: 34, eye: [0, 1.3, 6.5], look: [0.8, 0.85, -6], par: [0.4, 0.1, 0.25, 0.06], fog: [4, 30],
  kt: [3.52, -1.66],
  drift: (w) => [0.45 + 0.35 * Math.sin(w * 0.1), -0.45 + 0.15 * Math.sin(w * 0.13 + 1)],
  edge: [0, -1, 56, 0.5],
};

/** Shared by every scene on the page: idle rings are counted per page session, and stop for good once someone interacts. */
const session = { idleRings: 0, interacted: false };

const NOT_TEXT = new Set(["hidden", "checkbox", "radio", "button", "submit", "reset", "image", "file", "range", "color"]);
const isField = (el: Element | null): el is HTMLInputElement | HTMLTextAreaElement =>
  (el instanceof HTMLInputElement && !NOT_TEXT.has(el.type)) || el instanceof HTMLTextAreaElement;

const UNIFORMS = ["uVP", "uEye", "uViewport", "uTime", "uAmp", "uLand", "uReveal", "uMode", "uLineW", "uDotPx", "uZFront",
  "uZBack", "uHalfW", "uFog", "uHover", "uKt", "uEdge", "uRing", "uRingKt", "uGain"] as const;
type Uniforms = Record<(typeof UNIFORMS)[number], WebGLUniformLocation | null>;

const VERTEX = `
  precision highp float;
  attribute vec2 aPos;          // a point on the ground plane: (x, z) in world units
  attribute float aSide;        // line quads: -1 / +1 across the line (0 for the surface and the dots)
  uniform mat4 uVP;
  uniform vec3 uEye;
  uniform vec2 uViewport;       // drawing buffer, device px
  uniform float uTime, uAmp, uLand, uReveal, uMode, uLineW, uDotPx, uZFront, uZBack, uHalfW;
  uniform vec2 uFog;            // view depth where the fog starts and where it is total
  uniform vec3 uHover;          // the light (silver): x, z, strength
  uniform vec3 uKt;             // the explained patch (teal): x, z, strength
  uniform vec4 uEdge;           // calm toward the form: NDC direction, width in device px, depth
  uniform vec4 uRing[${RINGS}];       // rings: x, z, age in seconds, strength
  uniform float uRingKt[${RINGS}];    // 1: a teal ring from the explained patch; 0: a silver one
  varying float vA, vT, vEdge, vHalf, vMode;

  float land(vec2 p) {
    vec2 a = vec2(${RIDGE_A[0].toFixed(2)}, ${RIDGE_A[1].toFixed(2)}), ab = vec2(${RIDGE_AB[0].toFixed(2)}, ${RIDGE_AB[1].toFixed(2)});
    float t = clamp(dot(p - a, ab) / dot(ab, ab), 0.0, 1.0);
    vec2 q = p - (a + ab * t);
    return uLand * 1.45 * exp(-dot(q, q) * 0.3) * (0.62 + 0.38 * sin(t * 8.5 + 0.4));
  }

  // four slow travelling waves over a gently warped plane, at unit amplitude
  float waves(vec2 p) {
    vec2 q = p + 0.55 * vec2(sin(p.y * 0.42 + uTime * 0.21), cos(p.x * 0.37 - uTime * 0.17));
    return 0.28 * sin(dot(q, vec2(0.50, 0.36)) + uTime * 0.48)
         + 0.17 * sin(dot(q, vec2(-0.41, 0.66)) - uTime * 0.39 + 1.3)
         + 0.08 * sin(dot(q, vec2(1.05, -0.83)) + uTime * 0.77 + 2.1)
         + 0.035 * sin(dot(q, vec2(-1.7, -1.3)) + uTime * 1.1);
  }

  float swell(vec2 p, float amp) { return waves(p) * amp + land(p); }

  // the surface with the light, the explained patch and the rings; lift gets the silver light, k the teal
  float relief(vec2 p, float amp, float calm, out float lift, out float k) {
    float h = swell(p, amp);
    vec2 d = p - uHover.xy;
    float r2 = dot(d, d), wave = sin(sqrt(r2) * 5.0 - uTime * 2.6);
    h += calm * uHover.z * (0.42 * exp(-r2 * 0.9) + 0.07 * wave * exp(-r2 * 0.14));
    lift = uHover.z * (exp(-r2 * 1.1) + 0.35 * exp(-r2 * 0.16) * (0.5 + 0.5 * wave));
    vec2 e = p - uKt.xy;
    float q2 = dot(e, e), ripple = sin(sqrt(q2) * 4.5 - uTime * 1.6);
    h += uKt.z * (0.16 * exp(-q2 * 1.3) + 0.035 * ripple * exp(-q2 * 0.3));
    k = uKt.z * (exp(-q2 * 1.5) + 0.3 * exp(-q2 * 0.32) * (0.5 + 0.5 * ripple));
    for (int i = 0; i < ${RINGS}; i++) {
      vec4 g = uRing[i];
      float f = g.w * exp(-pow((length(p - g.xy) - g.z * 3.0) * 2.2, 2.0));
      h += 0.24 * f * calm;
      if (uRingKt[i] > 0.5) k += f; else lift += f;
    }
    return h;
  }

  void main() {
    vec2 p = aPos;
    // how close this point lands to the canvas edge that meets the form: 1 at the edge, 0 past uEdge.z device px
    vec4 c0 = uVP * vec4(p.x, land(p), p.y, 1.0);
    float span = abs(uEdge.x) * uViewport.x + abs(uEdge.y) * uViewport.y;
    float near = 1.0 - smoothstep(0.15 * uEdge.z, uEdge.z, (1.0 - dot(c0.xy / c0.w, uEdge.xy)) * 0.5 * span);
    float calm = 1.0 - 0.85 * uEdge.w * near;      // wave amplitude: 15 % at the edge
    float contrast = 1.0 - 0.7 * uEdge.w * near;   // line contrast: 30 % at the edge
    float amp = uAmp * calm;

    float lift, k, lift2, k2;
    vec3 w = vec3(p.x, relief(p, amp, calm, lift, k), p.y);
    float e = 0.12, h0 = swell(p, amp);
    vec3 n = normalize(vec3(-(swell(p + vec2(e, 0.0), amp) - h0) / e, 1.0, -(swell(p + vec2(0.0, e), amp) - h0) / e));
    // low light from beyond the ridge: faces turned toward the viewer catch a glint
    float spec = pow(clamp(dot(reflect(-normalize(vec3(-0.3, 0.38, -0.87)), n), normalize(uEye - w)), 0.0, 1.0), 14.0);
    float diff = clamp(dot(n, normalize(vec3(-0.35, 0.75, 0.55))), 0.0, 1.0);
    float s = min(lift, 1.2);
    vT = clamp(k, 0.0, 1.0);
    vMode = uMode;

    vec4 clip = uVP * vec4(w, 1.0);
    float depth = clip.w;
    vEdge = 0.0; vHalf = 0.0;
    if (uMode < 0.5) {
      // a row: widen it into a quad across its screen direction; heavier near the viewer and where it is lit
      vec4 ahead = uVP * vec4(p.x + 0.06, relief(p + vec2(0.06, 0.0), amp, calm, lift2, k2), p.y, 1.0);
      vec2 hv = 0.5 * uViewport;
      vec2 dir = normalize(ahead.xy / ahead.w * hv - clip.xy / clip.w * hv);
      float width = uLineW * clamp(6.5 / depth, 0.6, 1.45) * (1.0 + 0.55 * s + 0.8 * vT);
      float reach = 0.5 * width + 1.0;
      clip.xy += vec2(-dir.y, dir.x) * aSide * reach / hv * depth;
      vEdge = aSide * reach;
      vHalf = 0.5 * width;
    }
    gl_Position = clip;
    if (uMode < 1.5) gl_Position.z -= 0.0006 * depth; // lines and dots sit a hair in front of their surface
    gl_PointSize = clamp(uDotPx / depth * (1.0 + 0.9 * s + 1.2 * vT), 1.0, 10.0);

    float fog = 1.0 - smoothstep(uFog.x, uFog.y, depth);
    float side = 1.0 - smoothstep(uHalfW * 0.6, uHalfW, abs(p.x));
    float z01 = (uZFront - p.y) / (uZFront - uZBack);
    float reveal = clamp((uReveal * 1.3 - z01) / 0.3, 0.0, 1.0);
    float lit = 0.16 + 0.42 * diff * diff + 0.95 * spec;
    vA = fog * fog * side * reveal * contrast * mix(0.7, 1.0, smoothstep(3.0, 7.0, depth))
       * (uMode > 1.5 ? 0.022 + 0.05 * diff + 0.05 * spec + 0.06 * s + 0.16 * vT : lit + 1.1 * s + 1.5 * vT);
  }`;

const FRAGMENT = `
  precision mediump float;
  uniform float uGain;
  varying float vA, vT, vEdge, vHalf, vMode;
  void main() {
    float a = vA * uGain;
    if (vMode < 0.5) a *= clamp(vHalf + 0.5 - abs(vEdge), 0.0, 1.0) * min(1.0, 2.0 * vHalf); // coverage across the line
    else if (vMode < 1.5) { vec2 c = gl_PointCoord * 2.0 - 1.0; a *= 1.0 - smoothstep(0.2, 1.0, dot(c, c)); }
    vec3 col = mix(vec3(0.9, 0.905, 0.915), vec3(0.176, 0.831, 0.749), vT); // silver; teal only for the explained patch
    gl_FragColor = vec4(col * a, 1.0);
  }`;

export class TerrainScene {
  /** False when WebGL is missing: nothing will draw, and onSupport(false) has been called. */
  readonly supported: boolean;

  private readonly canvas: HTMLCanvasElement;
  private readonly pointerTarget: HTMLElement;
  private readonly quiet: string;
  private readonly onSupport?: (ok: boolean) => void;
  private readonly motion: MediaQueryList;
  private reduced: boolean;
  private dprCap: number;
  private density: number;

  private gl: WebGLRenderingContext | null = null;
  private prog: WebGLProgram | null = null;
  private u = {} as Uniforms;
  private aPos = 0;
  private aSide = 0;
  private parts: Part[] = [];
  private vbo: WebGLBuffer | null = null;
  private qbo: WebGLBuffer | null = null;
  private built = 0;
  private grid = { x0: -12, x1: 12, zf: 4, zb: -30 };

  private wanted = false;
  private raf = 0;
  private visible = !document.hidden;
  private onscreen = true;
  private frames: number[] = [];
  private tuned = false;
  private dpr = 1;
  private aspect = 0;
  private fov = 0;
  private eye: Vec3 = [0, 0, 0];
  private axes: [Vec3, Vec3, Vec3] | null = null;
  private kt: Vec2 = TALL.kt;
  private readonly proj = new Float32Array(16);
  private readonly view = new Float32Array(16);
  private readonly vp = new Float32Array(16);
  private readonly ringData = new Float32Array(4 * RINGS);
  private readonly ringKt = new Float32Array(RINGS);
  private ro?: ResizeObserver;
  private io?: IntersectionObserver;
  private fieldTimer = 0;

  private s = {
    t: 14, wall: 0, drift: 0, last: 0, intro: 0, amp: 0, land: 0.35,
    calm: 0, calmOn: false, settle: 0, lastInput: 0, poll: 0, flare: 0, stillKt: false,
    cam: { x: 0, y: 0 },
    ptr: { x: 0, y: 0, inside: false, quiet: false, at: -1e9 },
    hover: { x: 1.5, z: 1, s: 0 },
    rings: [] as Ring[],
    queue: [] as { at: number; s: number }[],
    nextAuto: 7,
  };

  constructor(canvas: HTMLCanvasElement, options: TerrainOptions = {}) {
    this.canvas = canvas;
    this.pointerTarget = options.pointerTarget ?? canvas.parentElement ?? canvas;
    this.quiet = options.quiet ?? "p, h1, h2, li, a, button, input, .en-proof";
    this.onSupport = options.onSupport;
    this.motion = matchMedia("(prefers-reduced-motion: reduce)");
    this.reduced = this.motion.matches;
    this.s.intro = this.reduced ? 1 : 0;
    const nav = navigator as Navigator & { deviceMemory?: number };
    const weak = (nav.hardwareConcurrency || 8) <= 4 || (nav.deviceMemory || 8) <= 4;
    this.dprCap = Math.min(options.maxDpr ?? 2, weak ? 1.5 : 2);
    this.density = weak ? 0.7 : 1;
    this.supported = this.init();
    if (!this.supported) { this.gl = null; this.onSupport?.(false); return; }
    this.bind();
    this.readFields();
    this.resize();
  }

  /* ---------- GL setup ---------- */
  private init(): boolean {
    // no MSAA: lines antialias themselves in the fragment shader, dots are soft discs, the surface is near black
    const gl = this.canvas.getContext("webgl", { antialias: false, alpha: false, depth: true, stencil: false,
      premultipliedAlpha: false, preserveDrawingBuffer: false, powerPreference: "low-power" });
    if (!gl || gl.isContextLost()) return false;
    this.gl = gl;
    const info = gl.getExtension("WEBGL_debug_renderer_info");
    const renderer = String(info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    if (/swiftshader|llvmpipe|software/i.test(renderer)) { this.dprCap = 1; this.density = 0.6; } // no GPU: start small

    const prog = gl.createProgram();
    if (!prog) return false;
    for (const [type, src] of [[gl.VERTEX_SHADER, VERTEX], [gl.FRAGMENT_SHADER, FRAGMENT]] as const) {
      const sh = gl.createShader(type);
      if (!sh) return false;
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) { console.warn(gl.getShaderInfoLog(sh)); return false; }
      gl.attachShader(prog, sh);
      gl.deleteShader(sh);
    }
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { console.warn(gl.getProgramInfoLog(prog)); return false; }
    this.prog = prog;
    gl.useProgram(prog);
    const u = {} as Uniforms;
    for (const name of UNIFORMS) u[name] = gl.getUniformLocation(prog, name);
    this.u = u;
    this.aPos = gl.getAttribLocation(prog, "aPos");
    this.aSide = gl.getAttribLocation(prog, "aSide");
    // hidden lines: the surface writes depth (pushed back a hair) before the lines and dots test against it
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.polygonOffset(2, 2);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE); // additive light on black
    gl.clearColor(0, 0, 0, 1);
    this.vbo = null;
    this.buildGrid(this.built || this.density);
    return true;
  }

  /** The ground plane. Rows are spaced wider with distance (log), so near and far read as one even field.
      One buffer of grid points feeds the surface and the dots; a second (each point twice, one per side) the line quads.
      Index lists run front to back, so nearer geometry lands first and hides what is behind it. */
  private buildGrid(density: number): void {
    const gl = this.gl;
    if (!gl) return;
    this.built = density;
    const g = this.grid;
    const cols = Math.round(220 * density), rows = Math.round(120 * density);
    const s0 = 4, lnr = Math.log(1 + (g.zf - g.zb) / s0);
    const pts = new Float32Array(cols * rows * 2), quads = new Float32Array(cols * rows * 6);
    for (let j = 0, o = 0; j < rows; j++) {
      const z = g.zf - (Math.exp((j / (rows - 1)) * lnr) - 1) * s0;
      for (let i = 0; i < cols; i++, o++) {
        const x = g.x0 + (g.x1 - g.x0) * (i / (cols - 1));
        pts[o * 2] = x; pts[o * 2 + 1] = z;
        quads.set([x, z, -1, x, z, 1], o * 6);
      }
    }
    const at = (i: number, j: number) => j * cols + i;
    const fill: number[] = [], lines: number[] = [], dots: number[] = [];
    for (let j = 0; j < rows; j++) {
      for (let i = 0; i < cols - 1; i++) {
        const a = at(i, j), b = at(i + 1, j);
        lines.push(2 * a, 2 * a + 1, 2 * b, 2 * b, 2 * a + 1, 2 * b + 1);
        if (j < rows - 1) { const c = at(i, j + 1), d = at(i + 1, j + 1); fill.push(a, c, b, b, c, d); }
      }
      for (let i = j % 2 ? 2 : 0; i < cols; i += 4) dots.push(at(i, j));
    }
    this.disposeBuffers();
    const buf = (target: number, data: BufferSource): WebGLBuffer => {
      const b = gl.createBuffer();
      if (!b) throw new Error("WebGL buffer");
      gl.bindBuffer(target, b);
      gl.bufferData(target, data, gl.STATIC_DRAW);
      return b;
    };
    const vbo = (this.vbo = buf(gl.ARRAY_BUFFER, pts));
    const qbo = (this.qbo = buf(gl.ARRAY_BUFFER, quads));
    const part = (vb: WebGLBuffer, quad: boolean, mode: number, shader: number, gain: number, idx: number[]): Part =>
      ({ vbo: vb, quad, mode, shader, gain, ibo: buf(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(idx)), n: idx.length });
    this.parts = [
      part(vbo, false, gl.TRIANGLES, 2, 1, fill), // the surface
      part(qbo, true, gl.TRIANGLES, 0, 0.85, lines), // the rows
      part(vbo, false, gl.POINTS, 1, 1.1, dots), // the dots
    ];
  }

  private disposeBuffers(): void {
    const gl = this.gl;
    if (!gl || !this.vbo) return;
    gl.deleteBuffer(this.vbo);
    gl.deleteBuffer(this.qbo);
    for (const p of this.parts) gl.deleteBuffer(p.ibo);
    this.vbo = this.qbo = null;
    this.parts = [];
  }

  /* ---------- lifecycle ---------- */
  private readonly onVisibility = () => { this.visible = !document.hidden; this.sync(); };
  private readonly onMove = (e: PointerEvent) => this.pointer(e, false);
  private readonly onDown = (e: PointerEvent) => this.pointer(e, true);
  private readonly onLeave = () => { this.s.ptr.inside = false; };
  private readonly onLost = (e: Event) => { e.preventDefault(); this.halt(); this.gl = null; this.onSupport?.(false); };
  private readonly onRestored = () => {
    if (!this.init()) return;
    this.onSupport?.(true);
    this.resize();
    this.sync();
  };
  private readonly onMotion = () => { this.reduced = this.motion.matches; this.s.intro = 1; this.sync(); this.draw(); };
  private readonly onPulse = () => this.explain();
  /** Anything that shows someone is here: keeps the field awake, and a press or a key ends the idle rings. */
  private readonly onActivity = (e: Event) => {
    this.s.lastInput = this.s.wall;
    if (e.type !== "pointermove" && e.type !== "wheel") session.interacted = true;
  };
  private readonly onField = (e: Event) => {
    if (e.type === "focusin" && isField(e.target as Element)) session.interacted = true;
    if (e.type === "focusout") { clearTimeout(this.fieldTimer); this.fieldTimer = window.setTimeout(() => this.readFields(), 0); }
    else this.readFields();
  };

  private bind(): void {
    const t = this.pointerTarget, passive = { passive: true } as const;
    document.addEventListener("visibilitychange", this.onVisibility);
    t.addEventListener("pointermove", this.onMove, passive);
    t.addEventListener("pointerdown", this.onDown, passive);
    t.addEventListener("pointerleave", this.onLeave, passive);
    for (const type of ["pointermove", "pointerdown", "keydown", "wheel"]) document.addEventListener(type, this.onActivity, passive);
    for (const type of ["focusin", "focusout", "input", "change", "reset"]) document.addEventListener(type, this.onField, passive);
    window.addEventListener(TERRAIN_PULSE, this.onPulse);
    this.canvas.addEventListener("webglcontextlost", this.onLost);
    this.canvas.addEventListener("webglcontextrestored", this.onRestored);
    this.motion.addEventListener("change", this.onMotion);
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(this.canvas);
    this.io = new IntersectionObserver(([e]) => { this.onscreen = e.isIntersecting; this.sync(); });
    this.io.observe(this.canvas);
  }

  /** Begin: the rows rise in from the front, then the loop runs while the canvas is visible. */
  start(): void {
    if (!this.supported) return;
    this.wanted = true;
    this.sync();
    this.draw();
    this.canvas.setAttribute("data-live", "");
  }

  stop(): void { this.wanted = false; this.halt(); }

  private halt(): void { cancelAnimationFrame(this.raf); this.raf = 0; }

  /** The loop runs only while it is wanted, the tab is visible, the canvas is on screen, motion is allowed and the context lives. */
  private sync(): void {
    const run = this.wanted && this.visible && this.onscreen && !this.reduced && !!this.gl;
    if (run && !this.raf) { this.s.last = 0; this.frames = []; this.raf = requestAnimationFrame(this.frame); }
    else if (!run) this.halt();
  }

  private resize(): void {
    const gl = this.gl;
    if (!gl) return;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(window.devicePixelRatio || 1, this.dprCap);
    const W = Math.round(w * dpr), H = Math.round(h * dpr);
    if (this.canvas.width !== W || this.canvas.height !== H) { this.canvas.width = W; this.canvas.height = H; }
    this.dpr = W / w;
    this.aspect = w / h;
    gl.viewport(0, 0, W, H);
    // the phone band looks along the horizon, where rows crowd together: give it fewer of them
    const density = this.density * (this.wide ? 0.5 : 1);
    if (density !== this.built) this.buildGrid(density);
    if (!this.raf) this.draw();
  }

  destroy(): void {
    this.stop();
    clearTimeout(this.fieldTimer);
    const t = this.pointerTarget;
    document.removeEventListener("visibilitychange", this.onVisibility);
    t.removeEventListener("pointermove", this.onMove);
    t.removeEventListener("pointerdown", this.onDown);
    t.removeEventListener("pointerleave", this.onLeave);
    for (const type of ["pointermove", "pointerdown", "keydown", "wheel"]) document.removeEventListener(type, this.onActivity);
    for (const type of ["focusin", "focusout", "input", "change", "reset"]) document.removeEventListener(type, this.onField);
    window.removeEventListener(TERRAIN_PULSE, this.onPulse);
    this.canvas.removeEventListener("webglcontextlost", this.onLost);
    this.canvas.removeEventListener("webglcontextrestored", this.onRestored);
    this.motion.removeEventListener("change", this.onMotion);
    this.ro?.disconnect();
    this.io?.disconnect();
    const gl = this.gl;
    if (gl) {
      this.disposeBuffers();
      gl.deleteProgram(this.prog);
      // free the context once the canvas has really left the page; React's StrictMode re-runs effects on the same canvas,
      // which must then get a live context back
      const lose = gl.getExtension("WEBGL_lose_context"), canvas = this.canvas;
      window.setTimeout(() => { if (!canvas.isConnected) lose?.loseContext(); }, 0);
    }
    this.gl = null;
  }

  /* ---------- input ---------- */
  private pointer(e: PointerEvent, down: boolean): void {
    const r = this.canvas.getBoundingClientRect(), p = this.s.ptr;
    if (!r.width || !r.height) return;
    p.x = Math.max(-1, Math.min(1, ((e.clientX - r.left) / r.width) * 2 - 1));
    p.y = Math.max(-1, Math.min(1, 1 - ((e.clientY - r.top) / r.height) * 2));
    p.inside = true;
    p.at = this.s.wall;
    p.quiet = !!(e.target as Element | null)?.closest?.(this.quiet);
    session.interacted = true;
    if (down && e.button === 0 && !p.quiet) this.ringAt(e.clientX, e.clientY);
  }

  /** A silver ring from a point on screen (client coordinates): a press on an empty part of the panel. */
  private ringAt(clientX: number, clientY: number): void {
    if (this.reduced || !this.gl) return;
    const r = this.canvas.getBoundingClientRect();
    const [x, z] = this.pick(((clientX - r.left) / r.width) * 2 - 1, 1 - ((clientY - r.top) / r.height) * 2);
    this.ring(x, z, 1, false);
  }

  private ring(x: number, z: number, s: number, kt: boolean): void {
    const rings = this.s.rings;
    if (rings.length >= RINGS) rings.shift();
    rings.push({ x, z, age: 0, s, kt });
  }

  /** The explained patch speaks: it brightens and sends two teal rings outward (a demo account was chosen). */
  explain(): void {
    session.interacted = true;
    if (!this.gl) return;
    if (this.reduced) { this.s.stillKt = true; this.draw(); return; } // one still frame with the ring held
    this.camera();
    this.s.flare = 1;
    this.ring(this.kt[0], this.kt[1], 1, true);
    this.s.queue.push({ at: this.s.wall + 0.55, s: 0.75 });
  }

  /** Calm the field while a text field has focus or holds a value (anywhere on the page). */
  private readFields(): void {
    let on = isField(document.activeElement);
    if (!on) {
      for (const el of document.querySelectorAll("input, textarea")) {
        if (isField(el) && el.value) { on = true; break; }
      }
    }
    this.s.calmOn = on;
  }

  /* ---------- camera ---------- */
  private get wide(): boolean { return this.aspect > 1.35; }
  private get preset(): View { return this.wide ? WIDE : TALL; }

  private camera(): void {
    const s = this.s, P = this.preset;
    const eye: Vec3 = [P.eye[0] + s.cam.x * P.par[0], P.eye[1] - s.cam.y * P.par[1], P.eye[2]];
    const look: Vec3 = [P.look[0] + s.cam.x * P.par[2], P.look[1] - s.cam.y * P.par[3], P.look[2]];
    this.fov = (P.fov * Math.PI) / 180;
    const f = 1 / Math.tan(this.fov / 2), near = 0.5, far = 50, m = this.proj;
    m.fill(0);
    m[0] = f / this.aspect; m[5] = f; m[10] = (far + near) / (near - far); m[11] = -1; m[14] = (2 * far * near) / (near - far);
    const norm = (a: Vec3): Vec3 => { const l = Math.hypot(a[0], a[1], a[2]); return [a[0] / l, a[1] / l, a[2] / l]; };
    const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const Z = norm([eye[0] - look[0], eye[1] - look[1], eye[2] - look[2]]), X = norm(cross([0, 1, 0], Z)), Y = cross(Z, X), v = this.view;
    v.set([X[0], Y[0], Z[0], 0, X[1], Y[1], Z[1], 0, X[2], Y[2], Z[2], 0, -dot(X, eye), -dot(Y, eye), -dot(Z, eye), 1]);
    const o = this.vp;
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = m[r] * v[c * 4] + m[4 + r] * v[c * 4 + 1] + m[8 + r] * v[c * 4 + 2] + m[12 + r] * v[c * 4 + 3];
    }
    this.eye = eye;
    this.axes = [X, Y, Z];
    this.kt = P.kt;
  }

  /** The surface height at (x, z): the same land and swell as the vertex shader (keep the two in step). */
  private height(x: number, z: number): number {
    const s = this.s, t = s.t;
    const [ax, az] = RIDGE_A, [bx, bz] = RIDGE_AB;
    const k = Math.max(0, Math.min(1, ((x - ax) * bx + (z - az) * bz) / (bx * bx + bz * bz)));
    const qx0 = x - (ax + bx * k), qz0 = z - (az + bz * k);
    const land = s.land * 1.45 * Math.exp(-(qx0 * qx0 + qz0 * qz0) * 0.3) * (0.62 + 0.38 * Math.sin(k * 8.5 + 0.4));
    const qx = x + 0.55 * Math.sin(z * 0.42 + t * 0.21), qz = z + 0.55 * Math.cos(x * 0.37 - t * 0.17);
    const h = 0.28 * Math.sin(0.5 * qx + 0.36 * qz + t * 0.48) + 0.17 * Math.sin(-0.41 * qx + 0.66 * qz - t * 0.39 + 1.3)
            + 0.08 * Math.sin(1.05 * qx - 0.83 * qz + t * 0.77 + 2.1) + 0.035 * Math.sin(-1.7 * qx - 1.3 * qz + t * 1.1);
    return h * s.amp + land;
  }

  /** Where a screen point (NDC) meets the surface: march the ray until it dips under the terrain, then refine. */
  private pick(nx: number, ny: number): Vec2 {
    if (!this.axes) this.camera();
    const [X, Y, Z] = this.axes as [Vec3, Vec3, Vec3];
    const tan = Math.tan(this.fov / 2), dx = nx * tan * this.aspect, dy = ny * tan;
    const d: Vec3 = [X[0] * dx + Y[0] * dy - Z[0], X[1] * dx + Y[1] * dy - Z[1], X[2] * dx + Y[2] * dy - Z[2]];
    const l = Math.hypot(d[0], d[1], d[2]), e = this.eye, g = this.grid;
    d[0] /= l; d[1] /= l; d[2] /= l;
    const under = (t: number) => e[1] + d[1] * t < this.height(e[0] + d[0] * t, e[2] + d[2] * t);
    let a = 0.5, b = 0, t = 0.5;
    for (let i = 0; i < 64 && t < 34; i++, t += 0.06 + t * 0.045) { if (under(t)) { b = t; break; } a = t; }
    if (b) { for (let i = 0; i < 8; i++) { const m = (a + b) / 2; if (under(m)) b = m; else a = m; } t = b; }
    else t = d[1] < -0.02 ? Math.min(-e[1] / d[1], 30) : 30;
    return [Math.max(g.x0, Math.min(g.x1, e[0] + d[0] * t)), Math.max(g.zb, Math.min(g.zf, e[2] + d[2] * t))];
  }

  /* ---------- frame ---------- */
  private readonly frame = (now: number): void => {
    this.raf = requestAnimationFrame(this.frame);
    const s = this.s;
    // settled and nothing travelling: a near-still breath needs only ~30 frames a second
    const slow = s.settle > 0.98 && !s.rings.length && !s.queue.length;
    if (slow && s.last && now - s.last < 28) return;
    const dt = s.last ? Math.min(0.05, (now - s.last) / 1000) : 1 / 60;
    if (s.last && !slow) this.tune(now - s.last);
    s.last = now;
    this.step(dt);
    this.draw();
  };

  /** If frames run long on this device, render fewer pixels (2 -> 1.5 -> 1), then a sparser grid. */
  private tune(ms: number): void {
    if (this.tuned) return;
    this.frames.push(ms);
    if (this.frames.length < 90) return;
    const sorted = this.frames.slice(10).sort((a, b) => a - b), median = sorted[sorted.length >> 1];
    this.frames = [];
    if (median < 22) { this.tuned = true; return; }
    if (this.dprCap > 1) { this.dprCap = Math.max(1, this.dprCap - 0.5); this.resize(); }
    else if (this.density > 0.6) { this.density = 0.6; this.resize(); }
    else this.tuned = true;
  }

  private step(dt: number): void {
    const s = this.s, ease = (rate: number) => 1 - Math.exp(-dt * rate);
    s.wall += dt;
    if ((s.poll -= dt) <= 0) { s.poll = 1; this.readFields(); } // a value set by script fires no event
    s.calm += ((s.calmOn ? 1 : 0) - s.calm) * ease(2.5);
    const idle = s.wall - s.lastInput > SETTLE_AFTER ? 1 : 0;
    s.settle += (idle - s.settle) * ease(idle ? 0.35 : 3);
    const pace = (1 - 0.7 * s.calm) * (1 - 0.88 * s.settle); // a field in use: 30 %; settled: 12 %
    s.t += dt * pace;
    s.drift += dt * Math.max(0.15, pace);
    s.intro = Math.min(1, s.intro + dt / 2.4);
    s.flare -= s.flare * ease(1.1);
    this.levels();

    const p = s.ptr, active = p.inside && s.wall - p.at < 4;
    s.cam.x += ((active ? p.x : 0) - s.cam.x) * ease(2.2);
    s.cam.y += ((active ? p.y : 0) - s.cam.y) * ease(2.2);
    this.camera();

    // the light follows the pointer; with no pointer it wanders the open ground beside the copy
    const target = active ? this.pick(p.x, p.y) : this.pick(...this.preset.drift(s.drift));
    const follow = ease(active ? 5 : 0.9), hv = s.hover;
    hv.x += (target[0] - hv.x) * follow;
    hv.z += (target[1] - hv.z) * follow;
    const strength = (active ? (p.quiet ? 0.55 : 1) : 0.7) * (1 - 0.3 * s.calm) * (1 - 0.4 * s.settle);
    hv.s += (strength - hv.s) * ease(active ? 3 : 1.2);

    // now and then, while nobody is here yet, the light sends a gentle silver ring: twice per page session at most
    if (active || s.calmOn) s.nextAuto = Math.max(s.nextAuto, s.wall + 6);
    else if (s.wall > s.nextAuto && !session.interacted && session.idleRings < IDLE_RINGS && s.settle < 0.5) {
      session.idleRings++;
      this.ring(hv.x, hv.z, 0.55, false);
      s.nextAuto = s.wall + 9;
    }
    // the second teal ring of an explanation
    if (s.queue.length && s.wall >= s.queue[0].at) this.ring(this.kt[0], this.kt[1], s.queue.shift()!.s, true);

    for (const r of s.rings) r.age += dt;
    if (s.rings.length && s.rings[0].age >= RING_LIFE) s.rings = s.rings.filter((r) => r.age < RING_LIFE);
  }

  /** Wave amplitude (entrance x breath x calm x settle) and land height, shared by the shader and the picking. */
  private levels(): void {
    const s = this.s, intro = 1 - Math.pow(1 - s.intro, 3);
    s.amp = intro * (0.86 + 0.14 * Math.sin(s.wall * 0.74)) * (1 - 0.5 * s.calm) * (1 - 0.6 * s.settle);
    s.land = 0.35 + 0.65 * intro;
  }

  private draw(): void {
    const gl = this.gl;
    if (!gl || !this.aspect || !this.parts.length) return;
    if (this.reduced) this.still();
    const s = this.s, u = this.u, g = this.grid, P = this.preset, wide = this.wide;
    this.camera();
    this.ringData.fill(0);
    this.ringKt.fill(0);
    s.rings.forEach((r, i) => {
      const life = r.age / RING_LIFE, fade = Math.min(1, r.age / 0.12) * (1 - life) * (1 - life);
      const o = i * 4;
      this.ringData[o] = r.x; this.ringData[o + 1] = r.z; this.ringData[o + 2] = r.age; this.ringData[o + 3] = r.s * fade;
      this.ringKt[i] = r.kt ? 1 : 0;
    });
    const intro = 1 - Math.pow(1 - s.intro, 3);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(this.prog);
    gl.uniformMatrix4fv(u.uVP, false, this.vp);
    gl.uniform3fv(u.uEye, this.eye);
    gl.uniform2f(u.uViewport, this.canvas.width, this.canvas.height);
    gl.uniform1f(u.uTime, s.t);
    gl.uniform1f(u.uAmp, s.amp);
    gl.uniform1f(u.uLand, s.land);
    gl.uniform1f(u.uReveal, intro);
    gl.uniform1f(u.uZFront, g.zf);
    gl.uniform1f(u.uZBack, g.zb);
    gl.uniform1f(u.uHalfW, g.x1);
    gl.uniform2f(u.uFog, P.fog[0], P.fog[1]);
    gl.uniform3f(u.uHover, s.hover.x, s.hover.z, s.hover.s * intro);
    gl.uniform3f(u.uKt, this.kt[0], this.kt[1], intro * (1 + 0.8 * s.flare));
    gl.uniform4f(u.uEdge, P.edge[0], P.edge[1], P.edge[2] * this.dpr, P.edge[3]);
    gl.uniform4fv(u.uRing, this.ringData);
    gl.uniform1fv(u.uRingKt, this.ringKt);
    gl.uniform1f(u.uLineW, this.dpr * (this.dpr > 1.25 ? 0.95 : 1.15) * (wide ? 0.75 : 1));
    gl.uniform1f(u.uDotPx, 8 * this.dpr);
    for (const part of this.parts) {
      gl.bindBuffer(gl.ARRAY_BUFFER, part.vbo);
      gl.enableVertexAttribArray(this.aPos);
      gl.vertexAttribPointer(this.aPos, 2, gl.FLOAT, false, part.quad ? 12 : 8, 0);
      if (part.quad) { gl.enableVertexAttribArray(this.aSide); gl.vertexAttribPointer(this.aSide, 1, gl.FLOAT, false, 12, 8); }
      else { gl.disableVertexAttribArray(this.aSide); gl.vertexAttrib1f(this.aSide, 0); }
      if (part.shader === 2) gl.enable(gl.POLYGON_OFFSET_FILL); else gl.disable(gl.POLYGON_OFFSET_FILL);
      gl.uniform1f(u.uMode, part.shader);
      gl.uniform1f(u.uGain, part.gain * (wide && part.shader < 2 ? 0.6 : 1));
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, part.ibo);
      gl.drawElements(part.mode, part.n, gl.UNSIGNED_SHORT, 0);
    }
  }

  /** Reduced motion: a composed still, the light resting on open ground with one silver ring frozen mid-flight
      (and, once a demo account was chosen, a teal ring held around the explained patch). */
  private still(): void {
    const s = this.s;
    s.intro = 1; s.wall = 2.1; s.t = 14; s.calm = 0; s.settle = 0; s.flare = 0;
    s.cam.x = 0; s.cam.y = 0;
    this.levels();
    this.camera();
    const [x, z] = this.pick(...this.preset.drift(2));
    s.hover = { x, z, s: 0.7 };
    s.rings = [{ x, z, age: 0.9, s: 0.5, kt: false }];
    if (s.stillKt) s.rings.push({ x: this.kt[0], z: this.kt[1], age: 0.55, s: 0.9, kt: true });
  }
}
