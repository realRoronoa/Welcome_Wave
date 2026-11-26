"""The agents API (docs/AGENTS.md): who the agents are, what they did, how each writes, and the daily tick.

    GET   /api/agents?project_id          managers, admins, executives: the roster with each agent's mode and recent runs
    GET   /api/agents/runs?project_id&agent&limit   managers, admins: recent runs, each with a short, safe summary and its
                                           visibility ("private": the hire's private review, whose trace only they read)
    GET   /api/agents/runs/{run_id}       the run's own user, and the project's managers (not for a private run): {run, steps},
                                           the agent loop's privacy-filtered trace (app/agents/runtime.shared_steps)
    PATCH /api/agents/{key} {mode}         admins: "ai" or "rules" for this organisation (audited); the Supervisor and the
                                           Librarian write no text and cannot be switched (409)
    POST  /api/agents/tick?project_id     managers, admins (demo and self-hosted): run the daily tick now
    POST  /internal/lifecycle/tick         Cloud Scheduler (OIDC, like /internal/outbox/flush): every organisation's tick

Run summaries are written by the agents for this page and never carry a new hire's question or a private review's content."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel

from app.agents import registry as R
from app.api.auth import check_project, container, current_user, require
from app.ports import User

router = APIRouter()
WEEK = timedelta(days=7)
RUNS_READ = 2000  # recent runs read to count a week of them


def _projects(c, user: User, project_id: str | None) -> list[str]:
    if project_id:
        check_project(c, user, project_id)
        return [project_id]
    return [p.id for p in c.store.projects_for(user)]


def _at(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(str(value))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _runs(c, projects: list[str], limit: int = RUNS_READ) -> list[dict]:
    rows = [r for pid in projects for r in c.store.list_runs(pid, limit)]
    return sorted(rows, key=lambda r: _at(r.get("at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _agent(c, org_id: str, key: str, runs: list[dict], pending: list) -> dict:
    a = R.AGENTS[key]
    since = datetime.now(timezone.utc) - WEEK
    mine = runs if key == "supervisor" else [r for r in runs if R.agent_key(r.get("agent", "")) == key]
    week = [r for r in mine if (_at(r.get("at")) or since) >= since]
    last = mine[0] if mine else None
    return {"key": a.key, "name": a.name, "role": a.role, "triggers": list(a.triggers), "tools": list(a.tools), "approvals": list(a.approvals),
            "mode": R.mode(c.deps, org_id, key), "ai_available": R.ai_available(c.deps, key), "runs_7d": len(week),
            "failed_7d": sum(r.get("status") == "failed" for r in week),
            "last_run": {"at": _at(last.get("at")), "status": last.get("status"), "trigger": last.get("trigger"),
                         "trace_id": last.get("trace_id")} if last else None,
            "pending": sum(1 for x in pending if x.kind in a.approvals)}


def _roster(c, user: User, projects: list[str], only: str | None = None) -> list[dict]:
    runs = _runs(c, projects)
    pending = [a for pid in projects for a in c.store.approvals(pid)]
    return [_agent(c, user.org_id, k, runs, pending) for k in R.AGENTS if only is None or k == only]


@router.get("/api/agents")
def agents(project_id: str | None = None, user: User = Depends(require("manager", "executive")), c=Depends(container)):
    return {"agents": _roster(c, user, _projects(c, user, project_id)), "edges": R.graph_edges(c.graph)}  # the real graph


@router.get("/api/agents/runs")
def agent_runs(project_id: str | None = None, agent: str | None = Query(None, max_length=40), limit: int = Query(50, ge=1, le=200),
               user: User = Depends(require("manager")), c=Depends(container)):
    if agent and agent not in R.AGENTS:
        raise HTTPException(422, f"Unknown agent; one of {', '.join(R.AGENTS)}")
    rows = _runs(c, _projects(c, user, project_id))  # newest first by time across projects, then filtered and cut
    out = []
    for r in rows:
        key = R.agent_key(r.get("agent", ""))
        if agent and agent != "supervisor" and key != agent:
            continue
        out.append({"id": str(r["id"]), "agent": key, "agent_name": R.AGENTS[key].name if key in R.AGENTS else key,
                    "trigger": r.get("trigger"), "status": r.get("status"), "at": _at(r.get("at")), "summary": R.run_summary(r),
                    "trace_id": r.get("trace_id"), "visibility": _visibility(r, _detail(r))})
        if len(out) >= limit:
            break
    return {"runs": out}


def _detail(r: dict) -> dict:
    detail = r.get("detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = {}
    return detail if isinstance(detail, dict) else {}


def _visibility(r: dict, detail: dict) -> str:
    """"private" for the hire's private review (whatever its record says), else what the run recorded, else "shared"."""
    return "private" if detail.get("visibility") == "private" or r.get("trigger") == "presubmit.requested" else "shared"


@router.get("/api/agents/runs/{run_id}")
def agent_run(run_id: str = Path(max_length=64), user: User = Depends(current_user), c=Depends(container)):
    """One run with its trace. The asker reads their own run; the project's managers (and admins) read any run of it except
    a private one (the hire's pre-submit review), which only its own user reads. Anyone else: 404, as if there were none.
    The steps are the shared trace the run record keeps: lines our code wrote and the sources found, never the model's words,
    the arguments it chose or a new hire's question."""
    r = c.store.get_run(run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    detail = _detail(r)
    pid = str(r.get("project_id") or "")
    if pid and c.store.org_of_project(pid) != user.org_id:
        raise HTTPException(404, "Run not found")
    mine = bool(detail.get("user_id")) and str(detail.get("user_id")) == user.id
    private = _visibility(r, detail) == "private"
    manager = pid and (user.role == "admin" or (user.role == "manager" and c.store.is_member(pid, user.id)))
    if not (mine or (manager and not private)):
        raise HTTPException(404, "Run not found")
    key = R.agent_key(r.get("agent", ""))
    run = {"id": str(r["id"]), "agent": key, "agent_name": R.AGENTS[key].name if key in R.AGENTS else key, "project_id": pid or None,
           "trigger": r.get("trigger"), "status": r.get("status"), "at": _at(r.get("at")), "summary": R.run_summary(r),
           "trace_id": r.get("trace_id"), "visibility": _visibility(r, detail), "mine": mine, "agentic": bool(detail.get("agentic")),
           **{k: detail.get(k) for k in ("stopped", "turns", "tool_calls", "ms")}}
    return {"run": run, "steps": list(detail.get("steps") or [])}


class ModeIn(BaseModel):
    mode: Literal["ai", "rules"]


@router.patch("/api/agents/{agent_key}")
def set_mode(agent_key: str, body: ModeIn, project_id: str | None = None, user: User = Depends(require("admin")), c=Depends(container)):
    a = R.AGENTS.get(agent_key)
    if not a:
        raise HTTPException(404, "No such agent")
    if not a.switchable:
        raise HTTPException(409, f"The {a.name} writes no text: its mode cannot be changed")
    before = R.mode(c.deps, user.org_id, agent_key)
    if before != body.mode:
        modes = {**R.modes(c.deps, user.org_id), agent_key: body.mode}
        c.lifecycle.update_org_settings(user.org_id, agent_modes=modes)
        c.store.audit(user.org_id, user.id, "agent.mode_changed", agent_key, {"from": before, "to": body.mode})
    return _roster(c, user, _projects(c, user, project_id), only=agent_key)[0]


@router.post("/api/agents/tick")
def tick_now(project_id: str | None = None, user: User = Depends(require("manager")), c=Depends(container)):
    """Run the daily tick now (demo and self-hosted; on Google Cloud, Cloud Scheduler runs it). Forced: it runs even if today's
    tick ran, and its rules make a second run change nothing that the first one did."""
    if c.settings.mode == "gcp":
        raise HTTPException(404, "Not Found")
    if project_id:
        check_project(c, user, project_id)
    out = c.tick(user.org_id, project_id, force=True)
    c.store.audit(user.org_id, user.id, "agents.tick", project_id or user.org_id, {"ran": out["ran"], "proposals": out["proposals"]})
    if out.get("error"):
        raise HTTPException(503, out["error"])
    return {"ran": out["ran"], "proposals": out["proposals"], "alerts": out["alerts"]}


@router.post("/internal/lifecycle/tick")
def scheduled_tick(authorization: str | None = Header(default=None), c=Depends(container)):
    """Cloud Scheduler, with a Google-signed OIDC token for its service account (exactly like /internal/outbox/flush); off
    without that setup. Every organisation with a program in progress, once per day each, whoever calls first."""
    from app.agents import tick
    from app.api.outbox import _scheduler_only

    _scheduler_only(authorization, c)
    done = tick.run_all(c)
    return {"organisations": len(done), "ran": sum(1 for d in done if d.get("ran")),
            "skipped": sum(1 for d in done if d.get("skipped")), "failed": sum(1 for d in done if d.get("error"))}
