/** Light, dark, or follow the system. The choice lives on <html data-theme="light"|"dark"> (no attribute = follow the
 *  system), so styles.css switches every token at once. Importing this module applies the saved choice (theme-core.ts holds
 *  the side-effect-free parts, which the public site uses too). */
import { useEffect, useState } from "react";
import { apply, read, resolve, THEME_KEY as KEY, type ThemePref } from "./theme-core";

export type { ThemePref };

const EVENT = "ww-theme";

let current: ThemePref = read();

/** What is actually showing right now, after resolving "system". */
export function resolvedTheme(): "light" | "dark" {
  return resolve(current);
}

export function getTheme(): ThemePref {
  return current;
}

export function setTheme(p: ThemePref) {
  current = p;
  try {
    if (p === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, p);
  } catch { /* storage blocked: the choice lasts for this page */ }
  // switching every token at once would animate each transition on the page; suppress them for one frame
  const root = document.documentElement;
  root.classList.add("theme-switching");
  apply(p);
  dispatchEvent(new CustomEvent(EVENT, { detail: p }));
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-switching")));
}

export function useTheme(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(current);
  useEffect(() => {
    const on = () => setPref(current);
    addEventListener(EVENT, on);
    return () => removeEventListener(EVENT, on);
  }, []);
  return [pref, setTheme];
}

if (typeof document !== "undefined") {
  apply(current);
  // another tab changed the theme
  addEventListener("storage", (e) => {
    if (e.key !== KEY && e.key !== null) return;
    current = read();
    apply(current);
    dispatchEvent(new CustomEvent(EVENT, { detail: current }));
  });
  // the system flipped while we follow it: only the browser chrome colour needs updating
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => { if (current === "system") apply(current); });
}
