/** The command palette: ⌘K (Ctrl+K elsewhere), "/" outside a text field, or openPalette() from anywhere.
 *  Fuzzy search over the commands the shell hands in, a "Recent" group, and, when a question is typed, "Ask the project". */
import { Fragment, KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, ReactNode, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon, IconName, Logo } from "./ui";
import { Kbd, isMac, modKey, reducedMotion } from "./fx";
import "./styles/palette.css";

/** `glyph` draws any icon (the lifecycle pages' own); `icon` names one of ui.tsx's. */
export type Command = { id: string; label: string; group: string; icon?: IconName; glyph?: ReactNode; hint?: string; keywords?: string; run: () => void };

const OPEN_EVENT = "ww-palette-open";
const RECENT_KEY = "ww-palette-recent";
const RECENT_MAX = 5;
const CLOSE_MS = 120;

/** Opens the palette from anywhere (a button, a menu item). An optional query is typed in for the viewer. */
export function openPalette(query?: string): void {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: typeof query === "string" ? { query } : undefined }));
}

/* ---------- fuzzy matching ---------- */

export type Match = { score: number; positions: number[] };

const SEPARATORS = " -_/.:·,()[]";

/** Subsequence match with scoring, in the spirit of fzy: matches at word starts, runs of consecutive characters and an
 *  early first match score higher; long gaps cost a little. Returns the best placement of the query's characters. */
export function fuzzyMatch(query: string, text: string): Match | null {
  const q = query.toLowerCase().replace(/\s+/g, "");
  const t = text.toLowerCase();
  const n = q.length, m = t.length;
  if (!n) return { score: 0, positions: [] };
  if (n > m) return null;
  // quick reject: is q a subsequence at all?
  for (let i = 0, j = 0; ; j++) {
    if (i === n) break;
    if (j === m) return null;
    if (q[i] === t[j]) i++;
  }
  const bonus = new Float64Array(m);
  for (let j = 0; j < m; j++) {
    const prev = j ? text[j - 1] : "";
    if (j === 0) bonus[j] = 1;
    else if (SEPARATORS.includes(prev)) bonus[j] = 0.9;
    else if (/[a-z]/.test(prev) && /[A-Z]/.test(text[j])) bonus[j] = 0.75;
  }
  // best[i][j]: best score with q[i] matched at t[j]; from[i][j]: where q[i-1] was matched
  const NEG = -Infinity;
  const best: Float64Array[] = [], from: Int32Array[] = [];
  for (let i = 0; i < n; i++) {
    const row = new Float64Array(m).fill(NEG), back = new Int32Array(m).fill(-1);
    for (let j = i; j < m; j++) {
      if (q[i] !== t[j]) continue;
      const base = 1 + bonus[j] * 1.6;
      if (i === 0) { row[j] = base - Math.min(j, 12) * 0.04; continue; }
      const prevRow = best[i - 1];
      let top = NEG, at = -1;
      for (let k = i - 1; k < j; k++) {
        if (prevRow[k] === NEG) continue;
        const gap = j - k - 1;
        const s = prevRow[k] + (gap === 0 ? 1.4 : -Math.min(gap, 10) * 0.06);
        if (s > top) { top = s; at = k; }
      }
      if (at >= 0) { row[j] = top + base; back[j] = at; }
    }
    best.push(row); from.push(back);
  }
  let end = -1, score = NEG;
  for (let j = 0; j < m; j++) if (best[n - 1][j] > score) { score = best[n - 1][j]; end = j; }
  if (end < 0) return null;
  const positions: number[] = new Array(n);
  for (let i = n - 1, j = end; i >= 0; i--) { positions[i] = j; j = from[i][j]; }
  // whole-word and prefix agreement read as intent, so they lead
  const raw = query.trim().toLowerCase();
  if (t.startsWith(raw)) score += 3;
  else if (t.includes(" " + raw)) score += 2;
  else if (t.includes(raw)) score += 1;
  return { score, positions };
}

/** How convincingly the query names a command, 0..1 (1: it is a prefix of a word in the label). */
function quality(query: string, match: Match) {
  const n = query.replace(/\s+/g, "").length;
  if (!n) return 0;
  return Math.max(0, Math.min(1, match.score / (n * 2.6 + 2)));
}

/* ---------- recents ---------- */

function readRecent(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").slice(0, RECENT_MAX) : [];
  } catch { return []; }
}
function pushRecent(id: string) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify([id, ...readRecent().filter((x) => x !== id)].slice(0, RECENT_MAX))); }
  catch { /* storage blocked */ }
}

/* ---------- results model ---------- */

type Item =
  | { kind: "ask"; key: string; question: string }
  | { kind: "command"; key: string; command: Command; positions: number[] };
type Section = { title: string; items: Item[] };

function buildSections(commands: Command[], query: string, recent: string[], canAsk: boolean): { sections: Section[]; preferred: number } {
  const q = query.trim();
  if (!q) {
    const byId = new Map(commands.map((c) => [c.id, c]));
    const recentCmds = recent.map((id) => byId.get(id)).filter((c): c is Command => !!c);
    const seen = new Set(recentCmds.map((c) => c.id));
    const sections: Section[] = [];
    if (recentCmds.length) sections.push({ title: "Recent", items: recentCmds.map((c) => ({ kind: "command", key: "r:" + c.id, command: c, positions: [] })) });
    const groups = new Map<string, Item[]>();
    for (const c of commands) {
      if (seen.has(c.id)) continue;
      if (!groups.has(c.group)) groups.set(c.group, []);
      groups.get(c.group)!.push({ kind: "command", key: c.id, command: c, positions: [] });
    }
    for (const [title, items] of groups) sections.push({ title, items });
    return { sections, preferred: 0 };
  }

  type Scored = { c: Command; m: Match; s: number; order: number };
  const scored: Scored[] = [];
  commands.forEach((c, order) => {
    const onLabel = fuzzyMatch(q, c.label);
    // keywords and the group name can find a command too, a little behind a label match and without highlights
    const extra = [c.keywords, c.group].filter(Boolean).join(" ");
    const onExtra = extra ? fuzzyMatch(q, extra) : null;
    const extraScore = onExtra ? onExtra.score * 0.8 : -Infinity;
    if (onLabel && onLabel.score >= extraScore) scored.push({ c, m: onLabel, s: onLabel.score + (recent.includes(c.id) ? 0.6 : 0), order });
    else if (onExtra) scored.push({ c, m: { score: extraScore, positions: [] }, s: extraScore + (recent.includes(c.id) ? 0.6 : 0), order });
  });
  scored.sort((a, b) => b.s - a.s || a.order - b.order);
  // once something clearly matches, drop the stragglers that only share scattered letters with the query
  if (scored.length && q.replace(/\s+/g, "").length >= 3) {
    const floor = scored[0].s * 0.55;
    for (let i = scored.length - 1; i > 0; i--) if (scored[i].s < floor) scored.splice(i, 1);
  }

  // groups appear in the order of their best match; inside a group, best first
  const groups = new Map<string, Scored[]>();
  for (const r of scored) {
    if (!groups.has(r.c.group)) groups.set(r.c.group, []);
    groups.get(r.c.group)!.push(r);
  }
  const sections: Section[] = [];
  if (canAsk) sections.push({ title: "Ask", items: [{ kind: "ask", key: "ask", question: q }] });
  for (const [title, rows] of groups) sections.push({ title, items: rows.map((r) => ({ kind: "command", key: r.c.id, command: r.c, positions: r.m.positions })) });

  // the best command is selected when it clearly names what was typed; a question goes to the project
  const top = scored[0];
  const looksLikeQuestion = /\?\s*$/.test(q) || q.split(/\s+/).length >= 4;
  const strong = !!top && !looksLikeQuestion && (top.m.positions.length ? quality(q, top.m) >= 0.55 : top.m.score >= q.length * 2);
  return { sections, preferred: canAsk && strong ? 1 : 0 };
}

/* ---------- small pieces ---------- */

function Highlight({ text, positions }: { text: string; positions: number[] }) {
  if (!positions.length) return <>{text}</>;
  const set = new Set(positions);
  const out: ReactNode[] = [];
  let run = "", on = false;
  const flush = (k: number) => { if (run) out.push(on ? <mark key={k}>{run}</mark> : <Fragment key={k}>{run}</Fragment>); run = ""; };
  for (let i = 0; i < text.length; i++) {
    const hit = set.has(i);
    if (hit !== on) { flush(i); on = hit; }
    run += text[i];
  }
  flush(text.length);
  return <>{out}</>;
}

function Hint({ hint }: { hint: string }) {
  return <span className="pal-hint">{hint.split(/\s+/).filter(Boolean).map((k, i) => <Kbd key={i}>{k}</Kbd>)}</span>;
}

function SearchGlyph() {
  return (
    <svg className="pal-search" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" />
    </svg>
  );
}

function DefaultGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

const isEditable = (el: EventTarget | null) => {
  const e = el as HTMLElement | null;
  if (!e || !e.tagName) return false;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName) || e.isContentEditable;
};

/* ---------- the palette ---------- */

export default function CommandPalette({ commands, onAsk }: { commands: Command[]; onAsk?: (question: string) => void }) {
  const [state, setState] = useState<"closed" | "open" | "closing">("closed");
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [recent, setRecent] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const closeTimer = useRef<number>(0);
  const glide = useRef<{ animate: boolean }>({ animate: false });
  const uid = useId().replace(/:/g, "");
  const listId = `pal-list-${uid}`;
  const optId = (i: number) => `pal-opt-${uid}-${i}`;
  const stateRef = useRef(state);
  stateRef.current = state;

  const { sections, preferred } = useMemo(() => buildSections(commands, query, recent, !!onAsk), [commands, query, recent, onAsk]);
  const flat = useMemo(() => sections.flatMap((s) => s.items), [sections]);

  // a new query re-selects the best row; arrow keys and the mouse move it from there
  useEffect(() => { setActive(preferred); glide.current.animate = false; }, [query, preferred]);
  useEffect(() => { if (active >= flat.length) setActive(Math.max(0, flat.length - 1)); }, [flat.length, active]);

  const open = useCallback((initial?: string) => {
    window.clearTimeout(closeTimer.current);
    if (stateRef.current === "open") { if (initial !== undefined) setQuery(initial); inputRef.current?.focus(); return; }
    // opening the palette while the intro still plays means: get to work
    if (document.getElementById("ww-boot")) { (window as Window & { __wwBootSkip?: boolean }).__wwBootSkip = true; window.dispatchEvent(new Event("ww-boot-skip")); }
    restoreRef.current = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
    setRecent(readRecent());
    setQuery(initial ?? "");
    setState("open");
  }, []);

  const close = useCallback(() => {
    if (stateRef.current !== "open") return;
    const back = restoreRef.current;
    restoreRef.current = null;
    if (reducedMotion()) setState("closed");
    else { setState("closing"); closeTimer.current = window.setTimeout(() => setState("closed"), CLOSE_MS); }
    // the page behind is interactive again before focus goes back to it
    document.getElementById("root")?.removeAttribute("inert");
    if (back && back.isConnected) back.focus({ preventScroll: true });
    else inputRef.current?.blur();
  }, []);

  // global shortcuts and the open event
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = isMac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
      if (mod && !e.altKey && !e.shiftKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        if (stateRef.current === "open") close(); else open();
        return;
      }
      if (e.key === "/" && !e.defaultPrevented && !e.metaKey && !e.ctrlKey && !e.altKey && stateRef.current !== "open" && !isEditable(e.target) && !isEditable(document.activeElement)) {
        e.preventDefault();
        open();
      }
    };
    const onOpen = (e: Event) => {
      const d = (e as CustomEvent<{ query?: string } | undefined>).detail;
      open(d && typeof d.query === "string" ? d.query : undefined);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener(OPEN_EVENT, onOpen); };
  }, [open, close]);

  // while open: the page behind is inert and does not scroll; focus goes to the field
  useLayoutEffect(() => {
    if (state !== "open") return;
    const root = document.getElementById("root");
    const html = document.documentElement;
    const prevOverflow = html.style.overflow;
    root?.setAttribute("inert", "");
    html.style.overflow = "hidden";
    inputRef.current?.focus({ preventScroll: true });
    return () => { root?.removeAttribute("inert"); html.style.overflow = prevOverflow; };
  }, [state]);

  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  // keep the selected row in view (and the first group's label with it), and slide the highlight onto it
  const [glideBox, setGlideBox] = useState<{ y: number; h: number; animate: boolean } | null>(null);
  useLayoutEffect(() => {
    if (state === "closed") return;
    const list = listRef.current;
    const el = list?.querySelector<HTMLElement>(`#${CSS.escape(optId(active))}`);
    if (!list || !el) { setGlideBox(null); return; }
    if (active === 0) list.scrollTop = 0;
    else {
      const top = el.offsetTop, bottom = top + el.offsetHeight, pad = 8;
      if (top - pad < list.scrollTop) list.scrollTop = top - pad - (el.dataset.first ? 28 : 0);
      else if (bottom + pad > list.scrollTop + list.clientHeight) list.scrollTop = bottom + pad - list.clientHeight;
    }
    setGlideBox({ y: el.offsetTop, h: el.offsetHeight, animate: glide.current.animate });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, flat, state]);

  const move = (delta: number) => {
    if (!flat.length) return;
    glide.current.animate = true;
    setActive((a) => (a + delta + flat.length) % flat.length);
  };

  const runItem = (item: Item | undefined) => {
    if (!item) return;
    if (item.kind === "ask") { close(); onAsk?.(item.question); return; }
    pushRecent(item.command.id);
    close();
    item.command.run();
  };

  const ask = () => {
    const q = query.trim();
    if (!q || !onAsk) return;
    close();
    onAsk(q);
  };

  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.nativeEvent.isComposing || stateRef.current !== "open") return;
    const k = e.key;
    if (k === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (k === "Tab") { e.preventDefault(); return; } // the field is the only stop: focus stays in the dialog
    if (k === "ArrowDown" || (e.ctrlKey && (k === "n" || k === "N"))) { e.preventDefault(); move(1); return; }
    if (k === "ArrowUp" || (e.ctrlKey && (k === "p" || k === "P"))) { e.preventDefault(); move(-1); return; }
    if (k === "PageDown") { e.preventDefault(); glide.current.animate = true; setActive((a) => Math.min(flat.length - 1, a + 5)); return; }
    if (k === "PageUp") { e.preventDefault(); glide.current.animate = true; setActive((a) => Math.max(0, a - 5)); return; }
    if (k === "Enter") {
      e.preventDefault();
      if ((isMac ? e.metaKey : e.ctrlKey) && onAsk) ask();
      else runItem(flat[active]);
    }
  };

  if (state === "closed") return null;

  const q = query.trim();
  const narrow = window.matchMedia("(max-width: 560px)").matches;
  const activeItem = flat[active];
  let index = -1;
  const count = flat.filter((i) => i.kind === "command").length;

  return createPortal(
    <div className="pal-layer" data-state={state}>
      <div className="pal-backdrop" onMouseDown={close} aria-hidden="true" />
      <div className="pal" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKeyDown}>
        <div className="pal-field">
          <SearchGlyph />
          <input
            ref={inputRef}
            className="pal-input"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={activeItem ? optId(active) : undefined}
            aria-label={onAsk ? "Search commands or ask a question" : "Search commands"}
            placeholder={onAsk ? (narrow ? "Search or ask a question…" : "Type a command or ask about the project…") : "Type a command…"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            spellCheck={false}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            enterKeyHint="go"
          />
          <button type="button" className="pal-esc" onClick={close} aria-label="Close command palette">
            <Kbd>esc</Kbd><span className="pal-cancel">Cancel</span>
          </button>
        </div>

        <div className="pal-list" ref={listRef} id={listId} role="listbox" aria-label="Commands">
          {glideBox && <div className={`pal-glide${glideBox.animate ? " anim" : ""}`} style={{ transform: `translateY(${glideBox.y}px)`, height: glideBox.h }} aria-hidden="true" />}
          {sections.map((s, si) => {
            const labelId = `pal-g-${uid}-${si}`;
            return (
              <div key={s.title + si} role="group" aria-labelledby={labelId} className={`pal-group${s.title === "Ask" ? " pal-group-ask" : ""}`}>
                <div className="pal-label" id={labelId} role="presentation">{s.title}</div>
                {s.items.map((item, ii) => {
                  index++;
                  const i = index;
                  const selected = i === active;
                  const common = {
                    id: optId(i), role: "option" as const, "aria-selected": selected,
                    "data-first": ii === 0 ? "1" : undefined,
                    className: `pal-item${selected ? " on" : ""}${item.kind === "ask" ? " ask" : ""}`,
                    onMouseMove: () => { if (active !== i) { glide.current.animate = true; setActive(i); } },
                    onMouseDown: (e: ReactMouseEvent) => e.preventDefault(),
                    onClick: () => runItem(item),
                  };
                  if (item.kind === "ask") return (
                    <div key={item.key} {...common}>
                      <span className="pal-icon"><Icon name="chat" size={16} /></span>
                      <span className="pal-text"><span className="pal-ask-lead">Ask the project:</span> <span className="pal-q">“{item.question}”</span></span>
                      <span className="pal-hint"><Kbd>{modKey}</Kbd><Kbd>↵</Kbd></span>
                    </div>
                  );
                  const c = item.command;
                  return (
                    <div key={item.key} {...common}>
                      <span className="pal-icon">{c.glyph ?? (c.icon ? <Icon name={c.icon} size={16} /> : <DefaultGlyph />)}</span>
                      <span className="pal-text"><Highlight text={c.label} positions={item.positions} /></span>
                      {c.hint && <Hint hint={c.hint} />}
                    </div>
                  );
                })}
              </div>
            );
          })}
          {!flat.length && (
            <div className="pal-empty" role="presentation">
              <Logo size={24} className="pal-empty-mark" />
              <p className="pal-empty-title">{q ? <>No commands match “{q}”</> : "No commands yet"}</p>
              <p className="pal-empty-text">{q ? "Try fewer letters, or a word from the page you want." : "Commands appear here as you use Welcome Wave."}</p>
            </div>
          )}
        </div>

        <div className="pal-foot" aria-hidden="true">
          <span className="pal-legend"><Kbd>↑</Kbd><Kbd>↓</Kbd> navigate</span>
          <span className="pal-legend"><Kbd>↵</Kbd> select</span>
          <span className="pal-legend"><Kbd>esc</Kbd> close</span>
          {onAsk && <span className="pal-legend pal-legend-ask"><Kbd>{modKey}</Kbd><Kbd>↵</Kbd> ask the project</span>}
        </div>
        <div className="sr-only" role="status" aria-live="polite">
          {q ? `${count} ${count === 1 ? "command" : "commands"}${onAsk ? ", or ask the project" : ""}` : ""}
        </div>
      </div>
    </div>,
    document.body,
  );
}
