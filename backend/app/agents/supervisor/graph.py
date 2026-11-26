"""The Supervisor: one LangGraph state graph that routes every event to exactly one agent, chains agents
(Note-taker -> Planner, push -> Librarian -> Keeper, daily tick -> Coach -> Planner), pauses for human approval, and
escalates failures to a person. One node per specialist (librarian, note_taker, guide, planner, scout, reviewer, coach,
keeper), subgraphs for the agents with several steps (the Guide's tiers, the Coach's check and weekly summary, the
Reviewer's match and review), pauses as interrupt() nodes resumed with Command. Every agent invocation runs here: the
asynchronous ones and anything that may pause through `graph` (checkpointed), the synchronous ones through `chat_graph`.
The roster, triggers and proposals: app/agents/core/registry.py; the picture: docs/AGENTS.md ("How the LangGraph is built").

Specialist agents never call each other. Every hop goes through this graph, so every run is checkpointed (Postgres in
production), which is what lets an approval pause for days and resume exactly where it stopped, and, when the
organisation turned LangSmith tracing on (LANGSMITH_TRACING=true, app/container.configure_tracing), traced: each agent
run's record then carries its trace's id (`trace_id`), the link from GET /api/runs to the trace.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from app.agents.core import registry as R
from app.agents.core import runtime as rt
from app.agents.core.deps import Deps
from app.agents.core.llm import describe_error
from app.agents.core.registry import RulesMode
from app.agents.core.schemas import ActionItem, ProposedTask
from app.agents.specialists.coach import agent as coach
from app.agents.specialists.guide import agent as guide
from app.agents.specialists.keeper import agent as keeper
from app.agents.specialists.librarian import agent as ingestion
from app.agents.specialists.note_taker import agent as meeting
from app.agents.specialists.planner import agent as planner
from app.agents.specialists.planner import task
from app.agents.specialists.reviewer import agent as reviewer
from app.agents.specialists.scout import agent as scout
from app.agents.supervisor import orchestrator
from app.agents.supervisor.state import State
from app.ports import AIUnavailable, RepoBusy, TooLarge
from app.services import kt_review
from app.services.embeddings import DailyQuotaExceeded, SearchBusy

log = logging.getLogger("supervisor")
# Errors whose message we wrote for people (which limit, what to do): shown as they are. Any other error is named by its kind
# only, wherever a person reads it (the managers' incident notice, the run record, the chat's error): a database error's text
# can quote the row it was writing, a hire's question included.
PUBLIC_ERRORS = (AIUnavailable, TooLarge, DailyQuotaExceeded, SearchBusy)
# Errors a retry cannot fix: the node gives up at once instead of trying agent_retries times.
FINAL_ERRORS = (AIUnavailable, TooLarge, DailyQuotaExceeded)


def public_error(e: BaseException) -> str:
    """What a person is told about a failure (PUBLIC_ERRORS)."""
    return str(e)[:300] if isinstance(e, PUBLIC_ERRORS) else f"an unexpected error ({type(e).__name__})"


def trace_id() -> str | None:
    """The LangSmith trace this node runs in, or None when tracing is off."""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        return str(run.trace_id) if run is not None else None
    except Exception:  # noqa: BLE001 - a trace link is a nicety: never fail a run over it
        return None


# Every event goes to one specialist: the node of the same name (docs/AGENTS.md, "How the LangGraph is built").
ROUTES = {
    "repo.connected": "librarian", "repo.pushed": "librarian", "document.uploaded": "librarian",
    "meeting.recording_ready": "note_taker",
    "chat.question": "guide", "ask.route": "guide",
    "onboarding.started": "planner", "program.completion_due": "planner", "program.completed": "planner",
    "completion.requested": "planner",
    "first_task.requested": "scout",
    "pr.opened": "reviewer", "pr.updated": "reviewer", "pr.merged": "reviewer", "presubmit.requested": "reviewer",
    "lifecycle.tick": "coach", "week.closed": "coach", "summary.requested": "coach",
    "feedback.created": "keeper", "handover.requested": "keeper",
}
AGENT_NODES = ("librarian", "note_taker", "guide", "planner", "scout", "reviewer", "coach", "keeper")
APPROVAL_NODES = {"planner_approval": "task_plan", "reviewer_approval": "review", "approval": "rerecord, doc_edit, template_change"}
# answered while the caller waits, through the graph without a checkpointer (Container.chat_graph): they never pause
SYNC_EVENTS = frozenset({"chat.question", "ask.route", "ask.free", "first_task.requested", "presubmit.requested", "handover.requested",
                         "summary.requested", "completion.requested"})
# A free-form request in Ask: the Supervisor's own decision (a model's, with its own trace) of who handles it: the
# orchestrate node, then the Guide, or a proposal from the Coach, Planner or Scout (app/agents/supervisor/orchestrator.py).
FREE = "ask.free"
ORG_WIDE = frozenset({"lifecycle.tick", "week.closed", "program.completion_due"})  # their nodes record a run per project
PROPOSAL_AGENT = {"rerecord": "keeper", "doc_edit": "keeper", "template_change": "planner"}


OPEN_FOR_PR = frozenset({"approved", "in_progress", "changes_requested", "submitted"})  # task statuses a pull request can move


def build_supervisor(deps: Deps, checkpointer):
    store, settings = deps.store, deps.settings

    def guarded(name, fn, summary=None, record=True, owns_run=False):
        """A specialist node: retry its step, record the run, and return its update or an error for escalation.

        `name(state, out)` is the run's agent name (the core agents keep theirs: ingestion, meeting, qa, task, task_status,
        review), `summary(state, out)` its safe line (registry.record), `record(state, out)` whether this node records it
        (organisation-wide steps record one per project themselves). A private run (the hire's pre-submit review) records only
        that it ran: no keys, no error text. A synchronous request is tried once. AI off or "rules" mode is not retried.

        An agent loop's trace comes back as `out["trace"]` (registry.run_detail: shared steps, user_id, visibility, and
        `run_id`, the id to store the run under); it goes into the run record, never into the graph's state. A private run
        keeps only who it was for. `owns_run`: a failure is recorded under the event's run_id (the asker's run)."""
        def node(state: State, config) -> dict:
            ev = state["event"]
            private = ev.get("type") == "presubmit.requested"
            attempts = 1 if ev.get("type") in SYNC_EVENTS else settings.agent_retries
            last = None
            trace = trace_id()
            traced = {"trace_id": trace} if trace else {}
            run_name = name if isinstance(name, str) else name(state, None)
            for attempt in range(attempts):
                try:
                    out = fn(state, config) or {}
                    run = out.pop("trace", None) or {}
                    run_name = name if isinstance(name, str) else name(state, out)
                    if record is True or (callable(record) and record(state, out)):
                        detail = {"summary": reviewer.PRIVATE_SUMMARY} if private else {"keys": list(out)}
                        line = summary(state, out) if summary and not private else None
                        if line:
                            detail["summary"] = line[:200]
                        kept = {k: v for k, v in run.items() if k != "run_id" and (not private or k in {"user_id", "visibility"})}
                        store.record_run(ev.get("project_id", ""), run_name, ev["type"], "succeeded", {**detail, **kept, **traced},
                                         run_id=run.get("run_id"))
                    return {**out, "error": None}
                except RepoBusy:  # not a failure: the event is handed back to the queue and continued later
                    raise
                except FINAL_ERRORS as e:  # AI off, over a limit, a day's quota: trying again changes nothing
                    last = e
                    break
                except Exception as e:  # noqa: BLE001 - the supervisor owns failure handling
                    last = e
                    if attempt + 1 < attempts:
                        time.sleep(settings.retry_sleep_s * 2**attempt)
            log.warning("agent run failed", extra={"extra_fields": {"agent": run_name, "event": ev.get("type"),
                                                                    "error": "private review" if private else describe_error(last)}})
            detail = {"summary": "A private review could not finish."} if private else \
                {"error": public_error(last), "error_type": type(last).__name__}
            mine = {"user_id": ev.get("user_id"), "visibility": "private" if private else "shared"} if owns_run else {}
            for pid in projects_of(ev):
                store.record_run(pid, run_name, ev["type"], "failed", {**detail, **mine, **traced},
                                 run_id=ev.get("run_id") if owns_run else None)
            return {"error": "The private review could not finish." if private else f"{run_name} failed: {public_error(last)}"}

        return node

    def projects_of(ev: dict) -> list[str]:
        """The projects an organisation-wide event (the tick) touches, for its run records."""
        if ev.get("project_id"):
            return [ev["project_id"]]
        if not deps.lifecycle or not ev.get("org_id"):
            return [""]
        return sorted({p.project_id for p in deps.lifecycle.programs(ev["org_id"])}) or [""]

    def record_each(ev: dict, key: str, per: dict, line, trace: str | None) -> None:
        """One run record per project an organisation-wide node touched."""
        for pid, row in sorted(per.items()):
            R.record(deps, pid, key, ev["type"], "succeeded", line(row), trace)

    # ---------------- routing ----------------
    def route(state: State) -> dict:
        kind = state["event"].get("type", "")
        r = ROUTES.get(kind) or ("orchestrate" if kind == FREE else None)
        return {"route": r} if r else {"route": "escalate", "error": f"No agent handles event {state['event'].get('type')}"}

    def orchestrate(state, config):
        """ask.free: the Supervisor decides who handles the request (a model's decision with its own trace and run record).
        The Guide answers it as a question; the others return a proposal for now. Any failure: the Guide, the default."""
        ev = state["event"]
        steps: list = []
        user = store.get_user(ev["user_id"])
        live = rt.publisher(deps, run_id=ev.get("run_id"), org_id=user.org_id if user else "", user_id=ev.get("user_id"),
                            project_id=ev.get("project_id"))
        try:
            d = orchestrator.decide(deps, ev, steps, live)
        except Exception:  # noqa: BLE001 - the decision is a convenience: the Guide is always a safe default
            log.warning("ask.free decision failed", exc_info=True)
            d = {"handled_by": "guide", "why": "The Guide answers by default.", "proposal": "", "agentic": False}
        mine = d["handled_by"] != "guide"
        R.record(deps, ev.get("project_id"), "supervisor", ev["type"], "succeeded",
                 f"Routed a free-form request to the {R.AGENTS[d['handled_by']].name}", trace_id(),
                 run_id=ev.get("run_id") if mine else None, **R.run_detail(steps=steps, user_id=ev.get("user_id"), agentic=d["agentic"]))
        decision = {**d, "steps": steps}
        if not mine:
            return {"decision": decision}
        return {"decision": decision, "result": {"handled_by": d["handled_by"], "why": d["why"], "proposal": d["proposal"],
                                                 **d.get("details", {}), "run_id": ev.get("run_id"), "steps": [x.public() for x in steps]}}

    # ---------------- agents ----------------
    def ingest(state, config):
        ev = state["event"]
        if ev["type"] == "document.uploaded":
            try:
                out = ingestion.ingest_document(deps, ev["project_id"], ev["document_id"], ev["uri"], ev["title"], ev.get("mime", "application/pdf"))
            except Exception:
                store.update_document(ev["document_id"], status="failed")
                raise
            store.update_document(ev["document_id"], status="ready", page_count=out["pages"])
            return {"result": out}
        repo = store.get_repo(ev["repository_id"])
        if ev["type"] == "repo.connected":
            return {"result": ingestion.ingest_repo(deps, repo, ev.get("ref") or repo.default_branch)}
        default = ev.get("default_branch") or repo.default_branch
        if default != repo.default_branch:  # the default branch was switched or renamed on GitHub: rebuild from the new one
            info = deps.repos.repo_info(repo.full_name, repo.installation_id)
            if not info or info["default_branch"] != default:  # a push delayed from before a switch must not switch back
                return {"result": {"skipped": f"{default} is no longer the default branch"}}
            return {"result": ingestion.ingest_repo(deps, repo.model_copy(update={"default_branch": default}), default,
                                                    ev.get("changed_paths", []) + ev.get("removed_paths", []))}
        touched = ev.get("changed_paths", []) + ev.get("removed_paths", [])
        try:  # the Keeper compares KT-linked lines with the code as indexed before this push: read them first
            before = keeper.snapshot(deps, repo, touched)
        except Exception:  # noqa: BLE001 - the comparison is the Keeper's; indexing must not wait for it
            log.warning("KT snapshot failed", exc_info=True)
            before = []
        out = {"result": ingestion.ingest_push(deps, repo, ev.get("changed_paths", []), ev.get("removed_paths", []),
                                               ev.get("branch") or default)}
        return {**out, "kt_before": before} if before else out

    def keeper_outdated(state, config):
        ids, run = keeper.after_push_traced(deps, state["event"], state.get("kt_before", []), config["configurable"]["thread_id"], trace_id())
        return {"proposals": ids, "followup": {"agent": "keeper", "rerecord_proposals": len(ids)}, **({"trace": run} if run else {})}

    def ask(state, config):
        ev = state["event"]
        result, message_id = guide.answer(deps, ev["project_id"], ev["user_id"], ev["question"])
        return {"result": {**result.model_dump(), "message_id": message_id}}

    def coach_summary(state, config):
        """summary.requested {project_id, onboarding_id, user_id}: this week's summary, on request (POST .../summaries)."""
        from app.lifecycle import hires
        from app.lifecycle.clock import week_start

        ev = state["event"]
        user = store.get_user(ev["user_id"])
        hc = hires.load(R.lifecycle_ctx(deps, user.org_id, user), ev["onboarding_id"])
        s, t = coach.summarise(deps, hc, week_start(hc.ctx.clock.today()), trace_id(), user=user, run_id=ev.get("run_id"))
        if t is None:
            return {"result": s.model_dump(mode="json")}
        return {"result": {**s.model_dump(mode="json"), "run_id": t.run_id, "steps": t.public()}, "trace": t.trace()}

    def planner_completion_one(state, config):
        """completion.requested {project_id, onboarding_id, user_id}: the review with the draft (written when the criteria changed)."""
        from app.lifecycle import hires

        ev = state["event"]
        user = store.get_user(ev["user_id"])
        hc = hires.load(R.lifecycle_ctx(deps, user.org_id, user), ev["onboarding_id"])
        view, wrote, t = planner.completion(deps, hc, trace_id(), user=user, run_id=ev.get("run_id"))
        if t is None:
            return {"result": {"completion": view, "wrote": wrote}}
        return {"result": {"completion": view, "wrote": wrote, "run_id": t.run_id, "steps": t.public()}, "trace": t.trace()}

    def scout_node(state, config):
        return lifted(scout.run(deps, state["event"], trace_id()))

    def presubmit_node(state, config):
        return lifted(reviewer.presubmit(deps, state["event"]))

    def lifted(result: dict) -> dict:
        """A specialist's result with its run record's trace lifted out, for guarded() (never in the result or the state)."""
        run = result.pop("trace", None)
        return {"result": result, **({"trace": run} if run else {})}

    def keeper_handover(state, config):
        ev = state["event"]
        manager = store.get_user(ev["user_id"])
        person = store.get_user(ev["person_id"])
        ctx = R.lifecycle_ctx(deps, manager.org_id, manager)
        h, t = keeper.handover_traced(deps, ctx, ev["project_id"], person, date.fromisoformat(ev["last_day"]), trace_id(),
                                      run_id=ev.get("run_id"))
        if t is None:
            return {"result": h.model_dump(mode="json")}
        return {"result": {**h.model_dump(mode="json"), "run_id": t.run_id, "steps": t.public()}, "trace": t.trace()}

    def keeper_feedback(state, config):
        ids, run = keeper.feedback_created_traced(deps, state["event"], config["configurable"]["thread_id"], trace_id())
        return {"proposals": ids, "result": {"proposals": len(ids)}, **({"trace": run} if run else {})}

    def planner_template(state, config):
        ids, run = planner.propose_template_change_traced(deps, state["event"], config["configurable"]["thread_id"], trace_id())
        return {"proposals": ids, "result": {"proposals": len(ids)}, **({"trace": run} if run else {})}

    # ---------------- the daily tick: Coach, then (when due) week.closed and program.completion_due
    def coach_tick(state, config):
        ev, trace = state["event"], trace_id()
        out = coach.tick(deps, ev["org_id"], ev.get("project_id"), ev.get("day"), trace)
        record_each(ev, "coach", out["projects"], coach.tick_summary, trace)
        team = R.programs(deps, ev["org_id"], ev.get("project_id"), ev.get("day"))
        today = team[0].ctx.clock.today() if team else None
        week_due = bool(today and coach.week_missing(team, today))
        due = [hc.ob.id for hc in team if planner.is_due(hc)]
        tick = {"ran": ["coach"], "alerts": out["alerts"], "nudges": out["nudges"], "proposals": 0, "week_due": week_due,
                "completion_due": due, "day": ev.get("day")}
        told = guide.digests(deps, ev["org_id"], ev.get("day"))  # the Guide's daily digest of questions waiting for people
        if told:
            tick["ran"].append("guide")
            for pid in projects_of(ev):
                R.record(deps, pid, "guide", ev["type"], "succeeded", f"Sent the daily digest to {told} person(s)", trace)
        return {"tick": tick, "result": _tick_result(tick)}

    def coach_week(state, config):
        ev, trace = state["event"], trace_id()
        out = coach.week(deps, ev.get("org_id") or R.org_of(deps, ev.get("project_id")), ev.get("project_id"), ev.get("day"), trace)
        record_each(ev, "coach", out["projects"], lambda n: f"Wrote {n} weekly summar{'ies' if n != 1 else 'y'}", trace)
        tick = {**state.get("tick", {})}
        if tick:
            tick["ran"] = list(dict.fromkeys(tick["ran"] + ["coach"]))
        return {"tick": tick, "result": _tick_result(tick) if tick else {"summaries": out["written"]}}

    def planner_completion(state, config):
        ev, trace = state["event"], trace_id()
        tick = {**state.get("tick", {})}
        ids = tick.get("completion_due") if tick else ev.get("onboarding_ids")
        out = planner.completion_due(deps, ev.get("org_id") or R.org_of(deps, ev.get("project_id")), ev.get("project_id"), ids,
                                     ev.get("day"), trace)
        record_each(ev, "planner", out["projects"], lambda n: f"Drafted {n} completion review{'s' if n != 1 else ''}", trace)
        if tick:
            tick["ran"] = list(dict.fromkeys(tick["ran"] + ["planner"]))
            tick["proposals"] = tick.get("proposals", 0) + out["drafted"]
        return {"tick": tick, "result": _tick_result(tick) if tick else {"drafted": out["drafted"]}}

    def _tick_result(tick: dict) -> dict:
        return {"ran": tick.get("ran", []), "proposals": tick.get("proposals", 0), "alerts": tick.get("alerts", 0)}

    # ---------------- the new kinds of approval: one interrupt per proposal, decided in any order
    def await_proposal(state, config):
        aid = state["approval_id"]
        kind = store.get_approval(aid).kind
        decision = interrupt({"kind": kind, "approval_id": aid})
        approval = store.get_approval(aid)
        user_id = decision.get("user_id", "")
        if decision.get("decision") == "approved":
            if kind == "rerecord":
                result = keeper.apply_rerecord(deps, approval, user_id)
            elif kind == "doc_edit":
                result = keeper.apply_doc_edit(deps, approval, user_id, decision.get("text"))
            else:
                result = planner.apply_template_change(deps, approval, user_id)
            status = "approved"
        else:
            result, status = {"rejected": True}, "rejected"
        store.decide_approval(aid, status, user_id)
        close(f"/approvals/{aid}")
        what = {"rerecord": "a re-recording", "doc_edit": "a doc fix", "template_change": "a template change"}[kind]
        R.record(deps, approval.project_id, PROPOSAL_AGENT[kind], "approval.decided", "succeeded",
                 f"A manager {'approved' if status == 'approved' else 'rejected'} {what}", trace_id())
        return {"decided": [{"approval_id": aid, "kind": kind, "decision": status, "result": result}]}

    def watch_meeting(state, config):
        ev = state["event"]
        if R.mode(deps, R.org_of(deps, ev["project_id"]), "note_taker") == "rules":
            store.update_meeting(ev["meeting_id"], status="failed")
            raise RulesMode("The Note-taker is set to rules in Agents: it does not watch recordings without the AI.")
        try:
            res = meeting.process_recording(deps, ev["project_id"], ev["meeting_id"], ev["recording_uri"], float(ev["duration_s"]), ev.get("transcript_uri"))
        except Exception:
            # not left 'processing' for good: 'failed' says so, and lets the recording be added again (a retry of this
            # node sets it back to 'processing'; the segments analysed so far are kept for it: specialists/note_taker/agent.py)
            store.update_meeting(ev["meeting_id"], status="failed")
            raise
        kt_review.open_review(store, ev["project_id"], res.meeting_id, res.title)  # the transcript goes to the managers
        return {"result": {"meeting_id": res.meeting_id, "kt_links": len(res.kt_links), "action_items": len(res.action_items)},
                "action_items": [a.model_dump() for a in res.action_items]}

    def tell(user_id: str, kind: str, text: str, link: str, project_id: str) -> None:
        """Notifications are best effort: a failed one must never make a node retry (and repeat its writes)."""
        try:
            store.notify(user_id, kind, text, f"{link}{'&' if '?' in link else '?'}project={project_id}")
        except Exception:  # noqa: BLE001
            log.warning("notification failed", exc_info=True)

    def close(link: str) -> None:
        """The thing a notification points at is settled (an approval decided or closed): nobody needs to open it any more.
        Best effort, like tell()."""
        try:
            store.close_notifications(link)
        except Exception:  # noqa: BLE001
            log.warning("closing notifications failed", exc_info=True)

    def plan_tasks(state, config):
        ev = state["event"]
        pid = ev["project_id"]
        thread = config["configurable"]["thread_id"]
        existing = store.pending_approval_for_thread(thread, "task_plan")
        if existing:  # a retry after the approval was saved: do not propose (and ask the manager) twice
            return {"approval_id": existing.id, "result": {"proposed": sum(len(p["tasks"]) for p in existing.payload.get("plans", []))}}
        if ev["type"] == "onboarding.started":
            targets = [store.get_onboarding(ev["onboarding_id"])]
        else:
            targets = store.onboardings(pid)
        items = [ActionItem(**a) for a in state.get("action_items", [])]
        plans, created, run = [], [], None
        try:
            for ob in filter(None, targets):
                # Proposals in no pending approval were left by an attempt that died before saving it. Only old ones: a
                # concurrent run (a meeting's plan and an onboarding's) may be between creating its tasks and its approval.
                # Half a lease, because a dead run's event is redelivered only once its lease has lapsed.
                store.discard_orphan_proposals(ob.id, grace_s=settings.event_lease_s // 2)
                enrolled = bool(ev.get("enrolment") and ev.get("onboarding_id") == ob.id and deps.lifecycle)
                drafted = None
                if enrolled:  # the Planner thinks: it researches the focus area and drafts the plan's tasks (checked)
                    try:
                        drafted = planner.draft_plan(deps, ob, ev.get("run_id"))
                    except Exception:  # noqa: BLE001 - the Task agent's or the template's plan stands in
                        log.warning("the Planner's plan failed", exc_info=True)
                    if drafted is not None:
                        run = drafted[2].trace()
                try:
                    if drafted is not None and drafted[0]:
                        proposed = drafted[0]
                    else:
                        proposed = task.plan_for(R.deps_for(deps, R.org_of(deps, pid), "planner"), ob, items)
                except AIUnavailable as e:  # no model on this server: the program template's plan (app/lifecycle/enrolment.py)
                    if ev.get("template_tasks") and ev["type"] == "onboarding.started":
                        proposed = [ProposedTask.model_validate(t) for t in ev["template_tasks"]]
                    elif isinstance(e, RulesMode):  # the Planner is set to rules: the program's template plan, or nothing
                        proposed = template_plan(ob)
                    else:
                        raise
                full = None
                if ev.get("enrolment") and ev.get("onboarding_id") == ob.id and deps.lifecycle:  # an enrolment: ONE 30/60/90 plan
                    template_tasks = [ProposedTask.model_validate(t) for t in ev.get("template_tasks", [])]
                    full = planner.plan_30_60_90(deps, ob, proposed, template_tasks)
                    proposed = full["tasks"]
                if not proposed:
                    continue
                start = ob.start_date if full else max(ob.start_date, date.today())
                rows = store.create_tasks(ob.id, proposed, start)
                created += [r.id for r in rows]
                new_hire = store.get_user(ob.new_hire_id)
                entry = {"onboarding_id": ob.id, "new_hire": new_hire.name if new_hire else ob.new_hire_id,
                         "tasks": [r.model_dump(mode="json") for r in rows]}
                if full:
                    entry.update(milestones=full["milestones"], enrolment=ev["enrolment"],
                                 phases=planner.phase_view(full["phases"], full["milestones"], [r.model_dump() for r in rows], start))
                if drafted is not None:  # the Planner's reasons and trace, for the manager deciding the plan
                    entry["agent"] = drafted[1]
                plans.append(entry)
            if not plans:
                return {"result": {"proposed": 0}, **({"trace": run} if run else {})}
            approval = store.create_approval(pid, "task_plan", thread, {"source": ev["type"], "plans": plans})
        except Exception:
            for tid in created:  # too young for the orphan sweep: withdraw them so the retry's plan does not see them as taken
                store.update_task(tid, status="rejected")
            raise
        text = ("The Planner drafted a 30/60/90 plan for your review." if any(p.get("phases") for p in plans)
                else f"The Task agent proposed {sum(len(p['tasks']) for p in plans)} tasks for your review.")
        for m in store.managers(pid):
            tell(m.id, "approval", text, f"/approvals/{approval.id}", pid)
        return {"approval_id": approval.id, "result": {"proposed": sum(len(p["tasks"]) for p in plans)}, **({"trace": run} if run else {})}

    def await_task_approval(state, config):
        decision = interrupt({"kind": "task_plan", "approval_id": state["approval_id"]})
        approval = store.get_approval(state["approval_id"])
        ids = [t["id"] for p in approval.payload["plans"] for t in p["tasks"]]
        approve, edits = set(decision.get("approve", [])), decision.get("edits", {})
        for tid in ids:
            if tid in approve:
                edit = {k: v for k, v in edits.get(tid, {}).items() if k in {"title", "description", "due_date"} and v is not None}
                if isinstance(edit.get("due_date"), str):
                    edit["due_date"] = date.fromisoformat(edit["due_date"])
                store.update_task(tid, status="approved", **edit)
                t = store.get_task(tid)
                ob = store.get_onboarding(t.onboarding_id)
                tell(ob.new_hire_id, "task", f"New task: {t.title}", f"/tasks/{tid}", approval.project_id)
            else:
                store.update_task(tid, status="rejected")
        status = "approved" if approve >= set(ids) else ("rejected" if not approve else "partially_approved")
        enrolments = []
        manager = store.get_user(decision.get("user_id", "")) if decision.get("user_id") else None
        for p in approval.payload["plans"]:  # an enrolment's plan: its milestones, access requests and invitation now
            mine = {t["id"] for t in p["tasks"]} & approve
            if p.get("enrolment") and mine and deps.lifecycle:
                from app.lifecycle import enrolment

                org = R.org_of(deps, approval.project_id)
                done = enrolment.finalize(R.lifecycle_ctx(deps, org, manager), p, len(mine), decision.get("milestones"))
                if done:
                    enrolments.append(done)
        store.decide_approval(approval.id, status, decision.get("user_id", ""))
        close(f"/approvals/{approval.id}")
        result = {"approved": len(approve & set(ids)), "rejected": len(set(ids) - approve)}
        return {"result": {**result, "enrolments": enrolments} if enrolments else result}

    def template_plan(ob) -> list[ProposedTask]:
        from app.lifecycle import templates as T

        program = deps.lifecycle.get_program(ob.id) if deps.lifecycle else None
        t = deps.lifecycle.get_template(program.template_id) if program else None
        return T.plan_tasks(t, program.duration_days) if t and not task_exists(ob) else []

    def task_exists(ob) -> bool:
        return any(t.status != "rejected" for t in store.tasks(onboarding_id=ob.id))

    def hire_login(onboarding_id: str) -> str:
        ob = store.get_onboarding(onboarding_id)
        hire = store.get_user(ob.new_hire_id) if ob else None
        return (hire.github_login or "").lower() if hire else ""

    def pr_status(state, config):
        ev = state["event"]
        branch, body, author = ev.get("branch", ""), ev.get("body", ""), (ev.get("author") or "").lower()
        candidates = [t for t in store.tasks(project_id=ev["project_id"]) if t.status in OPEN_FOR_PR]
        logins = {ob_id: hire_login(ob_id) for ob_id in {t.onboarding_id for t in candidates}}
        # Only the new hire's own pull request moves their task. Pick among the author's tasks (two hires may share an
        # issue), rather than matching any task and then refusing it.
        found = task.find_task_for_pr([t for t in candidates if author and logins[t.onboarding_id] == author], branch, body)
        if not found:
            # With no username to compare (none on record for the hire, or none in the event), only a PR that names the
            # task id counts - never "Closes #N", which anyone can write in a PR.
            unverified = [t for t in candidates if not author or not logins[t.onboarding_id]]
            found = task.find_task_for_pr(unverified, branch, body, by_issue=False)
        if not found:
            return {"result": {"matched_task": None}}
        if ev["type"] == "pr.merged":
            store.update_task(found.id, status="done")
            if deps.lifecycle:  # only a merge (never an approved review) completes "First change merged" and counts as contributed
                deps.lifecycle.record_metric(R.org_of(deps, ev["project_id"]), ev["project_id"], "pr.merged", found.id,
                                             {"pr": ev.get("pr_number")})
            # A review still waiting for the manager is moot once the code is merged: deciding it later would move a done task
            # back to changes_requested with no pull request left to change. Close it and drop its parked run.
            for old in store.close_review_approvals(found.id):
                close(f"/approvals/{old.id}")
                if checkpointer:
                    try:
                        checkpointer.delete_thread(old.thread_id)
                    except Exception:  # noqa: BLE001 - a leftover checkpoint is dead weight, never a wrong decision
                        log.warning("checkpoint cleanup failed", exc_info=True)
            return {"result": {"matched_task": found.id, "status": "done"}}
        store.update_task(found.id, status="submitted", pr_number=ev["pr_number"], pr_url=ev.get("pr_url"))
        return {"task_id": found.id, "result": {"matched_task": found.id, "status": "submitted"}}

    def do_review(state, config):
        ev = state["event"]
        pid = ev["project_id"]
        thread = config["configurable"]["thread_id"]
        existing = store.pending_approval_for_thread(thread, "review")
        if existing:  # a retry after the approval was saved
            rid = existing.payload["review_id"]
            result = {"review_id": rid, "recommendation": existing.payload.get("recommendation")}
            if not retire_older_reviews(existing.payload["task_id"], existing.id):
                return {"review_id": rid, "result": {**result, "superseded": True}}
            return {"review_id": rid, "approval_id": existing.id, "result": result}
        t = store.get_task(state["task_id"])
        res, thought = reviewer.review_pr_traced(deps, pid, t, ev["full_name"], ev.get("installation_id"), ev["pr_number"], ev["head_sha"])
        run = {"trace": thought.trace()} if thought is not None else {}
        row = store.save_review(t.id, ev["pr_number"], ev.get("pr_url", ""), ev["head_sha"], res)  # unique per (task, commit)
        payload = {"review_id": row.id, "task_id": t.id, "recommendation": res.recommendation, "pr_url": ev.get("pr_url"),
                   "pr_updated_at": ev.get("pr_updated_at")}
        if thought is not None:  # the Reviewer's run, for the manager deciding: GET /api/agents/runs/{run_id}
            payload["run_id"] = thought.run_id
        approval = store.create_approval(pid, "review", thread, payload)
        if not retire_older_reviews(t.id, approval.id):  # a review of a newer commit is waiting (or was decided): this one is moot
            return {"review_id": row.id, "result": {"review_id": row.id, "recommendation": res.recommendation, "superseded": True}, **run}
        ob = store.get_onboarding(t.onboarding_id)
        tell(ob.new_hire_id, "review", f"Review agent feedback on PR #{ev['pr_number']}: {res.recommendation.replace('_', ' ')}", f"/reviews/{row.id}", pid)
        tell(ob.manager_id, "approval", f"A review is waiting for your decision (PR #{ev['pr_number']}).", f"/approvals/{approval.id}", pid)
        return {"review_id": row.id, "approval_id": approval.id, "result": {"review_id": row.id, "recommendation": res.recommendation}, **run}

    def retire_older_reviews(task_id: str, approval_id: str) -> bool:
        """A review of an older commit must not be decidable once the PR has moved on: approving it would mark the task
        done on code nobody reviewed. Reviews finish in any order, so the store keeps the one for the newest PR state,
        which need not be `approval_id`. Close the others and drop their parked runs (nothing will resume them).
        False if `approval_id` was closed: this run then ends without parking."""
        retired = store.supersede_review_approvals(task_id)
        for old in retired:
            close(f"/approvals/{old.id}")
            if checkpointer and old.id != approval_id:  # this run's own thread is still running; it is cleaned up when it ends
                checkpointer.delete_thread(old.thread_id)
        return approval_id not in {a.id for a in retired}

    def await_review_decision(state, config):
        decision = interrupt({"kind": "review", "approval_id": state["approval_id"]})
        verdict = decision.get("decision", "changes_requested")
        store.decide_review(state["review_id"], verdict, decision.get("user_id", ""))
        if verdict == "approved" or store.get_task(state["task_id"]).status != "done":  # a merged (done) task stays done
            store.update_task(state["task_id"], status="done" if verdict == "approved" else "changes_requested")
        store.decide_approval(state["approval_id"], "approved" if verdict == "approved" else "rejected", decision.get("user_id", ""))
        close(f"/approvals/{state['approval_id']}")
        t = store.get_task(state["task_id"])
        ob = store.get_onboarding(t.onboarding_id)
        # to the review, which says what to change, rather than the task list
        tell(ob.new_hire_id, "review", f"Your manager marked '{t.title}' as {verdict.replace('_', ' ')}.", f"/reviews/{state['review_id']}", ob.project_id)
        return {"result": {"decision": verdict}}

    def escalate(state, config):
        ev = state.get("event", {})
        for m in store.managers(ev.get("project_id", "")) if ev.get("project_id") else []:
            tell(m.id, "incident", f"An agent needs a human: {state.get('error')}", "/runs", ev["project_id"])
        return {"result": {"escalated": True, "error": state.get("error")}}

    # ---------------- each specialist's steps: subgraphs where an agent has several ----------------
    class CoachState(TypedDict, total=False):
        event: dict
        tick: dict
        result: dict

    def coach_steps():
        """lifecycle.tick: check (blockers, nudges, the Guide's digest) -(a week closed)-> weekly; week.closed: weekly."""
        c = StateGraph(CoachState)
        c.add_node("check", lambda st, config: coach_tick(st, config))
        c.add_node("weekly", lambda st, config: coach_week(st, config))
        c.add_conditional_edges(START, lambda st: st["event"]["type"], {"lifecycle.tick": "check", "week.closed": "weekly"})
        c.add_conditional_edges("check", lambda st: "week closed" if st["tick"].get("week_due") else "done",
                                {"week closed": "weekly", "done": END})
        c.add_edge("weekly", END)
        return c.compile(checkpointer=False)

    class ReviewState(TypedDict, total=False):
        event: dict
        task_id: str
        review_id: str
        approval_id: str
        result: dict
        trace: dict  # the Reviewer's run record part (guarded() takes it off; never in the Supervisor's state)

    def reviewer_steps():
        """A pull request: match it to the hire's task -(a code task)-> review it (the review approval waits outside)."""
        r = StateGraph(ReviewState)
        r.add_node("match", lambda st, config: pr_status(st, config))
        r.add_node("review", lambda st, config: do_review(st, config))
        r.add_edge(START, "match")
        r.add_conditional_edges("match", lambda st: "code task" if st.get("task_id") and store.get_task(st["task_id"]).kind == "code" else "done",
                                {"code task": "review", "done": END})
        r.add_edge("review", END)
        return r.compile(checkpointer=False)

    guide_tiers, coach_graph, reviewer_graph = guide.tiers(deps), coach_steps(), reviewer_steps()

    def keep(out: dict, *keys) -> dict:
        return {k: out[k] for k in keys if k in out}

    # ---------------- the specialists: one node each ----------------
    def librarian(state, config):
        return ingest(state, config)

    def note_taker(state, config):
        return watch_meeting(state, config)

    def guide_node(state, config):
        ev = state["event"]
        if ev["type"] == "chat.question":
            return ask(state, config)
        seed = (state.get("decision") or {}).get("steps") or []  # ask.free: the Supervisor's decision comes first in the trace
        done = guide_tiers.invoke({"event": ev, "trace": trace_id(), "steps": seed}, config)
        return {"result": done["result"], "trace": guide.trace_of(done, ev.get("user_id"))}

    def planner_node(state, config):
        ev = state["event"]
        if ev["type"] in ORG_WIDE:  # the tick's chain, or program.completion_due on its own
            return planner_completion(state, config)
        if ev["type"] == "program.completed":
            return planner_template(state, config)
        if ev["type"] == "completion.requested":
            return planner_completion_one(state, config)
        return plan_tasks(state, config)  # onboarding.started, and the Note-taker's chain

    def reviewer_node(state, config):
        if state["event"]["type"] == "presubmit.requested":
            return presubmit_node(state, config)
        return keep(reviewer_graph.invoke({"event": state["event"]}, config), "task_id", "review_id", "approval_id", "result", "trace")

    def coach_node(state, config):
        if state["event"]["type"] == "summary.requested":
            return coach_summary(state, config)
        return keep(coach_graph.invoke({"event": state["event"]}, config), "tick", "result")

    def keeper_node(state, config):
        ev = state["event"]
        if ev["type"] == "repo.pushed":
            return keeper_outdated(state, config)
        if ev["type"] == "feedback.created":
            return keeper_feedback(state, config)
        return keeper_handover(state, config)

    def run_name(agent):
        """The name a node's run is recorded under: the core agents keep theirs."""
        def name(state, out):
            ev = state["event"]["type"]
            if agent == "reviewer" and ev != "presubmit.requested":
                return "review" if out and out.get("review_id") else "task_status"
            if agent == "planner" and ev in {"onboarding.started", "meeting.recording_ready"}:
                return "task"
            if agent == "guide" and ev == "chat.question":
                return "qa"
            return {"librarian": "ingestion", "note_taker": "meeting"}.get(agent, agent)
        return name

    def summary_of(state, out) -> str | None:
        ev, r = state["event"]["type"], out.get("result") or {}
        if ev in {"ask.route", FREE}:
            return guide.summary(r)
        if ev == "first_task.requested":
            return scout.summary(r)
        if ev == "handover.requested":
            return f"Drafted a handover plan of {len(r.get('plan', {}).get('items', []))} items"
        if ev == "feedback.created":
            return f"Read new reader feedback: {len(out.get('proposals', []))} proposal(s)"
        if ev == "program.completed":
            return f"Read a completed program: {len(out.get('proposals', []))} template proposal(s)"
        if ev == "repo.pushed" and "followup" in out:
            return f"Checked KT explanations after a push: {len(out.get('proposals', []))} re-recording proposal(s)"
        if ev == "summary.requested":
            return "Wrote this week's summary on request"
        if ev == "completion.requested":
            return "Drafted a completion review"
        return None

    def records(state, out) -> bool:
        ev = state["event"]["type"]
        if ev in ORG_WIDE:
            return False  # one record per project, written by the step itself
        if ev == "completion.requested":
            return bool((out.get("result") or {}).get("wrote"))  # a read that wrote nothing is not a run
        return True

    # ---------------- graph ----------------
    g = StateGraph(State)
    g.add_node("route", route)
    g.add_node("librarian", guarded(run_name("librarian"), librarian))
    g.add_node("note_taker", guarded(run_name("note_taker"), note_taker))
    g.add_node("guide", guarded(run_name("guide"), guide_node, summary_of, owns_run=True))
    g.add_node("orchestrate", orchestrate)
    g.add_node("planner", guarded(run_name("planner"), planner_node, summary_of, records))
    g.add_node("scout", guarded(run_name("scout"), scout_node, summary_of))
    g.add_node("reviewer", guarded(run_name("reviewer"), reviewer_node, summary_of))
    g.add_node("coach", guarded(run_name("coach"), coach_node, summary_of, records))
    g.add_node("keeper", guarded(run_name("keeper"), keeper_node, summary_of))
    g.add_node("planner_approval", await_task_approval)
    g.add_node("reviewer_approval", await_review_decision)
    g.add_node("approval", await_proposal)
    g.add_node("escalate", escalate)

    def after(chain):
        """The edge out of a specialist: an error escalates, else `chain(state)` names the next step (a label of its map)."""
        def pick(state: State):
            if state.get("error"):
                return "done" if state["event"].get("type") == "presubmit.requested" else "error"  # private: never an incident
            return chain(state)
        return pick

    def park(label: str):
        """One paused branch per proposal: each waits for its own decision, in any order (Container.decide resumes one)."""
        def pick(state: State):
            ids = state.get("proposals") or []
            if not ids or not checkpointer:
                return "done"  # the chat graph never parks; nothing it runs proposes
            return [Send("approval", {"event": state["event"], "approval_id": a}) for a in ids]
        return pick

    def planner_next(state: State):
        if state.get("approval_id"):
            return "task_plan"
        return park("template_change")(state)

    stop = {"error": "escalate", "done": END}
    g.add_edge(START, "route")
    g.add_conditional_edges("route", lambda s: s["event"].get("type") if s["route"] != "escalate" else "unknown event",
                            {**ROUTES, FREE: "orchestrate", "unknown event": "escalate"})
    g.add_conditional_edges("orchestrate", lambda s: FREE if s["decision"]["handled_by"] == "guide" else "proposal",
                            {FREE: "guide", "proposal": END})
    g.add_conditional_edges("librarian", after(lambda s: "repo.pushed" if s.get("kt_before") else "done"), {"repo.pushed": "keeper", **stop})
    g.add_conditional_edges("note_taker", after(lambda s: "meeting.recording_ready" if s.get("action_items") and
                                                store.onboardings(s["event"]["project_id"]) else "done"),
                            {"meeting.recording_ready": "planner", **stop})
    g.add_conditional_edges("planner", after(planner_next), {"task_plan": "planner_approval", "template_change": "approval", **stop})
    g.add_conditional_edges("reviewer", after(lambda s: "review" if s.get("approval_id") else "done"), {"review": "reviewer_approval", **stop})
    g.add_conditional_edges("coach", after(lambda s: "program.completion_due" if (s.get("tick") or {}).get("completion_due") else "done"),
                            {"program.completion_due": "planner", **stop})
    g.add_conditional_edges("keeper", after(park("rerecord, doc_edit")), {"rerecord, doc_edit": "approval", **stop})
    for name in ("guide", "scout"):
        g.add_conditional_edges(name, after(lambda s: "done"), stop)
    for name in ("planner_approval", "reviewer_approval", "approval", "escalate"):
        g.add_edge(name, END)
    return g.compile(checkpointer=checkpointer)


def run_event(graph, event: dict, thread_id: str) -> dict[str, Any]:
    return graph.invoke({"event": event}, {"configurable": {"thread_id": thread_id}})


def resume(graph, thread_id: str, decision: dict, interrupt_id: str | None = None) -> dict[str, Any]:
    """Resume the paused run with a decision; `interrupt_id` names which of its pauses it answers when it has several (a push
    can park several rerecord proposals on one run)."""
    value = {interrupt_id: decision} if interrupt_id else decision
    return graph.invoke(Command(resume=value), {"configurable": {"thread_id": thread_id}})
