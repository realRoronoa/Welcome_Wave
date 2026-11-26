"""ask.free: the Supervisor's decision of which agent handles a free-form request typed in Ask (docs/AGENTS.md).

It is a model's decision with its own trace (runtime.run_agent with no tools, one final-only turn: FreeRoute). The Guide is the
default; "book a KT session with Ravi about webhooks" is the Planner's, "what should I do next?" the Coach's, "which first task
should I pick?" the Scout's. For those three the result is, for now, a short proposal text: Track K's agents fill in the real
calls. Without a model (the offline demo, the AI off, "rules" mode, or every model attempt failed) rules decide
(`rule_route`): a question about the code, documents or KT content is the Guide's, and only an explicit request for a plan,
a first task or coaching goes to the Planner, Scout or Coach. The request is data: it reaches the model only inside the data block, and the proposal text is checked in code
(registry.check_text) before anyone reads it.

Then the specialist itself runs (`dispatch`): the Coach's next step (coach.next_step), the Planner's KT session proposal
(planner.kt_session) or the Scout's picks (scout.suggest), each its own agent loop whose steps continue this trace and whose
output code checks. Its proposal is {text, actions: [{label, href: "#/..."}]} (app-internal links the web app shows as
buttons) with its details (the session, the picks, the signals). Nothing is done: a person acts on it. When the specialist
cannot run (no program here, no model, "rules" mode) or fails, the decision's own proposal text stands."""
from __future__ import annotations

import logging
import re
import time

from app.agents.core import prompts
from app.agents.core import registry as R
from app.agents.core import runtime as rt
from app.agents.core.llm import NoToolUse, embed_data
from app.agents.core.schemas import FreeRoute

log = logging.getLogger("agents.orchestrator")

# ---------- the rules (no model): a question about the project is the Guide's; only an explicit request is a specialist's ----------
# The text is cut into clauses. A clause is a request for the Planner, Coach or Scout only when its own wording asks that agent
# for its job: booking or planning, the asker's next step or progress, choosing a first task. Any other clause that asks
# something (a question, or "explain", "show me" ...) is a question about the code, documents or KT content, and one such clause
# makes the whole request the Guide's: "I am stuck: how do I run the tests?" and "Which issue did Ravi suggest as my first PR?"
# are answered, not proposed. Nothing asked at all is the Guide's too (the default).
_ASK = r"^(?:(?:ok(?:ay)?|so|hi|hey|please|now|then|also|and)\s+)*"  # filler before a request
_POLITE = (_ASK + r"(?:(?:can|could|would|will) you\s+(?:please\s+)?|help me\s+|i (?:want|need|would like|'d like) (?:you )?to\s+"
           r"|let'?s\s+|i'?d like to\s+)?")
_TASK = r"(?:task|issue|ticket|pr|pull request|change|bug)s?"
_FIRST = r"(?:first|starter|starting|newcomer|beginner|good first|easy|small|safe)"
RULES = (  # (agent, the request forms that ask it for its job); the first agent with a requesting clause wins
    ("planner", re.compile(
        _POLITE + r"(?:book|schedule|arrange|set up|organi[sz]e|plan|line up|draft|make|create|write|prepare|build|update)\b"
        r".*\b(?:kt|session|call|meeting|walkthrough|sync|1:1|one-on-one|plan|30/60/90)\b"
        r"|^how (?:do|can|could|should) i (?:book|schedule|arrange|set up|get|request|plan)\b.*\b(?:kt|walkthrough)\b"
        r"|\b30/60/90\b", re.I)),
    ("coach", re.compile(
        r"\bwhat (?:should|do|can|could) i (?:do|focus on|work on) next\b|\bi'?m stuck\b|\bi am stuck\b|\bhow am i doing\b"
        r"|\bmy progress\b|\bam i on track\b|\bwhat should i focus on\b"
        r"|\b(?:my|our) next steps?\b|^(?:what(?:'s| is| are)\s+)?(?:the\s+)?next steps?(?: for me)?$", re.I)),
    ("scout", re.compile(
        r"\bwhat (?:can|should|could) i (?:work on|pick up|start (?:on|with)|take on)\b"
        rf"|^(?:which|what)\s+(?:{_FIRST}\s+){{0,2}}{_TASK}\s+(?:should|could|can|would|do) i\b"
        rf"|^what(?:'s| is| are| would be)\s+(?:a|an|the|some|my)?\s*(?:good|safe|easy|best|right)?\s*{_FIRST}\s+{_TASK}"
        r"(?:\s+(?:for me|to (?:start|pick|take|work)(?: on| with| up)?))?\s*$"  # the whole clause: "the first PR checklist" is a question for the Guide
        rf"|{_POLITE}(?:suggest|recommend|find|pick|choose|shortlist|give|assign|propose|get)\b(?:\s+me)?\s+(?:a|an|my|some|one)?\s*"
        rf"(?:good\s+|safe\s+|easy\s+|small\s+)?{_FIRST}\s+{_TASK}"
        rf"|^i (?:need|want|would like|'d like)\s+(?:a|an|my|some)\s+(?:good\s+)?{_FIRST}\s+{_TASK}"
        rf"|^(?:my\s+|a\s+)?(?:good\s+)?{_FIRST}\s+{_TASK}$", re.I)),
)
_CUT = re.compile(r"([?!;\n]+|\.(?=\s|$)|:(?=\s)|,|\s+(?:but|and so|so)\s+|\s+and\s+(?=(?:what|which|where|who|whom|whose|when|why|how)\b))", re.I)
_WH = r"(?:what|which|where|who|whom|whose|when|why|how)\b"
_AUX = (r"(?:is|are|was|were|am|do|does|did|can|could|should|would|will|has|have|had|shall)\s+"
        r"(?:i|you|we|it|he|she|they|there|this|that|these|those|the|a|an|my|our|your|any|every|all)\b")
_QUESTION = re.compile(rf"^(?:{_WH}|{_AUX})", re.I)
_INFO = re.compile(_ASK + r"(?:(?:can|could|would) you\s+(?:please\s+)?)?(?:explain|tell me|show me|describe|walk me through|summari[sz]e"
                   r"|list|define|point me|help me understand|find out|clarify)\b", re.I)
_REPORTED = re.compile(r"\b(?:said|mentioned|suggested|recommended|explained|told|discussed|talked about|showed)\b"
                       r"|\b(?:did|does|has|had)\s+\w+\s+(?:say|mention|suggest|recommend|explain|tell|discuss|talk|show)\b", re.I)
_PLEA = re.compile(r"^(?:(?:can|could|would) you\s+(?:please\s+)?)?(?:help|any help|help me(?: out)?|any ideas|thoughts|advice|"
                   r"hi|hello|hey|thanks?|thank you|ok(?:ay)?|sorry)$", re.I)


def clauses(text: str) -> list[tuple[str, bool]]:
    """The text's clauses, each (clause, ends a question): a sentence's last clause before "?" is a question."""
    parts = _CUT.split(text)
    out = []
    for i in range(0, len(parts), 2):
        c = re.sub(r"\s+", " ", parts[i]).strip(" \t\"'`()[]-—")
        if c:
            out.append((c, i + 1 < len(parts) and "?" in parts[i + 1]))
    return out


def rule_route(text: str) -> str:
    """The rules' agent for a request (see above): "guide", "planner", "coach" or "scout"."""
    wants = None
    for c, ends_q in clauses(text):
        asks = ends_q or bool(_QUESTION.match(c))
        key = next((k for k, rx in RULES if rx.search(c)), None)
        if key and asks and _REPORTED.search(c):
            key = None  # "which issue did Ravi suggest ...?": a question about what someone said, not a request
        if key:
            wants = wants or key
        elif (asks or _INFO.match(c)) and not _PLEA.match(c):
            return "guide"  # a question about the project: answered, never proposed
    return wants or "guide"
PROPOSALS = {  # the rules' proposal per agent: what it will propose, never a claim that anything happened
    "planner": "The Planner can propose a KT session for this, with the right speaker and an agenda from the code; a manager approves it.",
    "coach": "The Coach can look at your program, tasks and recent activity and suggest your next step.",
    "scout": "The Scout can shortlist safe first tasks with the reasons for each; your manager picks one.",
}


def decide(deps, ev: dict, steps: list, on_step=None) -> dict:
    """{handled_by, why, proposal, agentic}. Appends its steps to `steps` (and streams them)."""
    user = deps.store.get_user(ev["user_id"])
    org = user.org_id
    text = ev.get("question") or ""
    ctx = rt.ToolCtx(deps=R.deps_for(deps, org, "guide"), org_id=org, project_id=ev.get("project_id"), user=user, agent="supervisor")
    agentic = True
    t0, before = time.monotonic(), len(steps)
    try:
        out = rt.run_agent(ctx, agent="supervisor", system=prompts.SUPERVISOR_FREE, tools=[], output=FreeRoute, max_steps=0, timeout_s=10,
                           task="Decide which agent handles the request in the data.\n\n"
                                + embed_data({"request": text, "asker_role": user.role}), on_step=on_step, trace=steps)
        pick = out.output
    except NoToolUse:
        pick, agentic = None, False
    if pick is None:
        key = rule_route(text)
        pick = FreeRoute(handled_by=key, why=f"Matched to the {R.AGENTS[key].name} by the request's wording.")
        agentic = False
    proposal = ""
    if pick.handled_by != "guide":
        facts = f"{text}\n{PROPOSALS[pick.handled_by]}"
        kept, _ = R.check_text(pick.proposal, facts, 400) if pick.proposal else (None, None)
        proposal = kept or PROPOSALS[pick.handled_by]
    name = R.AGENTS[pick.handled_by].name
    why = R.check_text(pick.why, text, 300)[0] or f"The {name} handles this kind of request."
    took = round((time.monotonic() - t0) * 1000) - sum(s.ms for s in steps[before:])
    rt.step(steps, "think", f"Handed to the {name}", shared=f"Handed to the {name}", ms=max(0, took), on_step=on_step)
    out = {"handled_by": pick.handled_by, "why": why, "proposal": proposal, "agentic": agentic}
    if pick.handled_by != "guide":
        out.update(dispatch(deps, pick.handled_by, ev, steps, on_step))
    return out


def dispatch(deps, key: str, ev: dict, steps: list, on_step=None) -> dict:
    """The specialist's own loop for ask.free (see the module docstring): {proposal: {text, actions}, details}, or {} when it
    cannot run or fails (the decision's proposal stands)."""
    from app.agents.specialists.coach import agent as coach
    from app.agents.specialists.planner import agent as planner
    from app.agents.specialists.scout import agent as scout

    fn = {"coach": coach.next_step, "planner": planner.kt_session, "scout": scout.suggest}.get(key)
    try:
        done = fn(deps, ev, steps, on_step) if fn else None
    except Exception:  # noqa: BLE001 - a proposal is a convenience: the decision's text stands, and the Guide is never the fallback here
        log.warning("ask.free specialist failed", exc_info=True)
        done = None
    if not done:
        return {}
    text, actions = done.pop("proposal"), done.pop("actions", [])
    return {"proposal": {"text": text, "actions": [a for a in actions if str(a.get("href", "")).startswith("#/")]}, "details": done}
