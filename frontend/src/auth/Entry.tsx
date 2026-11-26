/** The full-page frame for everything before the workspace: sign-in, sign-up, email check, invitations, errors.
    Wide screens: a black brand panel on the left (the knowledge terrain, a WebGL scene, under the pitch and three value
    lines; without WebGL, a still field of strokes weaving the mark), the form on the right. Phones: one column, a 232 px terrain band above the form. */
import { InputHTMLAttributes, MouseEvent, ReactNode, useEffect, useRef, useState } from "react";
import { bootVisible, hideBoot } from "../boot";
import { focusIn, handedOver, navigateWithTransition, reducedMotion, takeArrival } from "../surface";
import { Logo } from "../ui";
import { pulseTerrain, TerrainScene } from "./terrain";
import "../styles/entry.css";

/** Teal rings from the terrain's explained patch: call it when a demo account is chosen (or dispatch "ww-terrain-pulse"). */
export { pulseTerrain } from "./terrain";

const VALUES: { title: string; text: string; icon: ReactNode }[] = [
  {
    title: "Cited answers",
    text: "Every answer points to the file and line.",
    icon: <path d="M8 9h8M8 13h5M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />,
  },
  {
    title: "Code meets context",
    text: "Each line links to the KT session that explained it.",
    icon: <path d="M16 18l6-6-6-6M8 6l-6 6 6 6" />,
  },
  {
    title: "People stay in charge",
    text: "Managers approve plans and reviews.",
    icon: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4" />,
  },
];

/* The sign-in art where the terrain cannot draw (no WebGL, or a lost context): the intro's crowd (public/boot-field.js), still.
   A field of short strokes, faint and upright, every other row half a step over; where the mark's centre-line runs through
   them they stand up and lean along it, brighter, so a large W is woven out of the crowd; and one ring of the ripple that
   made it is still passing. The W sits in the panel's bottom right corner, clear of the pitch and the proof card, and the
   crowd fades out toward the copy. Built once, here, on a 720 x 960 grid drawn at 1:1 from that corner. Each group is one
   path per brightness step (a few dozen nodes, not a thousand). */
const FIELD_W = 720, FIELD_H = 960, GAP = 24, ROW = GAP * .9;
const LEAN = Math.atan2(12, 42); // the mark's stroke angle from vertical
const W_SCALE = 5, W_X = 692 - 56 * W_SCALE, W_Y = 850 - 53 * W_SCALE; // MARK_LINE's 64-unit grid: ink 452..692 x 640..850
const W_PTS = [[8, 11], [20, 53], [32, 11], [44, 53], [56, 11]].map(([x, y]) => [W_X + x * W_SCALE, W_Y + y * W_SCALE]);
const W_MID = [(W_PTS[0][0] + W_PTS[4][0]) / 2, (W_PTS[0][1] + W_PTS[1][1]) / 2];

type FieldGroup = { key: string; paths: { d: string; a: number }[] };
const FIELD: FieldGroup[] = (() => {
  const groups = new Map<string, Map<number, string[]>>();
  const add = (g: string, a: number, seg: string) => {
    const step = Math.round(Math.min(.6, a) * 40) / 40; // brightness in 1/40 steps
    if (step <= 0) return;
    const m = groups.get(g) ?? new Map<number, string[]>(); groups.set(g, m);
    (m.get(step) ?? m.set(step, []).get(step)!).push(seg);
  };
  for (let r = 0; r * ROW <= FIELD_H + ROW; r++) {
    for (let c = -1; c * GAP <= FIELD_W + GAP; c++) {
      const x = c * GAP + (r % 2 ? GAP / 2 : 0) + 6, y = r * ROW + 4;
      // the nearest of the W's four strokes, and how far
      let d = Infinity, seg = 0;
      for (let i = 0; i < 4; i++) {
        const [ax, ay] = W_PTS[i], [bx, by] = W_PTS[i + 1];
        const t = Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)));
        const di = Math.hypot(x - ax - t * (bx - ax), y - ay - t * (by - ay));
        if (di < d) { d = di; seg = i; }
      }
      const e = d < GAP * 1.6 ? Math.exp(-((d / (GAP * .55)) ** 2)) : 0;
      // the ripple that ran out of the W: one soft ring, a little past its corners; and depth: the crowd is clearest around
      // the W and falls away toward the top left, where the copy is
      const r0 = Math.hypot(x - W_MID[0], y - W_MID[1]);
      const ring = .09 * Math.exp(-(((r0 - 220) / 28) ** 2)), fall = Math.exp(-((r0 / 480) ** 2));
      const len = 6 + e * 8, ang = (seg % 2 ? 1 : -1) * LEAN * Math.min(1, e * 1.4);
      const sx = Math.sin(ang) * len / 2, sy = Math.cos(ang) * len / 2, rise = e * 2;
      const path = `M${(x + sx).toFixed(1)} ${(y - rise - sy).toFixed(1)}L${(x - sx).toFixed(1)} ${(y - rise + sy).toFixed(1)}`;
      if (e > .18) add(`w${seg + 1}`, .16 + e * .5, path);
      else add(ring > .02 ? "ring" : "crowd", .1 * fall + ring * fall + e * .5, path);
    }
  }
  return ["crowd", "ring", "w1", "w2", "w3", "w4"].map((key) => ({
    key, paths: [...(groups.get(key) ?? new Map<number, string[]>())].map(([a, segs]) => ({ a, d: segs.join("") })),
  }));
})();

/** The no-WebGL sign-in art (see FIELD): the crowd settles in, the W's four strokes stand up in the pen's order, left to right,
 *  then the ripple's ring; then it is still. Reduced motion: the still frame. */
export function StrokeField({ className = "" }: { className?: string }) {
  return (
    <svg className={`en-crowd ${className}`} viewBox={`0 0 ${FIELD_W} ${FIELD_H}`} aria-hidden="true" focusable="false">
      {FIELD.map((g) => (
        <g key={g.key} className={`en-crowd-${g.key}`}>
          {g.paths.map((p) => <path key={p.a} d={p.d} strokeOpacity={p.a} />)}
        </g>
      ))}
    </svg>
  );
}

/** Longest the terrain waits for the intro to hand over before it starts anyway. */
const BOOT_WAIT_MS = 8000;

/** The intro still covers the page: its node is there and has not begun its exit (src/boot.ts adds .ww-boot--done and
    fires "ww-boot-done" together). Not bootVisible(): that turns false as soon as hideBoot() is called, seconds before
    the intro actually leaves, and StrictMode re-runs this effect after EntryLayout's hideBoot(). */
function introCovering() {
  const el = document.getElementById("ww-boot");
  return bootVisible() || (!!el && !el.classList.contains("ww-boot--done"));
}

export function BrandPanel() {
  const panel = useRef<HTMLElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [webgl, setWebgl] = useState(true);

  useEffect(() => {
    const el = canvas.current, host = panel.current;
    if (!el || !host) return;
    let scene: TerrainScene;
    try {
      scene = new TerrainScene(el, { pointerTarget: host, onSupport: setWebgl });
    } catch {
      setWebgl(false);
      return;
    }
    // the intro plays first: the terrain rises in as it hands over, and draws nothing underneath it
    let timer = 0, pulse = 0;
    const go = () => {
      removeEventListener("ww-boot-done", go); clearTimeout(timer); scene.start();
      // arrived from the landing page's "Log in": the explained patch answers once, as the panel settles
      if (handedOver(5000)) {
        window.__wwHandover = undefined;
        if (!reducedMotion()) pulse = window.setTimeout(pulseTerrain, 700);
      }
    };
    if (introCovering()) {
      addEventListener("ww-boot-done", go);
      timer = window.setTimeout(go, BOOT_WAIT_MS);
    } else go();
    return () => { removeEventListener("ww-boot-done", go); clearTimeout(timer); clearTimeout(pulse); scene.destroy(); };
  }, []);

  return (
    <aside ref={panel} className={webgl ? "en-brand" : "en-brand no-gl"} aria-label="About Welcome Wave">
      <canvas ref={canvas} className="en-scene" aria-hidden="true" />
      {!webgl && <StrokeField />}
      <div className="en-scrim" aria-hidden="true" />
      <p className="en-wordmark"><a href="/" onClick={goHome} aria-label="Welcome Wave home page"><Logo size={30} /> Welcome Wave</a></p>
      <div className="en-pitch">
        <p className="en-eyebrow">Onboarding for software teams</p>
        <h2 className="en-headline">Learn the codebase from the people who built it.</h2>
        <ul className="en-values">
          {VALUES.map((v, i) => (
            <li key={v.title} style={{ animationDelay: `${180 + i * 110}ms` }}>
              <span className="en-value-icon">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{v.icon}</svg>
              </span>
              <span><strong>{v.title}</strong>{v.text}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="en-proof" aria-hidden="true">
        <span className="en-proof-q">Where are admission policies validated?</span>
        <span className="en-proof-chips">
          <span className="en-chip">engine/validate.py:42</span>
          <span className="en-chip kt">KT session · 12:04</span>
        </span>
      </div>
    </aside>
  );
}

/** Back to the public site, in this tab and without a reload (a cross-fade where View Transitions exist). */
function goHome(e: MouseEvent<HTMLAnchorElement>) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigateWithTransition("/", ".site");
}

/** The tab's title for a heading: the sign-in form's two modes get short names, any other screen its own heading. */
function tabTitle(title: ReactNode) {
  if (title === "Sign in to Welcome Wave") return "Sign in · Welcome Wave";
  if (title === "Create your account") return "Create account · Welcome Wave";
  return typeof title === "string" ? `${title} · Welcome Wave` : "Welcome Wave";
}

/** The page frame. `title` is the form's heading; `lead` the line under it. */
export function EntryLayout({ title, lead, children, footer, wide }:
  { title: ReactNode; lead?: ReactNode; children?: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const main = useRef<HTMLElement>(null);
  useEffect(hideBoot, []);
  useEffect(() => { document.title = tabTitle(title); }, [title]);
  // arrived by a link from the public site ("Log in", "Get started"): focus starts at this page's heading, as on a page load
  useEffect(() => { const form = main.current?.querySelector<HTMLElement>(".en-form"); if (form && takeArrival()) focusIn(form); }, []);
  return (
    <main className="entry" ref={main}>
      <BrandPanel />
      <div className="en-side">
        <a className="en-home" href="/" onClick={goHome}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
          Back to home
        </a>
        <div className={`en-form ${wide ? "wide" : ""}`}>
          <header className="en-head">
            <h1>{title}</h1>
            {lead && <p className="en-lead">{lead}</p>}
          </header>
          {children}
          {footer && <div className="en-foot">{footer}</div>}
        </div>
      </div>
    </main>
  );
}

/** The official multi-colour Google "G" (brand guidelines require its own colours). */
export function GoogleGlyph() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}

/** Button content with a small spinner while the action runs. */
export function Busy({ busy, children, label }: { busy: boolean; children: ReactNode; label: ReactNode }) {
  return busy ? <><span className="en-spin" aria-hidden="true" />{label}</> : <>{children}</>;
}

/** A password input with a show/hide toggle. Every input attribute passes straight through. */
export function PasswordInput(props: InputHTMLAttributes<HTMLInputElement>) {
  const [shown, setShown] = useState(false);
  return (
    <span className="en-pw">
      <input {...props} type={shown ? "text" : "password"} />
      <button type="button" className="en-pw-toggle" onClick={() => setShown(!shown)} aria-pressed={shown}
              aria-label={shown ? "Hide password" : "Show password"}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {shown
            ? <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19M14.12 14.12a3 3 0 1 1-4.24-4.24M1 1l22 22" />
            : <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></>}
        </svg>
      </button>
    </span>
  );
}

/** Initials for a name or an email address. */
export function initials(s: string) {
  const words = s.replace(/@.*/, "").replace(/\(.*?\)/g, "").split(/[\s._-]+/).filter(Boolean);
  return ((words[0]?.[0] ?? "?") + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
}

/** "Signed in as" with a way out: shown on every screen after the login worked but before the workspace. */
export function Identity({ email, onSwitch }: { email: string; onSwitch: () => void }) {
  return (
    <div className="en-identity">
      <span className="en-identity-avatar" aria-hidden="true">{initials(email)}</span>
      <span className="en-identity-text"><span className="en-identity-label">Signed in as</span><strong>{email}</strong></span>
      <button type="button" className="en-link quiet" onClick={onSwitch}>Use a different account</button>
    </div>
  );
}
