/* Runs in <head>, before the intro paints. A file because the CSP forbids inline scripts.
   The public site (/about, any unknown path) is light: the intro plays its paper variant and dissolves into the paper page;
   the landing page (/) is a dark film, so its intro plays the dark variant (.ww-boot-dark), whatever the OS or the saved choice (which is not touched). The app (/login, /signup, /app, /github/callback
   and old "/#/..." links) and the pre-boarding page (/welcome/<token>) apply the saved light/dark choice so the intro already
   matches it; src/theme.ts takes over once they load. The path rule is surfaceFor() in src/surface.ts: keep the two in sync.
   The browser chrome colour matches the intro's field edge (#0a0a0a dark, #f2f2f2 light, = --b-bg in index.html); src/boot.ts
   holds it there while the intro is up and restores the page's colour as the intro dissolves.
   A new hire's pre-boarding page (/welcome/<token>) has no intro: a link from an email should show the page, so the root gets
   "ww-no-boot", which hides #ww-boot at once (index.html) and lets boot-words.js remove it. The legal documents (LEGAL below:
   /terms, /privacy, /recording, /acceptable-use, /dpa, /subprocessors, the paths of src/legal/paths.ts) have none either: they
   open from a sign-up checkbox, a calendar invitation or a contract, to be read. Everywhere else it plays on every load. */
(function () {
  var root = document.documentElement, meta = document.querySelector('meta[name="theme-color"]');
  var p = location.pathname;
  var app = /^\/(login|signup|github\/callback)\/?$/.test(p) || /^\/app(\/|$)/.test(p) || (p === "/" && /^#\//.test(location.hash))
    || /^\/welcome\/[^/]+\/?$/.test(p); // a new hire's pre-boarding page follows their light/dark choice, as the app does
  // keep in step with src/legal/paths.ts (and nginx.conf.template, which serves the shell for the same six)
  var LEGAL = /^\/(terms|privacy|recording|acceptable-use|dpa|subprocessors)\/?$/;
  if (!app) {
    root.setAttribute("data-theme", "light");
    if (LEGAL.test(p)) root.classList.add("ww-no-boot");
    // the landing page (/) opens on a dark live-action film: its intro is dark so nothing flashes white before the hero
    var film = p === "/" || p === "/index.html";
    if (film) root.classList.add("ww-boot-dark");
    if (meta) meta.setAttribute("content", film ? "#0a0a0a" : "#f2f2f2");
    return;
  }
  if (/^\/welcome\/[^/]+\/?$/.test(p)) root.classList.add("ww-no-boot");
  var t = null;
  try { t = localStorage.getItem("ww-theme"); } catch (e) {}
  if (t === "light" || t === "dark") root.setAttribute("data-theme", t);
  var dark = t === "dark" || (t !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  if (meta) meta.setAttribute("content", dark ? "#0a0a0a" : "#f2f2f2");
})();
