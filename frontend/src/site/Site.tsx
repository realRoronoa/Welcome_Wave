/* The public site: "/" (the landing page), "/about", the legal documents (/terms, /privacy, /recording, /acceptable-use, /dpa,
   /subprocessors: src/legal/paths.ts names them, Legal.tsx renders them, in a chunk of its own), and a 404 for any other path
   that is not the app's. Its own chunk: it never downloads firebase or the workspace. The landing page is a live-action film over a shared stage
   (cinematic/CinematicLandingPage.tsx): real footage, then the real product. While it shows, <html> carries .is-site
   (site.css's scope; the app's theme code leaves the page alone) and data-theme="light" (About and the 404 are paper, whatever
   the OS or the saved choice; the landing page is dark from its first paint: .s-dark), plus .motion unless the visitor
   prefers reduced motion (hidden entrance states exist only under it). Leaving undoes all of it and re-applies the saved
   theme for the app. */
import { CSSProperties, lazy, ReactNode, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { hideBoot } from "../boot";
import { LEGAL_PAGES, legalPage } from "../legal/paths";
import { focusIn, NAVIGATE, seemsSignedIn, surfaceFor, takeArrival } from "../surface";
import { applySavedTheme } from "../theme-core";
import { addScene, reveal, resetNight, setNight, stopReveals } from "./clock";
import { waveform } from "./demo";
import { Finale, Footer } from "./Footer";
import { interceptAnchors, prefetch, saveScroll, toApp, toSite } from "./handover";
import Nav from "./Nav";
import { col, Hw, Stop, Words } from "./parts";
import "./site.css"; // before the film's own stylesheet, which builds on it
import CinematicLandingPage from "./cinematic/CinematicLandingPage";

/** The legal documents' reading page: its own chunk, so the landing page never downloads the documents' text. */
const Legal = lazy(() => import("./Legal"));

/** Every page the site has, and its tab title: a path that is not here is a 404 ("Nobody explained this page"). */
const TITLES: Record<string, string> = {
  "/": "Welcome Wave · Learn the codebase from the people who built it",
  "/about": "About · Welcome Wave",
  ...Object.fromEntries(LEGAL_PAGES.map((p) => [p.path, `${p.title} · Welcome Wave`])),
};

export default function Site() {
  const [path, setPath] = useState(location.pathname);
  const pathRef = useRef(path);
  pathRef.current = path;
  const root = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const html = document.documentElement;
    const saved = html.getAttribute("data-theme");
    html.classList.add("is-site");
    html.setAttribute("data-theme", "light");
    // motion follows the visitor's setting live: hidden entrance states, the pinned scrubber and the opening finale all key
    // on html.motion, so turning on Reduce Motion while the page is open shows their final frames at once
    const still = matchMedia("(prefers-reduced-motion: reduce)");
    const motion = () => html.classList.toggle("motion", !still.matches);
    motion();
    still.addEventListener("change", motion);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", "#f2f2f2");
    history.scrollRestoration = "manual"; // the site restores history.state.y itself, once its page has rendered
    addEventListener("pagehide", saveScroll);
    const unanchor = interceptAnchors(root.current!);
    hideBoot();
    return () => {
      still.removeEventListener("change", motion);
      unanchor();
      removeEventListener("pagehide", saveScroll);
      // a fixed "auto", not the value found on mount: the setting is per history entry and pushState copies it, so after
      // Back to "/" the value found would be the site's own "manual", and the next Log in would hand it to the app's entries
      history.scrollRestoration = "auto";
      html.classList.remove("is-site", "motion", "s-night", "s-dark");
      if (saved && saved !== "light") html.setAttribute("data-theme", saved);
      document.querySelector('meta[name="robots"][data-site]')?.remove();
      resetNight();
      stopReveals();
      applySavedTheme();
    };
  }, []);

  useEffect(() => {
    const on = () => {
      // an address of the app's (/login, /app): main.tsx swaps this site out once the app's chunk is in; until then the page on
      // screen stays as it is (it must not re-render as a 404, nor take the focus hand-off meant for the sign-in page)
      if (surfaceFor(location.pathname, location.hash) !== "site") return;
      if (location.pathname !== pathRef.current) return setPath(location.pathname);
      restorePosition(); // Back or Forward between entries of this page ("/", "/#product", "/#security"): where each was left
    };
    addEventListener("popstate", on);
    addEventListener(NAVIGATE, on);
    return () => { removeEventListener("popstate", on); removeEventListener(NAVIGATE, on); };
  }, []);

  // each page: its title, whether search engines may index it, its entrances, the scroll position it was left at (or the
  // anchor it was opened at), and, when a link brought the visitor here in place, focus on its heading
  useLayoutEffect(() => {
    const dark = path === "/";
    document.documentElement.classList.toggle("s-dark", dark);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0a0a0a" : "#f2f2f2");
    const known = path in TITLES;
    document.title = TITLES[path] ?? "Page not found · Welcome Wave";
    let robots = document.querySelector<HTMLMetaElement>('meta[name="robots"][data-site]');
    if (!known && !robots) {
      robots = Object.assign(document.createElement("meta"), { name: "robots", content: "noindex" });
      robots.dataset.site = "";
      document.head.appendChild(robots);
    } else if (known) robots?.remove();
    const target = restorePosition();
    // a legal document arrives with its own chunk: Legal.tsx moves focus and finds its anchor once the text is there
    if (!legalPage(path) && takeArrival()) {
      const h1 = root.current?.querySelector<HTMLElement>("main h1");
      if (target) focusIn(target); else if (h1) focusIn(h1);
    }
    if (root.current) reveal(root.current);
  }, [path]);

  const home = path === "/";
  const signedIn = seemsSignedIn();
  const legal = legalPage(path);
  return (
    <div className="site" ref={root} data-path={path} id="top">
      <a className="s-skip" href="#content">Skip to content</a>
      <Nav path={path} />
      <main id="content" tabIndex={-1}>
        {home ? <Landing signedIn={signedIn} /> : path === "/about" ? <About />
          : legal ? <Suspense fallback={<div style={{ minHeight: "100vh" }} aria-busy="true" />}><Legal key={legal.id} id={legal.id} /></Suspense>
          : <NotFound />}
        {path === "/" || path === "/about" ? <Finale signedIn={signedIn} /> : null}
      </main>
      <Footer home={home} signedIn={signedIn} />
    </div>
  );
}

/** Scroll to where this history entry was left (history.state.y), or to its anchor if it was opened at one, or to the top.
 *  Returns the anchor's element, if any. */
function restorePosition(): HTMLElement | null {
  const target = location.hash.length > 1 ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
  const y = (history.state as { y?: number } | null)?.y;
  if (target && !y) target.scrollIntoView({ behavior: "instant" });
  else scrollTo({ top: typeof y === "number" ? y : 0, behavior: "instant" });
  return target;
}

/* ------------------------------------------------------------------------------------------------------------------ landing */

/** The entrance plays once per page load: coming back to "/" later shows the cover as it ended. */
let coverPlayed = false;
/** .is-go to the last keyframe (site.css "the cover" motion: everything is still by 1570 ms), plus a frame or two */
const COVER_MS = 1650;
/** if the intro never reports that it left (a script error, an odd restore), show the cover as it ends rather than hidden. Counted
 *  from the page's first frame, like the intro's own stall timer: a tab opened in the background gets no frames until it is
 *  shown, so its intro and entrance still play when the visitor switches to it. */
const COVER_FAILSAFE_MS = 12000;

function Cover({ signedIn }: { signedIn: boolean }) {
  const [go, setGo] = useState(coverPlayed);
  const [done, setDone] = useState(coverPlayed);

  // start as the intro leaves (or now, if it has already gone): .is-go 150 ms after "ww-boot-done", then site.css's delays.
  // Once it has played, .is-done swaps the animations for the plain end state (the word masks open, the teal copy goes).
  // Reduced motion switched on at any point before that also ends it, so switching it off again can never replay the entrance.
  useEffect(() => {
    if (coverPlayed) return;
    let timer = 0, failsafe = 0;
    const rm = matchMedia("(prefers-reduced-motion: reduce)");
    const raf = requestAnimationFrame(() => { failsafe = window.setTimeout(finish, COVER_FAILSAFE_MS); });
    const stop = () => {
      removeEventListener("ww-boot-done", start);
      rm.removeEventListener("change", still);
      cancelAnimationFrame(raf);
      clearTimeout(failsafe);
    };
    function finish() { stop(); clearTimeout(timer); coverPlayed = true; setGo(true); setDone(true); }
    function still() { if (rm.matches) finish(); }
    function start() {
      removeEventListener("ww-boot-done", start);
      cancelAnimationFrame(raf);
      clearTimeout(failsafe);
      timer = window.setTimeout(() => {
        coverPlayed = true; setGo(true);
        timer = window.setTimeout(() => { stop(); setDone(true); }, COVER_MS);
      }, 150);
    }
    rm.addEventListener("change", still);
    const boot = document.getElementById("ww-boot");
    if (boot && !boot.classList.contains("ww-boot--done")) addEventListener("ww-boot-done", start);
    else start();
    return () => { stop(); clearTimeout(timer); };
  }, []);

  // the headline: its aria-label is the sentence a screen reader reads, once (the set copy is aria-hidden); the DOM holds the
  // text once too, which is what a copy, a crawler or a reader view gets. The copy is set by hand. Breaks fall on phrases: from
  // 600 px "Learn the codebase / from the people / who built it."; phones "Learn the / codebase / from the people / who built it."
  // (the first <br> shows on phones only). Each word rises out of its own mask, line by line (Hw `l`: phone and tablet-up line,
  // the last line one step later: a beat before the payoff); the full stop is the mark's first stroke, drawn in place.
  return (
    <section id="home" className={`s-cover${go ? " is-go" : ""}${done ? " is-done" : ""}`} data-tone="night" aria-labelledby="home-h">
      <div className="s-grid s-cover-in">
        <p className="s-kicker" style={col("1 / -1")}><span>Onboarding for software teams</span></p>
        <div className="s-h1-host" style={col("1 / -1")}>
          <h1 id="home-h" className="s-h1" aria-label="Learn the codebase from the people who built it.">
            <span className="s-h1-v" aria-hidden="true">
              <Hw l="0 0" h=".08em">Learn</Hw> <Hw l="0 0">the</Hw><br className="s-br-sm" />{" "}
              <Hw l="1 0" hs=".039em">codebase</Hw><br />{" "}
              <Hw l="2 1" h=".051em">from</Hw> <Hw l="2 1">the</Hw> <Hw l="2 1">people</Hw><br />{" "}
              <Hw l="4 3" h=".021em">who</Hw> <Hw l="4 3">bui<span className="s-h1-lt">l</span>t</Hw>{" "}
              <span className="s-nowrap"><Hw l="4 3">it</Hw><Stop /></span>
            </span>
          </h1>
        </div>
        <div className="s-cover-side" style={col("1 / 6", "1 / 5")}>
          <p className="s-lede s-in">
            Seniors explain the code once, on a recorded call. Welcome Wave links every moment to the lines it covers.
          </p>
          <div className="s-actions s-in">
            {signedIn
              ? <a className="s-btn s-btn-brand" href="/app" onClick={(e) => toApp(e, "/app")} {...prefetch}>Open Welcome Wave</a>
              : <a className="s-btn s-btn-brand" href="/signup" onClick={(e) => toApp(e, "/signup")} {...prefetch}>Get started</a>}
            <a className="s-btn s-btn-line" href="#product">See how it works</a>
          </div>
        </div>
      </div>
    </section>
  );
}

function Landing({ signedIn }: { signedIn: boolean }) {
  return (
    <>
      <CinematicLandingPage cover={<Cover signedIn={signedIn} />} signedIn={signedIn} />
      <AgentsBand />
      <Security />
    </>
  );
}

/** After the story, before security: the eight specialist agents (names as in the product), each in five words.
 *  The line above them is the one rule they share: an event reaches one agent, its proposal waits in Approvals, a person
 *  decides. It draws in once, left to right (transform only). */
const AGENTS = [
  ["Librarian", "Reads every repository and document."],
  ["Note-taker", "Turns KT sessions into notes."],
  ["Guide", "Answers questions with their sources."],
  ["Planner", "Drafts each hire’s 30/60/90 plan."],
  ["Scout", "Finds a safe first task."],
  ["Reviewer", "Reviews pull requests, privately too."],
  ["Coach", "Spots blockers early, nudges kindly."],
  ["Keeper", "Keeps explanations from going stale."],
];
const FLOW = ["An event", "One agent", "Approvals", "A person decides"];

function AgentsBand() {
  const band = useRef<HTMLElement>(null);
  // black under the nav, like the night before it and the security band after it
  useEffect(() => {
    const el = band.current!;
    let top = 0, h = 1, vh = 1;
    const off = addScene({
      el,
      read() { const b = el.getBoundingClientRect(); top = b.top; h = b.height; vh = innerHeight; },
      write() {
        const visible = Math.max(0, Math.min(top + h, vh) - Math.max(top, 0));
        setNight("agents", top <= 32 && top + h > 32, visible >= Math.min(h, vh) * 0.5);
      },
    });
    return () => { off(); setNight("agents", false, false); };
  }, []);
  // the headline breaks after its first sentence on every screen; the words rise in one run across both lines
  const lines = [["Eight", "agents."], ["One", "approval", "queue."]];
  let n = 0;
  return (
    <section ref={band} id="agents" className="s-ag" data-tone="night" data-nav="product" aria-labelledby="agents-h">
      <div className="s-grid">
        <h2 id="agents-h" className="s-h2 s-ag-h s-rv" style={col("1 / 11", "1 / -1")} aria-label="Eight agents. One approval queue.">
          <span aria-hidden="true">
            {lines.map((ws, l) => (
              <span key={l} className="s-ag-hl">
                {ws.map((w, i) => (
                  <span key={w}><span className="s-w"><span style={{ "--i": n++ } as CSSProperties}>{w}</span></span>{i < ws.length - 1 ? " " : null}</span>
                ))}
              </span>
            ))}
          </span>
        </h2>
        <p className="s-lede s-ag-lede s-rv" style={col("1 / 7", "1 / 5")}>Each agent proposes. Your people decide.</p>
        <ol className="s-ag-flow s-rv" style={col("1 / -1")} aria-label="How a proposal reaches a person">
          {FLOW.map((f, i) => <li key={f} style={{ "--i": i } as CSSProperties}><span>{f}</span></li>)}
        </ol>
        <ul className="s-ag-list s-rv" style={col("1 / -1")}>
          {AGENTS.map(([k, v], i) => (
            <li key={k} style={{ "--i": i } as CSSProperties}>
              <span className="s-ag-n s-tnum" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span>
              <h3 className="s-ag-k">{k}</h3>
              <p>{v}</p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/** After the story, in the night it ends on: what a security review asks first, as a quiet grid. */
const SECURITY = [
  ["Role-based access", "Every request is checked on the server, for every role."],
  ["Audit log", "Every administrative action, exportable as CSV."],
  ["Read-only code access", "Nothing ever writes to your repositories."],
  ["Secret redaction", "Keys and tokens are removed from KT transcripts before they’re stored."],
  ["Row-level security", "Each organisation’s data is kept apart in Postgres."],
  ["Export and deletion", "Take your data out, or delete it, at any time."],
];

function Security() {
  const band = useRef<HTMLElement>(null);
  // black under the nav, like the night before it and the finale after it
  useEffect(() => {
    const el = band.current!;
    let top = 0, h = 1, vh = 1;
    const off = addScene({
      el,
      read() { const b = el.getBoundingClientRect(); top = b.top; h = b.height; vh = innerHeight; },
      write() {
        const visible = Math.max(0, Math.min(top + h, vh) - Math.max(top, 0));
        setNight("security", top <= 32 && top + h > 32, visible >= Math.min(h, vh) * 0.5);
      },
    });
    return () => { off(); setNight("security", false, false); };
  }, []);
  return (
    <section ref={band} id="security" className="s-sec" data-tone="night" data-nav="security" aria-labelledby="security-h">
      <div className="s-grid">
        <h2 id="security-h" className="s-sec-h s-rv" style={col("1 / 7", "1 / -1")}>Ready for your security review.</h2>
        <ul className="s-sec-list s-rv" style={col("1 / -1")}>
          {SECURITY.map(([k, v], i) => (
            <li key={k} style={{ "--i": i } as CSSProperties}><h3 className="s-sec-k">{k}</h3><p>{v}</p></li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* -------------------------------------------------------------------------------------------------------------------- about */

const LAYERS = [
  ["Repository", "read-only"], ["KT session", "Google Meet recording, or an upload"], ["Gemini", "video and code"],
  ["LangGraph agents", "Supervisor, Librarian, Note-taker, Guide, Planner, Scout, Reviewer, Coach, Keeper"], ["Postgres + pgvector", ""], ["FastAPI", "the API"],
  ["Your workspace", "React"],
];

function About() {
  return (
    <article className="s-about" aria-labelledby="about-h">
      <header className="s-grid s-about-head">
        <p className="s-kicker" style={col("3 / 12")}>About</p>
        <h1 id="about-h" className="s-h2 s-about-h1 s-rv" style={col("3 / 13")}><Words text="Explanations shouldn’t end when the call does." /></h1>
        <p className="s-lede s-rv" style={col("3 / 8", "1 / -1")}>
          Welcome Wave is an onboarding platform for software teams, built by Vijayabaskar and Aman Nautiyal. It started as our
          on-the-job training project, and we built it to run in production.
        </p>
        <FadingCall style={col("9 / 13", "1 / -1")} />
      </header>
      <AboutPart n="01" title="The problem">
        <p className="s-statement s-rv" style={col("3 / 12")}>
          The best way to learn a codebase is from the person who wrote it. That person can only explain it so many times. The
          explanation happens on a call, and the call is gone a week later.
        </p>
      </AboutPart>
      <AboutPart n="02" title="What we built">
        <p className="s-lede s-about-built s-rv" style={col("3 / 10", "1 / -1")}>
          A platform that watches the KT call, both speech and shared screen, and links each moment to the code on screen. It uses
          those links to answer new engineers with sources. Agents do the linking, planning and reviewing. People make the decisions.
        </p>
      </AboutPart>
      <AboutPart n="03" title="Principles">
        <ol className="s-principles s-rv" style={col("3 / 13")}>
          {[
            ["Explain once.", "An expert’s time is the scarcest thing on a team."],
            ["Cite, or say you don’t know.", "Every answer shows a file, a line, a document or a call."],
            ["People decide.", "Agents propose; managers approve."],
            ["Read-only by design.", "Welcome Wave never writes to your repositories."],
          ].map(([a, b], i) => (
            <li key={a}><span className="s-principle-n" aria-hidden="true">{String(i + 1).padStart(2, "0")}</span><b>{a}</b><span>{b}</span></li>
          ))}
        </ol>
      </AboutPart>
      <AboutPart n="04" title="How it is built">
        <div className="s-about-diagram s-rv" style={col("3 / 12")}><Diagram /></div>
      </AboutPart>
      <AboutPart n="05" title="Colophon">
        <p className="s-body s-rv" style={col("3 / 9", "1 / -1")}>
          Set in Geist and Geist Mono. Built with FastAPI and LangGraph agents, Gemini for video and code, Postgres with pgvector,
          Google Calendar and Meet for KT sessions, and React. Runs on one machine or on Google Cloud. Organisations are kept apart by Postgres row-level
          security.
        </p>
      </AboutPart>
      <AboutPart n="06" title="Built by">
        <ul className="s-names s-rv" style={col("3 / 13")}>
          <li>Vijayabaskar</li>
          <li>Aman Nautiyal</li>
        </ul>
      </AboutPart>
    </article>
  );
}

function AboutPart({ n, title, children }: { n: string; title: string; children: ReactNode }) {
  return (
    <section className="s-ch s-about-part" aria-labelledby={`about-${n}`}>
      <div className="s-grid">
        <h2 className="s-runhead s-rv" id={`about-${n}`}><span className="s-rh-n">{n}</span><span className="s-rh-title">{title}</span></h2>
        {children}
      </div>
    </section>
  );
}

/** About's signature: a recorded call that fades out. Day 1, the explanation is on the waveform; a week later it is gone. */
function FadingCall({ style }: { style?: CSSProperties }) {
  const bars = waveform().map((h, s) => { const y = 20 - h * 19; return `M${s + 0.18} ${y.toFixed(2)}h0.64V${(40 - y).toFixed(2)}h-0.64z`; }).join("");
  return (
    <figure className="s-fade s-rv" style={style} aria-label="A recorded KT call fading out: a week later, nobody can find what was said.">
      <p className="s-fade-head" aria-hidden="true"><span>KT call</span><span className="s-tnum">00:55</span></p>
      <div className="s-fade-wave" aria-hidden="true">
        <svg viewBox="0 0 105 40" preserveAspectRatio="none"><path d={bars} /></svg>
        <span className="s-fade-head-line" />
      </div>
      <p className="s-fade-foot" aria-hidden="true"><span>Day 1</span><span>Day 7 · <span className="s-tnum">--:--</span></span></p>
      <figcaption className="s-fade-cap">A week later, nobody can find what was said.</figcaption>
    </figure>
  );
}

/** How it is built: a hairline flow (a list on phones). */
function Diagram() {
  const sentence = "A repository (read-only) and a KT session recorded on Google Meet feed Gemini, which reads video and code. Then the LangGraph agents (a Supervisor and eight specialists: Librarian, Note-taker, Guide, Planner, Scout, Reviewer, Coach and Keeper) write to Postgres with pgvector, and FastAPI serves it to your workspace in React.";
  const box = (x: number, y: number, w: number, h: number, title: string, subs: string[], key: string) => (
    <g key={key} transform={`translate(${x} ${y})`}>
      <rect width={w} height={h} rx="8" />
      <text x="14" y={subs.length ? 27 : 37} className="d-t">{title}</text>
      {subs.map((t, i) => <text key={t} x="14" y={47 + i * 17} className="d-s">{t}</text>)}
    </g>
  );
  return (
    <>
      <svg className="s-diagram" viewBox="0 0 760 312" role="img" aria-label={sentence}>
        <defs>
          <marker id="s-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M1 1L9 5L1 9" className="d-a" />
          </marker>
        </defs>
        {box(0, 24, 200, 64, "Repository", ["read-only"], "r")}
        {box(0, 120, 200, 64, "KT session", ["Google Meet recording"], "k")}
        {box(270, 72, 180, 64, "Gemini", ["video and code"], "g")}
        {box(520, 54, 240, 104, "LangGraph agents", ["Supervisor, Librarian, Guide,", "Note-taker, Planner, Scout,", "Reviewer, Coach, Keeper"], "a")}
        {box(70, 236, 200, 64, "Postgres + pgvector", [], "p")}
        {box(330, 236, 150, 64, "FastAPI", ["the API"], "f")}
        {box(540, 236, 220, 64, "Your workspace", ["React"], "w")}
        <path className="d-l" markerEnd="url(#s-arrow)" d="M200 56 C236 56 236 104 268 104" />
        <path className="d-l" markerEnd="url(#s-arrow)" d="M200 152 C236 152 236 104 268 104" />
        <path className="d-l" markerEnd="url(#s-arrow)" d="M450 104 H518" />
        <path className="d-l" markerEnd="url(#s-arrow)" d="M640 158 V178 C640 188 632 192 620 192 H182 C174 192 170 198 170 210 V234" />
        <path className="d-l" markerEnd="url(#s-arrow)" d="M270 268 H328" />
        <path className="d-l" markerEnd="url(#s-arrow)" d="M480 268 H538" />
      </svg>
      <ol className="s-layers">
        {LAYERS.map(([t, s]) => <li key={t}><b>{t}</b>{s && <span>{s}</span>}</li>)}
      </ol>
    </>
  );
}

/* ---------------------------------------------------------------------------------------------------------------------- 404 */

function NotFound() {
  return (
    <section className="s-404" aria-labelledby="nf-h">
      <div className="s-grid">
        <p className="s-runhead"><span className="s-rh-n">404</span><span className="s-rh-title">Page not found</span><span className="s-rh-t s-tnum">--:--</span></p>
        <h1 id="nf-h" className="s-h2" style={col("3 / 12")}>Nobody explained this page.</h1>
        <p className="s-lede" style={col("3 / 9")}>It doesn’t exist, or it has moved.</p>
        <p className="s-actions" style={col("3 / -1")}>
          <a className="s-btn s-btn-ink" href="/" onClick={(e) => toSite(e, "/")}>Back to the home page</a>
          <a className="s-btn s-btn-line" href="/login" onClick={(e) => toApp(e, "/login")} {...prefetch}>Log in</a>
        </p>
        <div className="s-nf-art" style={col("3 / 11")} aria-hidden="true">
          <span className="s-nf-cell"><span>never explained</span></span>
          <span className="s-nf-line" />
        </div>
      </div>
    </section>
  );
}
