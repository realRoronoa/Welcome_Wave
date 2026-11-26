import { ReactNode, RefObject, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage, isManager } from "../api";
import { useSession } from "../session";
import { replaceRoute } from "../route";
import { EmptyState, Kbd, Skeleton, reducedMotion } from "../fx";
import { Avatar, CodeBlock, Glyph } from "../dataui";
import "../styles/tour.css";
import { useLive } from "../live";

/* ---------------------------------------------------------------- API shapes (GET /api/tour) */
type Order = "execution" | "reading";
type Clip = {
  meeting_id: string; meeting_title: string; start_s: number; end_s: number; confidence: number;
  speaker: string | null; speaker_user_id: string | null; code_changed_since: boolean; meeting_duration_s: number | null;
};
type Owner = { user_id: string | null; name: string; login: string; rule: string; team: boolean };
type FirstPr = { task_id: string; title: string; issue: number | null; new_hire: string; new_hire_id: string; status: string; match: number | null };
type ActionItem = { text: string; meeting_id: string; meeting_title: string; at_s: number | null };
type Stop = {
  key: string; n: number; depth: number; trail: string[]; path: string; symbol: string; container: string | null; kind: string;
  start_line: number; end_line: number; summary: string | null; file_summary: string | null; code: string[]; code_truncated: boolean;
  calls: string[]; called_by: string[]; ref_chapter: string | null;
  calls_total?: number; called_by_total?: number; // a hub's edge lists are cut at 20; these count them all
  kt: Clip[]; owner: Owner | null; tags: ("first_pr_target" | "action_item" | "stale_clip")[];
  first_pr: FirstPr | null; first_prs?: FirstPr[]; action_item: ActionItem | null; // first_prs: every hire's that lands here, the viewer's own first
};
type Chapter = { id: string; title: string; entry: string; pinned: (ActionItem & { key: string })[]; stops: Stop[] };
type Row = { key: string; path: string; symbol: string; container: string | null; start_line: number; end_line: number };
type Tour = {
  indexed: boolean; sha: string | null; order: Order; truncated: boolean; files: number; symbols: number; hint: string | null;
  index_truncated?: boolean; // the repository is past the index's read limit: files late in path order are not toured
  partial?: boolean; // a first-PR search failed or ran out of time: that target may be missing
  tests_total: number; unreached_total: number;
  stats: { stops: number; explained: number; kt_seconds: number; unexplained_by_owner: { user_id: string | null; name: string; login: string; count: number }[] };
  chapters: Chapter[]; tests: (Row & { calls: string[] })[]; unreached: (Row & { kind: string; summary: string | null })[];
};
type Step = { chapter: Chapter; index: number; stop: Stop };

/* ---------------------------------------------------------------- wording and links */
const firstName = (name: string) => name.replace(/\(.*?\)/g, " ").trim().split(/\s+/)[0] || name;
const label = (s: { symbol: string; container: string | null }) => (s.container ? `${s.container}.${s.symbol}` : s.symbol);
const lines = (a: number, b: number) => (a === b ? `L${a}` : `L${a}–${b}`);
const ts = (s: number) => {
  const t = Math.max(0, Math.floor(s)), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
};
const shortMeeting = (title: string) => { const h = title.split(":")[0].trim(); return title.includes(":") && h.length <= 24 ? h : title; };
const minutes = (s: number) => { const m = s / 60; return m >= 10 ? `${Math.round(m)} min` : `${m.toFixed(1)} min`; };
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const codeHref = (path: string, line?: number) => `#/code?path=${encodeURIComponent(path)}${line ? `&line=${line}` : ""}`;
const clipHref = (meetingId: string, at: number | null) => `#/meetings/${encodeURIComponent(meetingId)}${at != null ? `?t=${Math.floor(at)}` : ""}`;
const chatHref = (s: Stop) => `#/chat?q=${encodeURIComponent(`How does ${s.symbol} in ${s.path} work?`)}`;
const keyParts = (key: string) => { const i = key.indexOf("::"); return { path: key.slice(0, i), symbol: key.slice(i + 2) }; };

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

/** Inside something that scrolls sideways (a wide code block the reader tabbed into): ← and → belong to it. */
const scrollsSideways = (t: EventTarget | null) => {
  for (let el = t instanceof Element ? t : null; el && el !== document.body; el = el.parentElement) {
    if (el.scrollWidth > el.clientWidth + 1 && /auto|scroll/.test(getComputedStyle(el).overflowX)) return true;
  }
  return false;
};

/* Glyphs the shared set does not have (24px grid, after Lucide, ISC licence). */
const MARKS = {
  flag: ["M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z", "M4 22v-7"],
  pin: ["M12 17v5", "M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"],
  info: ["M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z", "M12 16v-4", "M12 8h.01"],
  chevron: ["M6 9l6 6 6-6"],
  next: ["M9 18l6-6-6-6"],
  left: ["M19 12H5", "M12 19l-7-7 7-7"],
  right: ["M5 12h14", "M12 5l7 7-7 7"],
  corner: ["M15 10l5 5-5 5", "M4 4v7a4 4 0 0 0 4 4h12"],
};
function Mark({ name, size = 14, title }: { name: keyof typeof MARKS; size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden={title ? undefined : true} role={title ? "img" : undefined} aria-label={title}>
      {MARKS[name].map((d) => <path key={d} d={d} />)}
    </svg>
  );
}

function useMedia(query: string) {
  const [match, setMatch] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query), on = () => setMatch(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** Visited stop keys, per project and person, in this browser only. Storage can be blocked: the tour works without it. */
function useVisited(storeKey: string) {
  const [visited, setVisited] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem(storeKey) ?? "[]") as string[]); } catch { return new Set(); }
  });
  const mark = useCallback((key: string) => setVisited((v) => {
    if (v.has(key)) return v;
    const next = new Set(v).add(key);
    try { localStorage.setItem(storeKey, JSON.stringify([...next])); } catch { /* private mode: progress lasts this visit */ }
    return next;
  }), [storeKey]);
  return [visited, mark] as const;
}

/* ---------------------------------------------------------------- page */
export default function CodeTour({ params }: { params: URLSearchParams }) {
  const { project, me } = useSession();
  const wide = useMedia("(min-width: 980px)");
  const [order, setOrder] = useState<Order>(params.get("order") === "reading" ? "reading" : "execution");
  const [tour, setTour] = useState<Tour | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const urlStop = params.get("stop");
  const [current, setCurrent] = useState<string | null>(urlStop);
  const [listOpen, setListOpen] = useState(false);
  const [visited, markVisited] = useVisited(`ww-tour:${project.id}:${me.id}`);
  const paneRef = useRef<HTMLElement>(null);
  const scrollOnChange = useRef(false);
  const refocus = useRef(false); // the control that moved the tour had focus: keep it in the page if that control goes away
  const [moves, setMoves] = useState(0);
  const [rev, setRev] = useState(0);
  useLive(["knowledge", "sources", "meetings"], () => setRev((n) => n + 1), project.id);
  const pendingChapter = useRef<string | null>(null); // after an order switch, start this chapter over in the new order

  useEffect(() => {
    let live = true;
    setBusy(true);
    void rev; // reloads when the code or its KT links change (useLive below)
    api<Tour>(`/tour?project_id=${encodeURIComponent(project.id)}&order=${order}`)
      .then((t) => {
        if (!live) return;
        const ch = pendingChapter.current ? t.chapters.find((c) => c.id === pendingChapter.current) : undefined;
        const first = ch?.stops.find((s) => !s.ref_chapter);
        pendingChapter.current = null;
        if (first) setCurrent(first.key); // reading order starts at the chapter's leaves, execution order at its entry point
        setTour(t);
        setError(null);
      })
      .catch((e) => { if (live) setError(errorMessage(e)); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [project.id, order, attempt, rev]);

  // the order on screen is the one the server answered with; while a switch loads, the control already shows the choice
  const shownOrder: Order = busy ? order : (tour?.order ?? order);

  // the walk: every stop that is not a pointer back to an earlier chapter, in chapter order
  const steps = useMemo<Step[]>(() => (tour?.chapters ?? []).flatMap((chapter) =>
    chapter.stops.filter((s) => !s.ref_chapter).map((stop) => ({ chapter, index: 0, stop }))).map((s, index) => ({ ...s, index })), [tour]);
  const byKey = useMemo(() => new Map(steps.map((s) => [s.stop.key, s])), [steps]);
  const names = useMemo(() => {
    const m = new Map<string, string>();
    for (const ch of tour?.chapters ?? []) for (const s of ch.stops) m.set(s.key, label(s));
    for (const r of [...(tour?.tests ?? []), ...(tour?.unreached ?? [])]) m.set(r.key, label(r));
    return m;
  }, [tour]);

  // a link such as #/code-tour?stop=engine/validate.py::check_privileged, followed while the page is open
  useEffect(() => { if (urlStop) setCurrent(urlStop); }, [urlStop]);

  // start at the linked stop, else where this person left off, else the first stop
  const step = (current && byKey.get(current)) || steps.find((s) => !visited.has(s.stop.key)) || steps[0] || null;
  const key = step?.stop.key ?? null;
  const loadedOrder = tour?.order;
  useEffect(() => {
    if (!key) return;
    if (current !== key) setCurrent(key); // pin the resolved start, so marking it visited does not move the start along
    markVisited(key);
    const q = new URLSearchParams({ stop: key });
    if (loadedOrder === "reading") q.set("order", "reading"); // what is on screen, not a switch that is loading or failed
    const to = `#/code-tour?${q.toString().replace(/%3A/g, ":").replace(/%2F/g, "/")}`;
    replaceRoute(to); // deep-linkable, without a history entry per stop; the router follows
  }, [key, loadedOrder, markVisited]); // eslint-disable-line react-hooks/exhaustive-deps

  const go = useCallback((to: string) => { // a pointer stop shares its key with the stop it points at
    if (!byKey.has(to)) return;
    const active = document.activeElement;
    refocus.current = !!active && active !== document.body;
    scrollOnChange.current = true;
    setCurrent(to);
    setListOpen(false);
    setMoves((n) => n + 1);
  }, [byKey]);

  const move = useCallback((dir: 1 | -1) => {
    const next = step ? steps[step.index + dir] : undefined;
    if (next) go(next.stop.key);
  }, [go, step, steps]);

  // bring the new stop's top into view when it starts above the fold (a long stop, Next pressed at its bottom)
  useLayoutEffect(() => {
    if (!scrollOnChange.current || !paneRef.current) return;
    scrollOnChange.current = false;
    const top = paneRef.current.getBoundingClientRect().top;
    const bar = wide ? 52 : 56;
    if (top < bar + 8 || top > innerHeight * 0.6) {
      scrollTo({ top: scrollY + top - bar - 16, behavior: reducedMotion() ? "auto" : "smooth" });
    }
  }, [key, wide]);

  // The stop pane remounts on every move, and the mobile list closes: a chip, a call-path crumb or a list entry that had
  // focus is gone. Put focus on the new stop's title so keyboard and screen-reader users keep their place. Prev and Next
  // live in the footer, which stays, so they keep focus.
  useLayoutEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    const a = document.activeElement;
    if (!a || a === document.body || !a.isConnected || a.closest("details:not([open])")) {
      document.getElementById("ct-stop-title")?.focus({ preventScroll: true });
    }
  }, [moves]);

  // J / → next, K / ← previous, unless typing, a dialog is open, this is the second key of a "g then letter" shortcut,
  // or (arrows only) focus is in something that scrolls sideways, such as a long line of code on a phone
  useEffect(() => {
    let gAt = 0;
    const on = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || document.querySelector("[aria-modal='true']")) return;
      const k = e.key;
      if (k === "g" || k === "G") { gAt = Date.now(); return; }
      if (Date.now() - gAt < 1400) { gAt = 0; return; }
      if ((k === "ArrowRight" || k === "ArrowLeft") && scrollsSideways(e.target)) return;
      if (k === "j" || k === "J" || k === "ArrowRight") { e.preventDefault(); move(1); }
      else if (k === "k" || k === "K" || k === "ArrowLeft") { e.preventDefault(); move(-1); }
    };
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, [move]);

  function switchOrder(o: Order) {
    if (o === order && !error) return; // already showing it, or already loading it
    pendingChapter.current = tour && o !== tour.order && step ? step.chapter.id : null;
    setError(null);
    if (o === order) setAttempt((n) => n + 1); // this order failed to load: ask again
    else setOrder(o);
  }
  const retry = () => {
    pendingChapter.current = tour && order !== tour.order && step ? step.chapter.id : null; // where the reader is now
    setError(null);
    setAttempt((n) => n + 1);
  };

  const head = (
    <header className="dp-head ct-head">
      <div>
        <p className="dp-eyebrow">{project.name}</p>
        <div className="ct-title-row">
          <h1>Code tour</h1>
          <span className="ct-info">
            <button type="button" className="ct-info-btn" aria-label="About the tour" aria-describedby="ct-info-tip"
              onKeyDown={(e) => { if (e.key === "Escape") e.currentTarget.blur(); }}><Mark name="info" size={14} /></button>
            <span role="tooltip" id="ct-info-tip" className="ct-tip">
              Built from the calls in the code. Callbacks and dynamic calls can be missing. Rebuilt on every push.
            </span>
          </span>
        </div>
        <p className="dp-sub">The codebase in the order it runs.</p>
      </div>
      {(!tour || (tour.indexed && tour.chapters.length > 0)) && (
        <div className="ct-seg" role="group" aria-label="Order of the stops">
          <span className={`ct-seg-thumb${shownOrder === "reading" ? " right" : ""}`} aria-hidden="true" />
          <button type="button" data-order="execution" aria-pressed={shownOrder === "execution"} onClick={() => switchOrder("execution")} disabled={!tour}
            title="Each entry point first, then what it calls, in the order the code runs">Execution order</button>
          <button type="button" data-order="reading" aria-pressed={shownOrder === "reading"} onClick={() => switchOrder("reading")} disabled={!tour}
            title="Leaves first: every function before the code that calls it">Reading order</button>
        </div>
      )}
    </header>
  );

  if (!tour) {
    return error ? (
      <section className="dp ct">
        {head}
        <div className="error ct-error" role="alert">
          <span>{error}</span>
          <button type="button" className="dp-btn small" onClick={retry}>Try again</button>
        </div>
      </section>
    ) : <TourSkeleton head={head} wide={wide} />;
  }

  if (!tour.indexed) {
    return (
      <section className="dp ct">
        {head}
        <EmptyState title="No code to tour yet"
          action={isManager(me.role) ? <a className="dp-btn primary" href="#/settings?tab=repository">Connect a repository</a> : undefined}>
          {isManager(me.role) ? "The tour appears once a repository is connected." : "It appears once your manager connects the repository."}
        </EmptyState>
      </section>
    );
  }

  if (!tour.chapters.length || !step) {
    return (
      <section className="dp ct">
        {head}
        <EmptyState title="No entry point found" action={<a className="dp-btn primary" href="#/code">Open Code and KT</a>}>
          {tour.hint ?? "Nothing in the code calls into the rest of it."}
        </EmptyState>
        {tour.unreached.length > 0 && <Appendices tour={tour} names={names} onGo={go} standalone />}
      </section>
    );
  }

  const s = step.stop;
  const explored = steps.filter((x) => visited.has(x.stop.key)).length;
  const chapterNo = tour.chapters.indexOf(step.chapter) + 1;
  const stepper = <Stepper tour={tour} current={s.key} visited={visited} names={names} onGo={go} wide={wide} />;

  return (
    <section className="dp ct">
      {head}
      <Strip tour={tour} steps={steps} me={me} />
      {tour.truncated && (
        <p className="ct-note"><Mark name="info" size={14} />A large codebase: showing the 12 biggest entry points.</p>
      )}
      {tour.index_truncated && (
        <p className="ct-note" role="note"><Mark name="info" size={14} />Some files of this large repository are not in the tour.</p>
      )}
      {tour.partial && (
        <p className="ct-note" role="note"><Mark name="info" size={14} />A first-PR target may be missing. Reload to try again.</p>
      )}
      {error && (
        <div className="error ct-error ct-error-inline" role="alert">
          <span>{order !== tour.order ? `Could not switch to ${order} order. ${error}` : error}</span>
          <button type="button" className="dp-btn small" onClick={() => {
            retry(); // the alert goes away with this button in it: keep focus on the order being asked for
            requestAnimationFrame(() => document.querySelector<HTMLElement>(`.ct-seg button[data-order="${order}"]`)?.focus());
          }}>Try again</button>
        </div>
      )}

      <div className={`ct-grid${busy ? " is-busy" : ""}`} aria-busy={busy}>
        {wide ? <aside className="ct-side">{stepper}</aside> : (
          <details className="ct-list" open={listOpen} onToggle={(e) => setListOpen((e.currentTarget as HTMLDetailsElement).open)}>
            <summary>
              <span className="ct-list-text">
                <span className="ct-list-kicker">Chapter {chapterNo} of {tour.chapters.length}</span>
                <code>{label(s)}</code>
              </span>
              <span className="ct-list-cta">All stops<Mark name="chevron" size={15} /></span>
            </summary>
            {stepper}
          </details>
        )}

        <div className="ct-main">
          <StopPane key={s.key} paneRef={paneRef} step={step} chapterNo={chapterNo} me={me} names={names} byKey={byKey} onGo={go} />
          <footer className="ct-foot">
            <button type="button" className="dp-btn ct-prev" onClick={() => move(-1)} disabled={step.index === 0} aria-keyshortcuts="K ArrowLeft">
              <Mark name="left" size={15} /><span className="ct-btn-text">Previous<span className="sr-only"> stop</span></span><span className="ct-keys" aria-hidden="true"><Kbd>K</Kbd></span>
            </button>
            <div className="ct-progress">
              <Progress steps={steps} visited={visited} at={step.index} />
              <p><span>Stop {step.index + 1} of {steps.length}</span><span className="ct-progress-sep" aria-hidden="true"> · </span><span>{Math.round((explored / steps.length) * 100)}% explored</span></p>
            </div>
            <button type="button" className="dp-btn primary ct-next" onClick={() => move(1)} disabled={step.index === steps.length - 1}
              aria-keyshortcuts="J ArrowRight">
              <span className="ct-btn-text">Next<span className="sr-only"> stop</span></span><Mark name="right" size={15} /><span className="ct-keys" aria-hidden="true"><Kbd>J</Kbd></span>
            </button>
          </footer>
        </div>
      </div>
      <p className="sr-only" role="status" aria-live="polite">{`Stop ${step.index + 1} of ${steps.length}: ${label(s)} in ${s.path}`}</p>
    </section>
  );
}

/* ---------------------------------------------------------------- the strip under the header */
function Strip({ tour, steps, me }: { tour: Tour; steps: Step[]; me: { id: string; role: string } }) {
  const st = tour.stats;
  const manager = isManager(me.role);
  const mine = st.unexplained_by_owner.find((o) => o.user_id === me.id); // nobody is told to ask themselves
  const ask = st.unexplained_by_owner.filter((o) => o !== mine).slice(0, 3);
  const mineTitle = mine ? `${plural(mine.count, "stop")} you own ${mine.count === 1 ? "is" : "are"} not explained yet` : "";
  const mineBody = mine ? <><span>Yours to explain</span><span className="ct-x">×{mine.count}</span></> : null;
  return (
    <div className="ct-strip">
      {steps.length <= 48 ? (
        <span className="ct-dots" aria-hidden="true">
          {steps.map((x) => <i key={x.stop.key} className={x.stop.kt.length ? "on" : ""} />)}
        </span>
      ) : (
        <span className="ct-share" aria-hidden="true"><span style={{ width: `${(st.explained / Math.max(1, st.stops)) * 100}%` }} /></span>
      )}
      <p className="ct-strip-text">
        <span><strong>{st.stops}</strong> {st.stops === 1 ? "stop" : "stops"}</span>
        <span className="ct-sep" aria-hidden="true">·</span>
        <span><strong>{st.explained}</strong> explained{st.kt_seconds > 0 && <span className="ct-dim"> ({minutes(st.kt_seconds)})</span>}</span>
        {(mine || ask.length > 0) && (
          <>
            <span className="ct-sep ask" aria-hidden="true">·</span>
            <span className="ct-people">
              {mine && (
                <span className="ct-ask-item ct-mine">
                  {manager || me.role === "expert"
                    ? <a href="#/kt-planner" title={`${mineTitle}. Open your KT queue.`}>{mineBody}</a>
                    : <span title={mineTitle}>{mineBody}</span>}
                </span>
              )}
              {mine && ask.length > 0 && <span className="ct-sep" aria-hidden="true">·</span>}
              {ask.length > 0 && (
                <span className="ct-ask">
                  <span>Ask:</span>
                  {ask.map((o, i) => {
                    const body = <><Avatar name={o.name} size={20} /><span>{firstName(o.name)}</span><span className="ct-x">×{o.count}</span></>;
                    const title = `${plural(o.count, "stop")} owned by ${firstName(o.name)} not explained yet`;
                    return (
                      <span key={o.login} className="ct-ask-item">
                        {manager && o.user_id
                          ? <a href={`#/kt-planner?presenter=${encodeURIComponent(o.user_id)}`} title={`${title}. Plan a KT session with ${firstName(o.name)}.`}>{body}</a>
                          : <span title={title}>{body}</span>}
                        {i < ask.length - 1 && <span aria-hidden="true">,</span>}
                      </span>
                    );
                  })}
                </span>
              )}
            </span>
          </>
        )}
      </p>
      {tour.sha && (
        <span className="ct-sha" title={`Built from ${plural(tour.symbols, "symbol")} in ${plural(tour.files, "file")} at commit ${tour.sha}. Rebuilds on every push.`}>
          <Glyph name="commit" size={13} />{tour.sha.slice(0, 7)}
        </span>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- the stepper */
function Stepper({ tour, current, visited, names, onGo, wide }:
  { tour: Tour; current: string; visited: Set<string>; names: Map<string, string>; onGo: (key: string) => void; wide: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [bar, setBar] = useState<{ y: number; h: number } | null>(null);
  const [glide, setGlide] = useState(false);

  useLayoutEffect(() => {
    const root = box.current;
    if (!root) return;
    const measure = () => {
      const el = root.querySelector<HTMLElement>('[aria-current="step"]');
      if (!el) return setBar(null);
      const r = el.getBoundingClientRect(), b = root.getBoundingClientRect();
      setBar({ y: r.top - b.top + root.scrollTop, h: r.height });
    };
    measure();
    const el = root.querySelector<HTMLElement>('[aria-current="step"]');
    if (el && wide) { // keep the current stop in view inside the scrolling list, without moving the page
      const r = el.getBoundingClientRect(), b = root.getBoundingClientRect();
      const top = r.top - b.top, bottom = r.bottom - b.top;
      if (top < 8) root.scrollTop += top - 48;
      else if (bottom > root.clientHeight - 8) root.scrollTop += bottom - root.clientHeight + 48;
    }
    const ro = new ResizeObserver(measure);
    ro.observe(root);
    return () => ro.disconnect();
  }, [current, tour, wide]);
  useEffect(() => { // place it without animating the first time, then let it glide
    if (!bar || glide) return;
    const raf = requestAnimationFrame(() => setGlide(true));
    return () => cancelAnimationFrame(raf);
  }, [bar, glide]);

  return (
    <div className="ct-steps" ref={box}>
      <span className={`ct-bar${bar ? " on" : ""}${glide ? " glide" : ""}`} aria-hidden="true"
        style={bar ? { transform: `translateY(${bar.y}px)`, height: bar.h } : undefined} />
      <nav aria-label="Tour stops">
        <ol className="ct-chapters">
          {tour.chapters.map((ch, ci) => (
            <li key={ch.id} className="ct-ch">
              <div className="ct-ch-head">
                <p className="ct-ch-n">Chapter {ci + 1}</p>
                <p className="ct-ch-title">{ch.title}</p>
                <code className="ct-ch-entry" title={ch.entry}>{ch.entry}</code>
                {ch.pinned.map((p) => (
                  <a key={p.meeting_id + p.text} className="ct-ch-pin" href={clipHref(p.meeting_id, p.at_s)}
                    title={`From ${shortMeeting(p.meeting_title)}${p.at_s != null ? ` at ${ts(p.at_s)}` : ""}: ${p.text}`}>
                    <Mark name="pin" size={12} /><span>{p.text}</span>
                  </a>
                ))}
              </div>
              <ol className="ct-stops">
                {ch.stops.map((s) => {
                  if (s.ref_chapter) {
                    const no = tour.chapters.findIndex((c) => c.id === s.ref_chapter) + 1;
                    return (
                      <li key={s.key + ch.id}>
                        <button type="button" className="ct-stop is-ref" onClick={() => onGo(s.key)} style={{ ["--depth" as string]: Math.min(s.depth, 4) }}>
                          <span className="ct-dot ref" aria-hidden="true" />
                          <span className="ct-stop-text">
                            <code className="ct-stop-sym">{label(s)}</code>
                            <span className="ct-stop-path"><Mark name="corner" size={11} />see Chapter {no}</span>
                          </span>
                        </button>
                      </li>
                    );
                  }
                  const on = s.key === current;
                  const explained = s.kt.length > 0;
                  return (
                    <li key={s.key}>
                      <button type="button" className={`ct-stop${on ? " is-current" : ""}`} aria-current={on ? "step" : undefined} onClick={() => onGo(s.key)}
                        style={{ ["--depth" as string]: Math.min(s.depth, 4) }}>
                        <span className={`ct-dot${explained ? " on" : ""}`} aria-hidden="true" />
                        <span className="ct-stop-text">
                          <code className="ct-stop-sym">{label(s)}</code>
                          <span className="ct-stop-path">{s.path}</span>
                        </span>
                        <span className="ct-stop-marks">
                          {s.tags.includes("first_pr_target") && <span className="ct-mark-flag"><Mark name="flag" size={13} title="First PR lands here" /></span>}
                          {s.tags.includes("action_item") && <Mark name="pin" size={13} title="Action item from a KT session" />}
                          {visited.has(s.key) && !on && <span className="ct-mark-seen"><Glyph name="check" size={13} /></span>}
                        </span>
                        <span className="sr-only">
                          {explained ? ", explained in a KT session" : ", not explained yet"}{visited.has(s.key) ? ", visited" : ""}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </li>
          ))}
        </ol>
      </nav>
      <Appendices tour={tour} names={names} onGo={onGo} />
    </div>
  );
}

function Appendices({ tour, names, onGo, standalone }:
  { tour: Tour; names: Map<string, string>; onGo: (key: string) => void; standalone?: boolean }) {
  const inTour = new Set(tour.chapters.flatMap((c) => c.stops.map((s) => s.key)));
  if (!tour.tests.length && !tour.unreached.length) return null;
  return (
    <div className={`ct-appendix${standalone ? " standalone" : ""}`}>
      {tour.tests.length > 0 && (
        <details>
          <summary><span>Tests</span><span className="dp-count">{Math.max(tour.tests_total, tour.tests.length)}</span><Mark name="chevron" size={14} /></summary>
          <p className="ct-appendix-note">What each test calls.</p>
          <ul>
            {tour.tests.map((t) => (
              <li key={t.key}>
                <a className="ct-appendix-sym" href={codeHref(t.path, t.start_line)}><code>{label(t)}</code><span>{t.path}</span></a>
                {t.calls.length > 0 && (
                  <p className="ct-appendix-calls">
                    <span>calls</span>
                    {t.calls.map((k) => inTour.has(k)
                      ? <button key={k} type="button" className="ct-chip" onClick={() => onGo(k)}>{names.get(k) ?? keyParts(k).symbol}</button>
                      : <span key={k} className="ct-chip is-static">{names.get(k) ?? keyParts(k).symbol}</span>)}
                  </p>
                )}
              </li>
            ))}
          </ul>
          {tour.tests_total > tour.tests.length && <p className="ct-appendix-note">And {tour.tests_total - tour.tests.length} more in Code and KT.</p>}
        </details>
      )}
      {tour.unreached.length > 0 && (
        <details open={standalone}>
          <summary><span>Unreached</span><span className="dp-count">{Math.max(tour.unreached_total, tour.unreached.length)}</span><Mark name="chevron" size={14} /></summary>
          <p className="ct-appendix-note">Nothing in the tour or the tests calls these.</p>
          <ul>
            {tour.unreached.map((u) => (
              <li key={u.key}>
                <a className="ct-appendix-sym" href={codeHref(u.path, u.start_line)}><code>{label(u)}</code><span>{u.path} · {lines(u.start_line, u.end_line)}</span></a>
              </li>
            ))}
          </ul>
          {tour.unreached_total > tour.unreached.length && <p className="ct-appendix-note">And {tour.unreached_total - tour.unreached.length} more.</p>}
        </details>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- one stop */
function StopPane({ paneRef, step, chapterNo, me, names, byKey, onGo }: {
  paneRef: RefObject<HTMLElement>; step: Step; chapterNo: number; me: { id: string; role: string };
  names: Map<string, string>; byKey: Map<string, Step>; onGo: (key: string) => void;
}) {
  const s = step.stop;
  const prs = s.first_prs?.length ? s.first_prs : s.first_pr ? [s.first_pr] : [];
  return (
    <article className={`ct-pane${s.kt.length ? " is-explained" : ""}`} ref={paneRef} aria-labelledby="ct-stop-title">
      <p className="ct-kicker">Chapter {chapterNo} · Stop {s.n} of {step.chapter.stops.length}</p>
      {s.trail.length > 0 && (
        <ol className="ct-trail" aria-label="Call path to this stop">
          {s.trail.map((k) => (
            <li key={k}>
              <button type="button" onClick={() => onGo(k)}>{names.get(k) ?? keyParts(k).symbol}</button>
              <Mark name="next" size={12} />
            </li>
          ))}
        </ol>
      )}
      <h2 id="ct-stop-title" className="ct-title" tabIndex={-1}>
        <span>{label(s)}</span>
        {s.kind !== "function" && <span className="dp-tag ct-kind">{s.kind}</span>}
      </h2>
      <p className="ct-where"><a href={codeHref(s.path, s.start_line)}>{s.path} · {lines(s.start_line, s.end_line)}</a></p>
      {s.summary ? <p className="ct-summary">{s.summary}</p>
        : s.file_summary ? <p className="ct-summary is-file"><span className="ct-from" title="No docstring: this is the file's description">{s.path.split("/").pop()}</span>{s.file_summary}</p>
        : <p className="ct-summary is-none">No docstring</p>}

      {(prs.length > 0 || s.action_item) && (
        <div className="ct-banners">
          {prs.map((pr) => <FirstPrBanner key={pr.task_id} pr={pr} me={me} />)}
          {s.action_item && (
            <a className="ct-banner ct-banner-pin" href={clipHref(s.action_item.meeting_id, s.action_item.at_s)}>
              <span className="ct-banner-icon" aria-hidden="true"><Mark name="pin" size={15} /></span>
              <span className="ct-banner-text">
                <strong>From {shortMeeting(s.action_item.meeting_title)}{s.action_item.at_s != null && ` at ${ts(s.action_item.at_s)}`}:</strong> {s.action_item.text}
              </span>
              <span className="ct-banner-go"><Glyph name="play" size={12} />Hear it</span>
            </a>
          )}
        </div>
      )}

      <KtCard stop={s} me={me} />

      <CodeBlock code={s.code.join("\n")} start={s.start_line}
        label={<><span className="ct-code-path" title={s.path}>{s.path}</span><span className="ct-code-lines">{lines(s.start_line, s.end_line)}</span></>} />
      {s.code_truncated && (
        <p className="ct-cut">Showing the first {s.code.length} lines. <a href={codeHref(s.path, s.start_line)}>Read the rest in Code</a></p>
      )}

      <div className="ct-edges">
        <Edges title="Calls" keys={s.calls} total={s.calls_total} names={names} byKey={byKey} onGo={onGo}
          empty="Nothing else here." />
        <Edges title="Called by" keys={s.called_by} total={s.called_by_total} names={names} byKey={byKey} onGo={onGo}
          empty={step.chapter.entry === s.key
            ? "Nothing calls it: the chapter starts here."
            : "No callers found."} />
      </div>

      <p className="ct-actions">
        <a className="dp-btn" href={codeHref(s.path, s.start_line)}><Glyph name="code" size={15} />Open in Code</a>
      </p>
    </article>
  );
}

function FirstPrBanner({ pr, me }: { pr: FirstPr; me: { id: string; role: string } }) {
  const mine = pr.new_hire_id === me.id;
  const manager = isManager(me.role);
  const waiting = pr.status === "proposed";
  const href = manager && waiting ? "#/approvals" : "#/tasks";
  const detail = waiting ? (manager ? "Plan waiting for your approval" : "Plan waiting for approval") : pr.title.replace(/^\s*first\s+pr\s*:\s*/i, "");
  return (
    <a className="ct-banner ct-banner-pr" href={href}>
      <span className="ct-banner-icon" aria-hidden="true"><Mark name="flag" size={15} /></span>
      <span className="ct-banner-text">
        <strong>{mine ? "Your first PR lands here" : `${pr.new_hire}'s first PR lands here`}{pr.issue != null && ` · issue #${pr.issue}`}</strong>
        <span className="ct-banner-sub">{detail}{pr.match != null && ` · ${Math.round(pr.match * 100)}% match`}</span>
      </span>
      <span className="ct-banner-go">{manager && waiting ? "Review" : "Open task"}<Glyph name="arrow" size={12} /></span>
    </a>
  );
}

function KtCard({ stop: s, me }: { stop: Stop; me: { id: string; role: string } }) {
  if (s.kt.length) {
    return (
      <section className="ct-kt on" aria-label="Explained in a KT session">
        {s.kt.map((k) => {
          const len = Math.round(k.end_s - k.start_s);
          return (
            <div key={k.meeting_id + k.start_s} className="ct-clip">
              <a className="ct-play" href={clipHref(k.meeting_id, k.start_s)} aria-label={`Play ${shortMeeting(k.meeting_title)} from ${ts(k.start_s)}`}>
                <Glyph name="play" size={14} />
              </a>
              <div className="ct-clip-body">
                <p className="ct-clip-text">
                  Explained on <a href={clipHref(k.meeting_id, k.start_s)}>{k.meeting_title}</a>
                  {k.speaker && <> by <strong>{k.speaker}</strong></>}
                </p>
                <p className="ct-clip-meta">
                  <span>{ts(k.start_s)}–{ts(k.end_s)}</span><span aria-hidden="true">·</span><span>{len} s</span>
                  {k.code_changed_since && <span className="dp-tag t-warn"><Glyph name="alert" size={11} />Code changed since</span>}
                </p>
                {k.meeting_duration_s && k.meeting_duration_s >= k.end_s ? (
                  <span className="ct-timeline" title={`${ts(k.start_s)}–${ts(k.end_s)} of a ${ts(k.meeting_duration_s)} session`} aria-hidden="true">
                    <span className="ct-tl-track">
                      <span className="ct-tl-clip" style={{ left: `${(k.start_s / k.meeting_duration_s) * 100}%`, width: `${Math.max(1.5, ((k.end_s - k.start_s) / k.meeting_duration_s) * 100)}%` }} />
                    </span>
                    <span className="ct-tl-end">{ts(k.meeting_duration_s)}</span>
                  </span>
                ) : null}
              </div>
              {k.speaker && <span className="ct-clip-who" title={k.speaker}><Avatar name={k.speaker} size={28} /></span>}
            </div>
          );
        })}
      </section>
    );
  }
  const o = s.owner;
  const who = o ? (o.team ? o.name : firstName(o.name)) : null;
  const self = !!o?.user_id && o.user_id === me.id;
  let action: ReactNode;
  if (isManager(me.role)) {
    action = <a className="dp-btn primary small" href={`#/kt-planner${o?.user_id ? `?presenter=${encodeURIComponent(o.user_id)}` : ""}`}><Glyph name="calendar" size={14} />Add to KT plan</a>;
  } else if (me.role === "expert" && self) {
    action = <a className="dp-btn primary small" href="#/kt-planner"><Glyph name="calendar" size={14} />Open my KT queue</a>;
  } else {
    action = <a className="dp-btn primary small" href={chatHref(s)}><Glyph name="message" size={14} />Ask about it</a>;
  }
  return (
    <section className="ct-kt off" aria-label="Not explained in a KT session yet">
      <div className="ct-clip">
        {o && !o.team ? <Avatar name={o.name} size={36} /> : <span className="ct-noone" aria-hidden="true"><Glyph name="users" size={16} /></span>}
        <div className="ct-clip-body">
          <p className="ct-clip-text">
            Not explained yet.{" "}
            {who ? (self ? <strong>You own this code.</strong> : <strong>Ask {who}.</strong>) : <span className="ct-dim">No owner listed.</span>}
          </p>
          {o && <p className="ct-clip-meta"><span>CODEOWNERS</span><code>{o.rule}</code></p>}
        </div>
        <span className="ct-clip-action">{action}</span>
      </div>
    </section>
  );
}

function Edges({ title, keys, total, names, byKey, onGo, empty }: {
  title: string; keys: string[]; total?: number; names: Map<string, string>; byKey: Map<string, Step>; onGo: (key: string) => void; empty: string;
}) {
  const more = Math.max(0, (total ?? keys.length) - keys.length);
  return (
    <div className="ct-edge">
      <p className="ct-edge-title">{title}</p>
      {keys.length ? (
        <p className="ct-chips">
          {keys.map((k) => {
            const name = names.get(k) ?? keyParts(k).symbol;
            const at = byKey.get(k);
            return at ? (
              <button key={k} type="button" className="ct-chip" onClick={() => onGo(k)} title={`${k} · stop ${at.index + 1}`}>
                <span className={`ct-chip-dot${at.stop.kt.length ? " on" : ""}`} aria-hidden="true" />{name}
              </button>
            ) : <a key={k} className="ct-chip is-static" href={codeHref(keyParts(k).path)} title={k}>{name}</a>;
          })}
          {more > 0 && <span className="ct-chip-more">and {more} more</span>}
        </p>
      ) : <p className="ct-edge-none">{empty}</p>}
    </div>
  );
}

function Progress({ steps, visited, at }: { steps: Step[]; visited: Set<string>; at: number }) {
  if (steps.length > 60) {
    const share = steps.filter((x) => visited.has(x.stop.key)).length / steps.length;
    return <div className="ct-meter" aria-hidden="true"><span style={{ width: `${share * 100}%` }} /></div>;
  }
  return (
    <div className="ct-segments" aria-hidden="true">
      {steps.map((x, i) => <i key={x.stop.key} className={`${visited.has(x.stop.key) ? "seen" : ""}${i === at ? " now" : ""}`} />)}
    </div>
  );
}

/** The loaded page's shape (strip, list or stepper, stop), so nothing below jumps when the tour arrives. */
function TourSkeleton({ head, wide }: { head: ReactNode; wide: boolean }) {
  return (
    <section className="dp ct" aria-busy="true">
      <span className="sr-only" role="status">Loading the code tour…</span>
      {head}
      <div className="ct-strip ct-sk">
        <Skeleton lines={1} height={14} style={{ width: "min(520px, 100%)" }} />
        <span className="ct-sk-more"><Skeleton lines={1} height={14} style={{ width: "70%" }} /></span>
      </div>
      <div className="ct-grid">
        {wide ? <aside className="ct-side"><div className="ct-steps"><Skeleton lines={8} height={13} /></div></aside> : (
          // the loaded list summary in its own type, shimmering, so it has the same height
          <div className="ct-list ct-list-sk" aria-hidden="true">
            <span className="ct-list-text skeleton">
              <span className="ct-list-kicker">Chapter 1 of 2</span>
              <span className="ct-list-code">Handler.do_POST</span>
            </span>
            <span className="ct-list-cta">All stops<Mark name="chevron" size={15} /></span>
          </div>
        )}
        <div className="ct-main">
          <div className="ct-pane">
            <Skeleton lines={1} height={11} style={{ width: 160 }} />
            <Skeleton lines={1} height={24} style={{ width: "55%", marginTop: 14 }} />
            <Skeleton lines={2} height={13} style={{ marginTop: 14 }} />
            <Skeleton lines={1} height={64} style={{ marginTop: 18 }} />
            <Skeleton lines={1} height={180} style={{ marginTop: 14 }} />
          </div>
        </div>
      </div>
    </section>
  );
}
