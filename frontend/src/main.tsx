import { hideBoot } from "./boot"; // the intro's hand-off (src/boot.ts) and its browser-chrome colour guard, shared by both surfaces
import React, { Component, lazy, ReactNode, Suspense, useEffect, useRef, useState, useTransition } from "react";
import { createRoot } from "react-dom/client";
import { NAVIGATE, Surface, surfaceFor } from "./surface";

/* Three chunks, loaded on demand: the public site never downloads the app (firebase, the workspace), the app never
   downloads the site, and a new hire's pre-boarding page (/welcome/<token>) downloads neither. */
const Site = lazy(() => import("./site/Site"));
const AppEntry = lazy(() => import("./AppEntry"));
const Portal = lazy(() => import("./lifecycle/portal/Portal"));

/** One address per page: old links into the app ("/#/chat", "/#/meetings/x") move to /app, "/index.html" is "/", and a site
 *  address with a trailing slash ("/about/") loses it (the app's own addresses accept one either way). */
function normalize() {
  let p = location.pathname;
  const { search, hash } = location;
  if (p === "/index.html") p = "/";
  else if (p.length > 1 && p.endsWith("/") && surfaceFor(p, hash) === "site") p = p.replace(/\/+$/, "") || "/";
  if (p === "/" && /^#\//.test(hash)) p = "/app";
  if (p !== location.pathname) history.replaceState(history.state, "", `${p}${search}${hash}`);
}

const current = (): Surface => surfaceFor(location.pathname, location.hash);

/* A chunk that will not load: usually a deploy replaced the build while this page was open (its hashed files are gone), or the
   network dropped. One reload fetches the current build; if the page has already reloaded for this, it says so instead. */
const RELOADED = "ww-chunk-reload";

function reloadOnce(): boolean {
  try {
    if (sessionStorage.getItem(RELOADED)) return false;
    sessionStorage.setItem(RELOADED, "1");
  } catch { return false; }
  location.reload(); // the address already names the page that failed to load
  return true;
}

// Vite's preload of a chunk's own dependencies (its CSS, shared modules) failed: same remedy
addEventListener("vite:preloadError", (e) => { if (reloadOnce()) e.preventDefault(); });

/** Clears the reload guard once a surface has actually rendered (it commits only after its chunk has arrived). */
function Settled() {
  useEffect(() => { try { sessionStorage.removeItem(RELOADED); } catch { /* storage blocked */ } }, []);
  return null;
}

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { if (!reloadOnce()) hideBoot(); }
  render() { return this.state.failed ? <Unavailable /> : this.props.children; }
}

/** Plain on purpose: neither surface's stylesheet may have loaded. */
function Unavailable() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const ink = dark ? "#fafafa" : "#0a0a0a", field = dark ? "#0a0a0a" : "#f2f2f2";
  return (
    <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24, background: field, color: ink,
                   font: '400 16px/1.5 "Geist", system-ui, sans-serif' }}>
      <div role="alert" style={{ maxWidth: 360, textAlign: "center" }}>
        <p style={{ margin: "0 0 6px", fontWeight: 600, fontSize: 18 }}>Couldn’t load Welcome Wave</p>
        <p style={{ margin: "0 0 20px", opacity: 0.75 }}>Check your connection, then reload the page.</p>
        <button type="button" onClick={() => { try { sessionStorage.removeItem(RELOADED); } catch { /* blocked */ } location.reload(); }}
                style={{ height: 44, padding: "0 20px", border: 0, borderRadius: 999, background: ink, color: field, font: "inherit", fontWeight: 500, cursor: "pointer" }}>
          Reload
        </button>
      </div>
    </main>
  );
}

/** While the other surface's chunk downloads, the page on screen stays (a transition) and a hairline at the top says so. */
function Pending() {
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    bar.current?.animate(
      still ? [{ opacity: 0 }, { opacity: 1 }]
        : [{ opacity: 0, transform: "scaleX(0)" }, { opacity: 1, transform: "scaleX(.2)", offset: 0.1 }, { opacity: 1, transform: "scaleX(.9)" }],
      { duration: still ? 1 : 6000, delay: 150, easing: "cubic-bezier(.2,.7,0,1)", fill: "both" },
    );
  }, []);
  return (
    <div ref={bar} role="progressbar" aria-label="Loading"
         style={{ position: "fixed", zIndex: 2147483647, top: 0, left: 0, right: 0, height: 2, background: "#fff",
                  mixBlendMode: "difference", transformOrigin: "left", opacity: 0, pointerEvents: "none" }} />
  );
}

function Root() {
  const [surface, setSurface] = useState(current);
  const [pending, startTransition] = useTransition();
  useEffect(() => {
    // a transition: the page on screen stays until the other surface's chunk has arrived, so a switch never flashes blank
    const on = () => { normalize(); const next = current(); startTransition(() => setSurface(next)); };
    addEventListener("popstate", on);
    addEventListener(NAVIGATE, on);
    return () => { removeEventListener("popstate", on); removeEventListener(NAVIGATE, on); };
  }, []);
  // One Suspense for the whole life of the page, OUTSIDE the keyed boundary: once it has revealed a surface, a transition that
  // suspends on the other surface's chunk keeps the old one on screen (and `pending` true) until the chunk is in. A Suspense
  // inside the keyed boundary would be a brand-new boundary on every switch, which shows its (empty) fallback at once.
  return (
    <>
      {pending && <Pending />}
      <Suspense fallback={null}>
        <Boundary key={surface}>
          {surface === "site" ? <Site /> : surface === "portal" ? <Portal /> : <AppEntry />}
          <Settled />
        </Boundary>
      </Suspense>
    </>
  );
}

normalize();
createRoot(document.getElementById("root")!).render(<React.StrictMode><Root /></React.StrictMode>);
