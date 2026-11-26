import { CSSProperties, FormEvent, ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, Citation, errorMessage, KTClip, mmss, post, when } from "../api";
import { useSession } from "../session";
import { replaceRoute } from "../route";
import { Icon, Logo } from "../ui";
import { reducedMotion } from "../fx";
import { useLive, watchRun } from "../live";
import { Glyph, GlyphName } from "./chat-parts";
import FeedbackButton from "../lifecycle/FeedbackButton";
import WhyThis from "../lifecycle/WhyThis";
import { STEP_GAP, Thinking } from "../lifecycle/Trace";
import { AgentGlyph } from "../lifecycle/Agents";
import { agentKey, agentName, explainOf, mergeSteps, normaliseStep, stepsOf, TraceStep } from "../lifecycle/agentModel";

/** The person a question was handed to, and their answer once they wrote it (the history joins it to the turn). */
type Expert = { status: "open" | "answered"; name: string | null; answer: string | null; answered_at: string | null };
type Answer = {
  id?: string; message_id?: string; feedback?: 1 | -1 | null; question: string; status: "answered" | "not_enough_information"; answer: string;
  citations: Citation[]; kt_clips: KTClip[]; confidence: number; stale?: boolean; expert?: Expert | null; routed_to?: string | null;
  key?: string; // this tab's key for a turn asked here (its run id), so the turn keeps its place when the answer lands
};

/** POST /api/ask/route (docs/specs/answer-routing.md): which tier answered, and what it needs to show. */
type Related = { kind: "verified_answer" | "expert_answer"; id: string; question: string; answer: string; similarity: number };
type Route = {
  tier: number; status: "answered" | "related" | "offered" | "routed" | "unrouted"; message_id?: string | null;
  answer?: string; citations?: Citation[]; kt_clips?: KTClip[]; confidence?: number;
  check?: { ok: boolean } | null; related?: Related[];
  request_id?: string; routed_to?: { id: string; name: string; role: string }; delivery?: "now" | "digest";
  escalation?: { available: boolean; next_tier: number | null }; why?: string[];
  note?: string; // why an escalation went nowhere
  explain?: unknown; // the Guide's explanation (agents contract): rules that fired, sources, whether AI wrote the answer
  run_id?: string | null; steps?: unknown[]; // the agent's run and its trace (agentic contract §2)
  thought_ms?: number; // how long it thought, as this tab measured it
  handled_by?: string | null; proposal?: unknown; actions?: unknown; // a free-form request another agent took (Coach, Planner, Scout)
};

const DEMO_STARTERS = [
  "Which function reports missing labels?",
  "Why does validation return a list instead of raising?",
  "Where do default labels get added?",
];
const STARTERS = [
  "Where does a request enter?",
  "How do I run the tests?",
  "What should I read first?",
];

/** Three questions to start with: the demo's own, or, on a real project, the part its KT sessions explained most first. */
type Starter = { q: string; kt?: boolean };
function useStarters(projectId: string, demo: boolean, on: boolean): Starter[] {
  const [explained, setExplained] = useState<string | null>(null);
  useEffect(() => {
    if (demo || !on) return;
    let live = true;
    api<{ modules?: { module: string; kt_minutes: number }[] }>(`/coverage?project_id=${encodeURIComponent(projectId)}`).then((d) => {
      const best = (d.modules ?? []).filter((m) => m.kt_minutes > 0 && m.module && !/^\(|^docs|^tests?\b/.test(m.module))
        .sort((a, b) => b.kt_minutes - a.kt_minutes)[0];
      if (live) setExplained(best ? best.module : null);
    }).catch(() => { /* no coverage yet: the plain starters */ });
    return () => { live = false; };
  }, [projectId, demo, on]);
  if (demo) return DEMO_STARTERS.map((q) => ({ q }));
  return explained ? [{ q: `How does ${explained} work?`, kt: true }, ...STARTERS.slice(0, 2).map((q) => ({ q }))] : STARTERS.map((q) => ({ q }));
}

/** A run id for one question: the steps stream on its topic before the answer exists. */
function newRunId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16)); // insecure origins have no randomUUID
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** What a screen reader hears when the answer lands. */
function spoken(r: Route) {
  if (r.handled_by && agentKey(r.handled_by) !== "guide") return `${agentName(r.handled_by)} answered`;
  if (r.status === "routed") return `Sent to ${plain(r.routed_to?.name) || "a person"}`;
  if (r.status === "unrouted" || r.status === "offered" || (r.check && !r.check.ok)) return "Not enough evidence";
  return "Answered";
}

/** Where a source opens: the code at its line, the KT session at its moment, the document at its page. A verified answer has
 *  no page of its own (its text is quoted in the answer); it renders as a label. */
function sourceHref(c: Citation) {
  if (c.source_type === "code") return `#/code?path=${encodeURIComponent(c.ref.path)}&line=${c.ref.start_line}`;
  if ((c.source_type === "meeting" || c.source_type === "transcript") && c.ref?.meeting_id) return `#/meetings/${c.ref.meeting_id}?t=${Math.floor(c.ref.start_s ?? 0)}`;
  if (c.source_type === "doc" && c.ref?.document_id) return `#/documents/${encodeURIComponent(c.ref.document_id)}?page=${c.ref.page ?? 1}`;
  return undefined;
}
const SOURCE_GLYPH: Record<string, GlyphName> = { code: "file", doc: "doc", meeting: "play", transcript: "play", verified_answer: "shield" };
/** A source that is a moment of a KT session: teal, because that is exactly what teal means. */
const isKt = (c: Citation) => c.source_type === "meeting" || c.source_type === "transcript";

const idOf = (a: Answer) => a.message_id ?? a.id;
const plain = (name: string | null | undefined) => (name ?? "").replace(/\s*\(.*?\)/g, "").trim() || name || "";
/** "Ravi (Senior), the domain owner, already has this question." reads "Ravi also owns this code." */
function ownerNote(why?: string[]) {
  const w = why?.[0] ?? "";
  const m = /^(.*?), the domain owner, already has this question/.exec(w);
  return m ? `${plain(m[1])} also owns this code.` : w.replace(/^Not enough evidence:\s*/i, "") || "Nobody else can take it.";
}
const notEnough = (r: Route) => !!r.why?.some((w) => /^not enough evidence/i.test(w));

/* What each turn's route said lives only in the response (the chat history keeps the answer, not the tier), so it is kept for
   this tab: a turn still shows its tier after a page change or a reload. */
const routesKey = (projectId: string) => `ww-routes:${projectId}`;
function readRoutes(projectId: string): Record<string, Route> {
  try { return JSON.parse(sessionStorage.getItem(routesKey(projectId)) ?? "{}") ?? {}; } catch { return {}; }
}
function writeRoutes(projectId: string, routes: Record<string, Route>) {
  try { sessionStorage.setItem(routesKey(projectId), JSON.stringify(routes)); } catch { /* storage full or blocked: tiers show for this page only */ }
}

/** A route result as a chat turn, until the history (which has the same message id) replaces it. */
function turnOf(question: string, r: Route): Answer {
  const routed = r.status === "routed" || r.status === "unrouted" || r.status === "offered";
  return {
    message_id: r.message_id ?? undefined, question, status: routed ? "not_enough_information" : "answered",
    answer: r.answer ?? r.related?.[0]?.answer ?? "", citations: r.citations ?? [], kt_clips: r.kt_clips ?? [], confidence: r.confidence ?? 0,
    routed_to: r.routed_to?.id ?? null,
    expert: r.routed_to ? { status: "open", name: r.routed_to.name, answer: null, answered_at: null } : null,
  };
}

/** POST /api/ask/free: the Supervisor's pick for a typed request (agentic contract §4, ask.free). */
type Free = { handled_by?: string; why?: string; result?: unknown; run_id?: string | null; steps?: unknown[] };

/** A question being answered: its turn shows the question and the assistant's steps as they happen. */
type Pending = { key: string; question: string; person: boolean; steps: TraceStep[] };

/** q: a question handed over from the command palette; it is asked once, then dropped from the URL.
 *  focus: a turn to show (a notification that an expert answered it); it is scrolled to and marked, then dropped from the URL. */
export default function Chat({ q: handed, focus }: { q?: string | null; focus?: string | null } = {}) {
  const { project, demo } = useSession();
  const [items, setItems] = useState<Answer[] | null>(null);
  const [routes, setRoutes] = useState<Record<string, Route>>(() => readRoutes(project.id));
  const [votes, setVotes] = useState<Record<string, 1 | -1>>({});
  const [q, setQ] = useState("");
  const [person, setPerson] = useState(false);
  const [busy, setBusy] = useState<null | "ai" | "person">(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [said, setSaid] = useState(""); // the polite live region: "Searching…", then "Answered"
  const [escalating, setEscalating] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [here, setHere] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const asking = useRef(false);
  const routing = useRef(true); // false once the server says tiered routing is turned off: plain chat then
  const freeOk = useRef(true); // false once the server shows it has no /ask/free: the Guide's routing directly
  const liveSteps = useRef<TraceStep[]>([]);
  const keys = useRef<Record<string, string>>({}); // message id → the run id its turn was asked under (keeps the turn mounted)
  const fresh = useRef<Set<string>>(new Set()); // turns answered on this page: their answer is revealed, not just shown

  const load = useCallback(() => {
    if (asking.current) return Promise.resolve(); // the answer on its way is appended when it lands; a reload now would race it
    return api<Answer[]>(`/chat/history?project_id=${project.id}`).then((rows) => {
      setItems(rows);
      const seen: Record<string, 1 | -1> = {}; // votes from earlier visits
      for (const r of rows) { const id = idOf(r); if (id && (r.feedback === 1 || r.feedback === -1)) seen[id] = r.feedback; }
      setVotes(seen);
    }).catch((e) => { setItems((xs) => xs ?? []); setError(errorMessage(e)); });
  }, [project.id]);
  useEffect(() => { load(); }, [load]);
  useLive("inbox", () => { load(); }, project.id); // an expert's answer shows up as soon as it is given
  const landed = useRef<string | null>(null); // the turn whose answer just arrived
  useEffect(() => {
    if (focus) return;
    const behavior = reducedMotion() ? "auto" : "smooth";
    const key = landed.current;
    if (!key) { end.current?.scrollIntoView({ behavior, block: "end" }); return; } // the history, or the newest step
    landed.current = null;
    // an answer: once its steps have folded, bring its start into view if it runs under the composer (not its end)
    const t = window.setTimeout(() => {
      const el = document.querySelector<HTMLElement>(`[data-turn="${key}"]`);
      const box = el?.getBoundingClientRect();
      const room = innerHeight - 120;
      if (el && box && box.bottom > room) el.scrollIntoView({ behavior, block: box.height > room - 80 ? "start" : "end" });
    }, reducedMotion() ? 0 : 280);
    return () => window.clearTimeout(t);
  }, [items?.length, pending?.key, pending?.steps.length]); // eslint-disable-line react-hooks/exhaustive-deps

  function remember(id: string | null | undefined, r: Route) {
    if (!id) return;
    setRoutes((all) => { const next = { ...all, [id]: r }; writeRoutes(project.id, next); return next; });
  }

  // a notification about one turn: load the latest (the answer is new), then bring that turn into view
  useEffect(() => {
    if (!focus) return;
    load().then(() => {
      setHere(focus);
      requestAnimationFrame(() => document.getElementById(`turn-${focus}`)?.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" }));
      replaceRoute("#/chat"); // the same notification opened again must work again
    });
  }, [focus, load]);

  // The palette hands each question over as ?q=. It is asked once (React may run this effect twice for one value), then the
  // URL goes back to #/chat, which clears the guard, so the next question from the palette is asked too, even the same one.
  const handedLast = useRef<string | null>(null);
  useEffect(() => {
    if (!handed) { handedLast.current = null; return; }
    if (handedLast.current === handed) return;
    handedLast.current = handed;
    replaceRoute("#/chat"); // a reload must not ask it again
    ask(handed, false);
  }, [handed]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Ask. The run gets its id here, and its steps are watched BEFORE the question is sent, so none is missed; they render
   *  as they arrive. The answer's own `steps` are the record (privacy-filtered); the streamed ones fill in when it has none.
   *  `offered`: the turn the Guide offered a person for, so its draft for that person goes along. */
  async function ask(question: string, askPerson: boolean, offered?: string) {
    if (!question.trim() || busy) return;
    const runId = newRunId();
    const t0 = performance.now();
    setBusy(askPerson ? "person" : "ai"); setError(null); setQ(""); asking.current = true;
    liveSteps.current = [];
    setPending({ key: runId, question, person: askPerson, steps: [] });
    setSaid(askPerson ? "Finding the right person…" : "Searching…");
    let shownBy = t0; // when the paced list will have shown the newest step (Thinking reveals one per STEP_GAP ms)
    const watch = routing.current ? watchRun(runId, (raw) => {
      const step = normaliseStep(raw, liveSteps.current.length);
      if (!step) return;
      if (!liveSteps.current.some((x) => x.n === step.n)) shownBy = Math.max(performance.now(), shownBy + STEP_GAP);
      liveSteps.current = mergeSteps(liveSteps.current, [step]);
      setPending((p) => (p && p.key === runId ? { ...p, steps: liveSteps.current } : p));
    }) : null;
    let handedOver = false;
    try {
      let turn: Answer;
      if (routing.current) {
        try {
          await watch?.ready;
          // a typed request goes to the Supervisor, who picks the Guide (an answer) or another agent (a proposal); asking a
          // person, and servers without /ask/free, go straight to the Guide's routing
          const free = !askPerson && freeOk.current ? await askFree(question, runId) : null;
          const r = free ?? await post<Route>("/ask/route", {
            project_id: project.id, question, ask_person: askPerson, run_id: runId, ...(offered ? { message_id: offered } : {}),
          });
          // a fast run streams its steps in one burst: let them be seen one by one before the answer folds them (≤ 1.2 s)
          const catchUp = Math.min(1200, shownBy + 350 - performance.now());
          if (liveSteps.current.length > 1 && catchUp > 0 && !reducedMotion()) await new Promise((ok) => window.setTimeout(ok, catchUp));
          const own = stepsOf(r);
          const steps = own.length ? own : liveSteps.current;
          // the run id is kept only when the server ran one (it answered with it, or streamed its steps)
          const kept: Route = { ...r, run_id: r.run_id ?? (steps.length ? runId : null), steps, thought_ms: Math.round(performance.now() - t0) };
          remember(r.message_id ?? runId, kept);
          turn = turnOf(question, r);
          handedOver = r.status === "routed";
          setSaid(spoken(kept));
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 404 && /turned off/i.test(e.message))) throw e;
          routing.current = false; // this server has no routing: ask the plain way
          turn = await askPlain(question);
          handedOver = !!turn.routed_to;
          setSaid(turn.status === "answered" ? "Answered" : "Not enough evidence");
        }
      } else {
        turn = await askPlain(question);
        handedOver = !!turn.routed_to;
        setSaid(turn.status === "answered" ? "Answered" : "Not enough evidence");
      }
      turn = { ...turn, key: runId };
      if (turn.message_id) keys.current[turn.message_id] = runId;
      fresh.current.add(runId);
      landed.current = runId;
      // a refresh may have brought this turn in already; never show it twice
      const id = idOf(turn);
      setItems((xs) => (id && (xs ?? []).some((x) => idOf(x) === id)) ? xs : [...(xs ?? []), turn]);
      if (askPerson) setPerson(false);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 429 ? "Too many questions in a minute. Wait a moment, then ask again; your question is still in the box." : errorMessage(e));
      setQ(question);
      setSaid("");
    } finally { watch?.close(); setPending(null); asking.current = false; setBusy(null); }
    if (handedOver) load(); // the history names who it went to, and later their answer
  }

  /** POST /ask/free → {handled_by, why, result, run_id, steps}, as a route: the Guide's result is /ask/route's own; another
   *  agent's is its proposal. null when this server has no /ask/free (it is then not tried again). */
  async function askFree(question: string, runId: string): Promise<Route | null> {
    try {
      const f = await post<Free>("/ask/free", { project_id: project.id, question, run_id: runId });
      const steps = Array.isArray(f.steps) && f.steps.length ? f.steps : undefined;
      if (!f.handled_by || agentKey(f.handled_by) === "guide") {
        const r = (f.result ?? {}) as Route;
        return { ...r, run_id: f.run_id ?? r.run_id, steps: steps ?? r.steps, handled_by: "guide" };
      }
      const why = typeof f.why === "string" && f.why.trim() ? [f.why.trim()] : [];
      return { tier: 0, status: "answered", handled_by: f.handled_by, proposal: (f.result as { proposal?: unknown } | null)?.proposal ?? "",
               why, run_id: f.run_id, steps };
    } catch (e) {
      if (e instanceof ApiError && (e.status === 405 || (e.status === 404 && !/turned off/i.test(e.message)))) { freeOk.current = false; return null; }
      throw e;
    }
  }

  async function askPlain(question: string): Promise<Answer> {
    const a = await post<Answer>("/chat", { project_id: project.id, question });
    return { ...a, question };
  }

  /** Tier 4: the question the mentor has goes on to the owner of the code it is about. */
  async function escalate(id: string, r: Route) {
    if (!r.request_id) return;
    setEscalating(id); setError(null);
    try {
      const next = await post<Route>("/ask/route", { project_id: project.id, escalate_request_id: r.request_id });
      if (next.status === "routed") remember(id, { ...next, message_id: id });
      else { // nobody further: it stays with the person who has it, and the card says why
        remember(id, { ...r, escalation: { available: false, next_tier: null }, note: ownerNote(next.why) });
      }
      await load();
    } catch (e) { setError(errorMessage(e)); } finally { setEscalating(null); }
  }

  /** Helpful / not helpful. Pressing the pressed one again takes the vote back. */
  async function vote(id: string, value: 1 | -1) {
    const before = votes[id];
    const next = before === value ? 0 : value;
    setVotes((v) => { const n = { ...v }; if (next) n[id] = next; else delete n[id]; return n; });
    try { await post(`/chat/${id}/feedback`, { value: next }); } catch (e) {
      setVotes((v) => { const n = { ...v }; if (before) n[id] = before; else delete n[id]; return n; });
      setError(errorMessage(e));
    }
  }

  const empty = items !== null && items.length === 0 && !busy && !pending;
  const starters = useStarters(project.id, demo, empty);

  /* One list for answered turns and the one being answered: a turn keeps its key (its run id) when the answer lands, so its
     question and its steps stay where they are and fold into "Thought for …" instead of being drawn again. */
  const turns = (items ?? []).map((a, i) => {
    const mid = idOf(a);
    const key = a.key ?? (mid ? keys.current[mid] : undefined) ?? mid ?? `turn-${i}`;
    const r = mid ? routes[mid] : routes[key];
    const steps = r ? stepsOf(r) : [];
    const handled = r ? handledOf(r) : null;
    const isNew = fresh.current.has(key);
    return (
      <article key={key} id={mid ? `turn-${mid}` : undefined} data-turn={key} className={`ak-turn${mid && mid === here ? " here" : ""}${isNew ? " is-new" : ""}`}>
        <p className="ak-q">{a.question}</p>
        <Thinking steps={steps} live={false} ms={r?.thought_ms} />
        {handled && r ? (
          <HandledCard h={handled} r={r} />
        ) : r && (r.status === "routed" || r.status === "unrouted") ? (
          <Routed a={a} r={r} busy={escalating === mid} onEscalate={() => mid && escalate(mid, r)} />
        ) : r && r.status === "offered" ? (
          <NotEnough r={r} onAskPerson={() => ask(a.question, true, mid)} disabled={!!busy} />
        ) : r && r.status === "related" ? (
          <RelatedCard r={r} onAskPerson={() => ask(a.question, true)} disabled={!!busy}
                       vote={mid ? <Vote id={mid} votes={votes} onVote={vote} /> : null} />
        ) : a.status === "answered" ? (
          <AnswerCard a={a} r={r} onAskPerson={() => ask(a.question, true)} disabled={!!busy}
                      vote={mid ? <Vote id={mid} votes={votes} onVote={vote} /> : null} />
        ) : (
          <Unsure a={a} onAskPerson={!a.routed_to && !a.expert ? () => ask(a.question, true) : undefined} disabled={!!busy} />
        )}
      </article>
    );
  });
  if (pending) {
    turns.push(
      <article key={pending.key} className="ak-turn is-asking" aria-busy="true">
        <p className="ak-q">{pending.question}</p>
        <Thinking steps={pending.steps} live
                  waiting={pending.person ? "Finding the right person…" : "Checking the code, documents and KT sessions…"} />
      </article>,
    );
  }

  const composer = (
    <form className="ak-composer" onSubmit={(e: FormEvent) => { e.preventDefault(); ask(q, person); }}>
      {error && <p className="error" role="alert">{error}</p>}
      <div className={`ak-box${person ? " to-person" : ""}`}>
        <label htmlFor="q" className="sr-only">Your question</label>
        <input id="q" value={q} onChange={(e) => setQ(e.target.value)} disabled={!!busy} autoComplete="off" autoFocus={empty}
               placeholder={person ? "Your question for a person" : "Ask a question"} />
        <button type="button" className="ak-person" aria-pressed={person} onClick={() => setPerson((p) => !p)}
                title={person ? "Goes to a person, not the assistant" : "Send this to a person instead"}>
          <Glyph name="user" size={15} /><span>Ask a person</span>
        </button>
        <button type="submit" className="ak-send" disabled={!!busy || !q.trim()}>{person ? "Send" : "Ask"}</button>
      </div>
    </form>
  );

  return (
    <section className={`chat ask${empty ? " is-empty" : ""}`}>
      {empty ? (
        <>
          <header className="ak-hero">
            <Logo size={36} motion="arrive" className="ak-hero-mark" />
            <h1>Ask the project</h1>
            <p>Answers cite the code, documents and KT sessions.</p>
          </header>
          {composer}
          <div className="ak-empty">
            <p className="ak-empty-label" id="ak-try">Try one of these</p>
            <ul className="ak-starters" aria-labelledby="ak-try">
              {starters.map((s, i) => (
                <li key={s.q} style={{ ["--i" as string]: i } as CSSProperties}>
                  <button type="button" onClick={() => ask(s.q, false)}>
                    <span className="ak-starter-q">{s.q}</span>
                    {s.kt && <span className="ak-starter-kt" title="Explained in a KT session"><span className="sr-only">Explained in a KT session</span></span>}
                    <Glyph name="arrow" size={14} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </>
      ) : (
        <>
          <header className="page-head ak-head">
            <h1>Ask the project</h1>
            <p>Answers cite the code, documents and KT sessions.</p>
          </header>
          <div className="ak-thread">
            {items === null && (
              <div className="ak-turn" aria-busy="true"><span className="sr-only" role="status">Loading…</span>
                <div className="ak-skel" style={{ width: "46%", height: 14 }} /><div className="ak-card"><div className="ak-skel" style={{ height: 52 }} /></div>
              </div>
            )}
            {turns}
            <div ref={end} className="ak-end" />
          </div>
          {composer}
        </>
      )}
      <p className="sr-only" role="status" aria-live="polite">{said}</p>
    </section>
  );
}

function Vote({ id, votes, onVote }: { id: string; votes: Record<string, 1 | -1>; onVote: (id: string, v: 1 | -1) => void }) {
  return (
    <div className="ak-vote" role="group" aria-label="Was this answer helpful?">
      <span>{votes[id] ? "Thanks" : "Helpful?"}</span>
      <button className="icon" aria-pressed={votes[id] === 1} aria-label="Helpful" title={votes[id] === 1 ? "Take back your vote" : "Helpful"} onClick={() => onVote(id, 1)}><Icon name="up" size={16} /></button>
      <button className="icon" aria-pressed={votes[id] === -1} aria-label="Not helpful" title={votes[id] === -1 ? "Take back your vote" : "Not helpful"} onClick={() => onVote(id, -1)}><Icon name="down" size={16} /></button>
    </div>
  );
}

/** The small line at the top of a card that says which tier answered, with "Why this?" beside it (the Guide's rules, sources
 *  and trace, or the route's `why` lines from an older server). */
function Tier({ icon, children, r, end, tag = true }: { icon: ReactNode; children: ReactNode; r?: Route; end?: ReactNode; tag?: boolean }) {
  return (
    <div className="ak-tier">
      <span className="ak-tier-start">
        <span className="ak-tier-label">{icon}{children}</span>
        {r && <WhyThis explain={explainOf(r)} why={r.why} tag={tag} />}
      </span>
      {end}
    </div>
  );
}

/** The answer's words, with its numbered markers ("[2]") as small links to the source they cite. */
function AnswerText({ text, citations }: { text: string; citations: Citation[] }) {
  const parts = text.split(/(\[\d{1,2}\])/g);
  return (
    <p className="ak-text">
      {parts.map((part, i) => {
        const m = /^\[(\d{1,2})\]$/.exec(part);
        const c = m ? citations[Number(m[1]) - 1] : undefined;
        if (!m || !c) return part;
        const href = sourceHref(c);
        const cls = `ak-cite${isKt(c) ? " is-kt" : ""}`;
        return href
          ? <a key={i} className={cls} href={href} title={c.label} aria-label={`Source ${m[1]}: ${c.label}`}>{m[1]}</a>
          : <span key={i} className={cls} title={c.label}>{m[1]}</span>;
      })}
    </p>
  );
}

/** Tier 1: an answer from the sources, every sentence checked against them. */
function AnswerCard({ a, r, vote, onAskPerson, disabled }: { a: Answer; r?: Route; vote: ReactNode; onAskPerson: () => void; disabled: boolean }) {
  const checked = r?.check?.ok;
  const failed = r?.check && !r.check.ok;
  if (failed) return <NotEnough r={r} onAskPerson={onAskPerson} disabled={disabled} />;
  const numbered = /\[\d{1,2}\]/.test(a.answer);
  return (
    <div className="ak-card">
      <Tier icon={<Logo size={14} />} r={r}
            end={checked ? <span className="ak-checked" title="Every sentence matches a cited source"><Glyph name="check" size={13} />Sources checked</span> : undefined}>
        Answer
      </Tier>
      {a.stale && <p className="ak-stale"><Glyph name="alert" size={14} />The code this cites has changed since. Ask again for a fresh answer.</p>}
      <AnswerText text={a.answer} citations={a.citations} />
      {a.citations.length > 0 && <Sources citations={a.citations} numbered={numbered} />}
      {a.kt_clips.length > 0 && <Clips clips={a.kt_clips} />}
      {a.expert && <ExpertNote expert={a.expert} />}
      <div className="ak-foot">
        {vote}
        <button type="button" className="ak-quiet" onClick={onAskPerson} disabled={disabled}><Glyph name="user" size={14} />Ask a person</button>
      </div>
    </div>
  );
}

/** The sources as chips: the code at its line, the document at its page, the KT session at its moment (teal). Numbered when
 *  the answer cites them by number. */
function Sources({ citations, numbered = false }: { citations: Citation[]; numbered?: boolean }) {
  return (
    <div className="ak-block">
      <p className="ak-label">Sources</p>
      <ul className="ak-sources">
        {citations.map((c, j) => {
          const href = sourceHref(c);
          const va = c.source_type === "verified_answer" && c.chunk_id?.startsWith("va-") ? c.chunk_id.slice(3) : null;
          const inner = (
            <>
              {numbered ? <span className="ak-src-n" aria-hidden="true">{j + 1}</span> : <Glyph name={SOURCE_GLYPH[c.source_type] ?? "file"} size={13} />}
              <span className="ak-src-l">{c.label}</span>
            </>
          );
          const cls = `ak-src${isKt(c) ? " is-kt" : ""}`;
          return (
            <li key={j} style={{ ["--i" as string]: j } as CSSProperties}>
              {href ? <a href={href} className={cls} title={c.label}>{inner}</a> : <span className={cls} title="A verified answer, quoted above">{inner}</span>}
              {va && <FeedbackButton kind="answer" target={va} label="this verified answer" />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Moments of KT sessions that explain the cited code: teal, because that is exactly what teal means. */
function Clips({ clips }: { clips: KTClip[] }) {
  return (
    <div className="ak-block ak-block-kt">
      <p className="ak-label ak-label-kt">Explained in a KT session</p>
      <ul className="ak-clips">
        {clips.map((k, j) => (
          <li key={j}>
            <a className="ak-clip" href={`#/meetings/${k.meeting_id}?t=${Math.floor(k.start_s)}`}>
              <span className="ak-clip-play" aria-hidden="true"><Glyph name="play" size={10} fill /></span>
              <span className="ak-clip-t">{mmss(k.start_s)}</span>
              <span className="ak-clip-title">{k.meeting_title}{k.symbol ? <span className="ak-clip-sym"> · {k.symbol}</span> : null}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A free-form request another agent took (agentic contract §4, `ask.free`): who answered, what it proposes, and where to act
 *  on it. Read defensively; links go only to pages of this app. */
type Handled = { agent: string; name: string; text: string; actions: { label: string; href: string | null }[] };
function handledOf(r: Route): Handled | null {
  const raw = r.handled_by as unknown;
  const k = typeof raw === "string" ? raw : raw && typeof raw === "object" ? String((raw as { agent?: unknown }).agent ?? "") : "";
  if (!k || agentKey(k) === "guide") return null;
  const p = (r.proposal && typeof r.proposal === "object" ? r.proposal : {}) as Record<string, unknown>;
  const text = [p.text, p.summary, p.message, p.body, typeof r.proposal === "string" ? r.proposal : null, r.answer]
    .find((x): x is string => typeof x === "string" && !!x.trim()) ?? "";
  const list = Array.isArray(p.actions) ? p.actions : Array.isArray(r.actions) ? r.actions : [];
  const actions = (list as unknown[]).map((x) => {
    if (typeof x === "string") return { label: x, href: null };
    const o = (x ?? {}) as Record<string, unknown>;
    const label = [o.label, o.title, o.text].find((v): v is string => typeof v === "string" && !!v.trim());
    const given = [o.href, o.url, o.route].filter((v): v is string => typeof v === "string" && !!v);
    const to = given.find((v) => v.startsWith("#/"));
    if (given.length && !to) return null; // a link out of the app is never followed from agent output
    const href = to ?? (o.approval_id ? "#/approvals" : null);
    return label ? { label, href } : null;
  }).filter((x): x is { label: string; href: string | null } => !!x).slice(0, 4);
  return { agent: agentKey(k), name: agentName(k), text: text.trim(), actions };
}

function HandledCard({ h, r }: { h: Handled; r: Route }) {
  return (
    <div className="ak-card ak-handled">
      <Tier icon={<AgentGlyph agent={h.agent} size={14} />} r={r} tag={false}>{h.name} answered</Tier>
      {h.text && <p className="ak-text">{h.text}</p>}
      {h.actions.length > 0 && (
        <div className="ak-actions">
          {h.actions.map((x, i) => x.href
            ? <a key={i} className={`dp-btn small${i ? " ghost" : ""}`} href={x.href}>{x.label}</a>
            : <span key={i} className="ak-action-note">{x.label}</span>)}
        </div>
      )}
    </div>
  );
}

/** Tier 2: the same question was answered before. Shown without names. */
function RelatedCard({ r, vote, onAskPerson, disabled }: { r: Route; vote: ReactNode; onAskPerson: () => void; disabled: boolean }) {
  return (
    <div className="ak-card">
      <Tier icon={<Glyph name="shield" size={14} />} r={r}>Answered before</Tier>
      <ul className="ak-related">
        {(r.related ?? []).map((x) => (
          <li key={`${x.kind}-${x.id}`}>
            <p className="ak-related-q">{x.question}</p>
            <p className="ak-text">{x.answer}</p>
            <p className="ak-related-meta">
              <span>{x.kind === "verified_answer" ? "Verified answer" : "Answered by a teammate"}</span>
              {x.kind === "verified_answer" && <FeedbackButton kind="answer" target={x.id} label="this verified answer" />}
            </p>
          </li>
        ))}
      </ul>
      <div className="ak-foot">
        {vote}
        <button type="button" className="ak-quiet" onClick={onAskPerson} disabled={disabled}><Glyph name="user" size={14} />Not it? Ask a person</button>
      </div>
    </div>
  );
}

/** Tiers 3 and 4: sent to the mentor, or on to the owner of the code. Their answer appears here when they write it. */
function Routed({ a, r, busy, onEscalate }: { a: Answer; r: Route; busy: boolean; onEscalate: () => void }) {
  const owner = r.tier >= 4;
  const name = plain(r.routed_to?.name ?? a.expert?.name);
  if (r.status === "unrouted") {
    return (
      <div className="ak-card is-quiet">
        <Tier icon={<Glyph name="alert" size={14} />} r={r}>{notEnough(r) ? "Not enough evidence" : "No one to ask yet"}</Tier>
        <p className="ak-text ak-soft">Nobody else on this project can answer this yet.</p>
      </div>
    );
  }
  return (
    <div className="ak-card is-quiet">
      {notEnough(r) && <p className="ak-evidence"><Glyph name="alert" size={14} />Not enough evidence in the sources.</p>}
      <div className="ak-routed">
        <span className="ak-avatar" aria-hidden="true">{name.slice(0, 1).toUpperCase() || "?"}</span>
        <div className="ak-routed-text">
          <p className="ak-routed-to">Sent to {name} <span className="ak-role">{owner ? "code owner" : "mentor"}</span><WhyThis explain={explainOf(r)} why={r.why} /></p>
          <p className="ak-soft">{a.expert?.status === "answered" ? "Answered below." : r.note ? r.note : r.delivery === "digest" ? "In their daily digest." : "They were told now."}</p>
        </div>
        {r.escalation?.available && a.expert?.status !== "answered" && (
          <button type="button" className="dp-btn small" onClick={onEscalate} disabled={busy}>{busy ? "Sending…" : "Ask the code owner"}</button>
        )}
      </div>
      {a.expert?.status === "answered" && <ExpertNote expert={a.expert} />}
    </div>
  );
}

/** A turn the assistant could not answer, from the history: who has it now, or an offer to ask a person. */
function Unsure({ a, onAskPerson, disabled }: { a: Answer; onAskPerson?: () => void; disabled: boolean }) {
  return (
    <div className="ak-card is-quiet">
      <p className="ak-evidence"><Glyph name="alert" size={14} />Not enough evidence in the sources.</p>
      {a.expert ? <ExpertNote expert={a.expert} /> : onAskPerson && (
        <div className="ak-foot"><span /><button type="button" className="dp-btn small" onClick={onAskPerson} disabled={disabled}><Glyph name="user" size={14} />Ask a person</button></div>
      )}
    </div>
  );
}

/** Not enough evidence: the Guide offers a person (and has a draft ready for them), or its check failed. */
function NotEnough({ r, onAskPerson, disabled }: { r?: Route; onAskPerson: () => void; disabled: boolean }) {
  const next = /^Nothing was sent yet:\s*(.*)$/i.exec(r?.why?.find((w) => /^Nothing was sent yet/i.test(w)) ?? "")?.[1];
  return (
    <div className="ak-card is-quiet">
      <div className="ak-tier">
        <span className="ak-tier-start">
          <span className="ak-evidence"><Glyph name="alert" size={14} />Not enough evidence in the sources.</span>
          {r && <WhyThis explain={explainOf(r)} why={r.why} tag={false} />}
        </span>
      </div>
      {next && <p className="ak-soft">{next.replace(/\s*\((?:[^)]*)\)/g, "").replace(/^./, (c) => c.toUpperCase())}</p>}
      <div className="ak-foot"><span /><button type="button" className="dp-btn small" onClick={onAskPerson} disabled={disabled}><Glyph name="user" size={14} />Ask a person</button></div>
    </div>
  );
}

function ExpertNote({ expert }: { expert: Expert }) {
  const name = plain(expert.name) || "An expert";
  if (expert.status !== "answered") {
    return <p className="ak-wait"><Icon name="inbox" size={14} />With {name}. Their answer will appear here.</p>;
  }
  return (
    <div className="ak-expert" role="group" aria-label={`Answer from ${name}`}>
      <p className="ak-expert-head"><Icon name="check" size={14} /><strong>{name} answered</strong>{expert.answered_at && <span>{when(expert.answered_at)}</span>}</p>
      <p className="ak-text">{expert.answer}</p>
    </div>
  );
}
