/** #/agents: the multi-agent model, visible. A diagram (the Supervisor, the eight specialists, Approvals as the one way out),
 *  then one accordion row per agent: what starts it, what it uses, what it proposes, its mode and its recent runs.
 *  Managers and admins; executives read it without the actions. Admins switch an agent between AI and rules only.
 *  GET /api/agents, GET /api/agents/runs, PATCH /api/agents/{key}, POST /api/agents/tick (agents contract, Track H).
 *  A server without the agents answers 404: the page says so and nothing else breaks. Styles: styles/agents.css. */
import { ReactNode, useEffect, useId, useRef, useState } from "react";
import { patch, post, when } from "../api";
import { useSession } from "../session";
import { replaceRoute } from "../route";
import { toast } from "../ui";
import { EmptyState, reducedMotion, Skeleton } from "../fx";
import { Glyph } from "../dataui";
import { Accordion, AccordionItem } from "../accordion";
import AgentMap from "./AgentMap";
import {
  Agent, AgentRun, AgentsData, CrewMember, eventLabel, eventLabels, isPrivateRun, KIND_LABEL, LOCKED, normaliseAgents, TickResult, agentName,
} from "./agentModel";
import { RunTraceView } from "./Trace";
import { isStatus, LcHead, LoadError, plural, titleCase, useAction, useApi } from "./kit";
import "../styles/agents.css";

/* ---------------------------------------------------------------- glyphs, one per agent (24px grid, after Lucide, ISC) */

const AGENT_GLYPH: Record<string, string[]> = {
  supervisor: ["M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z", "M12 2v7", "M12 15v7", "M2 12h7", "M15 12h7"],
  librarian: ["M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z", "M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"],
  note_taker: ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"],
  guide: ["M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20z", "M16.2 7.8l-2.1 6.3-6.3 2.1 2.1-6.3z"],
  planner: ["M8 2v4", "M16 2v4", "M3 10h18", "M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"],
  scout: ["M4 22V4", "M4 4h13l-2.5 4.5L17 13H4"],
  reviewer: ["M6 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M18 15a3 3 0 1 1 0 6 3 3 0 0 1 0-6z", "M6 9v12", "M13 6h3a2 2 0 0 1 2 2v7"],
  coach: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  keeper: ["M21 12a9 9 0 1 1-2.64-6.36", "M21 3v6h-6"],
};
export function AgentGlyph({ agent, size = 17 }: { agent: string; size?: number }) {
  const paths = AGENT_GLYPH[agent] ?? ["M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z"];
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths.map((d) => <path key={d} d={d} />)}
    </svg>
  );
}

/** What an agent that never asks for a decision does instead. */
const NO_PROPOSALS: Record<string, string> = {
  supervisor: "Holds every proposal for a person in Approvals.",
  librarian: "Nothing to decide. It keeps the sources current.",
  guide: "Nothing to decide. What it can't answer goes to a person.",
  scout: "The manager's pick from the shortlist is the decision.",
  coach: "Alerts go to the hire, then the mentor, then the manager.",
};

const RUN_STATE: Record<string, { label: string; tone: "ok" | "bad" | "live" | "wait" | "off" }> = {
  ok: { label: "Done", tone: "ok" }, done: { label: "Done", tone: "ok" }, succeeded: { label: "Done", tone: "ok" }, success: { label: "Done", tone: "ok" },
  completed: { label: "Done", tone: "ok" }, failed: { label: "Failed", tone: "bad" }, error: { label: "Failed", tone: "bad" },
  running: { label: "Running", tone: "live" }, started: { label: "Running", tone: "live" }, pending: { label: "Waiting", tone: "wait" },
  waiting: { label: "Waiting", tone: "wait" }, interrupted: { label: "Waiting", tone: "wait" }, escalated: { label: "Sent to a person", tone: "wait" },
  skipped: { label: "Skipped", tone: "off" }, busy: { label: "Retried later", tone: "off" },
};
const runState = (s: string) => RUN_STATE[s?.toLowerCase()] ?? { label: titleCase(s || "unknown"), tone: "off" as const };

/** A run's line; the server's default for an unknown trigger names the raw event ("Ran on lifecycle.tick"). */
const runSummary = (r: AgentRun) => {
  const m = /^Ran on ([\w.]+)$/.exec(r.summary ?? "");
  return m ? `Ran on ${eventLabel(m[1]).replace(/^([A-Z])(?=[a-z])/, (c) => c.toLowerCase())}` : r.summary || eventLabel(r.trigger);
};

const effectiveMode = (a: Agent) => (a.mode === "rules" || !a.ai_available ? "rules" : "ai");

export function ModeChip({ a }: { a: Agent }) {
  const m = effectiveMode(a);
  const title = LOCKED.has(a.key) ? "It writes no text, so its mode is fixed" : !a.ai_available ? "AI isn't set up on this server" : undefined;
  return <span className={`ag-mode${m === "rules" ? " is-rules" : ""}`} title={title}>{m === "ai" ? "AI" : "Rules only"}</span>;
}

/* ---------------------------------------------------------------- the page */

export default function Agents({ params }: { params?: URLSearchParams }) {
  const { project, me, config } = useSession();
  const manager = me.role === "manager" || me.role === "admin", admin = me.role === "admin";
  const path = `/agents?project_id=${encodeURIComponent(project.id)}`;
  const data = useApi<AgentsData>(path, ["agents", "runs", "approvals", "lifecycle"]);
  const [open, setOpen] = useState<string | null>(params?.get("agent") ?? null);
  const [tick, setTick] = useState<{ r: TickResult; at: string } | null>(null);
  const act = useAction();
  const canTick = manager && config.mode !== "gcp";

  const d = data.data ? normaliseAgents(data.data) : null;
  const pendingTotal = d?.agents.reduce((n, a) => n + a.pending, 0) ?? 0;

  function runTick() {
    act.run("tick", async () => {
      try { return await post<TickResult>(`/agents/tick?project_id=${encodeURIComponent(project.id)}`, {}); }
      catch (e) { throw isStatus(e, 404) ? new Error("The daily check isn't available on this server yet.") : e; }
    }).then((r) => {
      if (!r) return;
      setTick({ r: { ran: r.ran ?? [], proposals: r.proposals ?? 0, alerts: r.alerts ?? 0 }, at: new Date().toISOString() });
      data.reload();
    });
  }
  // the palette's "Run daily check now" lands here with ?tick=1: run it once, then drop it from the address
  const ticked = useRef(false);
  useEffect(() => {
    if (params?.get("tick") !== "1" || ticked.current || !canTick || !d) return;
    ticked.current = true;
    replaceRoute("#/agents");
    runTick();
  }, [params?.get("tick"), !!d]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Open an agent's row from the diagram: fold the others, bring it into view, and put focus on its header. */
  function openRow(key: string) {
    setOpen(key);
    requestAnimationFrame(() => {
      const el = document.getElementById(`agent-${key}`);
      el?.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
      el?.querySelector<HTMLElement>(".acc-trigger")?.focus({ preventScroll: true });
    });
  }

  const head = (
    <LcHead title="Agents" sub="Each agent proposes. People decide in Approvals."
            actions={canTick && d ? (
              <button type="button" className="dp-btn" onClick={runTick} disabled={!!act.busy}>
                <Glyph name="clock" size={15} />{act.busy ? "Running…" : "Run daily check now"}
              </button>
            ) : undefined} />
  );

  if (data.error && !data.data) {
    return (
      <section className="lc ag">
        {head}
        {isStatus(data.error, 404)
          ? <EmptyState title="Agents aren't on this server yet">They appear here once the server is updated. Everything else works as before.</EmptyState>
          : <LoadError error={data.error} retry={data.reload} />}
      </section>
    );
  }
  if (!d) {
    return (
      <section className="lc ag" aria-busy="true">
        {head}
        <span className="sr-only" role="status">Loading…</span>
        <div className="lc-card ag-map-card"><Skeleton lines={1} height={300} /></div>
        {[0, 1, 2, 3].map((i) => <div key={i} className="lc-card ag-skel-row"><Skeleton lines={2} height={12} /></div>)}
      </section>
    );
  }

  const names = (keys: string[]) => keys.map((k) => d.agents.find((a) => a.key === k)?.name ?? agentName(k));
  return (
    <section className="lc ag">
      {head}
      {act.error && <p className="error" role="alert">{act.error}</p>}
      {tick && (
        <div className="ag-tick" role="status">
          <span className="ag-tick-mark" aria-hidden="true"><Glyph name="check" size={14} /></span>
          <div className="ag-tick-text">
            <p className="ag-tick-t">Daily check ran <span className="lc-dim">· {when(tick.at)}</span></p>
            <p className="lc-dim">
              {tick.r.ran.length ? `${names(tick.r.ran).join(", ")} ran.` : "Nothing was due."}
              {" "}{plural(tick.r.proposals, "proposal")}, {plural(tick.r.alerts, "alert")}.
            </p>
          </div>
          {tick.r.proposals > 0 && <a className="dp-btn small" href="#/approvals">Open Approvals</a>}
          <button type="button" className="dp-btn ghost small" onClick={() => setTick(null)} aria-label="Dismiss">Dismiss</button>
        </div>
      )}

      {d.agents.length === 0 ? (
        <EmptyState title="No agents on this project">Agents appear once the project has a repository.</EmptyState>
      ) : (
        <>
          <div className="lc-card ag-map-card">
            <AgentMap agents={d.agents} edges={d.edges} pendingTotal={pendingTotal} canApprove={manager} onOpen={openRow} />
          </div>

          <div className="ag-list-head" aria-hidden="true">
            <span>Agent</span><span className="ag-cols"><span>Mode</span><span>Runs, 7 days</span><span>Waiting</span><span>Last run</span></span>
          </div>
          <Accordion open={open} onChange={setOpen} className="ag-acc">
            {d.agents.map((a) => (
              <AccordionItem key={a.key} id={a.key} domId={`agent-${a.key}`} level={2} title={a.name} sub={a.role}
                             icon={<AgentGlyph agent={a.key} />} className="ruled" meta={<RowMeta a={a} />}>
                <AgentBody a={a} manager={manager} admin={admin} onChange={(next) => data.set((x) => x && ({
                  ...x, agents: x.agents.map((y) => (y.key === next.key ? { ...y, ...next } : y)),
                }))} />
              </AccordionItem>
            ))}
          </Accordion>
        </>
      )}
    </section>
  );
}

function RowMeta({ a }: { a: Agent }) {
  return (
    <span className="ag-cols">
      <span><ModeChip a={a} /></span>
      <span className="ag-num" title="Runs in the last 7 days">
        {a.runs_7d}<span className="ag-c-word"> {a.runs_7d === 1 ? "run" : "runs"} this week</span>
        {a.failed_7d > 0 && <span className="ag-failed"> · {a.failed_7d} failed</span>}
      </span>
      <span className={`ag-num${a.pending ? " is-on" : ""}`} title="Proposals waiting for a decision">
        {a.pending ? a.pending : <span aria-hidden="true">–</span>}<span className={a.pending ? "ag-c-word" : "sr-only"}>{a.pending ? " waiting" : "Nothing waiting"}</span>
      </span>
      <span className="ag-when">{a.last_run ? <><span className="ag-c-word">Last run </span>{when(a.last_run.at)}</> : "Not run yet"}</span>
    </span>
  );
}

/* ---------------------------------------------------------------- an open row */

function Fact({ k, children }: { k: string; children: ReactNode }) {
  return <div className="ag-fact"><p className="ag-fact-k">{k}</p><div className="ag-fact-v">{children}</div></div>;
}

function AgentBody({ a, manager, admin, onChange }: { a: Agent; manager: boolean; admin: boolean; onChange: (a: Agent) => void }) {
  const events = a.key === "supervisor" ? ["Every event"] : eventLabels(a.triggers);
  return (
    <div className="ag-body">
      <div className="ag-facts">
        <Fact k="Starts on">
          {events.length ? <span className="ag-chips">{events.map((e) => <span key={e} className="ag-chip">{e}</span>)}</span> : <span className="lc-dim">Nothing yet</span>}
        </Fact>
        <Fact k="Uses">
          {a.tools.length ? <span className="ag-chips">{toolLabels(a.tools).map((t) => <span key={t} className="ag-chip is-tool">{t}</span>)}</span> : <span className="lc-dim">Its own rules</span>}
        </Fact>
        <Fact k="Proposes">
          {a.approvals.length ? (
            <span className="ag-props">
              {a.approvals.map((k) => manager
                ? <a key={k} className="ag-prop" href={`#/approvals?kind=${encodeURIComponent(k)}`}>{KIND_LABEL[k] ?? titleCase(k)}<Glyph name="arrow" size={12} /></a>
                : <span key={k} className="ag-prop is-static">{KIND_LABEL[k] ?? titleCase(k)}</span>)}
            </span>
          ) : <span className="lc-dim">{NO_PROPOSALS[a.key] ?? "Nothing to decide."}</span>}
        </Fact>
        <Fact k="Mode"><ModeControl a={a} admin={admin} onChange={onChange} /></Fact>
      </div>
      {a.crew && a.crew.length > 0 && <CrewList members={a.crew} />}
      {manager && <Runs a={a} admin={admin} />}
    </div>
  );
}

/** The Guide's research crew (docs/specs/rag-crew.md): who does what between a question and its answer, in the order the
 *  question meets them, on a hairline rail like the trace's. Only the Guide has one; an older server sends none. */
function CrewList({ members }: { members: CrewMember[] }) {
  const head = useId();
  return (
    <div className="ag-crew">
      <p className="ag-fact-k" id={head}>Research crew</p>
      <ol className="ag-crew-list" aria-labelledby={head}>
        {members.map((m, i) => (
          <li key={`${m.key}-${i}`}>
            <span className="ag-crew-name">{m.name}</span>
            {m.role && <span className="ag-crew-role">{m.role}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** The tools each agent uses, as the API names them (agents/core/registry.py), in plain words. */
const TOOL_LABEL: Record<string, string> = {
  checkpointer: "Saved progress", "store.approvals": "Approvals", "store.notify": "Notifications", "store.record_run": "Run history",
  "repos.files": "Repository (read-only)", "repos.file": "Repository (read-only)", "blobs.get": "Uploaded documents",
  "index.upsert": "Project search", "index.delete_paths": "Project search", "index.search": "Project search", "index.all_paths": "File list",
  "index.search_grid": "Project search", "index.symbols_named": "Code symbols", // the Guide's research crew (rag-crew spec)
  "index.symbols_in": "Code symbols", "store.mark_kt_code_changed": "KT links", "store.add_kt_links": "KT links", "store.kt_links": "KT links",
  "llm.structured (video)": "AI, for video and code", "llm.structured": "AI, for wording", "store.save_meeting_result": "KT session notes",
  "store.codeowners": "Code owners", "store.create_expert_request": "Expert inbox", "lifecycle.verified_answers": "Verified answers",
  "repos.good_first_issues": "Good first issues", "store.create_tasks": "Tasks", "lifecycle.templates": "Program templates",
  "lifecycle.programs": "Programs", "lifecycle.survey_responses (n >= 5)": "Survey results (5 or more)", knowledge_risk: "Knowledge risk",
  "repos.pr_files": "Pull request files", "repos.check_runs": "Check results", "store.save_review": "Reviews",
  "lifecycle.blockers": "Blockers", "lifecycle.summaries": "Weekly summaries", "lifecycle.metrics": "Program figures",
  "lifecycle.feedback": "Feedback", "lifecycle.handovers": "Handovers", "store.create_meeting": "KT sessions",
};
function toolLabel(t: string) {
  if (TOOL_LABEL[t]) return TOOL_LABEL[t];
  if (/^llm\b/i.test(t)) return "AI";
  const bare = t.replace(/^(store|lifecycle|repos|index|blobs)\./, "").replace(/\(.*?\)/g, "").trim();
  return /[_.]/.test(bare) || bare === bare.toLowerCase() ? titleCase(bare.replace(/[._]+/g, " ")) : bare;
}
const toolLabels = (tools: string[]) => [...new Set(tools.map(toolLabel))];

function ModeControl({ a, admin, onChange }: { a: Agent; admin: boolean; onChange: (a: Agent) => void }) {
  const act = useAction();
  const locked = LOCKED.has(a.key);
  const mode = effectiveMode(a);
  const note = locked ? "It writes no text, so its mode is fixed." : !a.ai_available ? "AI isn't set up on this server, so it writes from its rules."
    : mode === "rules" ? "Writes plain text from its rules, without AI." : "Rules decide; AI writes the wording.";
  if (!admin || locked) return <p className="ag-mode-line"><ModeChip a={a} /><span className="lc-dim">{note}</span></p>;
  function set(next: "ai" | "rules") {
    if (next === a.mode) return;
    const before = a;
    onChange({ ...a, mode: next });
    act.run(next, () => patch<Agent>(`/agents/${encodeURIComponent(a.key)}`, { mode: next })).then((r) => {
      if (!r) { onChange(before); return; }
      onChange({ ...a, ...r, mode: r.mode === "rules" ? "rules" : r.mode === "ai" ? "ai" : next });
      toast(next === "rules" ? `${a.name} now uses rules only` : `${a.name} uses AI again`);
    });
  }
  return (
    <div className="ag-mode-ctl">
      <div className="ag-seg" role="radiogroup" aria-label={`${a.name}'s mode`}>
        <button type="button" role="radio" aria-checked={a.mode === "ai"} disabled={!!act.busy || !a.ai_available} onClick={() => set("ai")}>AI</button>
        <button type="button" role="radio" aria-checked={a.mode === "rules"} disabled={!!act.busy} onClick={() => set("rules")}>Rules only</button>
      </div>
      <span className="lc-dim ag-mode-note">{note}</span>
      {act.error && <p className="error" role="alert">{act.error}</p>}
    </div>
  );
}

/** Recent runs. Each opens its trace (GET /api/agents/runs/{run_id}): what the agent did, step by step, with its sources.
 *  A private run (a hire's private review) says so and does not open. */
function Runs({ a, admin }: { a: Agent; admin: boolean }) {
  const { project } = useSession();
  const runs = useApi<{ runs: AgentRun[] }>(`/agents/runs?project_id=${encodeURIComponent(project.id)}&agent=${encodeURIComponent(a.key)}&limit=20`, ["runs"]);
  const rows = runs.data?.runs ?? [];
  const [openRun, setOpenRun] = useState<string | null>(null);
  return (
    <div className="ag-runs">
      <p className="ag-fact-k">Recent runs</p>
      {runs.error && !runs.data ? (
        <p className="lc-dim ag-runs-none">{isStatus(runs.error, 404) ? "Run history isn't available on this server yet." : "Runs could not be loaded."}</p>
      ) : !runs.data ? <div className="lc-skel-line" aria-busy="true" /> : rows.length === 0 ? (
        <p className="lc-dim ag-runs-none">No runs yet.</p>
      ) : (
        <ul className="ag-run-list">
          {rows.map((r) => {
            const s = runState(r.status);
            const priv = isPrivateRun(r);
            const open = !priv && openRun === r.id;
            const panel = `ag-run-${r.id}`;
            const cells = (
              <>
                <span className="ag-run-state"><i aria-hidden="true" />{s.label}</span>
                <span className="ag-run-sum">{runSummary(r)}{priv && <span className="ag-run-priv">Private run</span>}</span>
                <span className="ag-run-trig">{eventLabel(r.trigger)}</span>
                <span className="ag-run-at" title={new Date(r.at).toLocaleString()}>{when(r.at)}</span>
              </>
            );
            return (
              <li key={r.id} className={`ag-run t-${s.tone}${open ? " is-open" : ""}${priv ? " is-private" : ""}`}>
                {priv ? (
                  <div className="ag-run-row" title="Only the person it was for can see its steps">{cells}<span aria-hidden="true" /></div>
                ) : (
                  <button type="button" className="ag-run-row" aria-expanded={open} aria-controls={panel} onClick={() => setOpenRun(open ? null : r.id)}>
                    {cells}<span className="sr-only">{open ? "Hide steps" : "Show steps"}</span>
                    <svg className="ag-run-chev" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
                         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6" /></svg>
                  </button>
                )}
                {admin && r.trace_id && <span className="ag-run-trace" title={r.trace_id}>Trace {r.trace_id.slice(0, 12)}</span>}
                {!priv && (
                  <div id={panel} className="ag-run-steps" hidden={!open}>
                    {open && <RunTraceView runId={r.id} />}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- Home: a small summary for managers and admins */

/** Pending proposals by agent, each linking to Approvals filtered to that agent. Nothing at all when the server has no agents. */
export function AgentsSummary() {
  const { project } = useSession();
  const d = useApi<AgentsData>(`/agents?project_id=${encodeURIComponent(project.id)}`, ["approvals", "runs"]);
  if (!d.data) return null;
  const list = normaliseAgents(d.data).agents.filter((a) => a.pending > 0).sort((x, y) => y.pending - x.pending);
  return (
    <div className="lc-card ag-sum">
      <div className="ag-sum-top">
        <p className="ag-sum-k">Agents</p>
        <a className="lc-link" href="#/agents">All agents</a>
      </div>
      {list.length === 0 ? <p className="lc-dim ag-sum-none">Nothing waiting from agents.</p> : (
        <ul className="ag-sum-list">
          {list.map((a) => (
            <li key={a.key}>
              <a href={`#/approvals?agent=${encodeURIComponent(a.key)}`}>
                <AgentGlyph agent={a.key} size={14} /><span className="ag-sum-name">{a.name}</span>
                <span className="ag-sum-n" aria-label={`${a.pending} waiting`}>{a.pending}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
