"""The daily tick: `lifecycle.tick` once per organisation and day.

Who sends it: the self-hosted background loop (app/selfhost.py, TickLoop), Cloud Scheduler on Google Cloud
(POST /internal/lifecycle/tick, guarded like the outbox flush), POST /api/demo/advance (one tick for each day the demo clock
crosses) and POST /api/agents/tick (managers: "run it now").

Idempotent per (organisation, day): the event's dedupe key is `tick:<org>:<day>`, claimed through the same machinery as
every other event (Container.handle), so the loop, the scheduler and the demo clock run each day once whoever calls first.
A forced tick (the API's "run it now") skips the claim; the rules it runs are idempotent themselves (one nudge per alert,
one summary per week, a completion draft per state of the criteria). The tick never pauses: nothing it runs needs an
approval."""
from __future__ import annotations

import uuid
from datetime import date


def key(org_id: str, day: date, project_id: str | None = None) -> str:
    return f"tick:{org_id}:{day.isoformat()}" + (f":{project_id}" if project_id else "")


def today(c, org_id: str) -> date:
    from app.lifecycle.clock import for_org

    return for_org(c, org_id).today()


def run(c, org_id: str, project_id: str | None = None, day: date | None = None, force: bool = False) -> dict:
    """{ran: [agent keys], proposals, alerts} for the organisation's tick of `day` (its lifecycle today by default); a tick
    already done that day answers {ran: [], proposals: 0, alerts: 0, skipped: "duplicate"}."""
    day = day or today(c, org_id)
    event = {"type": "lifecycle.tick", "org_id": org_id, "day": day.isoformat(), **({"project_id": project_id} if project_id else {})}
    if force:
        out = c._run(event, f"tick:{uuid.uuid4().hex}")
    else:
        out = c.handle({**event, "dedupe": key(org_id, day, project_id)})
    if out.get("skipped"):
        return {"ran": [], "proposals": 0, "alerts": 0, "skipped": out["skipped"], "day": day.isoformat()}
    result = out.get("result") or {}
    if result.get("escalated"):
        return {"ran": [], "proposals": 0, "alerts": 0, "error": "The daily tick failed; the managers were told.", "day": day.isoformat()}
    return {"ran": result.get("ran", []), "proposals": result.get("proposals", 0), "alerts": result.get("alerts", 0), "day": day.isoformat()}


def _set_tenant(org_id: str | None) -> None:
    try:
        from app.adapters.postgres import current_org

        current_org.set(org_id)  # Postgres: everything after this runs under row-level security for this organisation
    except ImportError:
        pass


def run_all(c) -> list[dict]:
    """Every organisation with a program in progress (lifecycle_orgs, across tenants), each under its own tenant."""
    out = []
    _set_tenant(None)
    for org in c.lifecycle.lifecycle_orgs():
        _set_tenant(org)
        try:
            out.append({"org_id": org, **run(c, org)})
        except Exception as e:  # noqa: BLE001 - one organisation's failure must not stop the others' ticks
            out.append({"org_id": org, "error": type(e).__name__})
        finally:
            _set_tenant(None)
    return out
