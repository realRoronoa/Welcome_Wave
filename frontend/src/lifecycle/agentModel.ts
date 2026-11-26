/** The multi-agent model as the web app sees it: GET /api/agents, /api/agents/runs, PATCH /api/agents/{key},
 *  POST /api/agents/tick, and the `explain` block every agent output carries (agents contract, backend Track H).
 *  Everything here reads defensively: a server without the agents answers 404, and older payloads have no `explain`. */

export type AgentKey = "supervisor" | "librarian" | "note_taker" | "guide" | "planner" | "scout" | "reviewer" | "coach" | "keeper";

export type ExplainSource = { label: string; kind: "code" | "kt" | "doc" | "event" | "metric" | string; ref?: Record<string, unknown> | null };
export type Explain = {
  agent: string; agent_name: string; logic: string[]; sources: ExplainSource[];
  confidence: number | null; ai: boolean; trace_id: string | null;
  /** the agentic runtime's trace (agentic contract §2): what the agent did, step by step; empty on older payloads */
  steps: TraceStep[];
  /** the run behind it, when the steps are not in the payload (GET /api/agents/runs/{run_id} has them) */
  run_id: string | null;
  /** the agent's own one-paragraph reason for its output, when it gave one */
  rationale: string | null;
};

/* ---------------------------------------------------------------- traces: what an agent did, step by step */

/** One step of an agent run (agentic contract §2 `Step`): a thought, a tool it used, a self-check, or its conclusion. */
export type TraceStep = {
  n: number; kind: "think" | "tool" | "verify" | "final"; text: string; tool: string | null;
  sources: ExplainSource[]; ms: number;
};
/** How a step reads at a glance: one glyph per kind of work. Teal ("kt") only for a step about a KT session. */
export type StepLook = "search" | "read" | "kt" | "verify" | "route" | "think" | "done";
export type RunInfo = { id: string; agent: string; agent_name: string; status: string; at: string | null; ms: number | null; private: boolean };
export type RunTrace = { run: RunInfo | null; steps: TraceStep[]; private: boolean };

/* The Guide's research crew (docs/specs/rag-crew.md) opens its trace with two steps of its own: plan_search (the Planner read
   the question: a thought) and rank_evidence (the Judge ranked what the retrievers found: a check). */
const TOOL_LOOK: Record<string, StepLook> = {
  search_knowledge: "search", find_symbol: "search", verified_answers: "search", issues: "search", recent_changes: "search",
  tests_for: "search", read_file: "read", program: "read", hire_signals: "read", kt_moments: "kt", kt_coverage: "kt",
  who_knows: "route", code_owners: "route", plan_search: "think", rank_evidence: "verify",
};
/** A tool step with no words of its own still says what it did. */
const TOOL_TEXT: Record<string, string> = {
  search_knowledge: "Searching the sources", find_symbol: "Finding where it is defined", verified_answers: "Checking verified answers",
  issues: "Reading open issues", recent_changes: "Checking recent changes", tests_for: "Finding the tests", read_file: "Reading the code",
  program: "Reading the program", hire_signals: "Reading this week's activity", kt_moments: "Checking KT sessions",
  kt_coverage: "Checking what KT sessions explain", who_knows: "Finding who knows this", code_owners: "Checking code owners",
  plan_search: "Planning the search", rank_evidence: "Ranking the evidence",
};

export function stepLook(s: TraceStep): StepLook {
  if (s.kind === "final") return "done";
  // the crew's own steps look the same whatever kind a payload gives them (an older or a looser server may say "tool")
  if (s.tool === "plan_search" || s.tool === "rank_evidence") return TOOL_LOOK[s.tool];
  if (s.kind === "verify") return "verify";
  if (s.kind === "tool") {
    const byTool = s.tool ? TOOL_LOOK[s.tool] : undefined;
    if (byTool) return byTool;
    if (s.sources.length && s.sources.every((x) => x.kind === "kt")) return "kt";
    if (/^search|^find|^look/i.test(s.text)) return "search";
    if (/^read|^open/i.test(s.text)) return "read";
    if (/\bKT\b/.test(s.text)) return "kt";
    if (/owner|who knows|expert|mentor/i.test(s.text)) return "route";
    return s.sources.some((x) => x.kind === "code") ? "read" : "search";
  }
  return "think";
}

const STEP_KINDS = new Set(["think", "tool", "verify", "final"]);
const asSources = (v: unknown): ExplainSource[] =>
  Array.isArray(v) ? v.filter((s): s is ExplainSource => !!s && typeof s === "object" && typeof (s as ExplainSource).label === "string" && !!(s as ExplainSource).label) : [];

/** One step from any payload, or null when it is not one. Reads defensively: the runtime is new and payloads vary. */
export function normaliseStep(raw: unknown, i = 0): TraceStep | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const tool = str(r.tool);
  const known = typeof r.kind === "string" && STEP_KINDS.has(r.kind);
  if (!known && !tool) return null; // a trace step names its kind or its tool; other lists called "steps" are not traces
  const kind = (known ? r.kind : "tool") as TraceStep["kind"];
  const text = (str(r.text) ?? str(r.summary) ?? str(r.thought) ?? (tool ? TOOL_TEXT[tool] ?? null : null))
    ?.replace(/\s+/g, " ").replace(/^[\u2713\u2714\u2705]\s*/, "").trim(); // the glyph shows the tick; the text need not
  if (!text) return null;
  return { n: num(r.n) ?? i + 1, kind, text, tool, sources: asSources(r.sources), ms: Math.max(0, num(r.ms) ?? 0) };
}

/** Steps in order, one per number (a step that arrived twice, live and in the answer, counts once; the later copy wins). */
export function mergeSteps(a: TraceStep[], b: TraceStep[]): TraceStep[] {
  const byN = new Map<number, TraceStep>();
  for (const s of [...a, ...b]) byN.set(s.n, s);
  return [...byN.values()].sort((x, y) => x.n - y.n);
}

/** The steps a payload carries: its own `steps`, or its explain block's. */
export function stepsOf(x: unknown): TraceStep[] {
  const o = x as { steps?: unknown; explain?: { steps?: unknown } | null } | null | undefined;
  const raw = Array.isArray(o?.steps) ? o!.steps : Array.isArray(o?.explain?.steps) ? o!.explain!.steps : [];
  return mergeSteps([], (raw as unknown[]).map((s, i) => normaliseStep(s, i)).filter((s): s is TraceStep => !!s));
}

/** "4.2 s", "12 s", "1 min 5 s"; null when there is nothing to say. */
export function secs(ms: number | null | undefined): string | null {
  if (!ms || ms < 50) return null;
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}
/** "Thought for 6 steps · 4.2 s" */
export function thoughtLabel(steps: TraceStep[], ms?: number | null) {
  const t = secs(ms ?? steps.reduce((n, s) => n + s.ms, 0));
  return `Thought for ${steps.length} ${steps.length === 1 ? "step" : "steps"}${t ? ` · ${t}` : ""}`;
}

/* ---------------------------------------------------------------- the Guide's research crew (docs/specs/rag-crew.md) */

/** The `crew` block of an answer: how the Guide's research crew found its evidence. Counts only: the server never sends the
 *  question, a query or the model's words, and nothing here would show them. */
export type Crew = {
  queries: number; candidates: number; kept: number; judged: boolean; planner: "rules" | "model" | null; intent: string | null;
  by_kind: { code: number; doc: number; kt: number; verified: number };
  ms: { plan: number; retrieve: number; rank: number; judge: number; total: number };
};
/** The API names the kinds code, doc, kt and verified; the retrieval's own names (meeting, verified_answer) mean the same. */
const CREW_KIND: Record<string, keyof Crew["by_kind"]> = {
  code: "code", doc: "doc", docs: "doc", document: "doc", documents: "doc", kt: "kt", meeting: "kt", meetings: "kt", transcript: "kt",
  verified: "verified", verified_answer: "verified", verified_answers: "verified",
};

/** The crew block of a payload, or null when it has none or it says nothing. Read defensively: the block is new and optional. */
export function crewOf(x: unknown): Crew | null {
  const c = (x as { crew?: unknown } | null | undefined)?.crew;
  if (!c || typeof c !== "object" || Array.isArray(c)) return null;
  const o = c as Record<string, unknown>;
  // a count, or a list's length (a list is counted, never shown)
  const count = (v: unknown) => Math.max(0, Math.round(Array.isArray(v) ? v.length : num(v) ?? 0));
  const by_kind = { code: 0, doc: 0, kt: 0, verified: 0 };
  if (o.by_kind && typeof o.by_kind === "object") {
    for (const [k, v] of Object.entries(o.by_kind as Record<string, unknown>)) {
      const to = CREW_KIND[k.toLowerCase()];
      if (to) by_kind[to] += count(v);
    }
  }
  const t = (o.ms && typeof o.ms === "object" ? o.ms : {}) as Record<string, unknown>;
  const ms = { plan: count(t.plan), retrieve: count(t.retrieve), rank: count(t.rank), judge: count(t.judge), total: count(t.total) };
  const found = by_kind.code + by_kind.doc + by_kind.kt + by_kind.verified;
  const crew: Crew = {
    queries: count(o.queries), candidates: count(o.candidates) || found, kept: count(o.kept), judged: o.judged === true,
    planner: o.planner === "model" || o.planner === "rules" ? o.planner : null, intent: str(o.intent), by_kind, ms,
  };
  return crew.queries || crew.candidates || crew.kept || crew.judged || ms.total ? crew : null;
}

/** Retrieval is quick offline: under a second it reads in milliseconds ("38 ms"), above it as the trace does ("1.2 s"). */
const took = (ms: number) => (ms >= 1000 ? secs(ms) : ms >= 1 ? `${Math.round(ms)} ms` : null);

/** "3 searches · 21 sources: code 14, docs 4, KT 3 · cited 2 · ranked by the model · 1.2 s": the Ask page's one quiet line under
 *  an answer. A part that is absent or zero is left out; "cited" is what the answer on screen uses (the crew's own shortlist,
 *  "kept", is nearly always its cap, so it stays in the tooltip); "ranked by the model" only when the model graded the evidence. */
export function crewLine(c: Crew, cited = 0): string | null {
  const kinds = ([["code", c.by_kind.code], ["docs", c.by_kind.doc], ["KT", c.by_kind.kt], ["verified", c.by_kind.verified]] as const)
    .filter(([, n]) => n > 0).map(([k, n]) => `${k} ${n}`);
  const parts = [
    c.queries ? `${c.queries} ${c.queries === 1 ? "search" : "searches"}` : null,
    c.candidates ? `${c.candidates} ${c.candidates === 1 ? "source" : "sources"}${kinds.length ? `: ${kinds.join(", ")}` : ""}` : null,
    cited ? `cited ${cited}` : null,
    c.judged ? "ranked by the model" : null,
    took(c.ms.total),
  ].filter((p): p is string => !!p);
  return parts.length ? parts.join(" · ") : null;
}

/** The same numbers as a sentence: the line's tooltip, for whoever wonders what "kept" means. */
export function crewTitle(c: Crew): string {
  const said = [
    c.queries ? `planned ${c.queries} ${c.queries === 1 ? "search" : "searches"}${c.planner === "model" ? " with the model's help" : ""}` : null,
    c.candidates ? `found ${c.candidates} ${c.candidates === 1 ? "source" : "sources"}` : null,
    c.kept ? `kept the ${c.kept === 1 ? "one" : c.kept} that best ${c.kept === 1 ? "matches" : "match"} the question` : null,
  ].filter((p): p is string => !!p);
  const joined = said.length > 1 ? `${said.slice(0, -1).join(", ")} and ${said[said.length - 1]}` : said[0] ?? "";
  const first = joined ? `The Guide's research crew ${joined}.` : "";
  return [first, c.judged ? "A model graded each source for this question." : ""].filter(Boolean).join(" ");
}

/** A run's trace from GET /api/agents/runs/{run_id} → {run, steps}, read defensively. A private run carries no steps. */
export function normaliseRun(d: unknown): RunTrace {
  const o = (d ?? {}) as { run?: Record<string, unknown> | null; steps?: unknown; private?: unknown };
  const r = o.run && typeof o.run === "object" ? o.run : null;
  const priv = o.private === true || r?.private === true || r?.visibility === "private";
  const run: RunInfo | null = r ? {
    id: str(r.id) ?? str(r.run_id) ?? "", agent: agentKey(str(r.agent)), agent_name: agentName(str(r.agent), str(r.agent_name)),
    status: str(r.status) ?? str(r.stopped) ?? "", at: str(r.at) ?? str(r.started_at), ms: num(r.ms) ?? num(r.duration_ms), private: priv,
  } : null;
  return { run, steps: priv ? [] : stepsOf(o), private: priv };
}
/** A run the reader may not open: a hire's private review, or one the server marks private. */
export const isPrivateRun = (r: { private?: unknown; visibility?: unknown; trigger?: unknown }) =>
  r.private === true || r.visibility === "private" || r.trigger === "presubmit.requested";

export type AgentRunRef = { at: string; status: string; trigger: string; trace_id: string | null };
/** One member of an agent's crew: the Guide's research is split into a Planner, Retrievers, Fusion, a Judge, the Writer and
 *  the Critic (docs/specs/rag-crew.md), listed in the order a question meets them. */
export type CrewMember = { key: string; name: string; role: string };
export type Agent = {
  key: string; name: string; role: string; triggers: string[]; tools: string[]; approvals: string[];
  mode: "ai" | "rules"; ai_available: boolean; runs_7d: number; failed_7d: number; last_run: AgentRunRef | null; pending: number;
  /** the agent's crew, when it has one (GET /api/agents adds it to the Guide); absent on older servers */
  crew?: CrewMember[];
};
export type AgentEdge = { from: string; to: string; via: string };
export type AgentsData = { agents: Agent[]; edges: AgentEdge[] };
export type AgentRun = {
  id: string; agent: string; agent_name: string; trigger: string; status: string; at: string; summary: string; trace_id: string | null;
  private?: boolean; visibility?: string; // a run whose steps only the person it was for may read
};
export type TickResult = { ran: string[]; proposals: number; alerts: number };

/** Names and roles exactly as the contract has them: the fallback when a payload names an agent by key only. */
export const AGENT_REGISTRY: { key: AgentKey; name: string; role: string }[] = [
  { key: "supervisor", name: "Supervisor", role: "Routes every event to one agent and pauses for people" },
  { key: "librarian", name: "Librarian", role: "Reads repositories and documents and keeps the index current" },
  { key: "note_taker", name: "Note-taker", role: "Turns a recorded KT session into notes linked to code" },
  { key: "guide", name: "Guide", role: "Answers with sources; sends the rest to the right person" },
  { key: "planner", name: "Planner", role: "Drafts the 30/60/90 plan, the completion review and template improvements" },
  { key: "scout", name: "Scout", role: "Finds a safe first task and writes its brief" },
  { key: "reviewer", name: "Reviewer", role: "Reviews pull requests, and branches privately for the hire" },
  { key: "coach", name: "Coach", role: "Spots blockers early, nudges kindly and writes the weekly summary" },
  { key: "keeper", name: "Keeper", role: "Keeps explanations current: re-recordings, doc fixes, handovers" },
];
const BY_KEY = Object.fromEntries(AGENT_REGISTRY.map((a) => [a.key, a]));
/** Older run names map onto the registry (ingestion → librarian, …), as the backend's registry does. */
const ALIAS: Record<string, AgentKey> = {
  ingestion: "librarian", meeting: "note_taker", qa: "guide", task: "planner", review: "reviewer", task_status: "reviewer",
  note_taker: "note_taker", "note-taker": "note_taker",
};
export const agentKey = (k: string | null | undefined) => (k ? ALIAS[k] ?? k : "");
export const agentName = (k: string | null | undefined, fallback?: string | null) =>
  fallback || BY_KEY[agentKey(k)]?.name || (k ? k.replace(/_/g, "-").replace(/^./, (c) => c.toUpperCase()) : "Agent");

/** Which agent proposes each approval kind (the attribution on an approval card). */
export const KIND_AGENT: Record<string, AgentKey> = {
  task_plan: "planner", template_change: "planner", review: "reviewer", kt_followup: "note_taker", rerecord: "keeper", doc_edit: "keeper",
};
/** Approval kinds in words, for filters and the agents page. */
export const KIND_LABEL: Record<string, string> = {
  task_plan: "Task plans", review: "Reviews", kt_followup: "KT session reviews", rerecord: "Re-recordings", doc_edit: "Doc fixes",
  template_change: "Template changes",
};
/** Agents that can never be switched to "rules only" (the API answers 409). */
export const LOCKED = new Set(["librarian", "supervisor"]);

/** Events in plain words. Unknown events fall back to their own name, spaced. */
const EVENT_LABEL: Record<string, string> = {
  "repo.connected": "Repository connected", "repo.pushed": "New commits pushed", "document.uploaded": "Document uploaded",
  "meeting.recording_ready": "KT recording ready", "chat.question": "Question asked", "ask.route": "Question asked", "ask.free": "Question asked",
  "onboarding.started": "Onboarding started", "program.completion_due": "Completion review due", "program.completed": "Program completed",
  "first_task.requested": "First task requested", "pr.opened": "Pull request opened", "pr.updated": "Pull request updated",
  "pr.merged": "Pull request merged", "presubmit.requested": "Private review requested", "lifecycle.tick": "Daily check",
  "week.closed": "Week ended", "feedback.created": "Feedback flagged", "handover.requested": "Handover requested",
  all: "Every event", manual: "Run by hand", tick: "Daily check",
};
export function eventLabel(e: string | null | undefined) {
  if (!e) return "Unknown";
  return EVENT_LABEL[e] ?? e.replace(/[._]+/g, " ").replace(/^./, (c) => c.toUpperCase());
}
/** The distinct plain-word events of a list (two events can read the same). */
export const eventLabels = (events: string[] | null | undefined) => [...new Set((events ?? []).map(eventLabel))];

/** The `explain` block of any payload, or null when it has none (or an unusable one). */
export function explainOf(x: unknown): Explain | null {
  const e = (x as { explain?: unknown } | null | undefined)?.explain as Partial<Explain> | null | undefined;
  if (!e || typeof e !== "object") return null;
  if (!e.agent && !e.agent_name) return null;
  return {
    agent: agentKey(e.agent ?? ""), agent_name: agentName(e.agent, e.agent_name),
    logic: Array.isArray(e.logic) ? e.logic.filter((l): l is string => typeof l === "string" && !!l.trim()) : [],
    sources: Array.isArray(e.sources) ? e.sources.filter((s) => s && typeof s.label === "string") : [],
    confidence: typeof e.confidence === "number" ? e.confidence : null,
    ai: !!e.ai, trace_id: typeof e.trace_id === "string" && e.trace_id ? e.trace_id : null,
    steps: stepsOf(x), run_id: str((e as { run_id?: unknown }).run_id) ?? str((x as { run_id?: unknown }).run_id),
    rationale: str((e as { rationale?: unknown }).rationale)?.trim() || str((x as { rationale?: unknown }).rationale)?.trim() || null,
  };
}

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && /^\d+(\.\d+)?$/.test(v) ? Number(v) : null);
const str = (v: unknown) => (typeof v === "string" && v ? v : null);

/** Where a source opens: the code at its line, the KT session at its moment, the document at its page. Events and figures
 *  have no page of their own. */
export function sourceHref(s: ExplainSource): string | null {
  const r = (s.ref ?? {}) as Record<string, unknown>;
  const target = str(r.target_ref), targetKind = str(r.target_kind); // a flagged target (the Keeper's doc fixes)
  if (s.kind === "code") {
    const path = str(r.path) ?? str(r.file_path) ?? (targetKind === "code" ? target : null);
    if (!path) return null;
    const line = num(r.line) ?? num(r.start_line);
    return `#/code?path=${encodeURIComponent(path)}${line ? `&line=${line}` : ""}`;
  }
  if (s.kind === "kt") {
    const id = str(r.meeting_id) ?? str(r.id);
    if (!id) return null;
    const t = num(r.at_s) ?? num(r.start_s) ?? num(r.t);
    return `#/meetings/${encodeURIComponent(id)}${t ? `?t=${Math.floor(t)}` : ""}`;
  }
  if (s.kind === "doc") {
    if (targetKind === "code" && target) return `#/code?path=${encodeURIComponent(target)}`;
    const id = str(r.document_id) ?? str(r.id) ?? (targetKind === "doc" ? target : null);
    if (id) return `#/documents/${encodeURIComponent(id)}${num(r.page) ? `?page=${num(r.page)}` : ""}`;
    const path = str(r.path);
    return path ? `#/code?path=${encodeURIComponent(path)}` : null;
  }
  return null;
}
/** A KT source's label without the moment it already ends with ("… at 03:02", "…, 3:02"), when the time is shown beside it. */
export function ktLabel(label: string, t: number | null): string {
  if (t === null) return label;
  const m = Math.floor(t / 60), ss = String(Math.floor(t % 60)).padStart(2, "0");
  return label.replace(new RegExp(`(?:\\s+at|,|\\s*·)\\s*0*${m}:${ss}\\s*$`), "").trim() || label;
}
/** A KT source's moment, for the time stamp beside it. */
export function sourceTime(s: ExplainSource): number | null {
  if (s.kind !== "kt") return null;
  const r = (s.ref ?? {}) as Record<string, unknown>;
  return num(r.at_s) ?? num(r.start_s) ?? num(r.t);
}

/** An agent's crew, read defensively: a member needs a name; its key falls back to the name, its role to nothing. */
function crewMembers(v: unknown): CrewMember[] {
  return (Array.isArray(v) ? v : []).flatMap((m) => {
    const o = (m && typeof m === "object" ? m : {}) as Record<string, unknown>;
    const name = str(o.name)?.trim();
    return name ? [{ key: str(o.key)?.trim() || name, name, role: str(o.role)?.trim() ?? "" }] : [];
  });
}

/** Read the agents payload defensively: missing lists become empty, unknown agents keep their own key. */
export function normaliseAgents(d: Partial<AgentsData> | null | undefined): AgentsData {
  const agents = (Array.isArray(d?.agents) ? d!.agents : []).filter((a) => a && a.key).map((a) => ({
    ...a,
    name: a.name || agentName(a.key), role: a.role || BY_KEY[a.key]?.role || "",
    triggers: Array.isArray(a.triggers) ? a.triggers : [], tools: Array.isArray(a.tools) ? a.tools : [],
    approvals: Array.isArray(a.approvals) ? a.approvals : [], mode: a.mode === "rules" ? "rules" as const : "ai" as const,
    ai_available: a.ai_available !== false, runs_7d: a.runs_7d ?? 0, failed_7d: a.failed_7d ?? 0, last_run: a.last_run ?? null,
    pending: a.pending ?? 0, crew: crewMembers(a.crew),
  }));
  const edges = (Array.isArray(d?.edges) ? d!.edges : []).filter((e) => e && e.from && e.to);
  return { agents, edges };
}
