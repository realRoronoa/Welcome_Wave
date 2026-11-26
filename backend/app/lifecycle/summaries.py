"""Weekly progress summaries. Written from the records themselves, never by a model: every line cites what it says
(a task, a milestone, a blocker, an area's evidence, a check-in), so the hire and the manager can open the source of each
statement, and the summary works with the AI off. A week runs Monday to Sunday on the lifecycle clock; writing the
same week again replaces it. Questions are counted, never quoted: a hire's chat stays theirs."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.lifecycle import blockers as B
from app.lifecycle import progress
from app.lifecycle.clock import week_start
from app.lifecycle.context import HireCtx
from app.ports import ProgressSummary


def _in_week(at, start: date) -> bool:
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at)
        except ValueError:
            return False
    d = at.date() if isinstance(at, datetime) else at
    return d is not None and start <= d < start + timedelta(days=7)


def build(hc: HireCtx, start: date) -> dict:
    ctx, lines = hc.ctx, []
    activity = ctx.lc.task_activity([t.id for t in hc.tasks])
    done = [t for t in hc.tasks if t.status == "done" and t.id in activity and _in_week(activity[t.id], start)]
    if done:
        lines.append({"text": f"Finished {len(done)} task{'s' if len(done) != 1 else ''}: {', '.join(t.title for t in done[:5])}.",
                      "citations": [{"kind": "task", "ref": t.id, "label": t.title} for t in done]})
    reached = [m for m in hc.milestones if m.status == "done" and m.done_at and _in_week(m.done_at, start)]
    if reached:
        lines.append({"text": f"Reached {len(reached)} milestone{'s' if len(reached) != 1 else ''}: {', '.join(m.title for m in reached)}.",
                      "citations": [{"kind": "milestone", "ref": m.key, "label": m.title} for m in reached]})
    areas = progress.areas(hc)
    active = [a for a in areas if a["level"] != "not_started"]
    if active:
        lines.append({"text": "Areas: " + "; ".join(f"{a['area']} {a['level'].replace('_', ' ')}" for a in active) + ".",
                      "citations": [{"kind": "area", "ref": a["area"], "label": a["why"][0]} for a in active]})
    asked = [t for t in hc.chats if _in_week(t.get("created_at"), start)]
    if asked:
        answered = sum(t.get("status") == "answered" for t in asked)
        lines.append({"text": f"Asked {len(asked)} question{'s' if len(asked) != 1 else ''}; {answered} answered from sources.",
                      "citations": [{"kind": "chat", "ref": t.get("message_id") or t.get("id"), "label": "question"} for t in asked]})
    alerts = ctx.lc.blockers(hc.ob.id)
    opened = [a for a in alerts if _in_week(a.opened_at, start)]
    closed = [a for a in alerts if a.resolved_at and _in_week(a.resolved_at, start)]
    if opened or closed:
        lines.append({"text": f"Blockers: {len(opened)} opened, {len(closed)} resolved.",
                      "citations": [{"kind": "blocker", "ref": a.id,
                                     "label": B.describe(a, ctx.clock.now())} for a in {a.id: a for a in opened + closed}.values()]})
    checkins = [c for c in hc.checkins if _in_week(c.created_at, start)]
    if checkins:
        lines.append({"text": "Check-in written: " + ", ".join(f"day {c.day} ({c.author_role})" for c in checkins) + ".",
                      "citations": [{"kind": "checkin", "ref": c.id, "label": f"day {c.day}"} for c in checkins]})
    end = start + timedelta(days=7)
    upcoming = [m for m in progress.milestone_view(hc) if m["status"] == "pending" and end <= m["due_date"] < end + timedelta(days=7)]
    if upcoming:
        lines.append({"text": "Next week: " + ", ".join(m["title"] for m in upcoming) + ".",
                      "citations": [{"kind": "milestone", "ref": m["key"], "label": m["title"]} for m in upcoming]})
    if not lines:
        lines.append({"text": "Nothing recorded this week.", "citations": []})
    return {"week_start": start.isoformat(), "day": hc.day, "lines": lines,
            "why": ["Every line is counted from the records it cites; nothing is written by a model."]}


def write(hc: HireCtx) -> ProgressSummary:
    start = week_start(hc.ctx.clock.today())
    return hc.ctx.lc.save_summary(ProgressSummary(id="", onboarding_id=hc.ob.id, week_start=start, body=build(hc, start),
                                                  created_at=hc.ctx.clock.now()))
