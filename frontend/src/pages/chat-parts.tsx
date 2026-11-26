/** Small building blocks shared by the Ask, Code and Inbox pages: glyphs, copy button,
 *  an auto-growing textarea hook and a tiny, token-coloured syntax highlighter. */
import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import "../styles/chat.css";

const GLYPHS = {
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  check: ["M20 6L9 17l-5-5"],
  send: ["M12 19V5", "M5 12l7-7 7 7"],
  play: ["M7 4.5v15l12-7.5z"],
  file: ["M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z", "M14 3v6h6"],
  doc: ["M4 19.5A2.5 2.5 0 0 1 6.5 17H20", "M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"],
  shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z", "M9 12l2 2 4-4"],
  search: ["M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z", "M21 21l-4.3-4.3"],
  folder: ["M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"],
  arrow: ["M5 12h14", "M13 6l6 6-6 6"],
  user: ["M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2", "M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"],
  alert: ["M12 9v4", "M12 17h.01", "M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"],
  route: ["M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z", "M18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4z", "M6 15V9a4 4 0 0 1 4-4h6", "M18 9v6a4 4 0 0 1-4 4H8"],
  sparkle: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"],
  link: ["M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7", "M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"],
};
export type GlyphName = keyof typeof GLYPHS;

export function Glyph({ name, size = 16, fill = false }: { name: GlyphName; size?: number; fill?: boolean }) {
  return (
    <svg className="glyph" width={size} height={size} viewBox="0 0 24 24" fill={fill ? "currentColor" : "none"} stroke="currentColor"
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {GLYPHS[name].map((d) => <path key={d} d={d} />)}
    </svg>
  );
}

async function writeClipboard(text: string) {
  try { await navigator.clipboard.writeText(text); return true; } catch {
    // older browsers and insecure origins: fall back to a hidden textarea
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { /* nothing else to try */ }
    ta.remove();
    return ok;
  }
}

/** An icon button that copies `text` and confirms with a check for a moment. */
export function CopyButton({ text, label = "Copy", done = "Copied", className = "", showLabel = false }:
  { text: string; label?: string; done?: string; className?: string; showLabel?: boolean }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const click = async () => {
    const ok = await writeClipboard(text);
    setState(ok ? "ok" : "fail");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 1600);
  };
  const shown = state === "ok" ? done : state === "fail" ? "Copy failed" : label;
  return (
    <button type="button" className={`copy-btn ${state === "ok" ? "is-done" : ""} ${className}`} onClick={click} aria-label={shown} title={shown}>
      <span className="copy-icons"><Glyph name="copy" size={15} /><Glyph name="check" size={15} /></span>
      {showLabel && <span className="copy-label">{shown}</span>}
      <span className="sr-only" aria-live="polite">{state === "ok" ? done : ""}</span>
    </button>
  );
}

/** Grows a textarea with its content, up to `max` pixels. */
export function useAutoGrow(value: string, max = 240) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(max, el.scrollHeight)}px`;
    el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
  }, [value, max]);
  return ref;
}

/** Initials for an avatar: "Vijay (New hire)" becomes "V", "Ravi Kumar" becomes "RK". */
export function initials(name?: string | null) {
  const clean = (name || "").replace(/\(.*?\)/g, "").trim();
  if (!clean) return "?";
  const parts = clean.split(/\s+/);
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

/** Time a timer-driven state cycles through `items`, stopping on the last. */
export function useCycle(items: string[], every = 1500, active = true) {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (!active) { setI(0); return; }
    const t = window.setInterval(() => setI((n) => Math.min(items.length - 1, n + 1)), every);
    return () => window.clearInterval(t);
  }, [active, every, items.length]);
  return items[i];
}

/* ---------------- syntax highlighting ---------------- */

export type Tok = { k: "" | "kw" | "str" | "com" | "num" | "def" | "dec" | "head"; s: string };

const KW: Record<string, Set<string>> = {
  py: new Set("def class return if elif else for in not and or is None True False import from as with try except finally raise pass continue break while lambda yield async await global nonlocal assert del self".split(" ")),
  js: new Set("const let var function return if else for of in new class extends import from export default async await try catch finally throw typeof instanceof null undefined true false while switch case break continue interface type enum implements this as".split(" ")),
  go: new Set("func package import return if else for range var const type struct interface nil true false go defer chan map select switch case break continue default".split(" ")),
};
const DEF_AFTER = new Set(["def", "class", "function", "func", "interface", "type"]);

function langOf(path: string) {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "py") return "py";
  if (["js", "jsx", "ts", "tsx", "mjs", "cjs", "java", "kt", "cs", "rs", "swift", "c", "cc", "cpp", "h"].includes(ext)) return "js";
  if (ext === "go") return "go";
  if (ext === "md" || ext === "markdown") return "md";
  return "";
}

/** Splits each line into coloured tokens. Deliberately small: keywords, strings, comments, numbers, definitions. */
export function highlight(lines: string[], path: string): Tok[][] {
  const lang = langOf(path);
  if (!lang) return lines.map((s) => [{ k: "", s }]);
  if (lang === "md") return lines.map((s) => [{ k: /^#{1,6}\s/.test(s) ? "head" : "", s }]);
  const kw = KW[lang];
  const comment = lang === "py" ? "#" : "//";
  let triple: string | null = null; // open """ or ''' (python) or /* (others)
  return lines.map((line) => {
    const out: Tok[] = [];
    const push = (k: Tok["k"], s: string) => {
      if (!s) return;
      const last = out[out.length - 1];
      if (last && last.k === k) last.s += s; else out.push({ k, s });
    };
    let i = 0, lastWord = "";
    if (triple) {
      const end = line.indexOf(triple);
      if (end < 0) return [{ k: triple === "*/" ? "com" : "str", s: line }];
      push(triple === "*/" ? "com" : "str", line.slice(0, end + triple.length));
      i = end + triple.length; triple = null;
    }
    while (i < line.length) {
      const rest = line.slice(i);
      if (rest.startsWith(comment)) { push("com", rest); break; }
      if (lang !== "py" && rest.startsWith("/*")) {
        const end = rest.indexOf("*/", 2);
        if (end < 0) { push("com", rest); triple = "*/"; break; }
        push("com", rest.slice(0, end + 2)); i += end + 2; continue;
      }
      const tq = lang === "py" ? /^[rbfuRBFU]{0,2}("""|''')/.exec(rest) : null;
      if (tq) {
        const end = rest.indexOf(tq[1], tq[0].length);
        if (end < 0) { push("str", rest); triple = tq[1]; break; }
        push("str", rest.slice(0, end + 3)); i += end + 3; continue;
      }
      const str = (lang === "py" ? /^[rbfuRBFU]{0,2}("(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?)/ : /^("(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?|`(?:[^`\\]|\\.)*`?)/).exec(rest);
      if (str && (str[0].length > 1 || /^["'`]/.test(rest))) { push("str", str[0]); i += str[0].length; continue; }
      const dec = lang === "py" ? /^@[\w.]+/.exec(rest) : null;
      if (dec && !/\w/.test(line[i - 1] ?? "")) { push("dec", dec[0]); i += dec[0].length; continue; }
      const num = /^\d[\d_.xXa-fA-F]*/.exec(rest);
      if (num && !/\w/.test(line[i - 1] ?? "")) { push("num", num[0]); i += num[0].length; continue; }
      const word = /^[A-Za-z_$][\w$]*/.exec(rest);
      if (word) {
        const w = word[0];
        push(kw.has(w) ? "kw" : DEF_AFTER.has(lastWord) ? "def" : "", w);
        lastWord = w; i += w.length; continue;
      }
      push("", line[i]); i += 1;
    }
    return out;
  });
}

export function Tokens({ toks }: { toks: Tok[] }): JSX.Element {
  return <>{toks.map((t, i) => (t.k ? <span key={i} className={`tk-${t.k}`}>{t.s}</span> : t.s))}</>;
}

/** Reveals `text` progressively (word by word) when `animate` is true; renders instantly otherwise. */
export function useReveal(text: string, animate: boolean) {
  const [n, setN] = useState(animate ? 0 : text.length);
  const done = n >= text.length;
  useEffect(() => {
    if (!animate) { setN(text.length); return; }
    let raf = 0;
    const dur = Math.max(500, Math.min(1700, text.length * 5));
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur);
      let c = Math.round(text.length * (1 - Math.pow(1 - t, 1.6)));
      const sp = text.slice(c).search(/\s/); // snap to the end of a word
      c = t >= 1 || sp < 0 ? text.length : c + sp;
      setN(c);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [text, animate]);
  return { shown: text.slice(0, n), done };
}

/** Puts focus on an element once, after mount, without scrolling the page. */
export function useFocusOnce<T extends HTMLElement>(when: boolean) {
  const ref = useRef<T>(null);
  const did = useRef(false);
  const focus = useCallback(() => ref.current?.focus({ preventScroll: true }), []);
  useEffect(() => { if (when && !did.current) { did.current = true; focus(); } }, [when, focus]);
  return [ref, focus] as const;
}

export function Pill({ tone = "", children, title }: { tone?: "" | "kt" | "warn" | "info" | "rec"; children: ReactNode; title?: string }) {
  return <span className={`pill${tone ? ` pill-${tone}` : ""}`} title={title}>{children}</span>;
}
