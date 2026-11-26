/* S4, paper to ink. The finale is a tall track with a sticky black panel that opens from an inset, rounded card to the full
   viewport as it scrolls in (clip-path from the site's clock, in whole pixels), over contour lines drawn from the cover's own
   line: the swell under the headline returns, white on black, stacked into the sign-in page's terrain. The contours sit above
   the type, in the panel's upper half, and fade out before they reach it; on wide screens one stretch of a ridge is teal and
   labelled "Explained in KT #1" (the terrain's explained patch, as on the sign-in page).
   Its two ways in: "Book a demo" (a mailto once DEMO_EMAIL is set; until then a short note under the buttons) and "Open the
   live demo", which hands this black panel over to the sign-in page's black brand panel (handover.ts). The footer continues
   in black: the site's links, the legal documents (src/legal/paths.ts), the stack it is built on, and a way back to the top. */
import { CSSProperties, MouseEvent, useEffect, useRef } from "react";
import { LEGAL_PAGES } from "../legal/paths";
import { Logo } from "../ui";
import { addScene, setNight } from "./clock";
import { SWELL } from "./demo";
import { prefetch, toApp, toSite } from "./handover";
import { col, Words } from "./parts";

/** The finale's contours are the cover's line come back: the same wave the hero line rides under the headline (the intro's
 *  narrow wave, demo.ts SWELL: a long, almost level run, a dip, then the swell), stretched across a 1440 x 420 band and stacked
 *  like a terrain. Each row nearer the viewer is lower and taller; every swell crests near the right edge and runs out past it
 *  (the band is anchored right, so phones keep the crests). The front row is the hero line itself, in full white; the rows
 *  behind it fade. The teal stretch is the crest of the fifth row, with its label above it. */
const KT_LINE = 4, ROWS = 7;

/** Where "Book a demo" writes to. Empty: it jumps to a short note under the buttons instead (no address is published yet). */
const DEMO_EMAIL = "";
function contours() {
  const lines: string[] = [];
  let kt = "", peak: [number, number] = [0, 0];
  const [x0, x1] = [SWELL[0][0], SWELL[SWELL.length - 1][0]];
  const d = (ps: [number, number][]) => ps.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
  for (let k = 0; k < ROWS; k++) {
    const left = -40, right = 1560 - k * 20;                 // where the wave's own end would be: at or past the right edge
    const base = 60 + k * 34, amp = 2.2 + k * 0.55;
    const pts = SWELL.map(([x, y]): [number, number] => [left + ((x - x0) / (x1 - x0)) * (right - left), base + (y - 57.6) * amp]);
    lines.push(d(pts));
    if (k === KT_LINE) {
      const top = pts.reduce((a, b) => (b[1] < a[1] ? b : a));
      kt = d(pts.filter(([x]) => x >= top[0] - 80 && x <= top[0] + 80));
      peak = top;
    }
  }
  return { lines, kt, label: [peak[0], peak[1] - 16] as const };
}
const CONTOURS = contours();

export function Finale({ signedIn }: { signedIn: boolean }) {
  const track = useRef<HTMLElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = track.current!, pn = panel.current!;
    let top = 0, height = 1, vh = 1, vw = 1, last = "", lastClip = "", inkTrack = false;
    const off = addScene({
      el,
      read() {
        const b = el.getBoundingClientRect(); top = b.top; height = b.height; vh = innerHeight; vw = innerWidth;
        inkTrack = el.closest(".site")?.getAttribute("data-path") === "/"; // site.css: on the landing page the track around the card is black too
      },
      write() {
        const p = Math.min(1, Math.max(0, -top / Math.max(1, height - vh)));
        const q = Math.min(1, p / 0.6);
        const v = q.toFixed(3);
        if (v !== last) { last = v; pn.style.setProperty("--q", v); }
        // the opening card's inset and radius in whole pixels: a fractional inset draws a 1 px step along the top edge
        const clip = `inset(${Math.round((1 - q) * 0.06 * vw)}px round ${Math.round((1 - q) * 24)}px)`;
        if (clip !== lastClip) { lastClip = clip; pn.style.setProperty("--clip", clip); }
        // black under the nav: the panel's visible top (its clip inset shrinks as q grows) has reached the nav's middle
        const panelTop = Math.max(top, 0), inset = (1 - q) * 0.06 * vw;
        const bottom = Math.min(top + height, vh);
        // on the landing page the track around the opening card is black as well, so the nav is over black from the track's top
        const under = (inkTrack ? top : panelTop + inset) <= 32 && bottom > 32;
        const visible = Math.max(0, bottom - panelTop - inset) / vh;
        setNight("finale", under, visible >= 0.5);
      },
    });
    // leaving while the panel is under the nav (Back to a 404, say) must not leave the nav inverted or the browser chrome black
    return () => { off(); setNight("finale", false, false); };
  }, []);

  const into = (to: string) => (e: MouseEvent<HTMLAnchorElement>) => toApp(e, to, panel.current);
  // the live demo is the app itself: its sign-in page (one click per demo account), or the workspace when already signed in
  const live = signedIn ? "/app" : "/login";
  return (
    <section ref={track} className="s-finale" aria-labelledby="finale-h">
      <div ref={panel} className="s-finale-panel" data-tone="night">
        <svg className="s-contours s-rv" viewBox="0 0 1440 420" preserveAspectRatio="xMaxYMin slice" aria-hidden="true" focusable="false">
          {CONTOURS.lines.map((d, i) => (
            <path key={i} d={d} pathLength={1} className={i === CONTOURS.lines.length - 1 ? "is-front" : undefined} style={{ "--k": i } as CSSProperties} />
          ))}
          <g className="s-contours-kt">
            <path className="is-kt" d={CONTOURS.kt} pathLength={1} />
            <text x={CONTOURS.label[0]} y={CONTOURS.label[1]} textAnchor="middle">Explained in KT #1</text>
          </g>
        </svg>
        <div className="s-grid s-finale-in">
          <h2 id="finale-h" className="s-xl s-rv" style={col("1 / 12")}><Words text="Start with one call." /></h2>
          <p className="s-lede s-rv" style={col("1 / 7", "1 / 6")}>
            Your seniors already know the code. Record one KT session, and Welcome Wave links it to the code they explained.
          </p>
          <div className="s-actions s-rv" style={col("1 / -1")}>
            <a className="s-btn s-btn-paper" href={DEMO_EMAIL ? `mailto:${DEMO_EMAIL}?subject=${encodeURIComponent("Welcome Wave demo")}` : "#demo"}>Book a demo</a>
            <a className="s-textlink" href={live} onClick={into(live)} {...prefetch}>Explore the platform <span aria-hidden="true">→</span></a>
          </div>
          {!DEMO_EMAIL && (
            <p id="demo" className="s-demo-note s-rv" style={col("1 / 7", "1 / 6")}>
              Demos take thirty minutes on Google Meet, on your own repository, with the people who built Welcome Wave.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

export function Footer({ home, signedIn }: { home: boolean; signedIn: boolean }) {
  const foot = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = foot.current!;
    let top = 0, h = 1, vh = 1;
    const off = addScene({
      el,
      read() { const b = el.getBoundingClientRect(); top = b.top; h = b.height; vh = innerHeight; },
      write() {
        const visible = Math.max(0, Math.min(top + h, vh) - Math.max(top, 0));
        setNight("footer", top <= 32 && top + h > 32, visible >= Math.min(h, vh) * 0.5);
      },
    });
    return () => { off(); setNight("footer", false, false); };
  }, []);

  const a = (id: string) => (home ? `#${id}` : `/#${id}`);
  const site = (href: string) => (e: MouseEvent<HTMLAnchorElement>) => toSite(e, href);
  const link = (href: string, label: string) => <li key={label}><a href={href} onClick={site(href)}>{label}</a></li>;
  return (
    <footer ref={foot} className="s-footer" data-tone="night">
      <div className="s-grid s-foot-cols">
        <div className="s-foot-brand" style={col("1 / 3", "1 / -1")}>
          <Logo size={28} />
          <p>Learn the codebase from the people who built it.</p>
        </div>
        <nav aria-label="Site" style={col("3 / 5", "1 / 3")}>
          <h2 className="s-foot-h">Site</h2>
          <ul>{[link("/", "Home"), link(a("product"), "Product"), link(a("security"), "Security"), link("/about", "About")]}</ul>
        </nav>
        <nav aria-label="Features" style={col("5 / 7", "3 / 5")}>
          <h2 className="s-foot-h">Features</h2>
          <ul>{[link(a("knowledge"), "KT-to-code linking"), link(a("mentor"), "KT sessions"), link(a("contribution"), "30/60/90 program"), link(a("knowledge"), "Cited answers"), link(a("impact"), "Leadership reporting")]}</ul>
        </nav>
        <nav aria-label="Legal" style={col("7 / 9", "5 / 7")}>
          <h2 className="s-foot-h">Legal</h2>
          <ul>{LEGAL_PAGES.map((p) => link(p.path, p.short))}</ul>
        </nav>
        <nav aria-label="Account" style={col("9 / 11", "1 / 3")}>
          <h2 className="s-foot-h">Account</h2>
          <ul>
            {signedIn ? (
              <li><a href="/app" onClick={(e) => toApp(e, "/app")} {...prefetch}>Open Welcome Wave</a></li>
            ) : (
              <>
                <li><a href="/login" onClick={(e) => toApp(e, "/login")} {...prefetch}>Log in</a></li>
                <li><a href="/signup" onClick={(e) => toApp(e, "/signup")} {...prefetch}>Create account</a></li>
              </>
            )}
          </ul>
        </nav>
        <div style={col("11 / 13", "3 / 7")}>
          <h2 className="s-foot-h">Built on</h2>
          <ul className="s-foot-mono">
            {["FastAPI", "LangGraph", "Gemini", "Postgres + pgvector", "Google Calendar + Meet", "React"].map((t) => <li key={t}>{t}</li>)}
          </ul>
        </div>
      </div>
      <div className="s-grid s-foot-base">
        <p style={col("1 / 5", "1 / 4")}>Built by Vijayabaskar and Aman Nautiyal.</p>
        <p style={col("5 / 9", "4 / 6")}>© 2026 Welcome Wave</p>
        <p className="s-foot-top" style={col("10 / 13", "6 / 7")}><a href="#top">Back to top</a></p>
      </div>
    </footer>
  );
}
