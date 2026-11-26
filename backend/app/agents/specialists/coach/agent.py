"""The Coach: decides whether a new hire is stuck, nudges kindly and writes the weekly summary.

It thinks (app/agents/core/thinking.py). On the daily tick (`lifecycle.tick`), per active hire: the program is brought up to date
(auto milestones, and the blocker rules of app/lifecycle/blockers.py open their alerts), then the Coach reads `hire_signals`
(question topics counted, never quoted; task activity; CI runs; the environment check; review findings; the open alerts with
the stage each may reach now; milestones), looks closer where it helps (program, kt_moments, search_knowledge) and decides:
whether the hire is stuck, why, the next step, a kind private nudge, and for each open alert whether it is really blocking and
who should know now. It may judge that an alert the rules opened is not a blocker (with its reason), and flag a concern the
rules missed, tied to a signal.

Guardrails, in code: alert ids must be this hire's open alerts and concerns must cite a signal hire_signals returned; at most
one nudge per hire and tick, to the hire only, through the checked writer (no link, address or number the signals lack); the
stage is a cap the rules honour (app/lifecycle/blockers.detect): a mentor or manager is told only after the business-day delay,
in the order hire, mentor, manager, and never "untold"; an alert judged blocking that the nudge does not cover still gets the
rules' nudge; hire_signals is the Coach's alone and sensitive (its sources never reach a shared trace). Each hire is assessed
once a day (metric `coach.assessed`), so a forced tick repeats nothing.

Weekly (`week.closed`): the summary of each closed week, for every active hire without one: the counted lines of
app/lifecycle/summaries.py (each citing its records), plus an overview the Coach writes from those lines, citing them [n].
POST /api/onboardings/{id}/summaries writes the current week the same way.

Fallback: with no loop (the AI off, the offline demo, "rules" mode, the daily budget) or no answer, the rules: one nudge per
new alert in the rule's words (checked writer), escalation by business days, and the checked writer's overview."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.agents.core import prompts
from app.agents.core import registry as R

KEY = "coach"
NUDGE_MAX = 280
STAGE_WHY = {"hire": "Shown to the hire first.", "mentor": "Unresolved for a business day: the mentor was told.",
             "manager": "Unresolved for three business days: the manager was told."}


# ---------------------------------------------------------------- blockers
def blocker_explain(alert, why: list[str]) -> dict:
    """The explain block of one blocker alert (app/lifecycle/blockers.view): the rule that fired and what it saw."""
    ev = alert.evidence or {}
    src = {"repeated_topic": lambda: [R.source(f"{ev.get('n')} questions about {ev.get('module')}", "metric", module=ev.get("module"),
                                               questions=len(ev.get("questions") or []))],
           "stalled_task": lambda: [R.source(f"Task: {ev.get('title')}", "event", task_id=ev.get("task_id"))],
           "failing_ci": lambda: [R.source(f"{ev.get('n')} failed CI runs on PR #{ev.get('pr')}", "event", task_id=ev.get("task_id"),
                                           pr=ev.get("pr"))],
           "environment": lambda: [R.source(f"Environment check failing: {', '.join(ev.get('failing') or [])}", "event",
                                            failing_since=ev.get("failing_since"))],
           "unresolved_review": lambda: [R.source(f"Review of {ev.get('title')}", "event", review_id=ev.get("review_id"),
                                                  task_id=ev.get("task_id"))]}
    return R.explain(KEY, why, src.get(alert.rule, list)(), None, False)


def _first(name: str | None) -> str:
    return (name or "there").split(" (")[0].split(" ")[0]


def nudge_draft(alert, describe: str, mentor: str) -> str:
    """A kind next step for each rule, from what the rule saw."""
    e = alert.evidence or {}
    if alert.rule == "environment":
        failing = ", ".join(e.get("failing") or []) or "a tool"
        return (f"Your environment check is failing ({failing}). Fix the first failing tool and run the check again; {mentor} can "
                "pair with you on it.")
    if alert.rule == "repeated_topic":
        return (f"You have asked about {e.get('module')} {e.get('n')} times in {e.get('window_days')} days. A short session with "
                f"{mentor}, or the KT clips for {e.get('module')}, may get you unstuck faster.")
    if alert.rule == "stalled_task":
        return (f"'{e.get('title')}' has had no activity for {e.get('n')} business days. If something is in the way, say so: "
                f"{mentor} can help you split it up.")
    if alert.rule == "failing_ci":
        return (f"CI failed {e.get('n')} times on PR #{e.get('pr')}. Run the tests locally before the next push; {mentor} can look at "
                "the logs with you.")
    if alert.rule == "unresolved_review":
        return (f"The review findings on '{e.get('title')}' have been open for {e.get('n')} days. Reply to the first one on the pull "
                f"request; {mentor} can help.")
    return f"{describe}. {mentor} can help."


def _nudge(deps, hc, alert, describe: str, trace: str | None) -> bool:
    mentor_id = hc.program.mentor_id or hc.ob.manager_id
    mentor = (hc.ctx.person(mentor_id) or {}).get("name") or "your mentor"
    mentor = mentor.split(" (")[0]
    draft = nudge_draft(alert, describe, mentor)
    evidence = {k: v for k, v in (alert.evidence or {}).items() if k != "questions"}  # ids and times only, and not even those
    data = {"rule": alert.rule, "what_the_rule_saw": describe, "evidence": evidence, "hire_first_name": _first(hc.hire.name if hc.hire else None),
            "who_can_help": mentor}
    text, ai, _ = R.write(deps, KEY, hc.program.org_id, system=prompts.COACH_NUDGE, data=data, draft=draft, max_len=NUDGE_MAX)
    hc.ctx.store.notify(hc.ob.new_hire_id, "nudge", text, f"/program/{hc.ob.id}?tab=blockers&project={hc.ob.project_id}")
    hc.ctx.lc.record_metric(hc.program.org_id, hc.ob.project_id, "coach.nudge", hc.ob.new_hire_id,
                            {"blocker_id": alert.id, "rule": alert.rule, "ai": ai}, hc.ctx.clock.now())
    return True


def tick(deps, org_id: str, project_id: str | None, day=None, trace: str | None = None) -> dict:
    """lifecycle.tick for one organisation: detection on every active program, then the Coach's assessment of each hire (or, without
    a model, one rules nudge per alert not nudged yet). Returns counts per project (for the run records) and the programs, for the
    Supervisor's next events."""
    from app.lifecycle import blockers as B
    from app.lifecycle import hires

    team = R.programs(deps, org_id, project_id, day)
    nudged = {m.props.get("blocker_id") for m in deps.lifecycle.metrics(org_id, ["coach.nudge"])}
    per: dict[str, dict] = {}
    for hc in team:
        row = per.setdefault(hc.ob.project_id, {"programs": 0, "alerts": 0, "nudges": 0, "assessed": 0})
        row["programs"] += 1
        hires.refresh(hc)
        alerts = deps.lifecycle.blockers(hc.ob.id, open_only=True)
        row["alerts"] += len(alerts)
        today = hc.ctx.clock.today().isoformat()
        if any(m.subject_id == hc.ob.id and m.props.get("day") == today for m in deps.lifecycle.metrics(org_id, ["coach.assessed"])):
            continue  # assessed today already: a forced tick repeats nothing
        done = assess(deps, hc, alerts, nudged, trace)
        if done is not None:
            row["assessed"] += 1
            row["nudges"] += done["nudges"]
            continue
        for alert in alerts:
            if alert.id not in nudged and _nudge(deps, hc, alert, B.describe(alert, hc.ctx.clock.now()), trace):
                row["nudges"] += 1
    return {"projects": per, "programs": [hc.ob.id for hc in team], "alerts": sum(r["alerts"] for r in per.values()),
            "nudges": sum(r["nudges"] for r in per.values())}


# ---------------------------------------------------------------- the Coach's assessment (agent loop)
TOOLS = ("hire_signals", "program", "kt_moments", "search_knowledge", "kt_coverage")
STAGES = ("hire", "mentor", "manager")


def _mentor(hc) -> str:
    mentor_id = hc.program.mentor_id or hc.ob.manager_id
    return ((hc.ctx.person(mentor_id) or {}).get("name") or "your mentor").split(" (")[0]


def assess(deps, hc, alerts: list, nudged: set, trace: str | None = None, user=None, run_id: str | None = None) -> dict | None:
    """The Coach's daily decision for one hire (see the module docstring). None when no loop could run or it gave no answer:
    the rules nudge instead. Records the assessment (metric `coach.assessed`), each alert's decision (`coach.decision`, which
    blockers.detect honours as a cap) and the run (agent `coach`, for the hire: they and the managers read its shared trace)."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.runtime import shared_steps
    from app.agents.core.schemas import CoachAssessment
    from app.agents.tools import REGISTRY
    from app.lifecycle import blockers as B

    org, now = hc.program.org_id, hc.ctx.clock.now()
    task = ("Assess this new hire's onboarding today: are they stuck, why, and what is the next step? Start with hire_signals.\n\n"
            + embed_data({"onboarding_id": hc.ob.id, "hire_first_name": _first(hc.hire.name if hc.hire else None), "day": hc.day,
                          "who_can_help": _mentor(hc), "open_alerts": len(alerts)}))
    t = thinking.think(deps, KEY, org_id=org, project_id=hc.ob.project_id, user=user, system=prompts.COACH_AGENT, task=task,
                       tools=REGISTRY.select(*TOOLS), output=CoachAssessment, run_id=run_id, clock=hc.ctx.clock,
                       notes={"onboarding_id": hc.ob.id})
    if t is None or t.output is None:
        if t is not None:
            t.record(deps, hc.ob.project_id, KEY, "lifecycle.tick", "Assessed a hire: the rules decided", trace, user_id=hc.ob.new_hire_id)
        return None
    a: CoachAssessment = t.output
    by_id = {x.id: x for x in alerts}
    signals = t.ctx.notes.get("signals", set())
    judged: dict[str, dict] = {}
    for j in a.alerts:
        alert = by_id.get(j.alert_id.strip())
        if alert is None:
            t.note("verify", "Dropped a judgement of an alert that is not open", shared="Dropped a judgement that failed a check")
            continue
        allowed = B.stage_for(alert.opened_at, now)
        told = alert.notified_stage if alert.notified_stage in STAGES else "hire"
        cap = STAGES[max(STAGES.index(told), min(STAGES.index(j.stage), STAGES.index(allowed)))]
        if not j.blocking:  # not in the way: hold the escalation where it is
            cap = told
        reason, _ = t.text(j.reason, 300, _mentor(hc))
        judged[alert.id] = {"blocking": j.blocking, "cap": cap, "asked": j.stage, "reason": reason or ""}
        if STAGES.index(j.stage) > STAGES.index(allowed):
            t.note("verify", f"Held the {j.stage} stage: its business-day delay has not passed", shared="Held a stage until its delay passed")
    concerns = []
    for c in a.concerns[:3]:
        why, _ = t.text(c.why, 300)
        if c.signal.strip() in signals and why:
            concerns.append({"signal": c.signal.strip(), "why": why})
        else:
            t.note("verify", "Dropped a concern with no signal behind it", shared="Dropped a concern that failed a check")
    # the nudge: one at most, to the hire only, checked; a blocking alert it does not cover gets the rules' nudge
    want = [x for x in alerts if x.id not in nudged and judged.get(x.id, {"blocking": True})["blocking"]]
    nudges = 0
    text, why_not = (t.text(a.nudge, NUDGE_MAX, _mentor(hc)) if a.nudge.strip() else (None, None))
    if why_not:
        t.note("verify", f"The nudge's wording was not used: {why_not}", shared="The nudge's wording failed a check")
    if text and (want or concerns or a.stuck):
        hc.ctx.store.notify(hc.ob.new_hire_id, "nudge", text, f"/program/{hc.ob.id}?tab=blockers&project={hc.ob.project_id}")
        for x in want or [None]:
            hc.ctx.lc.record_metric(org, hc.ob.project_id, "coach.nudge", hc.ob.new_hire_id,
                                    {"blocker_id": x.id if x else None, "rule": x.rule if x else None, "ai": True, "run_id": t.run_id,
                                     "concern": None if x else (concerns[0]["signal"] if concerns else None)}, now)
        nudges, want = 1, []
    for x in want:  # judged blocking (or not judged at all), not covered: the rules' nudge, in the rule's words
        if _nudge(deps, hc, x, B.describe(x, now), trace):
            nudges += 1
    for aid, d in judged.items():
        hc.ctx.lc.record_metric(org, hc.ob.project_id, "coach.decision", hc.ob.id, {"blocker_id": aid, **d, "run_id": t.run_id}, now)
    hc.ctx.lc.record_metric(org, hc.ob.project_id, "coach.assessed", hc.ob.id,
                            {"day": hc.ctx.clock.today().isoformat(), "stuck": a.stuck, "run_id": t.run_id, "concerns": concerns,
                             "steps": shared_steps(t.steps)}, now)
    if judged:
        B.detect(hc)  # today's decisions apply now (a stage the Coach allows, after its delay; a hold)
    t.note("final", f"{'Stuck' if a.stuck else 'Not stuck'}: {len(judged)} alert{'s' if len(judged) != 1 else ''} judged, "
                    f"{nudges} nudge{'s' if nudges != 1 else ''}", shared=f"Assessed the hire: {nudges} nudge(s)")
    line = (f"Assessed a hire: {'stuck' if a.stuck else 'not stuck'}, {len(judged)} alert(s) judged, {nudges} nudge(s), "
            f"{len(concerns)} concern(s)")
    t.record(deps, hc.ob.project_id, KEY, "lifecycle.tick", line, trace, user_id=hc.ob.new_hire_id)
    return {"nudges": nudges, "run_id": t.run_id, "stuck": a.stuck, "next_step": a.next_step, "judged": judged, "concerns": concerns}


def decisions(hc) -> dict[str, dict]:
    """The Coach's latest decision per alert of this hire (for the blocker view's explain block)."""
    rows = [m for m in hc.ctx.lc.metrics(hc.program.org_id, ["coach.decision"]) if m.subject_id == hc.ob.id]
    out: dict[str, dict] = {}
    for m in sorted(rows, key=lambda m: m.at):
        out[m.props.get("blocker_id")] = m.props
    return out


def tick_summary(row: dict) -> str:
    thought = f", {row['assessed']} assessed by the Coach" if row.get("assessed") else ""
    return (f"Daily check of {row['programs']} program{'s' if row['programs'] != 1 else ''}: {row['alerts']} open "
            f"alert{'s' if row['alerts'] != 1 else ''}, {row['nudges']} nudge{'s' if row['nudges'] != 1 else ''} sent{thought}")


# ---------------------------------------------------------------- weekly summaries
def closed_week(day: date) -> date:
    from app.lifecycle.clock import week_start

    return week_start(day) - timedelta(days=7)


BACKFILL_WEEKS = 4  # a tick closes every week it finds missing, this far back (a server that was down, a demo clock that jumped)


def missing_weeks(hc, day: date) -> list[date]:
    """The closed weeks (oldest first, at most BACKFILL_WEEKS back) since the hire's start that have no summary."""
    last = closed_week(day)
    have = {s.week_start for s in hc.ctx.lc.summaries(hc.ob.id)}
    weeks = [last - timedelta(days=7 * k) for k in range(BACKFILL_WEEKS)]
    return sorted(w for w in weeks if hc.ob.start_date < w + timedelta(days=7) and w not in have)


def week_missing(hcs: list, day: date) -> list:
    """Active hires with a closed week that has no summary."""
    return [hc for hc in hcs if missing_weeks(hc, day)]


def overview_draft(lines: list[dict]) -> str:
    texts = [l["text"] for l in lines if l.get("text")]
    return " ".join(texts[:2]) if texts else "Nothing recorded this week."


def write_summary(deps, hc, start: date, trace: str | None = None):
    """The counted summary of the week from `start`, saved (summarise(), without the asker's trace)."""
    return summarise(deps, hc, start, trace)[0]


def summarise(deps, hc, start: date, trace: str | None = None, user=None, run_id: str | None = None, think: bool = True):
    """(the saved summary, the Coach's Thought or None). The counted summary of the week from `start`, with the Coach's overview and explain block, saved (replacing that week's).
    With a model that runs a loop (and `think`), the Coach writes the overview from the numbered lines, citing them [n]; code
    keeps it only if every [n] is a line and the text passes the checked writer. Else the checked writer's overview."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.runtime import shared_steps
    from app.agents.core.schemas import WeeklyOverview
    from app.agents.tools import REGISTRY
    from app.lifecycle import summaries
    from app.ports import ProgressSummary

    body = summaries.build(hc, start)
    lines = [l["text"] for l in body["lines"]]
    sources = [R.source(c["label"], "metric" if c["kind"] == "area" else "event", record=c["kind"], id=c["ref"])
               for l in body["lines"] for c in l["citations"][:3]][:12]
    t = None
    if think and any(l["citations"] for l in body["lines"]):
        task = ("Write the overview of this week from the counted lines, citing them [n].\n\n"
                + embed_data({"onboarding_id": hc.ob.id, "week_start": start.isoformat(),
                              "lines": [{"n": i + 1, "text": x} for i, x in enumerate(lines)]}))
        t = thinking.think(deps, KEY, org_id=hc.program.org_id, project_id=hc.ob.project_id, user=user, system=prompts.COACH_WEEKLY_AGENT,
                           task=task, tools=REGISTRY.select("hire_signals", "program"), output=WeeklyOverview, run_id=run_id,
                           max_steps=3, timeout_s=20, clock=hc.ctx.clock, notes={"onboarding_id": hc.ob.id})
    kept = None
    if t is not None and t.output is not None:
        marks = [int(m) for m in re.findall(r"\[(\d+)\]", t.output.overview)]
        cited = sorted({n for n in list(t.output.cited) + marks if 1 <= n <= len(lines)})
        bare = re.sub(r"\s*\[\d+\]", "", t.output.overview)
        ok, why_not = t.text(bare, 400, json.dumps(lines))
        if ok and cited and all(1 <= n <= len(lines) for n in marks):
            kept = t.output.overview.strip()
            sources = [R.source(c["label"], "metric" if c["kind"] == "area" else "event", record=c["kind"], id=c["ref"])
                       for n in cited for c in body["lines"][n - 1]["citations"][:3]][:12] or sources
        else:
            t.note("verify", f"The overview was not used: {why_not or 'it cited no counted line'}", shared="The overview failed a check")
    if kept:
        body["overview"] = kept
        body["why"] = body["why"] + ["The overview was written by the Coach from these lines, citing them [n]; the lines themselves are counted."]
        body["explain"] = R.explain(KEY, body["why"], sources, None, True, trace)
        body["agent"] = {"run_id": t.run_id, "steps": shared_steps(t.steps)}
    else:
        data = {"week_start": start.isoformat(), "lines": lines}
        text, ai, dropped = R.write(deps, KEY, hc.program.org_id, system=prompts.COACH_OVERVIEW, data=data,
                                    draft=overview_draft(body["lines"]), max_len=400)
        body["overview"] = text
        if ai:
            body["why"] = body["why"] + ["The overview was written by a model from these lines; the lines themselves are counted."]
        logic = body["why"] + ([f"The AI's overview was not used: {dropped}."] if dropped else [])
        body["explain"] = R.explain(KEY, logic, sources, None, ai, trace)
    row = hc.ctx.lc.save_summary(ProgressSummary(id="", onboarding_id=hc.ob.id, week_start=start, body=body, created_at=hc.ctx.clock.now()))
    return row, t


def week(deps, org_id: str, project_id: str | None, day=None, trace: str | None = None) -> dict:
    """week.closed: the summary of every closed week (up to BACKFILL_WEEKS back) an active hire has none of. Counts per project."""
    team = R.programs(deps, org_id, project_id, day)
    if not team:
        return {"projects": {}, "written": 0}
    today = team[0].ctx.clock.today()
    per: dict[str, int] = {}
    for hc in week_missing(team, today):
        for start in missing_weeks(hc, today):
            newest = start == closed_week(today)  # only the newest week thinks; a backfilled old one gets the checked writer
            row, t = summarise(deps, hc, start, trace, think=newest)
            if t is not None:
                t.record(deps, hc.ob.project_id, KEY, "week.closed", "Wrote a weekly overview from the counted lines", trace,
                         user_id=hc.ob.new_hire_id)
            per[hc.ob.project_id] = per.get(hc.ob.project_id, 0) + 1
            if start == closed_week(today):  # the newest week is emailed (hire and manager), not a backfilled old one
                from app.services import mailing

                mailing.weekly_summary(deps, deps.settings, hc, row)
    return {"projects": per, "written": sum(per.values())}


# ---------------------------------------------------------------- ask.free: "what should I do next?"
def next_step(deps, ev: dict, steps: list, on_step=None) -> dict | None:
    """The Coach's answer to a free-form request in Ask from a hire with a program: the one next step, from their signals and
    program, checked. None: the asker has no program here, or no loop could run (the Supervisor's proposal stands)."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import NextStep
    from app.agents.tools import REGISTRY
    from app.lifecycle import hires

    user = deps.store.get_user(ev["user_id"])
    ob = next((o for o in deps.store.onboardings(ev["project_id"]) if o.new_hire_id == user.id), None) if user else None
    hc = hires.load(R.lifecycle_ctx(deps, user.org_id, user), ob.id) if ob and deps.lifecycle else None
    if hc is None:
        return None
    task = ("The new hire asked what to do next (their words are in the data). Read hire_signals and program, then decide.\n\n"
            + embed_data({"request": ev.get("question", ""), "onboarding_id": hc.ob.id, "who_can_help": _mentor(hc)}))
    t = thinking.think(deps, KEY, org_id=user.org_id, project_id=hc.ob.project_id, user=user, system=prompts.COACH_NEXT_AGENT, task=task,
                       tools=REGISTRY.select("hire_signals", "program", "kt_moments", "search_knowledge"), output=NextStep, max_steps=4,
                       timeout_s=20, clock=hc.ctx.clock, notes={"onboarding_id": hc.ob.id}, steps=steps, on_step=on_step)
    if t is None or t.output is None:
        return None
    text, _ = t.text(t.output.next_step, 300, _mentor(hc))
    if not text:
        return None
    why, _ = t.text(t.output.why, 300, _mentor(hc))
    signals = [x for x in t.output.signals if x in t.ctx.notes.get("signals", set())][:5]
    ob_href = f"#/program/{hc.ob.id}"
    return {"proposal": text, "why_next": why or "", "signals": signals,
            "actions": [{"label": "Open your program", "href": ob_href}, {"label": "See your blockers", "href": f"{ob_href}?tab=blockers"}]}
