"""The agent loop every specialist runs (docs/AGENTS.md, "The agentic model"): think -> act -> observe -> ... -> final.

    run_agent(ctx, agent=, system=, task=, tools=, output=, max_steps=6, timeout_s=25, on_step=None) -> AgentOutcome

It is a LangGraph subgraph of two nodes in a loop:
    think   one model turn (LLM.agent_turn) with the tools bound. The final answer comes through `final_answer`, whose
            parameters are the output schema, so it is validated before it is accepted; an answer that does not fit is
            sent back with the reason, and the model tries again.
    act     runs the turn's tool calls (read-only tools that re-check permissions themselves) and appends their results,
            untrusted data inside embed_data, for the next turn to observe.
When the step budget, the token budget or the time runs out, the next turn may only call final_answer: the agent concludes
with what it has (or says the evidence is not enough). Past the hard deadline it stops with no output.

Budgets: `max_steps` tool calls (default 6; the Guide 8), `timeout_s` of wall clock (a reserve is kept to conclude in),
MAX_RUN_TOKENS tokens, and per organisation DAILY_TURNS model turns a day (counted in this process).

A model that cannot run a loop (the offline demo model, the AI off, "rules" mode, an unscripted FakeLLM) makes run_agent
raise NoToolUse before any step: the caller runs its deterministic path.

Every step is a `Step` of the trace, handed to `on_step` as it happens (the API streams it to the asker, topic
`agent:<run_id>`). `shared_steps` is the privacy-filtered trace that run records store and anyone else may read: the
lines our code wrote and the sources found, never the model's words, the arguments it chose, untrusted text, or the sources
of a sensitive tool.

Side effects are never tools. An agent's output may hold a PROPOSAL; the Supervisor turns it into an approval or a capped
person-route through the existing code.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Generic, Literal, TypedDict, TypeVar

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.core.citations import label_for
from app.agents.core.llm import FINAL, FunctionDecl, NoToolUse, Turn, describe_error, embed_data, schema_of
from app.agents.core.schemas import Chunk

log = logging.getLogger("agents.runtime")

T = TypeVar("T", bound=BaseModel)
SourceKind = Literal["code", "kt", "doc", "issue", "event", "metric"]
StepKind = Literal["think", "tool", "verify", "final"]
Stopped = Literal["done", "budget", "timeout", "error"]

MAX_RUN_TOKENS = 120_000  # tokens one run may use (input and output, every turn); past it the agent concludes
DAILY_TURNS = 2000  # model turns per organisation per day (settings.agent_daily_turns overrides)
MAX_RESULT_CHARS = 12_000  # what one tool result may put in front of the model
CONCLUDE_RESERVE = 0.25  # share of the timeout kept for the concluding turn (at most 12 s), on top of the slowest turn so far

__all__ = ["AgentOutcome", "Evidence", "NoToolUse", "REGISTRY", "Source", "Step", "Tool", "ToolCtx", "ToolDenied",
           "ToolRegistry", "ToolResult", "run_agent", "shared_steps", "untrusted"]


# ---------------------------------------------------------------- what tools return, and the trace
class Source(BaseModel):
    label: str
    kind: SourceKind
    ref: dict = Field(default_factory=dict)


class ToolResult(BaseModel):
    text: str  # what the model sees: already wrapped as untrusted data (untrusted())
    sources: list[Source] = Field(default_factory=list)
    summary: str  # one line for the asker's trace ("Read solve.py L32-102")
    shared: str = ""  # the line others see: written by our code, no argument the model chose, no untrusted text


class Step(BaseModel):
    """One entry of a run's trace (the contract's shape: model_dump() leaves the private fields out)."""
    n: int
    kind: StepKind
    text: str
    tool: str | None = None
    args: dict | None = None
    sources: list[Source] = Field(default_factory=list)
    ms: int = 0
    shared: str = Field("", exclude=True)  # the trace line anyone else reads ("" -> a generic line)
    sensitive: bool = Field(False, exclude=True)  # its sources stay out of the shared trace

    def public(self) -> dict:
        return self.model_dump(mode="json")


GENERIC = {"think": "Planned the next step", "tool": "Used a tool", "verify": "Checked the evidence", "final": "Finished"}


def shared_steps(steps: list[Step]) -> list[dict]:
    """The trace as anyone but the run's own user may read it, and as run records store it."""
    return [{"n": s.n, "kind": s.kind, "text": s.shared or GENERIC[s.kind], "tool": s.tool, "args": None,
             "sources": [] if s.sensitive else [x.model_dump(mode="json") for x in s.sources[:8]], "ms": s.ms} for s in steps]


def untrusted(payload: dict) -> str:
    """A tool's result for the model: data inside the data block, never instructions (prompts.UNTRUSTED says so)."""
    return embed_data(payload)


# ---------------------------------------------------------------- the context a tool runs in
class ToolDenied(RuntimeError):
    """The acting user may not read this (not a member of the project, another organisation, deactivated)."""


@dataclass
class Evidence:
    """A source a tool put in front of the model, numbered so the model can cite it ([n])."""
    n: int
    chunk: Chunk
    source: Source
    similarity: float = 0.0  # to the query that found it (0 for a file read or a lookup)


SOURCE_KIND = {"code": "code", "meeting": "kt", "doc": "doc", "verified_answer": "doc"}
REF_KEYS = ("path", "start_line", "end_line", "document_id", "page", "meeting_id", "start_s", "verified_answer_id", "request_id")


@dataclass
class ToolCtx:
    """Who an agent acts for and where. Tools re-check it (check()) and read only this organisation's project: the project
    comes from here, never from the model."""
    deps: Any  # app.agents.core.deps.Deps
    org_id: str
    project_id: str | None
    user: Any = None  # app.ports.User; None: the system (an event, the daily tick)
    agent: str = ""
    clock: Any = None  # app.lifecycle.clock.Clock, for the lifecycle reads
    evidence: list[Evidence] = field(default_factory=list)
    people: dict[str, str] = field(default_factory=dict)  # user id -> why a tool named them (who_knows, code_owners, kt_moments)
    notes: dict = field(default_factory=dict)  # tools' scratch for their agent (e.g. the related answers they showed)
    _checked: bool = False

    def check(self) -> None:
        """Raise ToolDenied unless the project is this organisation's and the acting user an active member of it (or an
        admin of the organisation)."""
        if self._checked:
            return
        store = self.deps.store
        if self.project_id:
            try:
                project = store.get_project(self.project_id)
            except Exception as e:  # noqa: BLE001 - an unknown project reads as nothing
                raise ToolDenied("unknown project") from e
            if project is None or str(project.org_id) != str(self.org_id):
                raise ToolDenied("the project is not this organisation's")
        if self.user is not None:
            u = store.get_user(self.user.id) or self.user
            if str(u.org_id) != str(self.org_id) or getattr(u, "status", "active") != "active":
                raise ToolDenied("the acting user is not an active member of this organisation")
            is_member = getattr(store, "is_member", None)
            if self.project_id and u.role != "admin" and is_member is not None and not is_member(self.project_id, u.id):
                raise ToolDenied("the acting user is not a member of this project")
        self._checked = True

    def lc(self):
        """A lifecycle Ctx for the services in app/lifecycle (routing, hires, ...)."""
        from app.agents.core.registry import lifecycle_ctx

        return lifecycle_ctx(self.deps, self.org_id, self.user, self.clock)

    def cite(self, chunk: Chunk, similarity: float = 0.0, label: str | None = None) -> int:
        """Number a source for the model (the same chunk keeps its number)."""
        for e in self.evidence:
            if e.chunk.id == chunk.id:
                e.similarity = max(e.similarity, similarity)
                return e.n
        ref = {k: chunk.ref[k] for k in REF_KEYS if k in chunk.ref}
        src = Source(label=(label or label_for(chunk))[:200], kind=SOURCE_KIND.get(chunk.source_type, "doc"), ref={"chunk_id": chunk.id, **ref})
        self.evidence.append(Evidence(len(self.evidence) + 1, chunk, src, similarity))
        return len(self.evidence)

    def cited(self, numbers) -> list[Evidence]:
        """The evidence these numbers name, in order, without duplicates or numbers no tool gave."""
        by_n = {e.n: e for e in self.evidence}
        return [by_n[n] for n in dict.fromkeys(numbers) if n in by_n]


# ---------------------------------------------------------------- tools and their registry
class Tool(BaseModel):
    """A read-only capability an agent may call. Declared once and registered (REGISTRY.register or @REGISTRY.tool)."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str  # snake_case, e.g. "search_knowledge"
    description: str  # for the model
    params: type[BaseModel]  # the arguments (their JSON schema is the function declaration)
    run: Callable[[ToolCtx, Any], ToolResult]
    sensitive: bool = False  # its result and sources never reach a trace that others read
    label: str = ""  # the shared trace's line when a result gives none ("Searched the knowledge base")

    def decl(self) -> FunctionDecl:
        return FunctionDecl(name=self.name, description=self.description, parameters=schema_of(self.params))


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", tool.name) or tool.name == FINAL:
            raise ValueError(f"bad tool name {tool.name!r}")
        if tool.name in self._tools and self._tools[tool.name].run is not tool.run:
            raise ValueError(f"a tool named {tool.name} is registered already")
        self._tools[tool.name] = tool
        return tool

    def tool(self, name: str, description: str, params: type[BaseModel], *, sensitive: bool = False, label: str = ""):
        """Decorator: @REGISTRY.tool("search_knowledge", "...", SearchArgs) def search(ctx, args) -> ToolResult."""
        def deco(fn):
            self.register(Tool(name=name, description=description, params=params, run=fn, sensitive=sensitive, label=label))
            return fn
        return deco

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def select(self, *names: str) -> list[Tool]:
        return [self._tools[n] for n in names]

    def names(self) -> list[str]:
        return sorted(self._tools)


REGISTRY = ToolRegistry()  # the shared tools register here when app.agents.tools is imported


# ---------------------------------------------------------------- the outcome
@dataclass
class AgentOutcome(Generic[T]):
    output: T | None
    steps: list[Step]
    sources: list[Source]
    stopped: Stopped
    turns: int = 0
    tool_calls: int = 0
    tokens: int = 0
    ms: int = 0
    error: str | None = None  # what went wrong (describe_error: never the model's text)
    messages: list[dict] = field(default_factory=list, repr=False)  # the conversation, for a follow-up turn (resume=)
    started: float = 0.0  # time.monotonic() when the first run began

    def elapsed(self) -> float:
        return time.monotonic() - self.started if self.started else 0.0


# ---------------------------------------------------------------- the per-organisation daily budget
_turns_lock = threading.Lock()
_turns: dict[tuple[str, date], int] = {}


def _spend_turn(org_id: str, limit: int) -> bool:
    """Count one model turn for the organisation today; False when its daily budget is used up."""
    key = (str(org_id or ""), date.today())
    with _turns_lock:
        for k in [k for k in _turns if k[1] != key[1]]:
            del _turns[k]
        if _turns.get(key, 0) >= limit:
            return False
        _turns[key] = _turns.get(key, 0) + 1
        return True


def _refund_turn(org_id: str) -> None:
    """A turn the model refused to run (NoToolUse) costs nothing."""
    key = (str(org_id or ""), date.today())
    with _turns_lock:
        if _turns.get(key):
            _turns[key] -= 1


def spend_model_call(deps, org_id: str | None) -> bool:
    """Count one model call made outside an agent loop (the research crew's planner and judge, app/rag/crew.py) against the
    organisation's daily budget of model turns, the one every agent loop spends. False when it is used up: the caller makes
    no call and goes on without it."""
    limit = int(getattr(getattr(deps, "settings", None), "agent_daily_turns", 0) or DAILY_TURNS)
    return _spend_turn(str(org_id or ""), limit)


def turns_today(org_id: str) -> int:
    return _turns.get((str(org_id or ""), date.today()), 0)


# ---------------------------------------------------------------- the loop
class LoopState(TypedDict, total=False):
    messages: list
    steps: list
    calls: int  # tool calls run
    turns: int
    tokens: int
    pending: list  # the last turn's calls, for act
    final: Any
    forced: bool  # the last turn could only conclude
    stopped: str | None
    error: str | None
    final_error: str | None  # why the last final_answer was not accepted
    retries: int  # turns that answered with neither a call nor a valid final answer


def _one_line(text: str, limit: int = 160) -> str:
    """A model's thought summary as one line for the trace: its first heading (**Planning the search**), else its first
    sentence, markdown marks removed."""
    head = re.search(r"\*\*([^*\n]{3,120})\*\*", text or "")
    if head:
        return head.group(1).strip(" :.")
    plain = re.sub(r"[*#`_]+", "", text or "").strip()
    line = re.split(r"(?<=[.!?])\s|\n", plain, maxsplit=1)[0].strip()
    return line if len(line) <= limit else line[: limit - 1].rsplit(" ", 1)[0] + "…"


def _trim(value, depth: int = 0):
    """The arguments a model chose, shortened for the asker's trace."""
    if isinstance(value, str):
        return value[:200]
    if isinstance(value, list):
        return [_trim(v, depth + 1) for v in value[:10]]
    if isinstance(value, dict) and depth < 2:
        return {str(k)[:40]: _trim(v, depth + 1) for k, v in list(value.items())[:12]}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


def run_agent(ctx: ToolCtx, *, agent: str, system: str, task: str, tools: list[Tool], output: type[T], max_steps: int = 6,
              timeout_s: float = 25.0, on_step: Callable[[Step], None] | None = None, smart: bool = False,
              resume: AgentOutcome | None = None, max_tokens: int = MAX_RUN_TOKENS, trace: list[Step] | None = None) -> AgentOutcome[T]:
    """Run one agent loop (see the module docstring). `task` is the first user message (put untrusted text in it only inside
    embed_data). `resume` continues an earlier outcome's conversation with `task` as the next message (e.g. one repair turn
    with max_steps=0). `trace`: the caller's own list of steps, which this run appends to (so steps the caller added before,
    such as a first search, keep their numbers). Raises NoToolUse when the model cannot run a loop; every other failure ends
    as `stopped`."""
    deps = ctx.deps
    llm = deps.llm
    max_steps = max(0, int(max_steps))  # 0: a follow-up turn that may only conclude
    if getattr(llm, "agent_turn", None) is None:
        raise NoToolUse("this model cannot run an agent loop")
    limit = int(getattr(deps.settings, "agent_daily_turns", 0) or DAILY_TURNS)
    by_name = {t.name: t for t in tools}
    decls = [t.decl() for t in tools]
    began = time.monotonic()
    hard = began + timeout_s
    conclude_at = hard - min(12.0, timeout_s * CONCLUDE_RESERVE)  # one slow turn (10 s+) must still fit to conclude
    first_turn = resume is None
    base_steps = trace if trace is not None else list(resume.steps) if resume else []
    base = {"turns": resume.turns if resume else 0, "calls": resume.tool_calls if resume else 0, "tokens": resume.tokens if resume else 0}
    started = resume.started if resume and resume.started else began

    def emit(steps: list, step: Step) -> None:
        steps.append(step)
        if on_step:
            try:
                on_step(step)
            except Exception:  # noqa: BLE001 - streaming a step must never fail the run
                log.warning("publishing an agent step failed", exc_info=True)

    def next_n(steps: list) -> int:
        return len(steps) + 1

    turn_s: list[float] = []  # how long the turns took: a turn is not started when the next one would not fit

    def think(state: LoopState) -> dict:
        nonlocal first_turn
        now = time.monotonic()
        if now >= hard - 1.0:
            return {"stopped": "timeout", "pending": []}
        calls, tokens = state["calls"] - base["calls"], state["tokens"] - base["tokens"]
        expect = max(turn_s) if turn_s else 0.0
        # why this turn may only conclude: the time ("timeout"), the step or token budget ("budget"), or a follow-up turn
        reason = ("timeout" if now + expect >= conclude_at else
                  "budget" if (max_steps and calls >= max_steps) or tokens >= max_tokens else None)
        forced = reason is not None or max_steps == 0
        if not _spend_turn(ctx.org_id, limit):
            if first_turn:
                raise NoToolUse("the organisation's daily agent budget is used up")
            return {"stopped": "budget", "pending": [], "error": "the daily agent budget is used up"}
        t0 = time.monotonic()
        try:
            turn: Turn = llm.agent_turn(system, state["messages"], [] if forced else decls, output, agent=agent, smart=smart,
                                        deadline=hard, final_only=forced)
        except NoToolUse:
            _refund_turn(ctx.org_id)
            if first_turn:
                raise
            return {"stopped": "error", "pending": [], "error": "the model stopped running tools"}
        except Exception as e:  # noqa: BLE001 - LLMError after the fallback, or anything else: the run ends, the caller decides
            log.warning("agent turn failed", extra={"extra_fields": {"agent": agent, "error": describe_error(e)}})
            late = time.monotonic() >= hard - 1.0 or "out of time" in str(e)
            return {"stopped": "timeout" if late else "error", "pending": [], "error": describe_error(e)}
        first_turn = False
        turn_s.append(time.monotonic() - t0)
        steps, messages = state["steps"], state["messages"]
        if turn.thought:
            emit(steps, Step(n=next_n(steps), kind="think", text=_one_line(turn.thought), ms=round(turn_s[-1] * 1000)))
        calls_made = [c.model_dump() for c in turn.tool_calls]
        if turn.final is not None:
            calls_made.append({"id": "", "name": FINAL, "args": turn.final.model_dump(mode="json")})
        messages.append({"role": "model", "thought": turn.thought, "calls": calls_made, "raw": turn.raw})
        up = {"turns": state["turns"] + 1, "tokens": state["tokens"] + turn.tokens, "forced": forced, "messages": messages}
        if turn.final is not None:
            return {**up, "final": turn.final, "pending": [], "stopped": reason or "done"}
        if turn.tool_calls:
            if forced:  # it could only conclude, and did not: the run ends without an output
                return {**up, "pending": [], "stopped": reason or "error", "error": turn.final_error or "it did not conclude"}
            return {**up, "pending": turn.tool_calls, "final_error": turn.final_error}
        retries = state.get("retries", 0) + 1
        if retries > 1:
            return {**up, "pending": [], "stopped": "error", "error": "the model answered without calling a function"}
        messages.append({"role": "user", "text": "Call one of the tools, or final_answer with your answer."})
        return {**up, "pending": [], "retries": retries}

    def act(state: LoopState) -> dict:
        steps, messages = state["steps"], state["messages"]
        calls = state["calls"]
        results = []
        for call in state["pending"]:
            t0 = time.monotonic()
            if call.name == FINAL:
                why = state.get("final_error") or "its arguments did not fit the schema"
                results.append({"id": call.id, "name": call.name,
                                "text": f"Not accepted: {why}. Call final_answer again with arguments that fit its schema."})
                continue
            if calls - base["calls"] >= max_steps:
                results.append({"id": call.id, "name": call.name, "text": "Not run: the step budget is used up. Call final_answer now."})
                continue
            if time.monotonic() >= conclude_at:
                results.append({"id": call.id, "name": call.name, "text": "Not run: out of time. Call final_answer now."})
                continue
            tool = by_name.get(call.name)
            if tool is None:
                results.append({"id": call.id, "name": call.name, "text": f"There is no tool named {call.name[:60]!r}."})
                emit(steps, Step(n=next_n(steps), kind="tool", text="Asked for a step that does not exist", shared="Asked for a step that does not exist",
                                 ms=round((time.monotonic() - t0) * 1000)))
                continue
            calls += 1
            try:
                args = tool.params.model_validate(call.args or {})
            except ValidationError as e:
                results.append({"id": call.id, "name": call.name, "text": f"Invalid arguments: {describe_error(e)}. Fix them and try again."})
                emit(steps, Step(n=next_n(steps), kind="tool", text=f"{tool.label or tool.name} · asked wrongly, trying again",
                                 tool=tool.name, shared=f"{tool.label or tool.name} · asked wrongly", sensitive=tool.sensitive,
                                 ms=round((time.monotonic() - t0) * 1000)))
                continue
            try:
                ctx.check()
                res = tool.run(ctx, args)
            except ToolDenied:
                res = ToolResult(text=untrusted({"error": "Not allowed for this user."}), summary=f"{tool.label or tool.name} · not allowed",
                                 shared=f"{tool.label or tool.name} · not allowed")
            except Exception as e:  # noqa: BLE001 - a broken tool is reported to the model, not raised
                log.warning("agent tool failed", extra={"extra_fields": {"agent": agent, "tool": tool.name, "error": describe_error(e)}})
                res = ToolResult(text=untrusted({"error": "The tool failed. Try another tool or other arguments."}),
                                 summary=f"{tool.label or tool.name} · failed", shared=f"{tool.label or tool.name} · failed")
            text = res.text if len(res.text) <= MAX_RESULT_CHARS else res.text[:MAX_RESULT_CHARS] + "\n(cut)"
            results.append({"id": call.id, "name": call.name, "text": text})
            emit(steps, Step(n=next_n(steps), kind="tool", text=res.summary[:240], tool=tool.name, args=_trim(dict(call.args or {})),
                             sources=res.sources[:8], ms=round((time.monotonic() - t0) * 1000),
                             shared=(res.shared or tool.label or f"Used {tool.name}")[:240], sensitive=tool.sensitive))
        messages.append({"role": "tool", "results": results})
        return {"messages": messages, "calls": calls, "pending": []}

    def after_think(state: LoopState) -> str:
        if state.get("stopped"):
            return "stop"
        return "act" if state.get("pending") else "think"

    g = StateGraph(LoopState)
    g.add_node("think", think)
    g.add_node("act", act)
    g.add_edge(START, "think")
    g.add_conditional_edges("think", after_think, {"act": "act", "think": "think", "stop": END})
    g.add_edge("act", "think")
    loop = g.compile(checkpointer=False)

    messages = list(resume.messages) if resume else []
    messages.append({"role": "user", "text": task})
    init: LoopState = {"messages": messages, "steps": base_steps, "calls": base["calls"], "turns": base["turns"],
                       "tokens": base["tokens"], "pending": [], "final": None, "stopped": None, "error": None, "retries": 0}
    try:
        out = loop.invoke(init, {"recursion_limit": 4 * (max_steps + 4) + 10})
    except NoToolUse:
        raise
    except Exception as e:  # noqa: BLE001 - the graph itself failed (recursion limit): the run ends
        log.warning("agent loop failed", extra={"extra_fields": {"agent": agent, "error": describe_error(e)}})
        out = {**init, "stopped": "error", "error": describe_error(e)}
    steps = out.get("steps", base_steps)
    sources, seen = [], set()
    for s in steps:
        for src in s.sources:
            key = (src.label, src.kind)
            if key not in seen:
                seen.add(key)
                sources.append(src)
    return AgentOutcome(output=out.get("final"), steps=steps, sources=sources, stopped=out.get("stopped") or "error",
                        turns=out.get("turns", 0), tool_calls=out.get("calls", 0), tokens=out.get("tokens", 0),
                        ms=round((time.monotonic() - started) * 1000), error=out.get("error"), messages=out.get("messages", messages),
                        started=started)


def step(steps: list[Step], kind: StepKind, text: str, *, shared: str = "", sources=(), ms: int = 0, tool: str | None = None,
         on_step: Callable[[Step], None] | None = None) -> Step:
    """Add a step written by the caller's own code (a verify step, the final line, a deterministic path's search) and stream it."""
    s = Step(n=len(steps) + 1, kind=kind, text=text[:240], tool=tool, sources=list(sources)[:8], ms=ms, shared=(shared or text)[:240])
    steps.append(s)
    if on_step:
        try:
            on_step(s)
        except Exception:  # noqa: BLE001
            log.warning("publishing an agent step failed", exc_info=True)
    return s


def publisher(deps, *, run_id: str | None, org_id: str, user_id: str | None, project_id: str | None = None):
    """on_step for a run someone is watching: each step goes to the live hub (Deps.live) on topic `agent:<run_id>`, for that
    user's streams only (app/live.py). None when nothing streams (no hub, no run id, a system run)."""
    hub = getattr(deps, "live", None)
    if hub is None or not run_id or not user_id:
        return None
    topic = f"agent:{run_id}"

    def publish(s: Step) -> None:
        hub.publish({"o": str(org_id), "t": topic, "p": project_id, "u": str(user_id), "step": s.public()})

    return publish


def chain(*fns):
    """One on_step from several (None skipped)."""
    live = [f for f in fns if f]
    if not live:
        return None

    def each(s: Step) -> None:
        for f in live:
            f(s)

    return each
