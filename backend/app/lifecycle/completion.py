"""Completion review: the evidence a manager signs a program off on. Each criterion says whether it is met and why; the
manager decides (complete, or extend by some days) and may sign off with criteria unmet, which the record keeps. A
completed program completes the onboarding (status 'completed'), so it leaves the active lists and the agents' plans."""
from __future__ import annotations

from app.lifecycle import progress
from app.lifecycle import templates as T
from app.lifecycle.context import HireCtx


def criteria(hc: HireCtx) -> list[dict]:
    ms = hc.milestones
    open_ms = [m.title for m in ms if m.status == "pending"]
    last = T.checkin_days(hc.program.duration_days)[-1]
    roles = {c.author_role for c in hc.checkins if c.day == last}
    alerts = hc.ctx.lc.blockers(hc.ob.id, open_only=True)
    levels = {a["area"]: a["level"] for a in progress.areas(hc)}
    contributed = [a for a, lvl in levels.items() if lvl == "contributed"]
    waiting = [a.system for a in hc.access if a.status == "requested"]
    return [
        {"key": "milestones", "label": "Every milestone done or skipped", "met": not open_ms,
         "evidence": {"open": open_ms}, "why": f"{len(open_ms)} still open." if open_ms else "All done or skipped."},
        {"key": "final_checkin", "label": f"Day-{last} check-in by hire and manager", "met": roles >= {"hire", "manager"},
         "evidence": {"written_by": sorted(roles)}, "why": f"Written by {', '.join(sorted(roles)) or 'nobody yet'}."},
        {"key": "no_blockers", "label": "No open blockers", "met": not alerts,
         "evidence": {"open": [a.id for a in alerts]}, "why": f"{len(alerts)} open." if alerts else "None open."},
        {"key": "contribution", "label": "A merged change in at least one area", "met": bool(contributed),
         "evidence": {"areas": contributed},
         "why": f"Contributed in {', '.join(contributed)}." if contributed else "No merged change yet."},
        {"key": "access", "label": "Access requests settled", "met": not waiting,
         "evidence": {"waiting": waiting}, "why": f"Waiting: {', '.join(waiting)}." if waiting else "All granted or not needed."},
    ]


def review(hc: HireCtx) -> dict:
    rows = criteria(hc)
    return {"day": hc.day, "duration_days": hc.program.duration_days, "criteria": rows, "ready": all(r["met"] for r in rows),
            "signed_off": hc.program.completion, "completed_at": hc.program.completed_at}


def sign_off(hc: HireCtx, decision: str, note: str, extend_days: int) -> dict:
    ctx, now = hc.ctx, hc.ctx.clock.now()
    snapshot = criteria(hc)
    record = {"decision": decision, "note": note[:2000], "by": ctx.user.id, "at": now.isoformat(),
              "unmet": [c["key"] for c in snapshot if not c["met"]]}
    if decision == "complete":
        ctx.lc.update_program(hc.ob.id, completed_at=now, completion=record)
        ctx.lc.set_onboarding_status(hc.ob.id, "completed")
        ctx.store.notify(hc.ob.new_hire_id, "completion", "Your onboarding program is complete. Welcome aboard.",
                         f"/program/{hc.ob.id}?project={hc.ob.project_id}")
    else:
        record["extend_days"] = extend_days
        ctx.lc.update_program(hc.ob.id, duration_days=hc.program.duration_days + extend_days, completion=record)
        ctx.store.notify(hc.ob.new_hire_id, "completion", f"Your program was extended by {extend_days} days.",
                         f"/program/{hc.ob.id}?project={hc.ob.project_id}")
    return record
