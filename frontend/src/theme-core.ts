/** The saved light/dark choice, without side effects: theme.ts (the app) applies it on import; the public site, which is always
 *  light, calls applySavedTheme() when it hands the page back to the app. While the site is showing (html.is-site) the site
 *  owns data-theme and the browser chrome colour, so apply() leaves them alone. */
export type ThemePref = "system" | "light" | "dark";

export const THEME_KEY = "ww-theme";

export function read(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch { return "system"; }
}

export function resolve(p: ThemePref): "light" | "dark" {
  if (p !== "system") return p;
  return typeof matchMedia !== "undefined" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function apply(p: ThemePref) {
  const root = document.documentElement;
  if (root.classList.contains("is-site")) return;
  if (p === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", p);
  // the browser chrome (mobile address bar) matches the top bar / rail of each theme
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", resolve(p) === "dark" ? "#000000" : "#0a0a0a");
}

export function applySavedTheme() {
  apply(read());
}
