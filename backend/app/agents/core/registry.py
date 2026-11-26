"""The agent roster (GET /api/agents), each agent's mode per organisation, and what every agent shares: the `explain` block
returned with its output, the checked way it lets a model write text, and its run records.

Nine agents, one Supervisor. Rules decide (ranking, routing, detection: app/lifecycle and the core agents); a model only
writes words (nudges, briefs, drafts, summaries, reasons), through `write()`, which sends the rules' own draft with the
facts and keeps the model's text only if it passes the checks in code. With the AI off, or the agent's mode set to
"rules" (org_settings.agent_modes), the draft is the text and no model is called. docs/AGENTS.md describes the model.

Run records (agent_runs) keep the names the agents always had (ingestion, meeting, qa, task, task_status, review);
RUN_NAMES maps them to the roster. A run's `summary` is a short line written here or by the agent, never from untrusted
text: no question a new hire asked, no finding of a private review."""
from __future__ import annotations

import dataclasses
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from pydantic import BaseModel, ValidationError

from app.agents.core.llm import LLMError, NoLLM, NoToolUse, embed_data
from app.agents.core.schemas import AgentText, AgentTexts
from app.ports import AIUnavailable

log = logging.getLogger("agents")

MODES = ("ai", "rules")


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    role: str
    triggers: tuple[str, ...]
    tools: tuple[str, ...]
    approvals: tuple[str, ...] = ()
    switchable: bool = True  # the Supervisor and the Librarian write no text: their mode cannot change
    writes: bool = True  # whether a model may write any of its text (ai_available)


AGENTS: dict[str, Agent] = {a.key: a for a in (
    Agent("supervisor", "Supervisor", "Routes every event to one agent and pauses for people",
          ("all",), ("checkpointer", "store.approvals", "store.notify", "store.record_run"), switchable=False, writes=False),
    Agent("librarian", "Librarian", "Reads repositories and documents and keeps the index current",
          ("repo.connected", "repo.pushed", "document.uploaded"),
          ("repos.files", "repos.file", "blobs.get", "index.upsert", "index.delete_paths", "store.mark_kt_code_changed"),
          switchable=False, writes=False),
    Agent("note_taker", "Note-taker", "Turns a recorded KT session into notes linked to code",
          ("meeting.recording_ready",), ("llm.structured (video)", "index.all_paths", "store.save_meeting_result", "store.add_kt_links"),
          ("kt_followup",)),
    Agent("guide", "Guide", "Answers with sources; sends the rest to the right person",
          ("chat.question", "ask.route"),
          ("index.search_grid", "index.symbols_named", "index.search", "llm.structured", "store.codeowners", "store.kt_links",
           "store.create_expert_request", "store.notify", "lifecycle.verified_answers")),
    Agent("planner", "Planner", "Drafts the 30/60/90 plan, the completion review and template improvements",
          ("onboarding.started", "program.completion_due", "program.completed"),
          ("program", "kt_coverage", "search_knowledge", "read_file", "issues", "tests_for", "recent_changes", "code_owners",
           "cohort_evidence (surveys: groups of 5 or more)", "who_knows", "kt_moments"),
          ("task_plan", "template_change")),
    Agent("scout", "Scout", "Finds a safe first task and writes its brief",
          ("first_task.requested",),
          ("first_task_candidates", "issues", "read_file", "tests_for", "kt_moments", "kt_coverage", "recent_changes", "code_owners",
           "program", "search_knowledge")),
    Agent("reviewer", "Reviewer", "Reviews pull requests, and branches privately for the hire",
          ("pr.opened", "pr.updated", "pr.merged", "presubmit.requested"),
          ("pr_diff", "read_file", "find_symbol", "tests_for", "kt_moments", "search_knowledge", "verified_answers"), ("review",)),
    Agent("coach", "Coach", "Spots blockers early, nudges kindly and writes the weekly summary",
          ("lifecycle.tick", "week.closed"),
          ("hire_signals (sensitive)", "program", "kt_moments", "search_knowledge", "kt_coverage")),
    Agent("keeper", "Keeper", "Keeps explanations current: re-recordings, doc fixes, handovers",
          ("repo.pushed", "feedback.created", "handover.requested"),
          ("kt_changes", "feedback_target", "person_footprint", "read_file", "find_symbol", "kt_moments", "search_knowledge",
           "verified_answers", "kt_coverage", "who_knows", "code_owners"),
          ("rerecord", "doc_edit")),
)}

# An agent's crew: the members that do its work, in the order a request meets them (GET /api/agents lists them). The Guide's
# research crew is docs/specs/rag-crew.md: everything between a question and its checked answer.
CREWS: dict[str, tuple[dict[str, str], ...]] = {
    "guide": (
        {"key": "search_planner", "name": "Search planner",
         "role": "Reads the question and plans up to five searches in the project's own words; a model adds a few more when one is set up"},
        {"key": "retrievers", "name": "Retrievers",
         "role": "Search code, documents, KT calls and verified answers in one pass, and look up every file and function the question names"},
        {"key": "fusion", "name": "Fusion", "role": "Merges every search's results into one list, the strongest agreement first"},
        {"key": "judge", "name": "Judge",
         "role": "Ranks the evidence by the names, words and meaning it shares with the question; a model grades each source when one is set up"},
        {"key": "writer", "name": "Writer",
         "role": "Answers from the evidence with a source for every sentence; without a model it quotes the sources"},
        {"key": "critic", "name": "Critic",
         "role": "Checks every sentence against its source and the evidence against the question; when something fails, the asker is offered a person"},
    ),
}

# agent_runs.agent as the core agents always wrote it -> the roster key
RUN_NAMES = {"ingestion": "librarian", "meeting": "note_taker", "qa": "guide", "task": "planner", "review": "reviewer",
             "task_status": "reviewer"}

# The Supervisor graph's nodes that are not specialists: routing, escalation and the pauses for a person (app/agents/supervisor/graph.py)
SUPERVISOR_NODES = frozenset({"route", "orchestrate", "escalate", "planner_approval", "reviewer_approval", "approval"})
PAUSES = frozenset({"planner_approval", "reviewer_approval", "approval"})


def graph_edges(compiled) -> list[dict]:
    """The diagram's edges, read from the compiled Supervisor graph itself (its conditional edges and their labels):
    {from, to, via} between agents, the Supervisor standing for its routing and pause nodes. Every route the Supervisor
    takes to a specialist (via the event), every chain from one specialist to the next (via the event it hands on), and every
    pause for a person (via "approval: <kind>"). Error and end edges are left out."""
    def agent(node: str) -> str | None:
        return "supervisor" if node in SUPERVISOR_NODES else node if node in AGENTS else None

    out = []
    for source, specs in compiled.builder.branches.items():
        for spec in specs.values():
            for label, target in (spec.ends or {}).items():
                a, b = agent(source), agent(target)
                if not a or not b or label in {"error", "done"} or a == b == "supervisor":
                    continue
                out.append({"from": a, "to": b, "via": f"approval: {label}" if target in PAUSES else str(label)})
    return out

# A run's summary when the agent left none: what kind of thing ran, never what anyone wrote.
DEFAULT_SUMMARIES = {
    ("librarian", "repo.connected"): "Indexed the repository",
    ("librarian", "repo.pushed"): "Re-indexed the files a push changed",
    ("librarian", "document.uploaded"): "Indexed an uploaded document",
    ("note_taker", "meeting.recording_ready"): "Wrote the notes of a recorded KT session",
    ("guide", "chat.question"): "Answered a question in chat, or passed it to a person",
    ("planner", "onboarding.started"): "Drafted a 30/60/90 plan for approval",
    ("planner", "meeting.recording_ready"): "Proposed tasks from a KT session",
    ("reviewer", "pr.opened"): "Matched a pull request to its task",
    ("reviewer", "pr.updated"): "Matched a pull request update to its task",
    ("reviewer", "pr.merged"): "Marked a merged pull request's task done",
}


class RulesMode(AIUnavailable):
    """The agent's mode is "rules" in this organisation: it calls no model."""


class RulesLLM:
    """What an agent in "rules" mode gets for its LLM: every call is refused, so each agent's AI-off path runs."""

    def __init__(self, key: str):
        self.key = key

    def structured(self, prompt, schema, *, smart=False, media=None, system=None):
        raise RulesMode(f"The {AGENTS[self.key].name} is set to rules in Agents: it writes no AI text.")

    def agent_turn(self, system, messages, tools, output_schema, **_):
        """No agent loop in rules mode: the agent's deterministic path runs (app/agents/core/runtime.py)."""
        raise NoToolUse(f"The {AGENTS[self.key].name} is set to rules in Agents: it runs no agent loop.")


# ---------------------------------------------------------------- modes
def agent_key(run_name: str) -> str:
    return RUN_NAMES.get(run_name, run_name)


def org_of(deps, project_id: str | None) -> str | None:
    if not project_id:
        return None
    try:
        return deps.store.get_project(project_id).org_id
    except Exception:  # noqa: BLE001 - an unknown project: no organisation, so the default mode
        return None


def modes(deps, org_id: str | None) -> dict:
    lc = getattr(deps, "lifecycle", None)
    if lc is None or not org_id:
        return {}
    try:
        return dict(lc.org_settings(org_id).agent_modes or {})
    except Exception:  # noqa: BLE001 - settings unreadable: every agent keeps its default
        log.warning("agent modes unreadable", exc_info=True)
        return {}


def mode(deps, org_id: str | None, key: str) -> str:
    if not AGENTS[key].switchable:
        return "rules"
    m = modes(deps, org_id).get(key, "ai")
    return m if m in MODES else "ai"


def ai_available(deps, key: str) -> bool:
    """A model can write this agent's text on this server (whatever its mode)."""
    return AGENTS[key].writes and not isinstance(deps.llm, NoLLM)


def deps_for(deps, org_id: str | None, key: str):
    """The agent's tools for one run: in "rules" mode its LLM refuses every call (RulesMode)."""
    if mode(deps, org_id, key) == "rules":
        return dataclasses.replace(deps, llm=RulesLLM(key))
    return deps


def lifecycle_ctx(deps, org_id: str, user=None, clock=None):
    """A lifecycle Ctx (app/lifecycle/context.py) from an agent's Deps: the services read the stores through it."""
    from app.lifecycle.clock import for_org
    from app.lifecycle.context import Ctx

    ns = SimpleNamespace(lifecycle=deps.lifecycle, store=deps.store, index=deps.index, deps=deps, settings=deps.settings)
    return Ctx(ns, clock or for_org(ns, org_id), user)


def clock_for(day: date | str | None):
    """The lifecycle clock as of `day` (a tick for a day the demo clock crossed), or now."""
    from app.lifecycle.clock import Clock

    if not day:
        return None
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return Clock((d - date.today()).days)


def programs(deps, org_id: str, project_id: str | None, day=None) -> list:
    """The organisation's active programs (one project's, when given), as of `day`: the hires the Coach checks on the daily
    tick and the Planner reviews for completion."""
    from app.lifecycle import hires

    ctx = lifecycle_ctx(deps, org_id, None, clock_for(day))
    pids = sorted({p.project_id for p in deps.lifecycle.programs(org_id, project_id)})
    return hires.of_projects(ctx, pids)


# ---------------------------------------------------------------- explain
SOURCE_KINDS = frozenset({"code", "kt", "doc", "event", "metric"})


def source(label: str, kind: str, **ref) -> dict:
    assert kind in SOURCE_KINDS, kind
    return {"label": str(label)[:200], "kind": kind, "ref": ref}


def explain(key: str, logic, sources=(), confidence: float | None = None, ai: bool = False, trace_id: str | None = None) -> dict:
    """Every output a person sees carries this: which agent, the rule lines that fired (the same text as `why`), the sources,
    a confidence where one exists, whether a model wrote any of the text, and the run's trace when tracing is on."""
    return {"agent": key, "agent_name": AGENTS[key].name, "logic": [str(x) for x in logic], "sources": list(sources),
            "confidence": None if confidence is None else round(float(confidence), 2), "ai": bool(ai), "trace_id": trace_id}


def citation_source(c) -> dict:
    """A QA citation (or KT clip) as an explain source."""
    d = c if isinstance(c, dict) else c.model_dump()
    if "meeting_id" in d and "file_path" in d:  # a KT clip
        return source(f"{d.get('meeting_title') or 'KT call'}: {d['file_path']}", "kt", meeting_id=d["meeting_id"], path=d["file_path"],
                      start_s=d.get("start_s"))
    kind = {"code": "code", "meeting": "kt", "doc": "doc", "verified_answer": "doc"}.get(d.get("source_type"), "doc")
    ref = {k: v for k, v in (d.get("ref") or {}).items() if k in {"path", "start_line", "end_line", "document_id", "page", "meeting_id",
                                                                     "start_s", "verified_answer_id", "request_id"}}
    return source(d.get("label") or "source", kind, chunk_id=d.get("chunk_id"), **ref)


# ---------------------------------------------------------------- writing, checked in code
_URL = re.compile(r"https?://[^\s)>\]]+", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def check_text(text: str, facts: str, max_len: int, numbers: bool = True) -> tuple[str | None, str | None]:
    """(text, None) if the model's text may be kept, else (None, why not). A model's words reach a person only when they add
    no link, address or number the facts do not have, carry no data delimiter, and fit the length."""
    t = (text or "").strip()
    if not t:
        return None, "it was empty"
    if len(t) > max_len:
        return None, f"it was longer than {max_len} characters"
    if "<<" in t or ">>" in t:
        return None, "it contained a data delimiter"
    for u in _URL.findall(t):
        if u not in facts:
            return None, "it contained a link the sources do not have"
    for e in _EMAIL.findall(t):
        if e not in facts:
            return None, "it contained an address the sources do not have"
    if numbers:
        for n in _NUMBER.findall(t):
            if n not in facts:
                return None, f"it contained a number ({n}) the facts do not have"
    return t, None


def write(deps, key: str, org_id: str | None, *, system: str, data: dict, draft: str, max_len: int,
          numbers: bool = True) -> tuple[str, bool, str | None]:
    """(text, ai, discarded): the model rewrites the rules' `draft` from `data` (untrusted text inside the data block only), and
    its text is kept if check_text passes. Rules mode, the AI off, a model error or a failed check: the draft, ai=False, and
    why the model's text was not used (None when no model was asked)."""
    if mode(deps, org_id, key) == "rules":
        return draft, False, None
    payload = {**data, "draft": draft}
    try:
        out = deps.llm.structured(embed_data(payload), AgentText, system=system)
    except (AIUnavailable, LLMError, ValidationError):
        return draft, False, None
    except Exception:  # noqa: BLE001 - the words are a nicety: the rules' text always stands
        log.warning("agent writing failed", exc_info=True)
        return draft, False, None
    text, why_not = check_text(out.text, json.dumps(payload, ensure_ascii=False, default=str), max_len, numbers)
    if text is None:
        return draft, False, why_not
    return text, True, None


def write_many(deps, key: str, org_id: str | None, *, system: str, data: dict, drafts: list[str], max_len: int) -> tuple[list[str], list[bool]]:
    """write() for a list of drafts in one call: each text is checked on its own, and a failed one keeps its draft."""
    if not drafts or mode(deps, org_id, key) == "rules":
        return list(drafts), [False] * len(drafts)
    payload = {**data, "drafts": drafts}
    try:
        out = deps.llm.structured(embed_data(payload), AgentTexts, system=system)
    except (AIUnavailable, LLMError, ValidationError):
        return list(drafts), [False] * len(drafts)
    except Exception:  # noqa: BLE001
        log.warning("agent writing failed", exc_info=True)
        return list(drafts), [False] * len(drafts)
    facts = json.dumps(payload, ensure_ascii=False, default=str)
    texts, ai = [], []
    for i, d in enumerate(drafts):
        t, _ = check_text(out.texts[i] if i < len(out.texts) else "", facts, max_len)
        texts.append(t or d)
        ai.append(t is not None)
    return texts, ai


# ---------------------------------------------------------------- run records
def trace_id() -> str | None:
    """The LangSmith trace the caller runs in, or None when tracing is off."""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        return str(run.trace_id) if run is not None else None
    except Exception:  # noqa: BLE001 - a trace link is a nicety
        return None


def record(deps, project_id: str | None, key: str, trigger: str, status: str, summary: str, trace: str | None = None,
           run_id: str | None = None, **detail) -> str | None:
    """One run record. `summary` must be safe to show any manager: never a hire's question, never private review content.
    An agent loop's trace goes in `detail` as runs_detail(...) gives it (steps privacy-filtered). `run_id`: the id to store it
    under (the asker's client-chosen run id), else a new one."""
    detail = {"summary": summary[:200], **detail, **({"trace_id": trace} if trace else {})}
    try:
        return deps.store.record_run(project_id or "", key, trigger, status, detail, run_id=run_id)
    except Exception:  # noqa: BLE001 - losing a run record must never fail the run it describes
        log.warning("run record failed", exc_info=True)
        return None


def run_summary(run: dict) -> str:
    """The line GET /api/agents/runs shows for a run."""
    detail = run.get("detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = {}
    key = agent_key(run.get("agent", ""))
    if detail.get("summary"):
        return str(detail["summary"])[:200]
    if run.get("status") == "failed":
        return f"{AGENTS[key].name if key in AGENTS else key} failed; the managers were told"
    return DEFAULT_SUMMARIES.get((key, run.get("trigger")), f"Ran on {run.get('trigger')}")


def plain(model) -> dict:
    return model.model_dump(mode="json") if isinstance(model, BaseModel) else dict(model)


def run_detail(outcome=None, *, user_id: str | None, private: bool = False, steps=None, agentic: bool | None = None,
               ms: int | None = None) -> dict:
    """What a run record keeps of an agent loop (GET /api/agents/runs/{run_id}): the shared, privacy-filtered steps
    (runtime.shared_steps), who the run was for, and who may read it: "shared" (that user and the project's managers) or
    "private" (that user alone, e.g. the hire's pre-submit review)."""
    from app.agents.core.runtime import shared_steps

    all_steps = list(steps if steps is not None else (outcome.steps if outcome is not None else []))
    out = {"steps": shared_steps(all_steps), "user_id": user_id, "visibility": "private" if private else "shared",
           "agentic": bool(outcome is not None) if agentic is None else agentic}
    if outcome is not None:
        out.update(stopped=outcome.stopped, turns=outcome.turns, tool_calls=outcome.tool_calls, ms=outcome.ms)
    if ms is not None:  # the whole run's wall time (the loop is part of it)
        out["ms"] = int(ms)
    elif "ms" not in out:
        out["ms"] = sum(int(s.get("ms") or 0) for s in out["steps"])
    return out
