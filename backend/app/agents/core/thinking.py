"""What the thinking specialists share (the Scout, Planner, Coach, Keeper and Reviewer; docs/AGENTS.md, "The agentic model").

Each specialist runs one agent loop (app/agents/core/runtime.py) inside its node: it plans, calls read-only tools, observes and
decides, and its final answer is a schema (app/agents/core/schemas.py). Then code checks that answer (the guardrails: real ids and
paths, caps, stage order, privacy, the checked writer's rules for any text a person reads) and turns any proposal into an
approval through the existing code. Nothing a model returns acts on its own authority.

    t = think(deps, "scout", org_id=..., project_id=..., user=..., system=prompts.SCOUT_AGENT, task=..., tools=[...],
              output=ScoutShortlist, run_id=ev.get("run_id"))
    if t is None or t.output is None:      # no loop could run (the AI off, the offline demo, "rules" mode, the daily budget)
        ...the deterministic path...       # or it ended without an answer (timeout, error): the rules stand in
    else:
        ...validate t.output in code...
    return {..., "run_id": t.run_id, "steps": t.public()}, t.trace()   # the asker's trace, and the run record's

`think` returns None only when no loop ran (NoToolUse). A loop that ran but did not conclude returns a Thought with output None,
so its steps still reach the run record ("Thought for 4 steps, then the rules decided")."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from app.agents.core import registry as R
from app.agents.core import runtime as rt
from app.agents.core.llm import NoToolUse

log = logging.getLogger("agents.thinking")

RULES_LINE = "The model did not conclude: the rules decided."


@dataclass
class Thought:
    """One specialist's agent loop: its outcome, the context its tools filled (evidence, people, notes), and the run's id."""
    out: rt.AgentOutcome
    ctx: rt.ToolCtx
    run_id: str
    user_id: str | None
    on_step: object = None
    extra: list = field(default_factory=list)  # steps the caller's code added after the loop (a check, the rules' fallback)

    @property
    def output(self):
        return self.out.output

    @property
    def steps(self) -> list[rt.Step]:
        return self.out.steps

    def note(self, kind: str, text: str, shared: str = "", sources=()) -> None:
        """A step written by our code (a guardrail that fired, the fallback), streamed like the loop's."""
        rt.step(self.out.steps, kind, text, shared=shared or text, sources=sources, on_step=self.on_step)

    def public(self) -> list[dict]:
        """The asker's own trace (the response; the stream already showed it)."""
        return [s.public() for s in self.out.steps]

    def trace(self, private: bool = False) -> dict:
        """The run record's part (guarded() merges it): the shared, privacy-filtered steps, who it was for, and the run id."""
        return {**R.run_detail(self.out, user_id=self.user_id, private=private), "run_id": self.run_id}

    def detail(self, private: bool = False, **extra) -> dict:
        """trace() without the run id, for registry.record(..., run_id=t.run_id, **t.detail())."""
        return {k: v for k, v in {**self.trace(private), **extra}.items() if k != "run_id"}

    def record(self, deps, project_id: str | None, key: str, trigger: str, summary: str, trace_id: str | None = None, **extra) -> None:
        """A run record of its own (for a run no guarded() node records: one hire's assessment in the daily tick)."""
        R.record(deps, project_id, key, trigger, "succeeded", summary, trace_id, run_id=self.run_id, **self.detail(**extra))

    def facts(self, *extra: str) -> str:
        """Everything the model was shown (its task and every tool result), for the checked writer: a person reads the model's
        text only if each link, address and number in it is in here."""
        parts = list(extra)
        for m in self.out.messages:
            if m.get("role") == "user":
                parts.append(m.get("text", ""))
            elif m.get("role") == "tool":
                parts.extend(r.get("text", "") for r in m.get("results", []))
        return "\n".join(parts)

    def text(self, value: str, max_len: int, *extra: str, numbers: bool = True) -> tuple[str | None, str | None]:
        """(the model's text, None) when it may reach a person (registry.check_text over what it was shown), else (None, why)."""
        return R.check_text(value or "", self.facts(*extra), max_len, numbers)

    def sources(self, numbers) -> list[dict]:
        """The explain sources of the [n] the model cited, only those a tool gave it."""
        out = []
        for e in self.ctx.cited(numbers or []):
            s = e.source
            out.append(R.source(s.label, s.kind if s.kind in R.SOURCE_KINDS else "doc", **s.ref))
        return out

    def found(self) -> list[dict]:
        """The sources the loop's tools showed (for an explain block when the model cited none)."""
        return [R.source(s.label, s.kind if s.kind in R.SOURCE_KINDS else "doc", **s.ref) for s in self.out.sources[:8]]


def think(deps, key: str, *, org_id: str, project_id: str | None, user, system: str, task: str, tools: list, output,
          run_id: str | None = None, max_steps: int = 6, timeout_s: float = 25.0, smart: bool = False, clock=None,
          notes: dict | None = None, steps: list | None = None, on_step=None) -> Thought | None:
    """Run the agent `key`'s loop (see the module docstring). Its mode applies: in "rules" mode the model refuses (NoToolUse).
    `run_id` is the asker's (their stream, topic agent:<run_id>); without one the run gets a fresh id for its record. Steps
    stream only to a person who asked (a system run, such as the daily tick, streams nothing). `steps`: a trace to continue
    (ask.free's decision comes first)."""
    mdeps = R.deps_for(deps, org_id, key)
    ctx = rt.ToolCtx(deps=mdeps, org_id=org_id, project_id=project_id, user=user, agent=key, clock=clock)
    ctx.notes.update(notes or {})
    user_id = user.id if user is not None else None
    live = on_step if on_step is not None else rt.publisher(deps, run_id=run_id, org_id=org_id, user_id=user_id, project_id=project_id)
    try:
        out = rt.run_agent(ctx, agent=key, system=system, task=task, tools=list(tools), output=output, max_steps=max_steps,
                           timeout_s=timeout_s, on_step=live, smart=smart, trace=steps)
    except NoToolUse:
        return None
    t = Thought(out, ctx, run_id if run_id and _uuid(run_id) else str(uuid.uuid4()), user_id, live)
    if out.output is None:
        log.info("agent did not conclude", extra={"extra_fields": {"agent": key, "stopped": out.stopped, "error": out.error}})
        t.note("final", RULES_LINE)
    return t


def _uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def known_paths(deps, project_id: str) -> tuple[set[str], set[str]]:
    """(the indexed files, the indexed files and their folders): what a model's path must be one of."""
    files = set(deps.index.all_paths(project_id))
    folders = {p.rsplit("/", i)[0] for p in files for i in range(1, p.count("/") + 1)}
    return files, files | folders


def real_paths(paths, allowed: set[str], limit: int = 10) -> list[str]:
    """The model's paths that are real (normalised the way tools write them), deduplicated, in its order."""
    out = []
    for p in paths or []:
        p = str(p).strip().lstrip("./").rstrip("/")
        if p and p in allowed and p not in out:
            out.append(p)
    return out[:limit]
