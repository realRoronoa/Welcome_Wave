"""The demo clock's people (MODE=demo only): what the seeded hires, their mentors and their manager plausibly do on a day the
demo clock crosses, so jumping ahead shows programs moving the way they do, not an organisation that stopped working.

POST /api/demo/advance runs `day()` for each day it crosses, before that day's tick (app/agents/supervisor/tick.py). For the seeded
hires only (people enrolled during the demo are left to the person driving it), on their program day:
  - documents are acknowledged and access granted in the first days; a failing environment check is fixed the next day;
  - learn and meet tasks are done by their due date, and a task in progress shows activity;
  - on day 8 the manager selects the Scout's top first task (when the hire has no code task), which opens its pull request on
    day 14 and is merged on day 21 (a merge, as GitHub would send it);
  - milestones a person marks are marked on their day; the auto ones follow from that evidence on the tick;
  - check-ins (both halves) and the anonymous survey are written three days after their day (on time: overdue is a week);
  - the day after a program's last day, a program whose every completion criterion is met is signed off (a seeded sign-off);
    otherwise it waits in the manager's completion review with the Planner's draft.
Nothing here decides an approval: a plan waiting for the manager keeps waiting."""
from __future__ import annotations

import hashlib
from datetime import date

from app.agents.core.schemas import ProposedTask
from app.lifecycle import checkins as CI
from app.lifecycle import completion, first_task, hires, preboarding
from app.lifecycle import templates as T
from app.lifecycle.clock import Clock
from app.lifecycle.context import Ctx
from app.ports import CheckinReview, EnvironmentCheck

SEEDED = frozenset({"ob-vijay", "ob-arjun", "ob-sara", "ob-tom"})
MANAGER = "u-priya"
FIRST_TASK_DAY, PR_DAY, MERGE_DAY = 8, 14, 21
ANSWER_AFTER = 3  # days after a check-in day that the simulated people write it (and the survey): on time, with room to do it yourself
HIRE_ANSWERS = {"going_well": "The KT clips and the code tour.", "hardest": "Knowing who owns what.", "need": "Review time.",
                "confidence": 4}
MANAGER_ANSWERS = {"strengths": "Careful, asks good questions.", "focus_next": "Own a small area end to end.",
                   "support": "Pairing with the mentor weekly.", "on_track": 4}
SURVEY_ANSWERS = {"clarity": 4, "support": 4, "docs": 4, "tools": 4, "belonging": 4}


def day(c, org_id: str, when: date) -> list[str]:
    """Simulate `when` for every seeded active program. Returns what happened, one line each (for logs and tests)."""
    clock = Clock((when - date.today()).days)
    ctx = Ctx(c, clock, c.store.get_user(MANAGER))
    done = []
    for hc in hires.of_projects(ctx, sorted({p.project_id for p in c.lifecycle.programs(org_id)})):
        if hc.ob.id in SEEDED and hc.ob.status == "active" and hc.day >= 0:
            done += [f"{hc.ob.id}: {x}" for x in _hire_day(c, hc)]
    return done


def _hire_day(c, hc) -> list[str]:
    ctx, lc, store = hc.ctx, hc.ctx.lc, hc.ctx.store
    now, d, out = ctx.clock.now(), hc.day, []
    for a in hc.acknowledgements:
        if not a.acknowledged_at:
            lc.acknowledge(hc.ob.id, a.doc_key, "", now)
            out.append(f"acknowledged {a.doc_key}")
    if d >= 2:
        for a in hc.access:
            if a.status == "requested":
                lc.update_access_item(a.id, status="granted", granted_at=now)
                out.append(f"granted {a.system}")
    last = hc.environment_checks[-1] if hc.environment_checks else None
    if (last and not last.ok) or (last is None and d >= 1):
        results = [{"name": n, "ok": True, "detail": "found"} for n, _ in preboarding.tools_for(hc)]
        lc.add_environment_check(EnvironmentCheck(id="", onboarding_id=hc.ob.id, ran_at=now, ok=True, results=results))
        out.append("environment check passes")
    out += _tasks(c, hc, now)
    for m in hc.milestones:
        if m.status == "pending" and m.key not in T.AUTO_MILESTONES and m.due_day <= d:
            lc.update_milestone(hc.ob.id, m.key, status="done", done_at=now, evidence={"by": MANAGER, "note": "Simulated by the demo clock"})
            out.append(f"milestone {m.key}")
    for k in T.checkin_days(hc.program.duration_days):
        if d < k + ANSWER_AFTER:  # written a few days after the check-in day: on time (overdue only a week after it)
            continue
        have = {(x.day, x.author_role) for x in lc.checkins(hc.ob.id)}
        for role, author, answers in (("hire", hc.ob.new_hire_id, HIRE_ANSWERS), ("manager", MANAGER, MANAGER_ANSWERS)):
            if (k, role) not in have:
                lc.save_checkin(CheckinReview(id="", onboarding_id=hc.ob.id, day=k, author_role=role, author_id=author,
                                              answers=CI.clean_answers(role, answers), created_at=now))
                out.append(f"day-{k} check-in ({role})")
        program = lc.get_program(hc.ob.id)
        if k not in program.survey_days:
            hc.program = program
            CI.submit_survey(hc, k, SURVEY_ANSWERS)
            out.append(f"day-{k} survey")
    hc.forget("milestones", "checkins", "tasks", "access", "acknowledgements", "environment_checks")
    hc.program = lc.get_program(hc.ob.id)
    if d > hc.program.duration_days and not hc.program.completed_at:
        hires.refresh(hc)
        hc.forget("milestones")
        if all(r["met"] for r in completion.criteria(hc)):
            completion.sign_off(hc, "complete", "Signed off at the completion review (simulated by the demo clock).", 0)
            store.audit(hc.program.org_id, MANAGER, "completion.complete", hc.ob.id, {"simulated": True, "unmet": []})
            c.deps.bus.publish("reviews", {"type": "program.completed", "project_id": hc.ob.project_id, "onboarding_id": hc.ob.id,
                                           "dedupe": f"completed:{hc.ob.id}"})
            out.append("completed")
    return out


def _tasks(c, hc, now) -> list[str]:
    store, lc, d, out = hc.ctx.store, hc.ctx.lc, hc.day, []
    touched = getattr(store, "task_created", None)  # the memory store's stand-in for a task's last activity
    for t in hc.tasks:
        due = (t.due_date - hc.ob.start_date).days if t.due_date else d
        if t.kind in {"learn", "meet"} and t.status in {"approved", "in_progress"} and due <= d:
            store.update_task(t.id, status="done")
            out.append(f"done: {t.title}")
        elif t.status == "in_progress" and touched is not None:
            touched[t.id] = now  # the hire is working on it
    code = [t for t in hc.tasks if t.kind == "code" and t.status not in {"rejected", "proposed"}]  # a proposal is the manager's
    if not code and d >= FIRST_TASK_DAY:
        cand = next(iter(first_task.shortlist(hc, limit=None)["candidates"]), None)
        if cand:  # the manager's selection; the Scout writes its brief, as a node of the Supervisor's graph
            brief = c.handle({"type": "first_task.requested", "project_id": hc.ob.project_id, "onboarding_id": hc.ob.id,
                              "user_id": MANAGER, "candidate_id": cand["id"]})["result"]
            if brief.get("escalated") or brief.get("error"):
                return out
            [row] = store.create_tasks(hc.ob.id, [ProposedTask(title=f"First PR: {cand['title']}", description=" ".join(cand["why"]),
                                                               kind="code", due_in_days=PR_DAY, related_paths=cand["files"],
                                                               source="first_pr", issue_number=cand.get("issue"))],
                                       hc.ob.start_date)
            store.update_task(row.id, status="approved")
            lc.update_program(hc.ob.id, first_task={**brief, "task_id": row.id, "selected_by": MANAGER, "selected_at": now.isoformat()})
            out.append(f"first task selected: {cand['title']}")
            hc.forget("tasks")
            code = [store.get_task(row.id)]
    for t in code:
        if t.status in {"approved", "in_progress", "changes_requested"} and d >= PR_DAY:
            pr = t.pr_number or 500 + int(hashlib.sha1(t.id.encode()).hexdigest(), 16) % 400
            store.update_task(t.id, status="submitted", pr_number=pr, pr_url=f"https://github.com/demo/mini-policy-engine/pull/{pr}")
            out.append(f"opened PR #{pr}")
        elif t.status == "submitted" and t.pr_number and d >= MERGE_DAY:
            store.update_task(t.id, status="done")
            lc.record_metric(hc.program.org_id, hc.ob.project_id, "pr.merged", t.id, {"pr": t.pr_number, "simulated": True}, now)
            out.append(f"merged PR #{t.pr_number}")
    hc.forget("tasks")
    return out

