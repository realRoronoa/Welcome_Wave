"""Blocker detection. Each rule fires on evidence and says what it saw:

  repeated_topic     the same topic (the module of the first code source of an answer, or of a routed question's best
                     match) asked about 3 or more times within 5 days; the evidence counts the questions, never quotes them
  stalled_task       a task in progress with no activity for 3 business days
  failing_ci         3 or more failed CI runs on the hire's pull request within 48 hours
  environment        the environment check failing for more than 24 hours
  unresolved_review  review findings (needs work, not approved) unresolved for more than 3 days

An alert stays open while its condition holds and closes itself (resolved_by 'system') when it clears; anyone it was
shown to can resolve it, and it then stays quiet until its condition clears once (a recurrence opens a new alert).
Escalation, counted in business days since it opened: the hire on day 0, the mentor after 1 unresolved business day, the
manager after 2 more (no mentor: the manager). Each stage is told once, by notification.

Detection runs when the hire's blockers, program or a home page is read, and when the demo clock moves: it needs no
scheduler, and an alert is never older than the last time anyone looked."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from app.lifecycle.clock import business_days
from app.lifecycle.context import HireCtx
from app.ports import BlockerAlert
from app.services.chunking import module_of

TOPIC_REPEATS, TOPIC_WINDOW_DAYS = 3, 5
STALLED_BUSINESS_DAYS = 3
CI_FAILURES, CI_WINDOW_H = 3, 48
ENV_FAILING_H = 24
REVIEW_DAYS = 3
MENTOR_AFTER, MANAGER_AFTER = 1, 3  # business days unresolved
STAGES = ("hire", "mentor", "manager")
COACH_HOLD_DAYS = 2  # a Coach decision caps an alert's stage this long (the next daily tick renews it; then the rules again)
FAILED = frozenset({"failure", "timed_out"})
OPEN_TASK = frozenset({"in_progress"})
TEXT = {
    "repeated_topic": "Asked about {subject} {n} times in {days} days",
    "stalled_task": "No activity on '{title}' for {n}",
    "failing_ci": "{n} failed CI runs on PR #{pr} in {hours} hours",
    "environment": "The environment check has failed for {n}{tools}",
    "unresolved_review": "Review findings on '{title}' unresolved for {n}",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _at(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _repeated_topic(hc: HireCtx, now: datetime) -> dict[str, dict]:
    since = now - timedelta(days=TOPIC_WINDOW_DAYS)
    asked: dict[str, list[dict]] = defaultdict(list)
    for turn in hc.chats:
        at = _at(turn.get("created_at"))
        code = [c for c in turn.get("citations", []) if c.get("source_type") == "code" and c.get("ref", {}).get("path")]
        if at and at >= since and code:
            asked[module_of(code[0]["ref"]["path"])].append({"message_id": turn.get("message_id"), "asked_at": at.isoformat()})
    counted = {q["message_id"] for qs in asked.values() for q in qs}
    for m in hc.ctx.lc.metrics(hc.program.org_id, ["route.asked"], since):
        if m.subject_id == hc.ob.new_hire_id and m.props.get("module") and m.props.get("message_id") not in counted:
            asked[m.props["module"]].append({"message_id": m.props.get("message_id"), "asked_at": m.at.isoformat()})
    return {mod: {"module": mod, "questions": qs, "window_days": TOPIC_WINDOW_DAYS, "n": len(qs)}
            for mod, qs in asked.items() if len(qs) >= TOPIC_REPEATS}


def _stalled(hc: HireCtx, now: datetime) -> dict[str, dict]:
    open_ = [t for t in hc.tasks if t.status in OPEN_TASK]
    activity = hc.ctx.lc.task_activity([t.id for t in open_])
    out = {}
    for t in open_:
        last = activity.get(t.id)
        n = business_days(last, now) if last else 0
        if n >= STALLED_BUSINESS_DAYS:
            out[t.id] = {"task_id": t.id, "title": t.title, "last_activity": last.isoformat(), "n": n}
    return out


def _failing_ci(hc: HireCtx, now: datetime) -> dict[str, dict]:
    ctx, out = hc.ctx, {}
    with_pr = [t for t in hc.tasks if t.pr_number and t.status not in {"done", "rejected"}]
    if not with_pr:
        return out
    repo = next(iter(ctx.store.repos_for(hc.ob.project_id)), None)
    latest: dict[str, dict] = {}
    for r in ctx.lc.reviews_for_tasks([t.id for t in with_pr]):
        latest[r["task_id"]] = r  # oldest first: the last one is the newest commit reviewed
    for t in with_pr:
        if not repo or t.id not in latest:
            continue
        runs = ctx.deps.repos.check_runs(repo.full_name, latest[t.id]["head_sha"], repo.installation_id)
        since = now - timedelta(hours=CI_WINDOW_H)
        recent = [r for r in runs if r.get("conclusion") in FAILED and (_at(r.get("completed_at")) or now) >= since]
        if len(recent) >= CI_FAILURES:
            shown = [{k: r.get(k) for k in ("name", "conclusion", "completed_at")} for r in recent[:10]]
            out[t.id] = {"task_id": t.id, "pr": t.pr_number, "runs": shown, "n": len(recent)}
    return out


def _environment(hc: HireCtx, now: datetime) -> dict[str, dict]:
    checks = hc.environment_checks
    if not checks or checks[-1].ok:
        return {}
    streak = []
    for c in reversed(checks):
        if c.ok:
            break
        streak.append(c)
    since = streak[-1].ran_at
    hours = (now - since).total_seconds() / 3600
    if hours <= ENV_FAILING_H:
        return {}
    failing = sorted({r["name"] for r in checks[-1].results if not r.get("ok")})
    return {"env": {"failing_since": since.isoformat(), "failing": failing, "checks": len(streak), "hours": int(hours)}}


def _unresolved_review(hc: HireCtx, now: datetime) -> dict[str, dict]:
    open_ = {t.id: t for t in hc.tasks if t.kind == "code" and t.status not in {"done", "rejected"}}
    latest: dict[str, dict] = {}
    for r in hc.ctx.lc.reviews_for_tasks(list(open_)):
        latest[r["task_id"]] = r
    out = {}
    for tid, r in latest.items():
        serious = [f for f in r.get("findings") or [] if f.get("severity") in {"blocker", "major"}]
        at = _at(r.get("created_at"))
        if r.get("recommendation") == "needs_work" and r.get("decision") != "approved" and serious and at:
            days = (now - at).days
            if days > REVIEW_DAYS:
                out[tid] = {"task_id": tid, "title": open_[tid].title, "review_id": r["id"], "findings": len(serious),
                            "since": at.isoformat(), "n": days}
    return out


RULES = {"repeated_topic": _repeated_topic, "stalled_task": _stalled, "failing_ci": _failing_ci, "environment": _environment,
         "unresolved_review": _unresolved_review}


def stage_for(opened_at: datetime, now: datetime) -> str:
    n = business_days(opened_at, now)
    return "manager" if n >= MANAGER_AFTER else "mentor" if n >= MENTOR_AFTER else "hire"


def describe(alert: BlockerAlert, now: datetime | None = None) -> str:
    """The alert in words, its durations counted when it is read (until `now`, the lifecycle clock's, or until it was
    resolved), in days: a stored count would freeze."""
    from datetime import timezone

    e = alert.evidence
    until = alert.resolved_at or now or datetime.now(timezone.utc)
    n = e.get("n", "")
    if alert.rule == "environment":
        since = _at(e.get("failing_since"))
        n = _plural(max(1, (until - since).days), "day") if since else _plural(max(1, int(e.get("hours") or 24) // 24), "day")
    elif alert.rule == "stalled_task":
        last = _at(e.get("last_activity"))
        n = _plural(business_days(last, until) if last else int(e.get("n") or 0), "business day")
    elif alert.rule == "unresolved_review":
        since = _at(e.get("since"))
        n = _plural((until - since).days if since else int(e.get("n") or 0), "day")
    return TEXT[alert.rule].format(subject=e.get("module", alert.subject), title=e.get("title", ""), n=n,
                                   days=e.get("window_days", ""), pr=e.get("pr", ""), hours=e.get("hours", CI_WINDOW_H),
                                   tools=f" ({', '.join(e['failing'])})" if e.get("failing") else "")


def coach_caps(hc: HireCtx, now: datetime) -> dict[str, str]:
    """The Coach's latest decision per alert of this hire, made in the last COACH_HOLD_DAYS (metric coach.decision,
    app/agents/specialists/coach/agent.py): the highest stage the alert may reach. The rules still decide the delays; the
    Coach decides when."""
    rows = [m for m in hc.ctx.lc.metrics(hc.program.org_id, ["coach.decision"], now - timedelta(days=COACH_HOLD_DAYS))
            if m.subject_id == hc.ob.id]
    return {m.props.get("blocker_id"): m.props.get("cap") for m in sorted(rows, key=lambda m: m.at)}


def capped(stage: str, cap: str | None, told: str | None) -> str:
    """The stage an alert reaches: the rules' (`stage`, after the business-day delays), at most the Coach's `cap`, and never
    below the stage already told (nobody is "untold")."""
    if cap not in STAGES:
        return stage
    floor = STAGES.index(told) if told in STAGES else 0
    return STAGES[max(floor, min(STAGES.index(stage), STAGES.index(cap)))]


def detect(hc: HireCtx) -> list[BlockerAlert]:
    """Open, refresh, escalate and close this hire's alerts. Returns the open ones."""
    ctx, lc = hc.ctx, hc.ctx.lc
    if hc.ob.status != "active":
        return lc.blockers(hc.ob.id, open_only=True)
    now = ctx.clock.now()
    found = {(rule, subject): ev for rule, fn in RULES.items() for subject, ev in fn(hc, now).items()}
    every = lc.blockers(hc.ob.id)
    # A person resolved it while the condition still held: it stays quiet until the condition clears once.
    quiet = {(a.rule, a.subject): a for a in every if a.resolved_by not in (None, "system") and not a.evidence.get("cleared")}
    for key, alert in quiet.items():
        if key not in found:
            lc.update_blocker(alert.id, evidence={**alert.evidence, "cleared": True})
    for alert in every:
        if alert.resolved_at is None and (alert.rule, alert.subject) not in found:
            lc.update_blocker(alert.id, resolved_at=now, resolved_by="system")
    caps = coach_caps(hc, now) if found else {}
    for (rule, subject), ev in found.items():
        if (rule, subject) in quiet:
            continue
        alert = lc.open_blocker(BlockerAlert(id="", onboarding_id=hc.ob.id, rule=rule, subject=subject, evidence=ev, opened_at=now))
        stage = capped(stage_for(alert.opened_at, now), caps.get(alert.id), alert.notified_stage)
        changes = {"evidence": ev} if ev != alert.evidence else {}
        if stage != alert.stage:
            changes["stage"] = stage
        if alert.notified_stage != stage:
            _tell(hc, alert.model_copy(update={"evidence": ev}), stage)
            changes["notified_stage"] = stage
        if changes:
            lc.update_blocker(alert.id, **changes)
    return lc.blockers(hc.ob.id, open_only=True)


def _tell(hc: HireCtx, alert: BlockerAlert, stage: str) -> None:
    ob, store = hc.ob, hc.ctx.store
    who = {"hire": ob.new_hire_id, "mentor": hc.program.mentor_id or ob.manager_id, "manager": ob.manager_id}[stage]
    name = hc.hire.name if hc.hire else "A new hire"
    text = describe(alert, hc.ctx.clock.now())
    msg = f"Possible blocker: {text}." if stage == "hire" else f"{name} may be blocked: {text}."
    store.notify(who, "blocker", msg, f"/program/{ob.id}?tab=blockers&project={ob.project_id}")


def view(hc: HireCtx, alerts: list[BlockerAlert]) -> list[dict]:
    from app.agents.specialists.coach.agent import blocker_explain, decisions  # the Coach runs these rules

    now = hc.ctx.clock.now()
    judged = decisions(hc) if alerts else {}
    out = []
    for a in alerts:
        why = [describe(a, now) + ".", {"hire": "Shown to the hire first.",
                                   "mentor": "Unresolved for a business day: the mentor was told.",
                                   "manager": "Unresolved for three business days: the manager was told."}[a.stage]]
        d = judged.get(a.id)
        if d and d.get("reason"):
            why.append(("The Coach judged it blocking: " if d.get("blocking") else "The Coach judged it not blocking: ") + d["reason"])
        out.append({"id": a.id, "rule": a.rule, "subject": a.subject, "text": describe(a, now), "stage": a.stage, "opened_at": a.opened_at,
                    "business_days_open": business_days(a.opened_at, now), "resolved_at": a.resolved_at, "evidence": a.evidence,
                    "why": why, "explain": blocker_explain(a, why)})
    return out


def resolve(hc: HireCtx, alert: BlockerAlert, user_id: str) -> BlockerAlert | None:
    return hc.ctx.lc.update_blocker(alert.id, resolved_at=hc.ctx.clock.now(), resolved_by=user_id)
