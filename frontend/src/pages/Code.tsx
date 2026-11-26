import { CSSProperties, KeyboardEvent, ReactNode, SyntheticEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage, isManager, KTClip, mmss } from "../api";
import { useSession } from "../session";
import { EmptyState, reducedMotion, Ring, Skeleton } from "../fx";
import { CopyButton, Glyph, highlight, initials, Tokens } from "./chat-parts";
import { Logo, MarkLine } from "../ui";
import { useLive } from "../live";
import FeedbackButton from "../lifecycle/FeedbackButton";

type FileRow = { path: string; kt_clips: number };
type Module = { module: string; files: number; files_explained: number; kt_minutes: number };
type Sym = { symbol: string; start_line: number; end_line: number };
type Said = { speaker: string; text: string };
type Utterance = { start_s: number; end_s: number; speaker: string; text: string };

const clipKey = (c: KTClip) => `${c.meeting_id}@${c.start_s}`;
/** Not code modules: never flagged as a KT gap (the same rule the dashboard's KT gaps use). */
const NOT_CODE = ["(root)", "docs", "tests"];
const isGap = (m: Module) => m.files_explained === 0 && m.kt_minutes === 0 && !NOT_CODE.includes(m.module.split("/")[0]);
const fileHref = (p: string, line?: number) => `#/code?path=${encodeURIComponent(p)}${line ? `&line=${line}` : ""}`;

/** The headline feature: every line of code shows whether, when and where it was explained in a KT session. */
export default function Code({ path, line }: { path: string | null; line: number | null }) {
  const { project, me } = useSession();
  const [files, setFiles] = useState<FileRow[] | null>(null);
  const [modules, setModules] = useState<Module[]>([]);
  const [lines, setLines] = useState<string[]>([]);
  const [clips, setClips] = useState<KTClip[]>([]);
  const [symbols, setSymbols] = useState<Sym[]>([]);
  const [said, setSaid] = useState<Record<string, Said>>({});
  const [loadingFile, setLoadingFile] = useState(false);
  const [filter, setFilter] = useState("");
  const [hot, setHot] = useState<[number, number] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rev, setRev] = useState(0);
  // the line the feedback flag sits on: the hovered (or focused) line, else the line a link pointed at
  const [flag, setFlag] = useState<{ n: number; top: number; h: number } | null>(null);
  const sourceRef = useRef<HTMLDivElement>(null);
  useLive(["knowledge", "sources"], () => setRev((n) => n + 1), project.id); // a re-index or new KT links update the file list

  useEffect(() => {
    let alive = true;
    setError(null);
    api<FileRow[]>(`/code/files?project_id=${project.id}`).then((r) => alive && setFiles(r))
      .catch((e) => { if (alive) { setFiles([]); setError(errorMessage(e)); } });
    api<{ modules: Module[] }>(`/coverage?project_id=${project.id}`).then((r) => alive && setModules(r.modules)).catch(() => { /* coverage labels are optional */ });
    return () => { alive = false; };
  }, [project.id, rev]);

  const current = path ?? files?.find((f) => f.kt_clips > 0)?.path ?? files?.[0]?.path ?? null;
  // The tree is an accordion: one folder open at a time, the open file's folder first; a filter opens every match.
  const currentDir = current ? (current.includes("/") ? current.slice(0, current.lastIndexOf("/")) : "(root)") : null;
  const [openDir, setOpenDir] = useState<string | null>(currentDir);
  useEffect(() => setOpenDir(currentDir), [currentDir]);

  useEffect(() => {
    if (!current) return;
    let alive = true; // a slow answer for the previous file must never replace the current one
    setLines([]); setClips([]); setSymbols([]); setError(null); setLoadingFile(true); setHot(null); setFlag(null);
    const q = `project_id=${project.id}&path=${encodeURIComponent(current)}`;
    api<{ lines: string[] }>(`/code/file?${q}`).then((r) => alive && setLines(r.lines)).catch((e) => alive && setError(errorMessage(e)))
      .finally(() => alive && setLoadingFile(false));
    api<{ clips: KTClip[]; symbols?: Sym[] }>(`/code/explanations?${q}`).then((r) => {
      if (!alive) return;
      setClips(r.clips); setSymbols(r.symbols ?? []);
    }).catch(() => { /* no clips shown */ });
    return () => { alive = false; };
  }, [current, project.id]);

  // who was speaking when each clip starts, and what they said: read from the call's transcript (optional garnish)
  useEffect(() => {
    let alive = true;
    const ids = [...new Set(clips.map((c) => c.meeting_id))];
    Promise.all(ids.map((id) => api<{ notes?: { utterances?: Utterance[] } }>(`/meetings/${id}/notes`)
      .then((r) => [id, r.notes?.utterances ?? []] as const).catch(() => [id, [] as Utterance[]] as const)))
      .then((pairs) => {
        if (!alive) return;
        const byMeeting = new Map(pairs);
        const out: Record<string, Said> = {};
        for (const c of clips) {
          const us = byMeeting.get(c.meeting_id) ?? [];
          const u = us.find((x) => x.start_s <= c.start_s + 1 && x.end_s >= c.start_s) ?? us.find((x) => x.start_s >= c.start_s && x.start_s < c.end_s);
          if (u) out[clipKey(c)] = { speaker: u.speaker, text: u.text };
        }
        setSaid(out);
      });
    return () => { alive = false; };
  }, [clips]);

  // A deep link (a planner agenda row, a tour stop, a risk row) points at a function's first line: bring that line to about
  // a third of the way down the visible code, so the function below it is on screen too. It scrolls only when the line
  // is hidden or sits in the lower part of the window (no glide on load for nothing).
  useEffect(() => {
    if (!line) return;
    const el = document.getElementById(`L${line}`);
    if (!el) return;
    const head = document.querySelector<HTMLElement>(".cv .cv-head")?.offsetHeight ?? 46;
    const bar = document.querySelector<HTMLElement>(matchMedia("(max-width: 899px)").matches ? ".topbar" : ".stage-bar");
    const covered = (bar?.getBoundingClientRect().bottom ?? 0) + head; // the sticky top bar, then the sticky file header
    const visible = Math.max(1, innerHeight - covered);
    const r = el.getBoundingClientRect();
    if (r.top < covered || r.top > covered + visible * 0.45) {
      scrollTo({ top: Math.max(0, scrollY + r.top - covered - visible / 3), behavior: reducedMotion() ? "auto" : "smooth" });
    }
  }, [lines, line]);

  // which clip covers each line; clips without a line range cover the whole file
  const byLine = useMemo(() => {
    const m = new Map<number, KTClip>();
    for (const c of clips) {
      const from = c.start_line ?? 1, to = c.end_line ?? lines.length;
      for (let n = from; n <= to; n++) if (!m.has(n) || m.get(n)!.confidence < c.confidence) m.set(n, c);
    }
    return m;
  }, [clips, lines.length]);

  const toks = useMemo(() => (current ? highlight(lines, current) : []), [lines, current]);

  /* The feedback flag follows the line under the pointer (or the focused play mark); at rest it sits on the line a link
     pointed at. It stays put while its card is open, so the note always goes with the line it was opened on. */
  const flagOpen = () => !!sourceRef.current?.querySelector(".cv-line-flag [aria-expanded='true']");
  const flagAt = (el: HTMLElement | null) => {
    const box = sourceRef.current;
    if (!el || !box) return setFlag(null);
    const n = Number(el.id.slice(1));
    setFlag((f) => (f?.n === n ? f : { n, top: el.getBoundingClientRect().top - box.getBoundingClientRect().top, h: el.offsetHeight }));
  };
  const pointAt = (e: SyntheticEvent) => {
    const li = (e.target as HTMLElement).closest?.("li[id^='L']") as HTMLElement | null;
    if (li && !flagOpen()) flagAt(li);
  };
  const rest = () => { if (!flagOpen()) flagAt(line ? document.getElementById(`L${line}`) : null); };
  useEffect(() => { if (lines.length) rest(); }, [lines, line]); // eslint-disable-line react-hooks/exhaustive-deps

  // the lines render once per file (and when a link, a clip or a mark changes them), not each time the flag moves
  const lineList = useMemo(() => (
    <ol className="lines cv-lines">
      {lines.map((text, i) => {
        const n = i + 1, clip = byLine.get(n);
        const first = clip && (clip.start_line ?? 1) === n;
        const last = clip && (clip.end_line ?? lines.length) === n;
        const isHot = hot && n >= hot[0] && n <= hot[1];
        const cls = [clip && "explained", first && "x-first", last && "x-last", line === n && "target", isHot && "hot"].filter(Boolean).join(" ");
        return (
          <li key={n} id={`L${n}`} className={cls}>
            <span className="ln">{n}</span>
            {first ? (
              <a className="mark cv-play" href={`#/meetings/${clip!.meeting_id}?t=${Math.floor(clip!.start_s)}`}
                 title={`${clip!.meeting_title} at ${mmss(clip!.start_s)}`} aria-label={`Watch explanation at ${mmss(clip!.start_s)}`}
                 onMouseEnter={() => setHot([clip!.start_line ?? 1, clip!.end_line ?? lines.length])} onMouseLeave={() => setHot(null)}
                 onFocus={() => setHot([clip!.start_line ?? 1, clip!.end_line ?? lines.length])} onBlur={() => setHot(null)}>
                <Glyph name="play" size={9} fill />
              </a>
            ) : <span className={`mark ${clip ? "cont" : ""}`} aria-hidden="true" />}
            <code>{toks[i] ? <Tokens toks={toks[i]} /> : (text || " ")}{!text && " "}</code>
          </li>
        );
      })}
    </ol>
  ), [lines, toks, byLine, line, hot]);

  const needle = filter.trim().toLowerCase();
  const shown = useMemo(() => (files ?? []).filter((f) => !needle || f.path.toLowerCase().includes(needle)), [files, needle]);
  const tree = useMemo(() => {
    const groups = new Map<string, FileRow[]>();
    for (const f of shown) {
      const dir = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "(root)";
      groups.set(dir, [...(groups.get(dir) ?? []), f]);
    }
    return [...groups.entries()];
  }, [shown]);

  function onFilterKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && shown[0]) { e.preventDefault(); location.hash = fileHref(shown[0].path); }
    if (e.key === "Escape" && filter) { e.preventDefault(); setFilter(""); }
  }

  const explained = byLine.size;
  const segs = current ? current.split("/") : [];

  return (
    <section className="code-page cv">
      <header className="page-head">
        <p className="page-eyebrow">{project.name}</p>
        <h1>Code and KT</h1>
        <p>Teal lines were explained in a KT session. Select the mark to watch.</p>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      {!files ? <CodeSkeleton /> : files.length === 0 ? (!error && (
        <EmptyState title="No code is indexed for this project yet">
          {isManager(me.role) ? <>Connect a repository in <a href="#/settings">Settings</a>; indexing takes a few minutes.</> : "Your manager connects the repository; check back soon."}
        </EmptyState>
      )) : (
        <div className="code-layout cv-layout">
          <aside className="files cv-files" aria-label="Files">
            <div className="cv-search">
              <Glyph name="search" size={15} />
              <label htmlFor="cv-filter" className="sr-only">Filter files</label>
              <input id="cv-filter" type="search" value={filter} onChange={(e) => setFilter(e.target.value)} onKeyDown={onFilterKey}
                     placeholder="Filter files" autoComplete="off" spellCheck={false} />
              <span className="cv-search-count" aria-live="polite">{needle ? `${shown.length}/${files.length}` : files.length}</span>
            </div>
            <div className="cv-tree">
              {tree.length === 0 && <p className="cv-nomatch">No file matches “{filter.trim()}”.</p>}
              {tree.map(([dir, rows], di) => {
                const mod = modules.find((m) => dir === m.module || dir.startsWith(m.module + "/"));
                const open = !!needle || openDir === dir;
                const fold = { inert: open ? undefined : "" } as Record<string, string | undefined>;
                return (
                  <div key={dir} className={`dir cv-dir${open ? " open" : ""}`}>
                    <p className="dir-name cv-dir-name">
                      <button type="button" className="cv-dir-label cv-dir-toggle" aria-expanded={open} aria-controls={`cv-dir-${di}`}
                              onClick={() => { if (!needle) setOpenDir(open ? null : dir); }}>
                        <svg className="cv-dir-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
                             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
                        <Glyph name="folder" size={13} />{dir}
                      </button>
                      {mod && (mod.kt_minutes > 0 ? <span className="cov">{mod.kt_minutes} min of KT</span>
                        : isGap(mod) ? <span className="cov none">never explained</span> : <span className="cov quiet">no KT</span>)}
                    </p>
                    <div className="cv-dir-fold" id={`cv-dir-${di}`} {...fold}><div className="cv-dir-clip">
                    <ul>
                      {rows.map((f) => (
                        <li key={f.path}>
                          <a href={fileHref(f.path)} aria-current={f.path === current ? "true" : undefined}>
                            <span className="cv-file-name"><Match text={f.path.split("/").pop() ?? f.path} needle={needle} /></span>
                            {f.kt_clips > 0 && <span className="cv-kt-count" aria-label={`${f.kt_clips} KT clips`}><Glyph name="play" size={8} fill />{f.kt_clips}</span>}
                          </a>
                        </li>
                      ))}
                    </ul>
                    </div></div>
                  </div>
                );
              })}
            </div>
          </aside>

          <div ref={sourceRef} className="source cv-source" aria-label={current ?? "file"} onMouseLeave={rest}>
            <div className="cv-head">
              <nav className="source-path cv-crumbs" aria-label="File path">
                {segs.map((s, i) => i < segs.length - 1 ? (
                  <span key={i} className="cv-crumb">
                    <button type="button" className="cv-crumb-dir" onClick={() => setFilter(segs.slice(0, i + 1).join("/") + "/")} title={`Show files in ${segs.slice(0, i + 1).join("/")}`}>{s}</button>
                    <span className="cv-crumb-sep" aria-hidden="true">/</span>
                  </span>
                ) : <span key={i} className="cv-crumb-file" aria-current="page">{s}</span>)}
              </nav>
              <span className="cv-head-meta">
                {lines.length > 0 && explained > 0 && (
                  <span className="cv-cover" title={`${explained} of ${lines.length} lines explained in a KT session`}>
                    <Ring value={explained / lines.length} size={18} stroke={3} tone="kt" />
                    <span>{explained}<span className="cv-faint">/{lines.length}<span className="cv-lbl"> explained</span></span></span>
                  </span>
                )}
                {lines.length > 0 && explained === 0 && <span className="cv-faint">{lines.length} lines</span>}
                {current && <FeedbackButton kind="code" target={current} label="this file" className="cv-file-flag" />}
                {current && <CopyButton text={current} label="Copy path" className="cv-copy" />}
              </span>
            </div>
            {loadingFile && lines.length === 0 ? (
              <div className="cv-skel"><LinesSkeleton /></div>
            ) : (
              <div className="cv-scroll" onMouseOver={pointAt} onFocus={pointAt}>{lineList}</div>
            )}
            {flag && current && lines.length > 0 && (
              <div className="cv-line-flag" style={{ top: flag.top, height: flag.h }}>
                <FeedbackButton key={`${current}:${flag.n}`} kind="code" target={current} where={`Line ${flag.n}`} label={`line ${flag.n}`} />
              </div>
            )}
          </div>

          <aside className="clips cv-clips" aria-label="KT clips for this file">
            <h2>Explained in {clips.length > 0 && <span className="cv-h-count">{clips.length}</span>}</h2>
            {clips.length === 0 && !loadingFile && (
              <div className="cv-noclips">
                <Logo size={24} />
                <p>Not explained in a KT session yet.</p>
              </div>
            )}
            {clips.length === 0 && loadingFile && <div className="cv-clip cv-clip-skel"><Skeleton lines={3} /></div>}
            {clips.map((c, i) => {
              const s = said[clipKey(c)];
              const range: [number, number] = [c.start_line ?? 1, c.end_line ?? lines.length];
              return (
                <a key={i} className="clip-card cv-clip spot" href={`#/meetings/${c.meeting_id}?t=${Math.floor(c.start_s)}`}
                   style={{ animationDelay: `${i * 70}ms` } as CSSProperties}
                   onMouseEnter={() => setHot(range)} onMouseLeave={() => setHot(null)} onFocus={() => setHot(range)} onBlur={() => setHot(null)}>
                  <span className="kt-tile cv-thumb" aria-hidden="true">
                    <MarkLine className="kt-tile-mark" />
                    <span className="kt-play"><Glyph name="play" size={13} fill /></span>
                    <span className="cv-thumb-at">{mmss(c.start_s)}</span>
                    <span className="cv-thumb-dur">{mmss(c.end_s - c.start_s)}</span>
                  </span>
                  <span className="cv-clip-body">
                    <strong className="cv-clip-sym">{c.symbol ?? "Whole file"}</strong>
                    <span className="cv-clip-mtg">{c.meeting_title}</span>
                    {s && (
                      <span className="cv-said">
                        <span className="cv-said-who"><span className="cv-avatar" aria-hidden="true">{initials(s.speaker)}</span>{s.speaker}</span>
                        <span className="cv-quote">“{s.text}”</span>
                      </span>
                    )}
                    <span className="cv-clip-foot">
                      <span className="clip-time">{mmss(c.start_s)}–{mmss(c.end_s)}</span>
                      {c.start_line != null && <span className="cv-faint">L{c.start_line}{c.end_line && c.end_line !== c.start_line ? `–${c.end_line}` : ""}</span>}
                    </span>
                    <span className={`conf conf-${c.method}`}>
                      {c.method === "semantic" ? "Matched by meaning" : c.method === "symbol" ? "Function name on screen" : "File visible on screen"}
                      {`, ${Math.round(c.confidence * 100)}% sure`}
                    </span>
                    {c.code_changed_since && <span className="warn cv-changed"><Glyph name="alert" size={12} /> Code changed since</span>}
                  </span>
                </a>
              );
            })}

            {symbols.length > 0 && (
              <>
                <h2 className="cv-outline-h">In this file</h2>
                <ul className="cv-outline">
                  {symbols.map((s) => {
                    const on = byLine.has(s.start_line);
                    return (
                      <li key={`${s.symbol}${s.start_line}`}>
                        <a href={fileHref(current!, s.start_line)} aria-current={line === s.start_line ? "true" : undefined}>
                          <span className={`cv-sym-dot${on ? " on" : ""}`} aria-label={on ? "Explained in a KT session" : undefined} />
                          <code>{s.symbol}</code>
                          <span className="cv-faint">L{s.start_line}</span>
                        </a>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}

function Match({ text, needle }: { text: string; needle: string }): JSX.Element {
  const at = needle ? text.toLowerCase().indexOf(needle) : -1;
  if (at < 0) return <>{text}</>;
  const parts: ReactNode[] = [text.slice(0, at), <mark key="m">{text.slice(at, at + needle.length)}</mark>, text.slice(at + needle.length)];
  return <>{parts}</>;
}

const WIDTHS = [62, 38, 0, 74, 55, 81, 46, 0, 68, 33, 77, 51, 0, 59, 42];
function LinesSkeleton() {
  return (
    <div className="cv-lines-skel" aria-hidden="true">
      {WIDTHS.map((w, i) => <span key={i}><i className="cv-sk-ln" />{w > 0 && <i className="cv-sk-code" style={{ width: `${w}%` }} />}</span>)}
    </div>
  );
}

function CodeSkeleton() {
  return (
    <div className="code-layout cv-layout" aria-busy="true">
      <span className="sr-only" role="status">Loading code…</span>
      <div className="cv-files cv-panel-skel"><Skeleton lines={8} height={12} /></div>
      <div className="cv-source"><div className="cv-head"><Skeleton lines={1} height={12} style={{ width: 220 }} /></div><div className="cv-skel"><LinesSkeleton /></div></div>
      <div className="cv-clips"><div className="cv-clip cv-clip-skel"><Skeleton lines={4} /></div></div>
    </div>
  );
}
