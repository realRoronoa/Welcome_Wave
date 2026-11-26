import { CSSProperties, Fragment, ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { api, errorMessage, ROLE_LABEL } from "../api";
import { useSession } from "../session";
import { replaceRoute } from "../route";
import { CountUp, EmptyState, Kbd, Reveal, Skeleton } from "../fx";
import { Avatar, Glyph, GlyphName, Pill, SectionHead, shortDay, Tone } from "../dataui";
import "../styles/risk.css";
import { useLive } from "../live";

/* ---------------------------------------------------------------- API shape (GET /api/knowledge-risk) */
type SpanState = "recorded" | "stale" | "none";
type Owner = { login: string; user_id: string | null; name: string; team: boolean; rule: string };
type Sym = {
  symbol: string; start_line: number; end_line: number; lines: number; state: SpanState; kt_seconds: number;
  clip: { meeting_id: string; meeting: string; at_s: number } | null;
};
type ReasonKind = "first_pr" | "task" | "action_item" | "question" | "named_not_shown" | "gap" | "stale";
type Reason = {
  kind: ReasonKind; text: string; weight: number; symbol: string | null; due: string | null; pending_approval: boolean;
  ref: { task_id?: string; meeting_id?: string; at_s?: number; message_id?: string; module?: string };
  meeting: string | null; answered?: boolean;
};
type Band = "critical" | "high" | "medium" | "low";
type FileRisk = {
  path: string; module: string; owners: Owner[]; voices: { speaker: string; user_id: string | null; seconds: number }[];
  lines_total: number; lines_recorded: number; lines_stale: number; recorded_share: number;
  symbols: Sym[]; unrecorded_symbols: { symbol: string; start_line: number; end_line: number }[];
  demand: Reason[]; demand_weight: number; risk: Band; risk_score: number;
  score_parts: { unrecorded: number; holder_factor: number; demand_factor: number };
  symbols_total?: number; unrecorded_total?: number; // a row lists its first 40 symbols; these count them all
};
type Person = {
  user_id: string; name: string; login: string | null; role: string; files_owned: number; sole_owner_files: number;
  lines_owned: number; lines_recorded: number; recorded_share: number; kt_minutes_spoken: number; open_expert_requests: number;
  onboardings_managed: number;
  exposure: { orphaned_files: string[]; orphaned_count?: number; unrecorded_lines: number; unrecorded_symbols: number; stale_lines: number;
    handover_minutes: number; risk_sum: number };
};
const orphans = (p: Person) => p.exposure.orphaned_count ?? p.exposure.orphaned_files.length;

/** An area (a module) whose every owned file has the same single named owner: it depends on one person. The headline is the
 *  area, not the person. Built from the files the page already has, so it needs nothing new from the API. */
type SoloArea = { module: string; holder: Owner; files: number; lines: number; recorded: number; risk: number };
function soloAreas(files: FileRisk[]): SoloArea[] {
  const by = new Map<string, FileRisk[]>();
  for (const f of files) by.set(f.module, [...(by.get(f.module) ?? []), f]);
  const out: SoloArea[] = [];
  for (const [module, fs] of by) {
    const owned = fs.filter((f) => f.owners.length > 0);
    const holder = owned[0]?.owners[0];
    if (!holder || holder.team || !owned.every((f) => f.owners.length === 1 && f.owners[0].login === holder.login)) continue;
    out.push({ module, holder, files: owned.length, lines: owned.reduce((n, f) => n + f.lines_total, 0),
      recorded: owned.reduce((n, f) => n + f.lines_recorded, 0), risk: owned.reduce((n, f) => n + f.risk_score, 0) });
  }
  return out.sort((a, b) => b.risk - a.risk || a.module.localeCompare(b.module));
}
const areaName = (m: string) => (m === "(root)" ? "Root files" : `${m}/`);
type Risk = {
  indexed: boolean; repository: { full_name: string; status: string } | null; has_codeowners: boolean;
  pace_s_per_line: number; pace_is_default: boolean;
  partial?: boolean; // some code searches failed or ran out of time: tasks and questions without a path may be missing
  truncated?: boolean; // the index hit its chunk cap: whole files late in the path order are left out
  files_total?: number; // file rows in the assessment; the response lists the riskiest 200
  summary: {
    code_files: number; code_lines: number; owned_lines: number; recorded_lines: number; stale_lines: number; recorded_line_share: number;
    single_owner_files: number; unowned_files: number; critical: number; high: number; medium: number; low: number;
    highest_exposure?: { user_id: string; name: string; recorded_share: number; handover_minutes: number } | null; // no longer shown: the headline is an area
  };
  files: FileRisk[]; people: Person[];
};

/* ---------------------------------------------------------------- helpers */
const BAND: Record<Band, { tone: Tone; label: string }> = {
  critical: { tone: "rec", label: "Critical" }, high: { tone: "warn", label: "High" },
  medium: { tone: "neutral", label: "Medium" }, low: { tone: "neutral", label: "Low" },
};
const STATE_LABEL: Record<SpanState, string> = { recorded: "explained in a KT session", stale: "explained, but the code changed since", none: "not recorded" };
const pct = (n: number) => `${Math.round(n * 100)}%`;
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const firstName = (name: string) => name.replace(/\(.*?\)/g, " ").trim().split(/\s+/)[0] || name;
const ts = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const lineRange = (a: number, b: number) => (a === b ? `L${a}` : `L${a}–${b}`);
const codeHref = (path: string, line?: number) => `#/code?path=${encodeURIComponent(path)}${line ? `&line=${line}` : ""}`;
const meetingHref = (id: string, at?: number) => `#/meetings/${encodeURIComponent(id)}${at ? `?t=${Math.floor(at)}` : ""}`;
const NB = "\u00a0"; // a number never wraps away from its unit
const duration = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}${NB}min${Math.round(s % 60) ? ` ${Math.round(s % 60)}${NB}s` : ""}` : `${Math.round(s)}${NB}s`);
const minutes = (m: number) => `${m < 10 ? m.toFixed(1) : Math.round(m)}${NB}min`;
const pace = (s: number) => `${s.toFixed(1)}${NB}s`;
const holderLabel = (f: FileRisk) =>
  f.owners.length === 0 ? "no owner" : f.score_parts.holder_factor === 1 ? "single holder" : f.score_parts.holder_factor === 0.6
    ? (f.owners.length === 1 ? "team" : "two holders") : "3+ holders";

/** One chip per demand signal on a file. Questions are grouped (one chip, a count); everything else is one chip per source.
 *  `brief` names the signal in the Why note. Questions carry no words or names: a new hire's chat is theirs, this page counts. */
type Chip = { key: string; glyph: GlyphName; label: string; brief: string; title: string; href?: string; weight: number };
function demandChips(f: FileRisk): Chip[] {
  const chips: Chip[] = [];
  const asked = { yes: new Set<string>(), no: new Set<string>(), w: { yes: 0, no: 0 } };
  for (const r of f.demand) {
    const due = r.due ? ` · due ${shortDay(r.due)}` : "";
    const pending = r.pending_approval ? " · awaiting approval" : "";
    const taskHref = r.pending_approval ? "#/approvals" : "#/tasks";
    switch (r.kind) {
      case "first_pr":
        chips.push({ key: `pr${r.ref.task_id}`, glyph: "pr", label: `First PR target${due}${pending}`, brief: "First PR", title: r.text, href: taskHref, weight: r.weight }); break;
      case "task":
        chips.push({ key: `t${r.ref.task_id}`, glyph: "task", label: `Task${due}${pending}`, brief: "Task", title: r.text, href: taskHref, weight: r.weight }); break;
      case "action_item":
        chips.push({ key: `a${r.ref.meeting_id}${r.ref.at_s}`, glyph: "play", label: `Action item · ${r.meeting ?? "KT session"}${r.ref.at_s != null ? ` ${ts(r.ref.at_s)}` : ""}`,
          brief: `Action item from ${r.meeting ?? "a KT session"}`, title: r.text, href: r.ref.meeting_id ? meetingHref(r.ref.meeting_id, r.ref.at_s) : undefined, weight: r.weight }); break;
      case "named_not_shown":
        chips.push({ key: `n${r.symbol}${r.ref.meeting_id}`, glyph: "video", label: `Named on ${r.meeting ?? "a KT session"}, never shown`,
          brief: `Named on ${r.meeting ?? "a KT session"}, not shown`, title: `${r.symbol}: ${r.text}`,
          href: r.ref.meeting_id ? meetingHref(r.ref.meeting_id, r.ref.at_s) : undefined, weight: r.weight }); break;
      case "gap":
        chips.push({ key: "gap", glyph: "layers", label: "Module never explained", brief: "Module never explained", title: r.text, weight: r.weight }); break;
      case "question": {
        const k = r.answered === false ? "no" : "yes";
        if (r.ref.message_id) asked[k].add(r.ref.message_id);
        asked.w[k] += r.weight; break;
      }
    }
  }
  const times = (n: number) => (n > 1 ? ` ×${n}` : "");
  if (asked.yes.size) chips.push({ key: "qy", glyph: "message", label: `Asked, no recording ×${asked.yes.size}`, brief: `Asked, no recording${times(asked.yes.size)}`,
    title: `${plural(asked.yes.size, "question")} from new hires about this code, answered from the code with no KT recording`, weight: asked.w.yes });
  if (asked.no.size) chips.push({ key: "qn", glyph: "message", label: `Asked, unanswered ×${asked.no.size}`, brief: `Asked, unanswered${times(asked.no.size)}`,
    title: `${plural(asked.no.size, "question")} from new hires that the assistant could not answer, matched to this code`, weight: asked.w.no });
  return chips;
}

/* ---------------------------------------------------------------- page */
export default function KnowledgeRisk({ params }: { params: URLSearchParams }) {
  const { project } = useSession();
  const [data, setData] = useState<Risk | null>(null);
  const [error, setError] = useState<string | null>(null);
  const switches = useRef(new Map<string, HTMLButtonElement>());

  const [rev, setRev] = useState(0);
  useLive(["knowledge", "sources", "people", "meetings"], () => setRev((n) => n + 1), project.id); // new KT links, a re-index, a new owner
  useEffect(() => {
    let live = true;
    setError(null); // the page is remounted per project, so the risk on screen stays until the fresh one arrives
    api<Risk>(`/knowledge-risk?project_id=${encodeURIComponent(project.id)}`)
      .then((d) => live && setData(d)).catch((e) => live && setError(errorMessage(e)));
    return () => { live = false; };
  }, [project.id, rev]);

  // ?simulate=<user_id> is the single source of truth, so a simulation can be deep-linked. Toggling replaces the URL (no history entry).
  const wanted = params.get("simulate");
  const sim = data?.people.find((p) => p.user_id === wanted) ?? null;
  function simulate(userId: string | null) {
    const q = new URLSearchParams(params);
    if (userId) q.set("simulate", userId); else q.delete("simulate");
    const qs = q.toString();
    replaceRoute(`#/knowledge-risk${qs ? `?${qs}` : ""}`);
  }
  useEffect(() => {
    if (!sim) return;
    const id = sim.user_id;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target as Element | null;
      if (t && t !== document.body && !t.closest?.("main")) return; // Escape in the top bar (notifications) or the drawer is theirs
      const card = switches.current.get(id)?.closest("li");
      const inside = !!card && card.contains(document.activeElement);
      simulate(null);
      if (inside) switches.current.get(id)?.focus(); // the card collapses: keep focus on its switch, not on <body>
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sim?.user_id]);

  // The simulated person's card sits below the files on narrow screens: a deep link and the Highest exposure button both
  // bring it into view (only when it is out of sight). After the button, focus moves to the card's switch, so keyboard
  // and screen-reader users land on the result too.
  function revealCard(userId: string, focus: boolean) {
    requestAnimationFrame(() => {
      const sw = switches.current.get(userId);
      const card = sw?.closest("li");
      if (!sw || !card) return;
      const r = card.getBoundingClientRect();
      if (r.top <= innerHeight * 0.6 && r.bottom > 0) return;
      card.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      if (focus) sw.focus({ preventScroll: true });
    });
  }
  const landed = useRef(false);
  useEffect(() => {
    if (landed.current || !data) return;
    landed.current = true;
    if (sim) revealCard(sim.user_id, false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, sim]);

  if (error) return <Frame project={project.name}><p className="error" role="alert">{error}</p></Frame>;
  if (!data) return <RiskSkeleton project={project.name} />;
  if (!data.indexed || data.files.length === 0) {
    const repo = data.repository;
    return (
      <Frame project={project.name}>
        <EmptyState
          title={data.indexed ? "No product code yet" : "No code to assess yet"}
          action={<a className="dp-btn primary" href="#/settings?tab=repository"><Glyph name="branch" size={15} />Repository settings</a>}>
          {data.indexed ? "Only docs, tests and root files so far."
            : !repo ? "Connect a repository to see who holds each part of the code."
            : repo.status === "failed" ? `Could not read ${repo.full_name}. Retry from repository settings.`
            : `Reading ${repo.full_name}. This page fills in when it finishes.`}
        </EmptyState>
      </Frame>
    );
  }

  const s = data.summary;
  const heldBy = (f: FileRisk, uid: string) => f.owners.length > 0 && f.owners.every((o) => o.user_id === uid);
  const areas = soloAreas(data.files), top = areas[0] ?? null;

  return (
    <section className={`dp rk${sim ? " is-sim" : ""}`}>
      <Head project={project.name}>
        <div className="rk-pace" title={data.pace_is_default
          ? "A default until KT sessions are linked to code. Handover estimates use it."
          : "Seconds of KT per line explained, from this project's KT sessions. Handover estimates use it."}>
          <Glyph name="clock" size={15} />
          <p><span><strong>{pace(data.pace_s_per_line)}</strong> of KT per line</span><span className="dp-meta">{data.pace_is_default ? "Default pace" : "Your team's KT pace"}</span></p>
        </div>
      </Head>

      {!data.has_codeowners ? (
        <div className="dp-banner" role="note">
          <Glyph name="alert" size={16} />
          <p>Add a CODEOWNERS file to see who holds each module.</p>
        </div>
      ) : s.unowned_files > 0 && (
        <div className="dp-banner" role="note">
          <Glyph name="alert" size={16} />
          <p>{plural(s.unowned_files, "file")} {s.unowned_files === 1 ? "has" : "have"} no owner in CODEOWNERS.</p>
        </div>
      )}

      {data.partial && (
        <div className="dp-banner" role="note">
          <Glyph name="alert" size={16} />
          <p>Some files may score lower than they should. Reload to try again.</p>
        </div>
      )}
      {data.truncated && (
        <div className="dp-banner" role="note">
          <Glyph name="alert" size={16} />
          <p>A large repository: some files are left out of the totals.</p>
        </div>
      )}

      <div className="dp-kpis">
        <Reveal className="dp-kpi spot">
          <p className="dp-kpi-label"><Glyph name="video" size={14} />Code on video</p>
          <p className="dp-kpi-value"><CountUp value={s.recorded_line_share * 100} format={(n) => `${Math.round(n)}%`} /></p>
          <div className="dp-seg" role="img" aria-label={`${s.recorded_lines} of ${s.code_lines} lines explained in a KT session${s.stale_lines ? `, ${s.stale_lines} stale` : ""}`}>
            {[{ n: s.recorded_lines, c: "kt" }, { n: s.stale_lines, c: "kt-stale" }, { n: s.code_lines - s.recorded_lines - s.stale_lines, c: "" }]
              .filter((p) => p.n > 0).map((p, i) => <span key={i} className={p.c} style={{ flexGrow: p.n, animationDelay: `${150 + i * 80}ms` }} />)}
          </div>
          <p className="dp-kpi-sub">{s.recorded_lines} of {s.code_lines} lines{s.stale_lines ? ` · ${s.stale_lines} stale` : ""}</p>
        </Reveal>
        <Reveal className="dp-kpi spot" delay={60}>
          <p className="dp-kpi-label"><Glyph name="users" size={14} />Single-owner files</p>
          <p className="dp-kpi-value"><CountUp value={s.single_owner_files} /><span className="dp-kpi-of">/ {s.code_files}</span></p>
          <div className="dp-seg" role="img" aria-label={`${s.single_owner_files} of ${s.code_files} files held by one person`}>
            {[{ n: s.single_owner_files, c: "mid" }, { n: s.code_files - s.single_owner_files, c: "" }]
              .filter((p) => p.n > 0).map((p, i) => <span key={i} className={p.c} style={{ flexGrow: p.n, animationDelay: `${150 + i * 80}ms` }} />)}
          </div>
          <p className="dp-kpi-sub">{!data.has_codeowners ? "No CODEOWNERS file" : s.single_owner_files === s.code_files ? "Every file rests on one person"
            : [s.code_files - s.single_owner_files - s.unowned_files > 0 && `${s.code_files - s.single_owner_files - s.unowned_files} shared`,
               s.unowned_files > 0 && `${s.unowned_files} unowned`].filter(Boolean).join(" · ")}</p>
        </Reveal>
        <Reveal className="dp-kpi spot" delay={120}>
          <p className="dp-kpi-label"><Glyph name="alert" size={14} />Critical files</p>
          <p className="dp-kpi-value"><CountUp value={s.critical} /></p>
          <div className="dp-seg" role="img" aria-label={`${s.critical} critical, ${s.high} high, ${s.medium + s.low} medium or low`}>
            {[{ n: s.critical, c: "risk" }, { n: s.high, c: "warn" }, { n: s.medium + s.low, c: "" }]
              .filter((p) => p.n > 0).map((p, i) => <span key={i} className={p.c} style={{ flexGrow: p.n, animationDelay: `${150 + i * 80}ms` }} />)}
          </div>
          <p className="dp-kpi-sub">{s.high ? `+${s.high} high` : s.critical ? "None high" : "Nothing critical or high"}</p>
        </Reveal>
        <Reveal className="dp-kpi spot rk-kpi-who" delay={180}>
          <p className="dp-kpi-label"><Glyph name="shield" size={14} />Rests on one person</p>
          {top ? (
            <>
              <p className="dp-kpi-value rk-kpi-name"><span>{areaName(top.module)}</span></p>
              <p className="dp-kpi-sub rk-kpi-why">
                Depends on one person: {firstName(top.holder.user_id ? top.holder.name : top.holder.login)}
                <span className="dp-meta">{plural(top.files, "file")} · {pct(top.lines ? top.recorded / top.lines : 0)} on video{areas.length > 1 ? ` · +${plural(areas.length - 1, "more area")}` : ""}</span>
              </p>
            </>
          ) : (
            <>
              <p className="dp-kpi-value rk-kpi-name"><span>None</span></p>
              <p className="dp-kpi-sub">{data.people.length ? "No area depends on one person" : "No person owns code in CODEOWNERS"}</p>
            </>
          )}
        </Reveal>
      </div>

      <div className="rk-layout">
        <div className="rk-grid">
          <section className="rk-col" aria-labelledby="rk-files">
            <SectionHead title="Files by risk" count={data.files_total ?? data.files.length} id="rk-files">
              {sim && (
                <span className="rk-sim-hint">
                  <span className="rk-sim-dot" aria-hidden="true" />Simulating {firstName(sim.name)} leaving
                  <button type="button" className="rk-sim-exit" onClick={() => simulate(null)}>Exit <Kbd>Esc</Kbd></button>
                </span>
              )}
            </SectionHead>
            <div className="rk-legend" aria-hidden="true">
              <span><i className="rk-sw s-recorded" />Explained</span>
              <span><i className="rk-sw s-stale" />Code changed since</span>
              <span><i className="rk-sw s-none" />Not recorded</span>
            </div>
            <ul className="rk-files">
              {data.files.map((f, i) => (
                <FileRow key={f.path} f={f} index={i}
                  dim={!!sim && !heldBy(f, sim.user_id)} orphan={!!sim && sim.exposure.orphaned_files.includes(f.path)} />
              ))}
            </ul>
            {(data.files_total ?? 0) > data.files.length && (
              <p className="rk-more" role="note">The {data.files.length} riskiest of {data.files_total} files.</p>
            )}
          </section>

          <aside className="rk-col" aria-labelledby="rk-people">
            <SectionHead title="Code owners" count={data.people.length} id="rk-people" />
            {data.people.length === 0 ? (
              <div className="rk-people-empty">
                <Glyph name="users" size={18} />
                <p>{data.has_codeowners ? "CODEOWNERS names only teams or people outside your organisation." : "Add a CODEOWNERS file to see who holds each module."}</p>
              </div>
            ) : (
              <ul className="rk-people">
                {data.people.map((p) => (
                  <PersonCard key={p.user_id} p={p} data={data} on={sim?.user_id === p.user_id} dim={!!sim && sim.user_id !== p.user_id}
                    onToggle={() => simulate(sim?.user_id === p.user_id ? null : p.user_id)}
                    switchRef={(el) => { if (el) switches.current.set(p.user_id, el); else switches.current.delete(p.user_id); }} />
                ))}
              </ul>
            )}
          </aside>
        </div>
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {sim ? `Simulating ${firstName(sim.name)} leaving: ${plural(orphans(sim), "file")} orphaned, about ${minutes(sim.exposure.handover_minutes)} of KT to hand over. Press Escape to stop.` : ""}
      </p>
    </section>
  );
}

/* ---------------------------------------------------------------- file row */
function FileRow({ f, index, dim, orphan }: { f: FileRisk; index: number; dim: boolean; orphan: boolean }) {
  const [open, setOpen] = useState(false);
  const whyId = useId();
  const band = BAND[f.risk];
  const chips = useMemo(() => demandChips(f), [f]);
  const firstGap = f.unrecorded_symbols[0]?.start_line ?? f.symbols[0]?.start_line;
  const slash = f.path.lastIndexOf("/");
  const recordedSyms = f.symbols.filter((s) => s.state !== "none").length;
  const stale = f.symbols.filter((s) => s.state === "stale");
  const voice = f.voices[0];
  const longest = f.symbols.reduce<Sym | null>((best, x) => (x.clip && (!best || x.kt_seconds > best.kt_seconds) ? x : best), null);
  const clip = longest?.clip ?? null;
  const moreSyms = Math.max(0, (f.unrecorded_total ?? f.unrecorded_symbols.length) - f.unrecorded_symbols.length);
  const barLabel = `${f.symbols_total ?? f.symbols.length} symbols${(f.symbols_total ?? 0) > f.symbols.length ? ` (the first ${f.symbols.length} shown)` : ""}: `
    + `${recordedSyms} explained in a KT session, ${f.unrecorded_total ?? f.unrecorded_symbols.length} not recorded`
    + (f.lines_stale ? `, ${f.symbols.filter((s) => s.state === "stale").length} with stale recordings` : "");

  return (
    <li className={`rk-file spot b-${f.risk}${dim ? " is-dim" : ""}${orphan ? " is-orphan" : ""}`} style={{ "--i": Math.min(index, 8) } as CSSProperties}>
      <div className="rk-score" aria-hidden="true">
        <span className="rk-score-n">{f.risk_score}</span>
        <span className="rk-score-m"><i style={{ width: `${f.risk_score}%` }} /></span>
      </div>
      <div className="rk-file-main">
        <div className="rk-file-top">
          <Pill tone={band.tone}><span className="sr-only">Risk </span>{band.label}<span className="sr-only">, score {f.risk_score} of 100</span></Pill>
          <a className="rk-path" href={codeHref(f.path, firstGap)} title={`Open ${f.path}${firstGap ? ` at line ${firstGap}` : ""}`}>
            {slash > 0 && <span className="rk-dir">{f.path.slice(0, slash + 1)}</span>}<span className="rk-base">{f.path.slice(slash + 1)}</span>
          </a>
          {orphan && <span className="dp-tag rk-orphan-tag"><Glyph name="alert" size={12} />Orphaned</span>}
          <span className="rk-owners">
            {f.owners.length === 0 ? <span className="rk-owner none">No owner</span> : f.owners.map((o) => (
              <span key={o.login} className={`rk-owner${o.team ? " team" : ""}`} title={`CODEOWNERS: ${o.rule}`}>
                {o.team ? <Glyph name="users" size={13} /> : <Avatar name={o.user_id ? o.name : o.login} size={20} />}
                <span><span className="sr-only">Owner </span>{o.user_id ? firstName(o.name) : o.name}</span>
              </span>
            ))}
          </span>
        </div>

        <div className="rk-bar" role="img" aria-label={barLabel}>
          {f.symbols.map((s) => {
            const title = `${s.symbol} · ${lineRange(s.start_line, s.end_line)} · ${STATE_LABEL[s.state]}${s.clip ? ` (${s.clip.meeting} at ${ts(s.clip.at_s)})` : ""}`;
            const href = s.clip ? meetingHref(s.clip.meeting_id, s.clip.at_s) : codeHref(f.path, s.start_line);
            return <a key={`${s.symbol}${s.start_line}`} className={`rk-seg s-${s.state}`} style={{ flexGrow: s.lines }} href={href} title={title} tabIndex={-1} aria-hidden="true" />;
          })}
        </div>

        <p className="rk-facts-line">
          {f.lines_stale ? (
            <span><strong>{f.lines_recorded}</strong> of {f.lines_total} lines current on video · <strong>{f.lines_stale}</strong> recorded before the code changed</span>
          ) : <span><strong>{f.lines_recorded}</strong> of {f.lines_total} lines on video</span>}
          {voice ? (() => {
            const who = `Explained by ${voice.user_id ? firstName(voice.speaker) : voice.speaker}${f.voices.length > 1 ? ` +${f.voices.length - 1}` : ""} · ${duration(voice.seconds)}`;
            return clip ? (
              <a className="rk-voice" href={meetingHref(clip.meeting_id, clip.at_s)} title={`Watch ${clip.meeting} at ${ts(clip.at_s)}`}>
                <Glyph name="play" size={12} />{who}, {clip.meeting}
              </a>
            ) : <span>{who}</span>;
          })() : f.lines_recorded + f.lines_stale === 0 ? <span>Never explained</span> : null}
        </p>

        {f.unrecorded_symbols.length > 0 && (
          <div className="rk-chiprow">
            <span className="rk-k">Not recorded</span>
            <span className="rk-chips">
              {f.unrecorded_symbols.map((u) => (
                <a key={`${u.symbol}${u.start_line}`} className="dp-chip rk-sym" href={codeHref(f.path, u.start_line)} title={`${u.symbol} · ${lineRange(u.start_line, u.end_line)}`}>
                  <span className="rk-sym-t">{u.symbol}</span>
                </a>
              ))}
              {moreSyms > 0 && <a className="dp-chip rk-sym rk-sym-more" href={codeHref(f.path)}>and {moreSyms} more</a>}
            </span>
          </div>
        )}

        {stale.length > 0 && (
          <div className="rk-chiprow">
            <span className="rk-k">Re-record</span>
            <span className="rk-chips">
              {stale.map((u) => (
                <a key={`${u.symbol}${u.start_line}`} className="dp-chip rk-sym is-stale" href={u.clip ? meetingHref(u.clip.meeting_id, u.clip.at_s) : codeHref(f.path, u.start_line)}
                  title={`${u.symbol} · ${lineRange(u.start_line, u.end_line)} · explained${u.clip ? ` in ${u.clip.meeting} at ${ts(u.clip.at_s)}` : ""}, but the code changed since`}>
                  <span className="rk-sym-t">{u.symbol}</span>
                </a>
              ))}
              {moreSyms > 0 && <a className="dp-chip rk-sym rk-sym-more" href={codeHref(f.path)}>and {moreSyms} more</a>}
            </span>
          </div>
        )}

        {chips.length > 0 && (
          <div className="rk-chiprow">
            <span className="rk-k">Needed for</span>
            <span className="rk-chips">
              {chips.map((c) => c.href ? (
                <a key={c.key} className="dp-tag rk-demand" href={c.href} title={c.title}><Glyph name={c.glyph} size={12} />{c.label}</a>
              ) : (
                <span key={c.key} className="dp-tag rk-demand" title={c.title}><Glyph name={c.glyph} size={12} />{c.label}</span>
              ))}
            </span>
          </div>
        )}

        <button type="button" className="rk-why" aria-expanded={open} aria-controls={whyId} onClick={() => setOpen((o) => !o)}>
          Why {f.risk_score}?<span className="sr-only"> ({f.path})</span><svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <div id={whyId} className="rk-why-body" hidden={!open}>
          <p className="rk-eq">
            <Factor label="Unrecorded" value={pct(f.score_parts.unrecorded)} />
            <span className="rk-op" aria-hidden="true">×</span><span className="sr-only">times</span>
            <Factor label={holderLabel(f)} value={f.score_parts.holder_factor.toFixed(1)} />
            <span className="rk-op" aria-hidden="true">×</span><span className="sr-only">times</span>
            <Factor label="Demand" value={f.score_parts.demand_factor.toFixed(2)} />
            <span className="rk-op" aria-hidden="true">=</span><span className="sr-only">equals</span>
            <Factor label="Risk" value={String(f.risk_score)} total />
          </p>
          <p className="rk-why-note">
            {f.demand_weight === 0 ? "No new hire is about to work on this file, so demand stays at its 0.50 floor."
              : <>Demand weight {f.demand_weight}{f.demand_weight > 4 ? " (counts up to 4)" : " of 4"}: {chips.map((c, i) => <Fragment key={c.key}>{i > 0 && " · "}{c.brief}{NB}+{c.weight}</Fragment>)}.</>}
            {" "}Unrecorded counts stale lines at half credit.
          </p>
        </div>
      </div>
    </li>
  );
}

function Factor({ label, value, total }: { label: string; value: string; total?: boolean }) {
  return <span className={`rk-factor${total ? " total" : ""}`}><span className="rk-factor-l">{label}</span> <span className="rk-factor-v">{value}</span></span>;
}

/* ---------------------------------------------------------------- person card */
function PersonCard({ p, data, on, dim, onToggle, switchRef }:
  { p: Person; data: Risk; on: boolean; dim: boolean; onToggle: () => void; switchRef: (el: HTMLButtonElement | null) => void }) {
  const bodyId = useId();
  const name = firstName(p.name);
  const orphaned = data.files.filter((f) => p.exposure.orphaned_files.includes(f.path));
  const noRecording = orphaned.every((f) => f.lines_recorded + f.lines_stale === 0);
  const x = p.exposure;

  return (
    <li className={`rk-person spot${on ? " is-on" : ""}${dim ? " is-dim" : ""}`}>
      <div className="rk-person-head">
        <Avatar name={p.name} size={38} />
        <div className="rk-person-id">
          <h3>{name}</h3>
          <p className="dp-meta">{ROLE_LABEL[p.role] ?? p.role}{p.login ? <> · <span className="rk-login">@{p.login}</span></> : null}</p>
        </div>
      </div>
      <ul className="rk-person-facts">
        <li><Glyph name="file" size={14} />Owns {plural(p.files_owned, "file")} · {p.lines_owned} lines</li>
        <li><Glyph name="video" size={14} />{p.kt_minutes_spoken > 0 ? `Spoke ${minutes(p.kt_minutes_spoken)} in KT sessions` : "Not in a KT session yet"}</li>
        <li className={p.open_expert_requests ? "" : "dim"} title={p.open_expert_requests ? `Questions the assistant could not answer, waiting in ${name}'s expert inbox` : undefined}>
          <Glyph name="message" size={14} />{p.open_expert_requests ? plural(p.open_expert_requests, "open question") : "No open questions"}</li>
      </ul>
      <div className="rk-sim-row">
        <span aria-hidden="true">Simulate departure</span>
        <button ref={switchRef} type="button" role="switch" aria-checked={on} aria-label={`Simulate departure of ${name}`} aria-controls={bodyId} className="rk-switch" onClick={onToggle}>
          <i aria-hidden="true" />
        </button>
      </div>
      <div id={bodyId} className="rk-sim-body" hidden={!on}>
        {on && (
          <>
            <p className="rk-sim-lead">
              <strong>If {name} leaves today:</strong>{" "}
              {orphaned.length === 0 ? <>nothing is orphaned.</> : (
                <>{plural(orphaned.length, "file")} {orphaned.length === 1 ? "has" : "have"} no other owner and {noRecording ? "no recording" : "little recording"}:{" "}
                  {orphaned.map((f, i) => (
                    <Fragment key={f.path}>{i > 0 && (i === orphaned.length - 1 ? " and " : ", ")}
                      <a className="rk-sim-path" href={codeHref(f.path, f.unrecorded_symbols[0]?.start_line)}>{f.path}</a>
                      {f.unrecorded_symbols.length > 0 && <span className="dp-dim"> ({f.unrecorded_symbols.map((u) => u.symbol).join(", ")})</span>}
                    </Fragment>
                  ))}.</>
              )}
            </p>
            {x.unrecorded_lines > 0 && (
              <div className="rk-handover">
                <p className="rk-handover-n">~{minutes(x.handover_minutes)}</p>
                <p className="rk-handover-t">
                  of KT to hand over the {x.unrecorded_lines} unrecorded {x.unrecorded_lines === 1 ? "line" : "lines"} only {name} holds.
                  {x.stale_lines > 0 && ` ${x.stale_lines} stale lines need re-recording too.`}
                </p>
              </div>
            )}
            <a className="dp-btn primary rk-plan" href={`#/kt-planner?presenter=${encodeURIComponent(p.user_id)}`}>
              <Glyph name="calendar" size={15} />Plan handover KT
            </a>
          </>
        )}
      </div>
    </li>
  );
}

/* ---------------------------------------------------------------- header, frames and loading */
function Head({ project, children }: { project: string; children?: ReactNode }) {
  return (
    <header className="dp-head">
      <div>
        <p className="dp-eyebrow">{project}</p>
        <h1>Knowledge risk</h1>
        <p className="dp-sub">Code only one person knows, ranked by what new hires touch next.</p>
      </div>
      {children}
    </header>
  );
}

/** The page with only its header and one block below it: errors and empty states. */
function Frame({ project, children }: { project: string; children: ReactNode }) {
  return <section className="dp rk"><Head project={project} />{children}</section>;
}

function RiskSkeleton({ project }: { project: string }) {
  return (
    <section className="dp rk" aria-busy="true">
      <span className="sr-only" role="status">Loading…</span>
      <Head project={project} />
      <div className="dp-kpis">{[0, 1, 2, 3].map((i) => <div key={i} className="dp-kpi"><Skeleton lines={3} height={14} /></div>)}</div>
      <div className="rk-layout">
        <div className="rk-grid">
          <div className="rk-col">
            <div className="rk-sk-head"><Skeleton lines={1} height={16} style={{ width: 140 }} /></div>
            <ul className="rk-files">{[0, 1, 2, 3].map((i) => (
              <li key={i} className="rk-file"><div className="rk-score"><Skeleton lines={1} height={26} /></div>
                <div className="rk-file-main"><Skeleton lines={3} height={12} /></div></li>
            ))}</ul>
          </div>
          <div className="rk-col">
            <div className="rk-sk-head"><Skeleton lines={1} height={16} style={{ width: 180 }} /></div>
            <ul className="rk-people">{[0, 1].map((i) => <li key={i} className="rk-person"><Skeleton lines={4} height={12} /></li>)}</ul>
          </div>
        </div>
      </div>
    </section>
  );
}
