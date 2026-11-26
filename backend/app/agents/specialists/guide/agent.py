"""The Guide: answers with sources, and sends the rest to the right person.

Two triggers. `chat.question` is the Q&A agent (app/agents/specialists/guide/qa.py) as it always was. `ask.route` (and an `ask.free` request
the Supervisor routes here) is tiered answer routing (app/lifecycle/routing.py), with tier 1 run as agentic RAG
(app/agents/core/runtime.py) when the model can run an agent loop:

  1. research the research crew (app/rag/crew.py, docs/specs/rag-crew.md): it plans several searches, runs them over code,
              documents, KT calls and verified answers, fuses and ranks the results. Its plan, search and rank steps open
              the trace; its evidence is the agent's first sources, each with why it ranked (our words), and their
              similarities to the question as asked are the retrieval gate's evidence
  2. loop     the agent plans and gathers (search_knowledge with rewritten queries, read_file, find_symbol, kt_moments,
              verified_answers) and calls final_answer with a GuideAnswer: the answer with [n] citations, or "not enough",
              who should answer (from who_knows, code_owners, kt_moments) and a draft for them
  3. verify   VERIFY, the model's own check of each claim against the cited source, and routing.citation_check, the hard
              guard that has the last word
  4. repair   at most one final-only turn that rewrites what either check found unsupported; the hard guard decides again
  5. decide   answered (tier 1); or the verified answers it judged related (tier 2); or a person is offered, with the
              agent's suggestion of who (used at tier 4 if routing.suggested_target finds them eligible; the mentor comes first)

Budgets: GUIDE_STEPS tool calls and GUIDE_TIMEOUT_S for the loop, GUIDE_POST_S for verify and repair. Guardrails stay in code:
the retrieval gate and the citation check, only the asker sends a question to a person (the capped path:
routing.deliver), no other hire's words in tier 2, the people a suggestion may name.

When the model cannot run a loop (the offline demo, the AI off, "rules" mode, the daily agent budget used) the
deterministic path answers from the same evidence under the same gates (routing.ai_answer): a model's one call, or offline
the extractive writer quoting the sources. Every result carries `explain`, `run_id` and `steps` (the trace; streamed live to
the asker on topic agent:<run_id>), `crew` (how the evidence was found: counts only) whenever the crew ran, and on tier 1
`writer` ("model", "extractive" or null). The run record keeps the shared, privacy-filtered trace. Run summaries name the
tier and the outcome, never the question."""
from __future__ import annotations

import time
import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.core import prompts
from app.agents.core import registry as R
from app.agents.core import runtime as rt
from app.agents.core.citations import label_for
from app.agents.core.llm import NoToolUse, defang, embed_data
from app.agents.core.schemas import ClaimCheck, GuideAnswer, Hit, QAResult
from app.agents.specialists.guide import qa

KEY = "guide"
DELIVERY = {"now": "told now", "digest": "in their digest"}
GUIDE_STEPS = 8  # tool calls in one loop
GUIDE_TIMEOUT_S = 40.0  # wall clock for the loop: free-tier turns took 10-20 s under load (2026-11-14), and the steps stream live meanwhile
GUIDE_POST_S = 10.0  # VERIFY and the repair turn, after the loop
GUIDE_SMART = False  # the loop and VERIFY run on MODEL_FAST: free-tier MODEL_SMART took 16-40 s a turn (eval, 2026-11-14)
SNIPPET = 1800
MAX_WHY = 4  # reasons shown with each of the crew's first results


def answer(deps, project_id: str, user_id: str, question: str):
    """chat.question: the QA agent, with the Guide's mode."""
    return qa.answer(R.deps_for(deps, R.org_of(deps, project_id), KEY), project_id, user_id, question)


# ---------------------------------------------------------------- tier 1 as an agent loop
class _Fallback(Exception):
    """The loop failed (the model erred mid-run): the deterministic path answers instead."""


def attempt(ctx, project_id: str, question: str, ask_person: bool, message_id: str | None, steps: list, on_step) -> dict:
    """Tier 1's attempt, as routing.attempt returns it, plus `agentic`, `route`, `related`, `crew` (the research crew's
    summary) and `writer`: the agent loop when the model can run one, else the deterministic attempt (the same evidence, the
    same gates, the same shape). The crew runs with the Guide's own Deps (ctx.deps: in rules mode its model refuses, so the
    crew plans and ranks by rules alone)."""
    from app.lifecycle import routing

    if ask_person:  # the asker asked for a person: no answer is attempted (routing.attempt reads the offered turn's draft)
        return {**routing.attempt(ctx, project_id, question, True, message_id), "agentic": False}
    found, degraded = routing.gather(ctx.deps, project_id, question, steps=steps, on_step=on_step)
    hits, summary = found.hits, {**found.summary(), "degraded": degraded}  # degraded: the embedder failed (the trace says so)
    try:
        ai = {**agentic(ctx, project_id, question, hits, steps, on_step, reasons=found.reasons), "crew": summary}
    except (NoToolUse, _Fallback):
        t1 = time.monotonic()
        ai = {**routing.ai_answer(ctx, project_id, question, hits), "earlier": None, "agentic": False, "crew": summary}
        if ai.get("check") is not None:
            ok = ai["check"]["ok"]
            quoted = "extractive" in {ai.get("writer"), routing.writer_of(ctx.deps.llm)} or routing.AI_OFF_WHY in (ai.get("why") or [])
            wrote = "Quoted the sources" if quoted else "Wrote an answer"
            rt.step(steps, "verify", f"{wrote} and checked every sentence against its sources" + ("" if ok else ": one was not supported"),
                    ms=_ms(t1), on_step=on_step)
    if degraded:
        ai["why"] = list(ai.get("why") or []) + [routing.SEARCH_DOWN]
    return ai


def _ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)


def agentic(ctx, project_id: str, question: str, hits: list[Hit], steps: list, on_step, reasons: dict[str, list[str]] | None = None) -> dict:
    """The loop, VERIFY, the hard guard, at most one repair, and the decision. `hits`: the research crew's evidence, best
    first (the agent's first sources); `reasons`: why each ranked where it did, lines our code wrote (crew.Retrieval.reasons).
    Raises NoToolUse (no loop possible) or _Fallback (the model failed mid-run)."""
    from app.agents.tools import GUIDE_TOOLS, REGISTRY
    from app.lifecycle import routing

    deps, user = ctx.deps, ctx.user
    tctx = rt.ToolCtx(deps=deps, org_id=user.org_id, project_id=project_id, user=user, agent=KEY, clock=ctx.clock)
    rows = []
    for h in hits:
        n = tctx.cite(h.chunk, h.similarity)
        why = list((reasons or {}).get(h.chunk.id) or [])[:MAX_WHY]
        rows.append({"n": n, "source": tctx.evidence[n - 1].source.label, "similarity": round(h.similarity, 2),
                     **({"why": why} if why else {}), "text": h.chunk.content[:SNIPPET]})
    task = ("Answer the asker's question in the data. Below are the first results the research crew found for it, best first, "
            "as sources [n], each with why it ranked there. If they do not state the answer, gather more with the tools; then "
            "call final_answer.\n\n" + embed_data({"question": question, "first_results": rows}))
    out = rt.run_agent(tctx, agent=KEY, system=prompts.GUIDE_AGENT, task=task, tools=REGISTRY.select(*GUIDE_TOOLS), output=GuideAnswer,
                       max_steps=GUIDE_STEPS, timeout_s=GUIDE_TIMEOUT_S, on_step=on_step, smart=GUIDE_SMART, trace=steps)
    if out.output is None and out.stopped in {"error", "timeout"} and out.tool_calls == 0:
        # the model failed, or slow turns used up the loop before it looked at anything: the one-call writer answers from
        # the crew's evidence instead of a refusal (the same gates decide)
        raise _Fallback(out.error or f"the loop stopped: {out.stopped}")
    base = {"result": None, "hits": hits, "draft": "", "cited_paths": [], "check": None, "retrieval": None, "earlier": None,
            "agentic": True, "route": None, "related": [], "outcome": out, "writer": None}
    if out.output is None:
        why = {"timeout": "the Guide ran out of time", "budget": "the Guide used its step budget"}.get(out.stopped, "the assistant stopped")
        return {**base, "cited_paths": _paths(tctx.evidence), "why": [f"Not enough evidence: {why} before it could answer."]}
    ans: GuideAnswer = out.output
    post_deadline = time.monotonic() + GUIDE_POST_S
    base.update(related=_related(tctx, ans), route=_route(tctx, ans))
    evid = tctx.cited(ans.cited) if ans.sufficient and ans.answer.strip() else []
    if not evid:
        return _not_answered(tctx, base, ans, "Not enough evidence: the sources the Guide read do not contain the answer.")
    gate = _gate(question, evid, hits)
    base["retrieval"] = gate
    gate_msg = routing.gate_refusal(gate)
    # A file the agent chose to open by following a lead (requirements.txt for "which packages?") has no similarity to the
    # question's words. Its gate is waived here and decided below, more strictly: every claim passes the hard guard, VERIFY
    # ran and supports every claim, and the answer addresses the question (shares its subject words). No repair then.
    waived = not gate["ok"]
    t0 = time.monotonic()
    ans = ans.model_copy(update={"answer": exact_names(ans.answer, [e.chunk for e in evid])})
    check = routing.citation_check(ans.answer, [e.chunk for e in evid])
    claims = [r for r in check["sentences"] if r["claim"]]
    unsupported = [r["text"] for r in claims if not r["sources"]]
    verdict = _verify(tctx, question, claims, evid, post_deadline) if check["ok"] else []
    flagged = verdict or []
    held = len(claims) - len(set(unsupported) | set(flagged))
    rt.step(steps, "verify", f"Verifying {len(claims)} claim{'s' if len(claims) != 1 else ''}: {held} supported", ms=_ms(t0), on_step=on_step)
    repaired = False
    if waived:
        # the answer is about the question: it holds one of its subject words, in any form or misspelt as the asker typed it
        topical = routing.held_share(question, ans.answer)[0] > 0
        if not (check["ok"] and not unsupported and verdict == [] and claims and topical):
            return _not_answered(tctx, base, ans, gate_msg)
        gate = base["retrieval"] = {**gate, "ok": True, "rule": "read"}
    if unsupported or flagged:
        bad = list(dict.fromkeys(unsupported + flagged))
        fixed = _repair(tctx, out, bad, post_deadline, on_step)
        t0 = time.monotonic()
        evid2 = tctx.cited(fixed.cited) if fixed and fixed.sufficient and fixed.answer.strip() else []
        if evid2:
            fixed = fixed.model_copy(update={"answer": exact_names(fixed.answer, [e.chunk for e in evid2])})
        check2 = routing.citation_check(fixed.answer, [e.chunk for e in evid2]) if evid2 else {"ok": False, "sentences": []}
        gate2 = _gate(question, evid2, hits) if evid2 else {"ok": False}
        rt.step(steps, "verify", "Checked the rewritten answer: " + ("every sentence has its source" if check2["ok"] and gate2["ok"]
                                                                       else "still not supported"), ms=_ms(t0), on_step=on_step)
        if not (check2["ok"] and gate2["ok"]):
            loose = bad[0] if bad else "the answer"
            return _not_answered(tctx, {**base, "check": check}, ans,
                                 f"Not enough evidence: \"{loose[:100]}\" is not supported by any one cited source.")
        ans, evid, check, gate, repaired = fixed, evid2, check2, gate2, True
        base["retrieval"] = gate
    cited = [e.chunk for e in evid]
    numbers = [e.n for e in evid]
    text = _renumber(routing.align_markers(ans.answer, cited, numbers), numbers)  # each claim's [n] at a source that says it
    result = QAResult(status="answered", answer=text, citations=[qa._citation(c) for c in cited],
                      kt_clips=_clips(tctx, deps, project_id, ans, evid), confidence=ans.confidence, writer="model")
    how = (_waived_how(tctx, evid) if gate["rule"] == "read" else
           routing.GATE_HOW[gate["rule"]].format(sim=gate["similarity"], min=routing.MIN_SIMILARITY, names=", ".join(gate["names"][:3])))
    acts = out.tool_calls
    return {**base, "result": result, "check": check, "draft": ans.answer, "cited_paths": _paths(evid), "writer": "model",
            "why": [f"Answered from {len(cited)} source{'s' if len(cited) != 1 else ''} the Guide found in {acts + 1} search and read "
                    f"step{'s' if acts else ''}: {how}.",
                    "Every sentence is supported by the cited sources (its names, numbers and negation agree)."
                    + (" The first draft was rewritten once to drop what the sources did not say." if repaired else "")]}


def _waived_how(tctx, evid) -> str:
    """Why an answer whose sources are below the retrieval gate was still given: the files the Guide opened itself, or (when
    it opened none) the checks alone. Never "opened" for a source that only a search found."""
    opened = tctx.notes.get("opened") or set()  # read_file and find_symbol note the sources they open
    if any(e.n in opened for e in evid):
        return "the Guide opened these files itself, and its check confirmed every claim against them"
    return ("the cited sources are below the similarity bar for the question as worded, but the model's check and the "
            "citation check confirmed every claim against them")


def _paths(evidence) -> list[str]:
    return list(dict.fromkeys(e.chunk.ref["path"] for e in evidence if e.chunk.source_type == "code" and e.chunk.ref.get("path")))[:10]


def _gate(question: str, evid, hits: list[Hit]) -> dict:
    """The retrieval gate on the agent's cited sources, against the question AS ASKED: routing.answer_gate (similarity, an
    identifier the question names, or the question's subject words: multi-hop sources the crew did not find pass when they
    hold at least routing.MIN_TERMS of them, since VERIFY checks every claim next), with each source's similarity from the
    crew's evidence (a file read takes that of the evidence of the same lines; a source the crew did not find, 0)."""
    from app.lifecycle import routing

    def sim(c) -> float:
        best = 0.0
        for h in hits:
            if h.chunk.id == c.id:
                return h.similarity
            r, q = h.chunk.ref, c.ref
            if c.source_type == "code" and h.chunk.source_type == "code" and r.get("path") == q.get("path") \
                    and (r.get("start_line") or 0) <= (q.get("end_line") or 0) and (q.get("start_line") or 0) <= (r.get("end_line") or 0):
                best = max(best, h.similarity)
        return best

    return routing.answer_gate(question, [Hit(chunk=e.chunk, score=0.0, similarity=sim(e.chunk)) for e in evid], verified=True)


def _verify(tctx, question: str, claims: list[dict], evid, deadline: float) -> list[str] | None:
    """VERIFY: the model checks each claim against the cited sources. Returns the sentences it found unsupported, or None when
    the check could not run (the hard guard still holds; a waived retrieval gate then does not pass)."""
    if not claims:
        return []
    left = deadline - time.monotonic()
    if left < 3:
        return None
    data = {"question": question, "sentences": [{"n": i + 1, "text": c["text"]} for i, c in enumerate(claims)],
            "sources": [{"n": e.n, "source": e.source.label, "text": e.chunk.content[:4000]} for e in evid]}
    try:
        out = rt.run_agent(tctx, agent=f"{KEY}_verify", system=prompts.GUIDE_VERIFY, tools=[], output=ClaimCheck, max_steps=0,
                           timeout_s=min(left, 8.0), task="Check each sentence of the answer in the data.\n\n" + embed_data(data))
    except NoToolUse:
        return None
    if out.output is None:
        return None
    by_n = {v.sentence: v for v in out.output.claims}
    return [c["text"] for i, c in enumerate(claims) if i + 1 in by_n and not by_n[i + 1].supported]


def _repair(tctx, out, bad: list[str], deadline: float, on_step) -> GuideAnswer | None:
    """The one repair turn: final_answer only, rewriting the sentences the checks found unsupported."""
    from app.agents.tools import GUIDE_TOOLS, REGISTRY

    left = deadline - time.monotonic()
    if left < 3:
        return None
    t0, at = time.monotonic(), len(out.steps)
    task = ("The checks found sentences in your answer that no single cited source states (listed in the data). Rewrite the "
            "answer so that every sentence is stated by one source you cite: correct or remove those sentences, keep names and "
            "numbers exactly as the sources write them (in the same letter case, even at the start of a sentence), add nothing new, "
            "end each sentence with its [n]. If too little is left, "
            "set sufficient=false. Call final_answer.\n\n" + embed_data({"unsupported": bad[:8]}))
    again = rt.run_agent(tctx, agent=KEY, system=prompts.GUIDE_AGENT, task=task, tools=REGISTRY.select(*GUIDE_TOOLS), output=GuideAnswer,
                         max_steps=0, timeout_s=left, resume=out, smart=GUIDE_SMART, trace=out.steps)
    del out.steps[at:]  # the repair turn's own summary (if any) gives way to the line below, which carries its time
    rt.step(out.steps, "think", f"Rewriting the answer: {len(bad)} sentence{'s' if len(bad) != 1 else ''} had no source that "
            f"state{'s' if len(bad) == 1 else ''} {'it' if len(bad) == 1 else 'them'}", ms=_ms(t0), on_step=on_step)
    return again.output


def exact_names(answer: str, chunks) -> str:
    """Names in the answer written in the sources' own letter case: "Solve.py" at the start of a sentence becomes "solve.py"
    when the cited sources write exactly one name that differs from it only by case. A name the sources do not have is
    left alone, so the citation check (case-sensitive, unchanged) still refuses it."""
    from app.lifecycle import routing

    text = "\n".join(f"{c.content}\n{label_for(c)}" for c in chunks)
    forms: dict[str, set[str]] = {}
    for name in routing.IDENT.findall(text):
        forms.setdefault(name.lower(), set()).add(name)

    def fix(m):
        name = m.group(0)
        if name in text:
            return name
        same = forms.get(name.lower(), set())
        return next(iter(same)) if len(same) == 1 else name

    return routing.IDENT.sub(fix, answer)


def _renumber(text: str, order: list[int]) -> str:
    """routing.renumber: the answer's [n] markers renumbered to the citations' order ([1] is the first citation)."""
    from app.lifecycle import routing

    return routing.renumber(text, order)


def _clips(tctx, deps, project_id: str, ans: GuideAnswer, evid) -> list:
    """KT clips: those that explained the cited code (qa.kt_clips_for), and the KT moments the agent cited or picked."""
    clips = qa.kt_clips_for(deps, project_id, [e.chunk for e in evid if e.chunk.source_type == "code"])
    links = tctx.notes.get("kt_links", {})
    for n in list(ans.kt) + [e.n for e in evid if e.chunk.source_type == "meeting"]:
        lk = links.get(n)
        if lk is not None and not any((c.meeting_id, c.file_path, c.start_s) == (lk.meeting_id, lk.file_path, lk.start_s) for c in clips):
            clips.append(lk)
    return clips[:3]


def _related(tctx, ans: GuideAnswer) -> list[dict]:
    """Verified answers the agent judged to answer the question, among those its tool showed with at least
    routing.RELATED_SIMILARITY (de-identified by the tool: never another hire's words)."""
    from app.lifecycle import routing

    shown = tctx.notes.get("related", {})
    return [{k: shown[rid][k] for k in ("kind", "id", "question", "answer", "similarity")}
            for rid in dict.fromkeys(ans.related) if rid in shown and shown[rid]["similarity"] >= routing.RELATED_SIMILARITY][:routing.MAX_RELATED]


def _route(tctx, ans: GuideAnswer) -> dict | None:
    """The agent's suggestion of who should answer, if its tools named that person. The reason kept is the tools' own line
    (CODEOWNERS, a KT call), not the model's words; routing.suggested_target checks eligibility again when it is used."""
    uid = (ans.route_to or "").strip()
    if not uid or uid not in tctx.people:
        return None
    u = tctx.deps.store.get_user(uid)
    return {"to_user_id": uid, "name": u.name if u else None, "why": tctx.people[uid][:300]} if u else None


def _not_answered(tctx, base: dict, ans: GuideAnswer, reason: str) -> dict:
    draft = defang((ans.owner_draft or ans.answer or "").strip())[:4000]
    why = [reason]
    if base.get("route"):
        why.append(f"The Guide suggests {base['route']['name']} should answer: {base['route']['why']}.")
    return {**base, "draft": draft, "cited_paths": base.get("cited_paths") or _paths(tctx.evidence), "why": why}


# ---------------------------------------------------------------- the subgraph
class TierState(TypedDict, total=False):
    event: dict
    trace: str | None
    ai: dict
    result: dict
    steps: list
    run_id: str
    t0: float  # time.monotonic() when the Guide started
    t1: float  # when its last tier began deciding (the final step's time)
    seeded: int  # steps that came before the Guide's own (ask.free: the Supervisor's decision)


def tiers(deps):
    """ask.route as a LangGraph subgraph, one node per tier (app/lifecycle/routing.py decides each):

        START -> tier1 -(answered)-> explain        the agent's (or QA's) answer through the retrieval gate and the citation check
                 tier1 -> tier2 -(related)-> explain   a verified answer to a similar question
                          tier2 -> person -> explain   nothing answered it: offer a person, or hand it to the mentor / owner
        START -> escalation -> explain               escalate_request_id: the domain owner, with the draft

    The API validated the caller and the request it escalates. Compiled without a checkpointer: it never pauses. `explain`
    adds the final step, `run_id` and `steps` to the result."""
    from app.lifecycle import routing

    def ctx_of(ev):
        user = deps.store.get_user(ev["user_id"])
        return R.lifecycle_ctx(R.deps_for(deps, user.org_id, KEY), user.org_id, user)

    def live(state):
        ev = state["event"]
        return rt.publisher(deps, run_id=state.get("run_id"), org_id=R.org_of(deps, ev.get("project_id")) or "", user_id=ev.get("user_id"),
                            project_id=ev.get("project_id"))

    def start(state):
        now = time.monotonic()
        seed = list(state.get("steps") or [])
        return {"steps": seed, "seeded": len(seed), "run_id": state["event"].get("run_id") or str(uuid.uuid4()), "t0": now, "t1": now}

    def tier1(state):
        ev = state["event"]
        ctx = ctx_of(ev)
        ai = attempt(ctx, ev["project_id"], ev["question"], bool(ev.get("ask_person")), ev.get("message_id"), state["steps"], live(state))
        return {"ai": ai, "t1": time.monotonic(), "result": routing.tier1(ctx, ev["project_id"], ev["question"], ai)}

    def tier2(state):
        ev = state["event"]
        return {"result": routing.tier2(ctx_of(ev), ev["project_id"], ev["question"], state["ai"], bool(ev.get("ask_person")))}

    def person(state):
        ev = state["event"]
        return {"result": routing.to_person(ctx_of(ev), ev["project_id"], ev["question"], state["ai"], bool(ev.get("ask_person")),
                                            bool(ev.get("urgent")), ev.get("message_id"))}

    def escalation(state):
        ev = state["event"]
        return {"result": routing.escalate(ctx_of(ev), deps.store.get_expert_request(ev["escalate_request_id"]), bool(ev.get("urgent")))}

    def explain(state):
        out, steps, ai = state["result"], state["steps"], state.get("ai") or {}
        out = {**out, **research(out, ai)}
        final_step(out, ai.get("route"), steps, live(state), _ms(state["t1"]))
        return {"result": {**out, "explain": explain_route(out, state.get("trace")), "run_id": state["run_id"],
                           "steps": [s.public() for s in steps]}}

    g = StateGraph(TierState)
    for name, fn in (("start", start), ("tier1", tier1), ("tier2", tier2), ("person", person), ("escalation", escalation), ("explain", explain)):
        g.add_node(name, fn)
    g.add_edge(START, "start")
    g.add_conditional_edges("start", lambda s: "escalate_request" if s["event"].get("escalate_request_id") else "question",
                            {"escalate_request": "escalation", "question": "tier1"})
    g.add_conditional_edges("tier1", lambda s: "answered" if s.get("result") else "not answered", {"answered": "explain", "not answered": "tier2"})
    g.add_conditional_edges("tier2", lambda s: "related" if s.get("result") else "not answered", {"related": "explain", "not answered": "person"})
    g.add_edge("person", "explain")
    g.add_edge("escalation", "explain")
    g.add_edge("explain", END)
    return g.compile(checkpointer=False)


def research(out: dict, ai: dict) -> dict:
    """What the answer adds about its evidence: `crew` on every tier whose attempt ran the research crew (counts, intent,
    planner, times: never the question, a query or the model's words), and on tier 1 `writer`: "model", "extractive" (the
    offline writer quoted the sources) or None when nothing was written (an offered turn)."""
    extra = {}
    if ai.get("crew"):
        extra["crew"] = ai["crew"]
    if out.get("tier") == 1 and out.get("status") in {"answered", "offered"}:
        extra["writer"] = ai.get("writer") if out.get("status") == "answered" else None
    return extra


def final_step(out: dict, route: dict | None, steps: list, on_step, ms: int = 0) -> None:
    """The trace's last line: what happened. Names reach only the asker's trace; the shared line says what kind of thing."""
    status, n = out.get("status"), len(out.get("citations") or [])
    who = (out.get("routed_to") or {}).get("name")
    if status == "answered":
        text = shared = f"✓ Sources checked · answered from {n} source{'s' if n != 1 else ''}"
    elif status == "related":
        text = shared = "Found a verified answer to a similar question"
    elif status == "offered":
        shared = "Not enough evidence · offered a person"
        text = "Not enough evidence · you can ask a person" + (f" (the Guide suggests {route['name']})" if route and route.get("name") else "")
    elif status == "routed":
        text, shared = f"Sent to {who} ({DELIVERY.get(out.get('delivery'), out.get('delivery'))})", "Handed the question to a person"
    else:
        text = shared = "Nobody to hand it to"
    rt.step(steps, "final", text, shared=shared, ms=ms, on_step=on_step)


def trace_of(done: dict, user_id: str | None) -> dict:
    """The run record's part of a Guide run (from the subgraph's final state): the shared steps, who it was for, whether the
    agent loop ran, and the run's id."""
    ai = done.get("ai") or {}
    steps = done.get("steps") or []
    before = sum(s.ms for s in steps[:done.get("seeded", 0)])  # ask.free: the Supervisor's decision came first
    total = _ms(done["t0"]) + before if done.get("t0") else None
    detail = R.run_detail(ai.get("outcome"), steps=steps, user_id=user_id, agentic=bool(ai.get("agentic")), ms=total)
    return {**detail, "run_id": (done.get("result") or {}).get("run_id")}


def explain_route(out: dict, trace: str | None = None) -> dict:
    tier = out.get("tier")
    sources, confidence, ai = [], None, False
    if tier == 1 and out.get("status") == "answered":
        sources = [R.citation_source(c) for c in out.get("citations", [])] + [R.citation_source(k) for k in out.get("kt_clips", [])]
        confidence, ai = out.get("confidence"), out.get("writer") == "model"  # the offline writer quotes: no model wrote it
    elif tier == 2:
        sources = [R.source(f"Verified answer ({r['similarity']:.2f} similar)", "doc", answer=r["kind"], id=r["id"]) for r in out.get("related", [])]
        confidence = out["related"][0]["similarity"] if out.get("related") else None
    elif out.get("routed_to"):
        who = out["routed_to"]
        sources = [R.source(f"Routed to {who['name']} ({DELIVERY.get(out.get('delivery'), out.get('delivery'))})", "event",
                            user_id=who["id"], request_id=out.get("request_id"))]
    return R.explain(KEY, out.get("why", []), sources, confidence, ai, trace)


def summary(out: dict) -> str:
    """The run's line: the tier and what happened, never the question."""
    tier, status = out.get("tier"), out.get("status")
    what = {"answered": "answered from sources", "related": "answered by a verified answer", "routed": "handed to a person",
            "unrouted": "nobody to hand it to", "offered": "not answered; a person was offered"}.get(status, status)
    who = {3: " (the mentor)", 4: " (the domain owner)"}.get(tier, "") if status == "routed" else ""
    return f"Routed a question: tier {tier}, {what}{who}"


def digests(deps, org_id: str, day=None) -> int:
    """The daily tick's part for the Guide: each person with the digest on gets one notification for the questions that wait
    for them (app/lifecycle/routing.digests). Rules only: the count, never the questions."""
    from app.lifecycle import routing

    return routing.digests(R.lifecycle_ctx(deps, org_id, None, R.clock_for(day)), org_id)
