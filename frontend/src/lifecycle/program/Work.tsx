/** Program sections: the first task (shortlist and brief), progress by area, blockers and weekly summaries. */
import { useState } from "react";
import { patch, post } from "../../api";
import { toast } from "../../ui";
import { Glyph } from "../../dataui";
import { How, plural, Quiet, shortDate, titleCase, useAction, useApi } from "../kit";
import WhyThis from "../WhyThis";
import { explainOf } from "../agentModel";
import { Actions, SectionProps } from "./shared";
import type { Area, Blocker, Candidate, FirstTaskBrief, Level, Shortlist, Summary } from "../types";

const codeHref = (path: string) => `#/code?path=${encodeURIComponent(path)}`;
const mmss = (s = 0) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/* ---------------------------------------------------------------- first task */

const FACTOR_LABEL: Record<string, string> = { focus: "Focus area", explained: "Explained", small: "Small", tested: "Tested", quiet: "Quiet files" };

function Brief({ b }: { b: FirstTaskBrief }) {
  const next = Array.isArray(b.next) ? b.next : b.next ? [b.next] : [];
  return (
    <div className="lc-brief">
      <div className="lc-inline">
        <p className="lc-brief-title">{b.title}</p>
        {b.issue ? <span className="lc-meta">Issue #{b.issue}</span> : null}
        <WhyThis explain={explainOf(b)} why={b.why} />
      </div>
      {b.files.length > 0 && (
        <div className="lc-chips">{b.files.map((f) => <a key={f} className="dp-chip" href={codeHref(f)}><Glyph name="file" size={12} />{f}</a>)}</div>
      )}
      {b.kt_clips?.length > 0 && (
        <div className="lc-clips">
          <p className="lc-label">Explained in a recorded session</p>
          <ul>
            {b.kt_clips.map((c, i) => {
              const at = c.at_s ?? c.start_s ?? 0;
              return (
                <li key={i}><a className="lc-clip" href={`#/meetings/${encodeURIComponent(c.meeting_id)}${at ? `?t=${Math.floor(at)}` : ""}`}>
                  <span className="clip-time">{mmss(at)}</span>{c.meeting_title ?? c.title ?? "KT session"}{(c.file_path ?? c.path) ? <span className="lc-dim"> · {c.file_path ?? c.path}</span> : null}
                </a></li>
              );
            })}
          </ul>
        </div>
      )}
      {b.acceptance_criteria?.length > 0 && (
        <div>
          <p className="lc-label">Done when</p>
          <ul className="lc-criteria">{b.acceptance_criteria.map((c, i) => <li key={i}><span className="lc-check" aria-hidden="true" />{c}</li>)}</ul>
        </div>
      )}
      {next.length > 0 && <p className="lc-next-line"><Glyph name="arrow" size={13} />{next.join(" ")}</p>}
    </div>
  );
}

function CandidateCard({ c, selected, busy, onSelect }: { c: Candidate; selected: boolean; busy: boolean; onSelect?: () => void }) {
  const score = Math.round((c.score ?? 0) * 100);
  const factors = Object.entries(c.factors ?? {}).filter(([, v]) => v > 0);
  return (
    <li className={`lc-cand${selected ? " on" : ""}`}>
      <div className="lc-cand-main">
        <p className="lc-cand-title">{c.title}</p>
        <p className="lc-dim lc-cand-sub">{c.kind === "issue" ? `Issue #${c.issue}` : c.issue ? `Task · issue #${c.issue}` : "Proposed task"} · {plural(c.files.length, "file")}</p>
        <div className="lc-factors">{factors.map(([k]) => <span key={k} className="lc-factor">{FACTOR_LABEL[k] ?? titleCase(k)}</span>)}</div>
      </div>
      <div className="lc-cand-side">
        <span className="lc-score" aria-label={`Score ${score} of 100`}>
          <span className="lc-score-bar"><i style={{ width: `${score}%` }} /></span><span className="lc-mono">{score}</span>
        </span>
        <div className="lc-inline">
          <WhyThis explain={explainOf(c)} why={c.why} />
          {onSelect && (selected ? <span className="lc-meta">Selected</span>
            : <button type="button" className="dp-btn small primary" disabled={busy} onClick={onSelect}>Select</button>)}
        </div>
      </div>
    </li>
  );
}

export function FirstTaskSection({ id, p, rel, reload }: SectionProps) {
  const list = useApi<{ first_task_shortlist: Shortlist }>(rel === "manager" ? `/onboardings/${encodeURIComponent(id)}/first-task/shortlist` : null);
  const act = useAction();
  const s = list.data?.first_task_shortlist;
  function select(c: Candidate) {
    act.run(c.id, () => post(`/onboardings/${encodeURIComponent(id)}/first-task`, { candidate_id: c.id })).then((r) => {
      if (r) { toast("First task selected"); reload(); list.reload(); }
    });
  }
  return (
    <div className="lc-stack">
      {p.first_task ? <Brief b={p.first_task} /> : rel !== "manager" && <Quiet icon="clock">Not selected yet. {rel === "hire" ? "Your manager picks it with you." : "The manager picks it."}</Quiet>}
      {rel === "manager" && (
        <div className="lc-shortlist">
          <div className="lc-inline">
            <p className="lc-label">{p.first_task ? "Shortlist" : "Choose from the shortlist"}</p>
            {s && (explainOf(s)
              ? <WhyThis explain={explainOf(s)} />
              : <How text={`Score: ${Object.entries(s.weights).map(([k, w]) => `${FACTOR_LABEL[k] ?? k} ${Math.round(w * 100)}`).join(", ")}. Files only one person can review are left out.`} />)}
          </div>
          {list.error ? <p className="error" role="alert">The shortlist could not be loaded.</p>
            : !s ? <div className="lc-skel-line" aria-busy="true" />
            : s.candidates.length === 0 ? <Quiet icon="alert">No candidates in {s.focus_area ?? "this project"} yet.</Quiet> : (
            <ul className="lc-cands">
              {s.candidates.map((c) => <CandidateCard key={c.id} c={c} selected={s.selected === c.id || p.first_task?.candidate_id === c.id}
                                                      busy={!!act.busy} onSelect={() => select(c)} />)}
            </ul>
          )}
          {s && s.excluded.length > 0 && (
            <details className="lc-details">
              <summary>{plural(s.excluded.length, "candidate")} left out</summary>
              <ul className="lc-list">
                {s.excluded.map((c) => <li key={c.id} className="lc-li"><span className="lc-li-main">{c.title}<span className="lc-li-sub">{c.why.join(" ")}</span></span></li>)}
              </ul>
            </details>
          )}
        </div>
      )}
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </div>
  );
}

/* ---------------------------------------------------------------- progress by area */

const LEVELS: Level[] = ["not_started", "exploring", "working", "contributed"];
const LEVEL_LABEL: Record<Level, string> = { not_started: "Not started", exploring: "Exploring", working: "Working", contributed: "Contributed" };

function LevelMeter({ level }: { level: Level }) {
  const at = LEVELS.indexOf(level);
  return <span className="lc-level" aria-hidden="true">{LEVELS.slice(1).map((l, i) => <i key={l} className={i < at ? "on" : ""} />)}</span>;
}

export function AreasSection({ id, rel }: SectionProps) {
  const areas = useApi<{ areas: Area[] }>(`/onboardings/${encodeURIComponent(id)}/areas`, ["lifecycle", "tasks", "knowledge"]);
  const act = useAction();
  function assess(a: Area, level: string) {
    act.run(a.area, () => patch(`/onboardings/${encodeURIComponent(id)}/areas/${a.area}`, { self_level: level })).then((r) => {
      if (r) { toast("Self-assessment saved"); areas.reload(); }
    });
  }
  if (areas.error) return <p className="error" role="alert">Progress could not be loaded.</p>;
  if (!areas.data) return <div className="lc-skel-line" aria-busy="true" />;
  if (areas.data.areas.length === 0) return <Quiet icon="layers">Areas appear once the repository is indexed.</Quiet>;
  return (
    <div className="lc-stack">
      <ul className="lc-list lc-areas">
        {areas.data.areas.map((a) => {
          const ev = a.evidence;
          const counts = [["step", ev.steps.length], ["clip watched", ev.watched.length], ["question", ev.questions.length], ["merged change", ev.merged.length]] as [string, number][];
          return (
            <li key={a.area} className="lc-li">
              <span className="lc-li-main"><code className="lc-area">{a.area}</code></span>
              <span className="lc-li-level"><LevelMeter level={a.level} /><span>{LEVEL_LABEL[a.level]}</span></span>
              <span className="lc-li-self">
                {rel === "hire" ? (
                  <label className="lc-self">
                    <span className="lc-dim">You</span>
                    <select value={a.self_level ?? ""} disabled={act.busy === a.area} onChange={(e) => e.target.value && assess(a, e.target.value)}>
                      <option value="">Not said</option>
                      {LEVELS.map((l) => <option key={l} value={l}>{LEVEL_LABEL[l]}</option>)}
                    </select>
                  </label>
                ) : <span className="lc-dim">{a.self_level ? `Says: ${LEVEL_LABEL[a.self_level]}` : "No self-assessment"}</span>}
              </span>
              <span className="lc-li-actions">
                <How label="Evidence" title="What the level is based on" text={
                  <>{counts.map(([k, n]) => <span key={k} className="lc-ev">{plural(n, k)}</span>)}<span className="lc-ev-why">{a.why.join(" ")}</span></>
                } align="end" />
              </span>
            </li>
          );
        })}
      </ul>
      <p className="lc-dim lc-footnote">Levels describe kinds of evidence. No scores, no ranking.</p>
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </div>
  );
}

/* ---------------------------------------------------------------- blockers */

function evidenceLines(e: Record<string, unknown>): string[] {
  return Object.entries(e).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) =>
    `${titleCase(k)}: ${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v)}`);
}

export function BlockersSection({ id, onChange }: SectionProps & { onChange?: () => void }) {
  const list = useApi<{ blockers: Blocker[] }>(`/onboardings/${encodeURIComponent(id)}/blockers`, ["lifecycle", "tasks"]);
  const act = useAction();
  const [resolving, setResolving] = useState<string | null>(null);
  const [note, setNote] = useState("");
  function resolve(b: Blocker) {
    act.run(b.id, () => post(`/blockers/${encodeURIComponent(b.id)}/resolve`, { note })).then((r) => {
      if (r) { toast("Blocker resolved"); setResolving(null); setNote(""); list.reload(); onChange?.(); }
    });
  }
  if (list.error) return <p className="error" role="alert">Blockers could not be loaded.</p>;
  if (!list.data) return <div className="lc-skel-line" aria-busy="true" />;
  const open = list.data.blockers.filter((b) => !b.resolved_at);
  const done = list.data.blockers.filter((b) => b.resolved_at);
  return (
    <div className="lc-stack">
      {open.length === 0 ? <Quiet>No open blockers.</Quiet> : (
        <ul className="lc-list">
          {open.map((b) => (
            <li key={b.id} className="lc-li lc-blocker">
              <span className={`lc-stage s-${b.stage}`}>{b.stage === "hire" ? "Hire" : b.stage === "mentor" ? "Mentor" : "Manager"}</span>
              <span className="lc-li-main">{b.text}<span className="lc-li-sub">Opened {shortDate(b.opened_at)} · {plural(b.business_days_open, "business day")}</span></span>
              <span className="lc-li-actions">
                {explainOf(b)
                  ? <WhyThis explain={explainOf(b)} detail={evidenceLines(b.evidence).map((l) => <span key={l} className="lc-ev">{l}</span>)} />
                  : <How label="Evidence" text={<>{evidenceLines(b.evidence).map((l) => <span key={l} className="lc-ev">{l}</span>)}<span className="lc-ev-why">{b.why.join(" ")}</span></>} align="end" />}
                {resolving !== b.id && <button type="button" className="dp-btn small" onClick={() => { setResolving(b.id); setNote(""); }}>Resolve</button>}
              </span>
              {resolving === b.id && (
                <form className="lc-addrow lc-resolve" onSubmit={(e) => { e.preventDefault(); resolve(b); }}>
                  <input autoFocus placeholder="Note (optional)" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} aria-label="Resolution note" />
                  <button type="submit" className="dp-btn primary small" disabled={!!act.busy}>Resolve</button>
                  <button type="button" className="dp-btn ghost small" onClick={() => setResolving(null)}>Cancel</button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
      {done.length > 0 && (
        <details className="lc-details">
          <summary>{plural(done.length, "resolved blocker")}</summary>
          <ul className="lc-list">
            {done.map((b) => <li key={b.id} className="lc-li is-done"><span className="lc-li-main">{b.text}</span><span className="lc-li-meta">Resolved {shortDate(b.resolved_at)}</span></li>)}
          </ul>
        </details>
      )}
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </div>
  );
}

/* ---------------------------------------------------------------- weekly summaries */

const CITE_HREF: Record<string, (ref: string) => string | null> = {
  task: () => "#/tasks", area: () => null, milestone: () => null, chat: () => "#/chat", blocker: () => null, checkin: () => null,
};

export function SummariesSection({ id }: SectionProps) {
  const list = useApi<{ summaries: Summary[] }>(`/onboardings/${encodeURIComponent(id)}/summaries`);
  const act = useAction();
  const [openLine, setOpenLine] = useState<string | null>(null);
  function generate() {
    act.run("gen", () => post(`/onboardings/${encodeURIComponent(id)}/summaries`, {})).then((r) => { if (r) { toast("This week's summary is written"); list.reload(); } });
  }
  if (list.error) return <p className="error" role="alert">Summaries could not be loaded.</p>;
  if (!list.data) return <div className="lc-skel-line" aria-busy="true" />;
  const rows = [...list.data.summaries].sort((a, b) => b.week_start.localeCompare(a.week_start));
  return (
    <div className="lc-stack">
      {rows.length === 0 ? <Quiet icon="file">No summaries yet.</Quiet> : rows.map((s) => (
        <article key={s.id} className="lc-summary">
          <div className="lc-summary-head lc-inline">
            <p><strong>Week of {shortDate(s.week_start)}</strong><span className="lc-dim"> · day {s.body.day}</span></p>
            <WhyThis explain={explainOf(s) ?? explainOf(s.body)} why={s.body.why} />
          </div>
          {s.body.lines.length === 0 ? <p className="lc-dim">Nothing recorded this week.</p> : (
            <ul className="lc-summary-lines">
              {s.body.lines.map((l, i) => {
                const k = `${s.id}:${i}`;
                return (
                  <li key={k}>
                    <span>{l.text}</span>
                    {l.citations.length > 0 && (
                      <button type="button" className="lc-src-btn" aria-expanded={openLine === k} onClick={() => setOpenLine(openLine === k ? null : k)}>
                        {plural(l.citations.length, "source")}
                      </button>
                    )}
                    {openLine === k && (
                      <span className="lc-chips">
                        {l.citations.map((c, j) => {
                          const href = CITE_HREF[c.kind]?.(c.ref) ?? null;
                          return href ? <a key={j} className="dp-chip" href={href}>{titleCase(c.kind)} · {c.label}</a>
                            : <span key={j} className="dp-chip">{titleCase(c.kind)} · {c.label}</span>;
                        })}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </article>
      ))}
      <Actions>
        <button type="button" className="dp-btn" onClick={generate} disabled={!!act.busy}>{act.busy ? "Writing…" : "Generate this week"}</button>
        {!rows.some((s) => explainOf(s) ?? explainOf(s.body)) && (
          <How text="Counted from records: tasks, milestones, areas, questions (counted, not quoted), blockers and check-ins. Nothing is written by a model." />
        )}
      </Actions>
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </div>
  );
}
