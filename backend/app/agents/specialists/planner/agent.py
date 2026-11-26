"""The Planner: drafts the 30/60/90 plan, the completion review and template improvements.

It thinks (app/agents/core/thinking.py):
- The plan (`onboarding.started` of an enrolment). It reads the program and the template (`program`), researches the focus
  area (kt_coverage, search_knowledge, read_file, issues, tests_for, recent_changes, code_owners) and drafts the tasks of ONE
  30/60/90 plan, each with acceptance criteria, a due day, its files, an open issue for a code task where one fits, and the
  milestone it reaches. Code keeps the template's bounds: due days inside the program, real paths and open issues only, at most
  MAX_AGENT_TASKS of its own, the template's milestones and the items it left out added (plan_30_60_90). The plan waits for the
  manager as the task_plan approval; nothing approves it but a person. Other plans (a KT call's action items, a hire without a
  program) are the Task agent's (app/agents/specialists/planner/task.py), as before.
- The completion draft (`program.completion_due`, and GET /api/onboardings/{id}/completion). The criteria of
  app/lifecycle/completion.py are counted by code; the Planner reads the program behind them and recommends complete or extend
  (7 to 30 days), judging for each unmet criterion whether it really blocks. Code requires that judgement before it keeps a
  "complete" with a criterion unmet, and the note passes the checked writer. Stored with the program (completion_draft) with a
  fingerprint of the criteria, and written again only when they change. The manager decides.
- Template improvements (`program.completed`). With at least MIN_COHORT completed programs of the template's track, it reads
  `cohort_evidence` (blockers hit early, when milestones were really reached, how long programs ran, and survey questions only
  from groups of SURVEY_MIN or more people) and proposes changes, each citing evidence rows by id. Code checks each change's
  field, bounds and evidence, and that a survey row cited is one the floor let through. An admin approves; approval makes a new
  template version, as PATCH /api/programs/templates/{id} does.
- ask.free: "plan a KT session with Ravi about webhooks" becomes a session proposal (presenter, files, agenda), which a person
  books; the presenter must be someone the people tools named.

Fallback: with no loop (the AI off, the offline demo, "rules" mode, the daily budget) or no answer, the rules: the Task agent's
tasks or the template's plan; completion criteria all met -> complete, else extend by 14 days; the template rules below; and
the checked writer's text."""
from __future__ import annotations

import hashlib
import json
from statistics import median

from app.agents.core import prompts
from app.agents.core import registry as R

KEY = "planner"
MAX_AGENT_TASKS = 8  # the Planner's own tasks in a plan (the template's items and milestone tasks are added by code)
PLAN_TOOLS = ("program", "kt_coverage", "search_knowledge", "read_file", "issues", "tests_for", "recent_changes", "code_owners")
SESSION_TOOLS = ("search_knowledge", "read_file", "kt_moments", "who_knows", "code_owners", "kt_coverage")
NOTICE_DAYS = 7  # the completion draft is written in the last week of a program
EXTEND_DAYS = 14
MIN_COHORT = 3  # completed programs of a track, at least (reporting's floor for a median)
SURVEY_MIN = 5  # people answering a survey question in one team and day, at least (checkins.SURVEY_MIN_GROUP)
EARLY_DAYS, EARLY_SHARE = 14, 0.5
LATE_DAYS = 5
OVERRUN_DAYS = 7
LOW_SCORE = 3.5

BLOCKER_ITEMS = {
    "environment": {"title": "Set up your environment with your mentor", "kind": "meet", "due_day": 1,
                    "description": "Pair with your mentor on the environment check on day one, before a failing tool becomes a blocker.",
                    "acceptance_criteria": ["The environment check passes"]},
    "repeated_topic": {"title": "Book a KT session on the area you ask about most", "kind": "meet", "due_day": 10,
                       "description": "Ask the expert of the area you keep asking about for a recorded walkthrough.",
                       "acceptance_criteria": ["KT session booked"]},
    "stalled_task": {"title": "Agree a short daily check-in with your mentor for two weeks", "kind": "meet", "due_day": 2,
                     "description": "Ten minutes a day: what you did, what is in the way.", "acceptance_criteria": ["Check-ins agreed"]},
    "failing_ci": {"title": "Run the CI checks locally before your first push", "kind": "learn", "due_day": 10,
                   "description": "Run the same tests and linters CI runs, from the README.", "acceptance_criteria": ["Checks pass locally"]},
    "unresolved_review": {"title": "Walk through your first review's findings with your mentor", "kind": "meet", "due_day": 16,
                          "description": "Thirty minutes on the findings of your first pull request review.",
                          "acceptance_criteria": ["Findings discussed"]},
}
SURVEY_ITEMS = {
    "clarity": {"title": "Agree what good looks like for your first 30 days", "kind": "meet", "due_day": 3,
                "description": "With your manager: the outcomes of the first month, written down.", "acceptance_criteria": ["Outcomes written"]},
    "support": {"title": "A weekly 30-minute check-in with your mentor", "kind": "meet", "due_day": 7,
                "description": "A standing slot to ask anything.", "acceptance_criteria": ["Check-in booked"]},
    "docs": {"title": "Flag three documents or files that were unclear", "kind": "learn", "due_day": 10,
             "description": "Use 'Unclear' or 'Outdated' on what slowed you down; the owners get the notes.",
             "acceptance_criteria": ["Three flags sent"]},
    "tools": {"title": "Check your access list with your manager", "kind": "meet", "due_day": 2,
              "description": "Go through the access items together on day two.", "acceptance_criteria": ["Every item granted or not needed"]},
    "belonging": {"title": "Coffee with three teammates outside your area", "kind": "meet", "due_day": 14,
                  "description": "Meet the people you will work with.", "acceptance_criteria": ["Three coffees"]},
}


# ---------------------------------------------------------------- the completion draft
def _criteria(hc) -> list[dict]:
    from app.lifecycle import completion

    return completion.criteria(hc)


def fingerprint(hc, rows: list[dict] | None = None) -> str:
    rows = rows if rows is not None else _criteria(hc)
    key = json.dumps([[r["key"], r["met"]] for r in rows] + [hc.program.duration_days], sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _first(hc) -> str:
    name = hc.hire.name if hc.hire else "the hire"
    return name.split(" (")[0].split(" ")[0]


def rules_draft(hc, rows: list[dict]) -> dict:
    unmet = [r for r in rows if not r["met"]]
    if not unmet:
        note = f"Recommend completing {_first(hc)}'s program: every criterion is met."
        decision, extend = "complete", 0
    else:
        reasons = "; ".join(f"{r['label'].lower()}: {r['why'].rstrip('.')}" for r in unmet)
        note = f"Recommend extending {_first(hc)}'s program by {EXTEND_DAYS} days. Not met yet: {reasons}."
        decision, extend = "extend", EXTEND_DAYS
    return {"decision": decision, "extend_days": extend, "note": note, "ai": False, "fingerprint": fingerprint(hc, rows),
            "unmet": [r["key"] for r in unmet]}


def _logic(rows: list[dict], draft: dict) -> list[str]:
    lines = [f"{r['label']}: {'met' if r['met'] else 'not met'} ({r['why'].rstrip('.')})." for r in rows]
    lines += [f"The Planner on {a['criterion'].replace('_', ' ')}: {a['assessment']}" for a in draft.get("assessments") or []]
    lines.append("Every criterion met: recommend completing." if draft["decision"] == "complete"
                 else f"A criterion is not met: recommend extending by {draft['extend_days']} days. The manager decides.")
    return lines


def completion_view(hc) -> dict:
    """The completion review with the Planner's draft and its explain block. Rules only: the stored draft when it was written
    for the criteria as they are now, else the rules' draft. The visibility registry builds 'completion' with this."""
    from app.lifecycle import completion

    review = completion.review(hc)
    rows = review["criteria"]
    stored = hc.program.completion_draft
    draft = stored if stored and stored.get("fingerprint") == fingerprint(hc, rows) else rules_draft(hc, rows)
    review["draft"] = draft
    review["explain"] = R.explain(KEY, _logic(rows, draft), [R.source(r["label"], "metric", criterion=r["key"], met=r["met"]) for r in rows],
                                  None, bool(draft.get("ai")))
    return review


def is_due(hc) -> bool:
    """The Supervisor sends program.completion_due for a program in its last week whose draft is missing or out of date."""
    p = hc.program
    if p.completed_at or hc.ob.status != "active" or hc.day < p.duration_days - NOTICE_DAYS:
        return False
    return not (p.completion_draft and p.completion_draft.get("fingerprint") == fingerprint(hc))


def write_completion(deps, hc, notify: bool = False, trace: str | None = None) -> dict:
    """Write and store the draft for the criteria as they are now; tell the manager the first time a program gets one."""
    return store_completion(deps, hc, notify, trace)[0]


def store_completion(deps, hc, notify: bool = False, trace: str | None = None, user=None, run_id: str | None = None,
                     record: bool = False):
    """write_completion, returning (the draft, the Planner's Thought or None). `record`: a run of its own for the Planner's loop
    (the daily tick's; a request's run is recorded by its node)."""
    draft, t = draft_completion(deps, hc, trace, user, run_id)
    first = not hc.program.completion_draft
    hc.ctx.lc.update_program(hc.ob.id, completion_draft=draft)
    hc.program = hc.ctx.lc.get_program(hc.ob.id)
    if record and t is not None:
        t.record(deps, hc.ob.project_id, KEY, "program.completion_due", f"Drafted a completion review ({draft['decision']})", trace)
    if notify and first:
        hc.ctx.store.notify(hc.ob.manager_id, "completion",
                            f"{_first(hc)}'s completion review is due: the Planner drafted a sign-off note ({draft['decision']}).",
                            f"/program/{hc.ob.id}?tab=completion&project={hc.ob.project_id}")
    return draft, t


def draft_completion(deps, hc, trace: str | None = None, user=None, run_id: str | None = None):
    """(the draft, the Planner's Thought or None): the Planner's recommendation from the evidence, checked; else the rules'."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.runtime import shared_steps
    from app.agents.core.schemas import CompletionDraft
    from app.agents.tools import REGISTRY

    rows = _criteria(hc)
    draft = rules_draft(hc, rows)
    data = {"hire_first_name": _first(hc), "day": hc.day, "duration_days": hc.program.duration_days, "decision": draft["decision"],
            "extend_days": draft["extend_days"], "criteria": [{"label": r["label"], "met": r["met"], "why": r["why"]} for r in rows]}
    task = ("Draft the completion review of this program. The criteria were counted by code; read program for the evidence.\n\n"
            + embed_data({**data, "onboarding_id": hc.ob.id, "criteria": [{"key": r["key"], "label": r["label"], "met": r["met"],
                                                                            "why": r["why"]} for r in rows]}))
    t = thinking.think(deps, KEY, org_id=hc.program.org_id, project_id=hc.ob.project_id, user=user, system=prompts.PLANNER_COMPLETION_AGENT,
                       task=task, tools=REGISTRY.select("program"), output=CompletionDraft, run_id=run_id, max_steps=3, timeout_s=20,
                       clock=hc.ctx.clock, notes={"onboarding_id": hc.ob.id})
    if t is not None and t.output is not None:
        d = t.output
        unmet = [r["key"] for r in rows if not r["met"]]
        notes = {}
        for a in d.assessments:
            text, _ = t.text(a.assessment, 300)
            if a.criterion in unmet and text:
                notes[a.criterion] = text
        decision = d.decision
        if decision == "complete" and set(unmet) - set(notes):
            t.note("verify", "Completion with an unmet criterion needs a judgement of it: the rules' recommendation stands",
                   shared="Kept the rules' recommendation")
            decision = draft["decision"]
        extend = max(7, min(30, int(d.extend_days or EXTEND_DAYS))) if decision == "extend" else 0
        note, why_not = t.text(d.note, 600, draft["note"])
        if note is None:
            t.note("verify", f"The note's wording was not used: {why_not}", shared="The note's wording failed a check")
        draft = {**draft, "decision": decision, "extend_days": extend, "note": note or draft["note"], "ai": note is not None,
                 "assessments": [{"criterion": k, "assessment": v} for k, v in notes.items()],
                 "agent": {"run_id": t.run_id, "steps": shared_steps(t.steps)}}
        return draft, t
    text, ai, _ = R.write(deps, KEY, hc.program.org_id, system=prompts.PLANNER_COMPLETION, data=data, draft=draft["note"], max_len=600)
    return {**draft, "note": text, "ai": ai}, t


def completion(deps, hc, trace: str | None = None, user=None, run_id: str | None = None):
    """GET /api/onboardings/{id}/completion: (the view, whether a draft was written, the Planner's Thought or None). A draft is
    written only when the stored one is missing or out of date and the program is not signed off yet."""
    wrote, t = False, None
    if not hc.program.completed_at:
        stored = hc.program.completion_draft
        if not (stored and stored.get("fingerprint") == fingerprint(hc)):
            _, t = store_completion(deps, hc, trace=trace, user=user, run_id=run_id)
            wrote = True
    return completion_view(hc), wrote, t


def completion_due(deps, org_id: str, project_id: str | None, onboarding_ids: list[str] | None, day=None,
                   trace: str | None = None) -> dict:
    """program.completion_due: a draft for each due program (those named, or every one of the organisation that is due)."""
    per: dict[str, int] = {}
    for hc in R.programs(deps, org_id, project_id, day):
        if (onboarding_ids is None or hc.ob.id in onboarding_ids) and is_due(hc):
            store_completion(deps, hc, notify=True, trace=trace, record=True)
            per[hc.ob.project_id] = per.get(hc.ob.project_id, 0) + 1
    return {"projects": per, "drafted": sum(per.values())}


# ---------------------------------------------------------------- template improvements
def _day(ob, at) -> int:
    return (at.date() - ob.start_date).days


def template_rules(deps, org_id: str, template) -> tuple[list[dict], list[dict], list[dict]]:
    """(changes, evidence, cohort): what the completed programs of the template's track and the survey say to change."""
    from app.lifecycle import checkins
    from app.lifecycle import templates as T

    lc, store = deps.lifecycle, deps.store
    tracks = {t.id: t.role_track for t in lc.templates(org_id, include_inactive=True)}
    cohort = []
    for p in lc.programs(org_id):
        ob = store.get_onboarding(p.onboarding_id)
        if p.completed_at and ob and tracks.get(p.template_id) == template.role_track:
            cohort.append((ob, p))
    if len(cohort) < MIN_COHORT:
        return [], [], cohort
    n, changes, evidence = len(cohort), [], []
    titles = {i.get("title") for i in template.plan_items}

    # blockers most of the cohort hit in their first two weeks
    for rule, item in BLOCKER_ITEMS.items():
        hit = [ob.id for ob, _ in cohort if any(a.rule == rule and _day(ob, a.opened_at) <= EARLY_DAYS for a in lc.blockers(ob.id))]
        if len(hit) / n >= EARLY_SHARE and item["title"] not in titles:
            what = rule.replace("_", " ")
            label = (f"{len(hit)} of {n} completed {template.role_track} programs opened {'an' if what[0] in 'aeiou' else 'a'} {what} "
                     f"blocker in their first {EARLY_DAYS} days")
            evidence.append({"label": label, "kind": "metric", "ref": {"rule": rule, "onboardings": hit}, "n": n})
            changes.append({"field": "plan_items", "from": None, "to": dict(item), "reason": f"{label}."})
            titles.add(item["title"])

    # milestones reached well after their day
    for m in template.milestones:
        days = []
        for ob, p in cohort:
            row = next((x for x in lc.milestones(ob.id) if x.key == m["key"] and x.done_at and x.status == "done"), None)
            if row:
                days.append(round(_day(ob, row.done_at) * T.BASE_DAYS / p.duration_days))
        if len(days) >= MIN_COHORT and median(days) - int(m["due_day"]) >= LATE_DAYS:
            to = int(round(median(days)))
            label = f"'{m['title']}': median day {to} in {len(days)} completed programs, planned for day {m['due_day']}"
            evidence.append({"label": label, "kind": "metric", "ref": {"milestone": m["key"], "days": sorted(days)}, "n": len(days)})
            changes.append({"field": f"milestones.{m['key']}.due_day", "from": int(m["due_day"]), "to": to, "reason": f"{label}."})

    # programs that ran well over
    lengths = [_day(ob, p.completed_at) for ob, p in cohort]
    if median(lengths) - template.duration_days >= OVERRUN_DAYS:
        to = int(round(median(lengths)))
        label = f"Median completion on day {to} across {n} completed programs, for a {template.duration_days}-day template"
        evidence.append({"label": label, "kind": "metric", "ref": {"days": sorted(lengths)}, "n": n})
        changes.append({"field": "duration_days", "from": template.duration_days, "to": to, "reason": f"{label}."})

    # survey questions scored low, only from groups of 5 or more people (a team's answers on one survey day: never fewer,
    # which could identify someone, and never answers of one person across several days)
    survey = checkins.floored_scores(lc.survey_responses(org_id))
    for q in checkins.SURVEY:
        if q["id"] not in survey:
            continue
        avg, answers = round(survey[q["id"]][0], 1), survey[q["id"]][1]
        item = SURVEY_ITEMS.get(q["id"])
        if avg < LOW_SCORE and item and item["title"] not in titles:
            label = f"Survey: '{q['text']}' averages {avg} of 5 ({answers} answers, in groups of {SURVEY_MIN} or more people)"
            evidence.append({"label": label, "kind": "metric", "ref": {"question": q["id"], "average": avg}, "n": answers})
            changes.append({"field": "plan_items", "from": None, "to": dict(item), "reason": f"{label}."})
            titles.add(item["title"])
    return changes, evidence, cohort


def propose_template_change(deps, ev: dict, thread: str, trace: str | None = None) -> list[str]:
    """program.completed {project_id, onboarding_id}: at most one pending template_change per template key. The Planner's changes
    from the cohort's evidence (checked), else the template rules'."""
    out = propose_template_change_traced(deps, ev, thread, trace)
    return out[0]


def propose_template_change_traced(deps, ev: dict, thread: str, trace: str | None = None):
    """(the approval ids this run parks on, the run record's trace or None)."""
    lc, store = deps.lifecycle, deps.store
    program = lc.get_program(ev["onboarding_id"])
    if not program:
        return [], None
    org, pid = program.org_id, program.project_id
    used = lc.get_template(program.template_id)
    template = next((t for t in lc.templates(org) if used and t.key == used.key), None)
    if not template:
        return [], None
    for a in store.approvals(pid):
        if a.kind == "template_change" and a.payload.get("template_key") == template.key:
            return ([a.id] if a.thread_id == thread else []), None  # a retry parks on its own; another run's proposal covers it
    thought = None
    agentic = think_template(deps, org, pid, template)
    if agentic is not None:
        changes, evidence, n, thought = agentic
        if thought.output is not None and not changes:
            return [], thought.trace()  # the Planner read the evidence and proposed nothing: its trace says why
    if thought is None or thought.output is None:
        changes, evidence, cohort = template_rules(deps, org, template)
        n = len(cohort)
        if not changes:
            return [], thought.trace() if thought is not None else None
        texts, ai = R.write_many(deps, KEY, org, system=prompts.PLANNER_REASONS,
                                 data={"template": template.title, "changes": [{k: c[k] for k in ("field", "from", "to")} for c in changes],
                                       "evidence": [e["label"] for e in evidence]},
                                 drafts=[c["reason"] for c in changes], max_len=300)
        for c, t in zip(changes, texts):
            c["reason"] = t
        logic = [e["label"] + "." for e in evidence] + [
            f"Rules over the {n} completed {template.role_track} programs and survey questions that {SURVEY_MIN} or more people in a team answered on one survey day.",
            "An admin decides: approving makes a new version of the template; running programs keep theirs."]
        ai_any = any(ai)
    else:
        logic = [c["reason"] for c in changes] + [
            f"The Planner read the outcomes of the {n} completed {template.role_track} programs and survey questions that {SURVEY_MIN} or "
            "more people in a team answered on one survey day; code checked each change's field, bounds and evidence.",
            "An admin decides: approving makes a new version of the template; running programs keep theirs."]
        ai_any = True
    payload = {"template_id": template.id, "template_key": template.key, "template_title": template.title,
               "template_version": template.version, "changes": changes, "evidence": evidence, "decider": "admin",
               "explain": R.explain(KEY, logic, [R.source(e["label"], "metric", **e["ref"]) for e in evidence], None, ai_any, trace)}
    if thought is not None and thought.output is not None:
        from app.agents.core.runtime import shared_steps

        payload["agent"] = {"run_id": thought.run_id, "steps": shared_steps(thought.steps)}
    approval = store.create_approval(pid, "template_change", thread, payload)
    admins = [m for m in store.managers(pid) if m.role == "admin"] or store.managers(pid)
    for m in admins:
        _tell(store, m.id, f"The Planner proposed {len(changes)} change{'s' if len(changes) != 1 else ''} to the {template.title} template.",
              f"/approvals/{approval.id}?project={pid}")
    return [approval.id], thought.trace() if thought is not None else None


MAX_CHANGES = 4


def think_template(deps, org: str, pid: str, template):
    """The Planner's template changes: (changes, evidence, cohort size, Thought) with the changes that pass the checks, or None
    when the cohort is too small or no loop could run."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import TemplateProposal
    from app.agents.tools import REGISTRY
    from app.agents.tools.hire import cohort

    rows, _, n = cohort(deps, org, template)
    if n < MIN_COHORT:
        return None
    task = ("Propose changes to this onboarding template only where the completed programs' evidence calls for one. Start with "
            "cohort_evidence.\n\n" + embed_data({"template": template.title, "completed_programs": n}))
    t = thinking.think(deps, KEY, org_id=org, project_id=pid, user=None, system=prompts.PLANNER_TEMPLATE_AGENT, task=task,
                       tools=REGISTRY.select("cohort_evidence"), output=TemplateProposal, max_steps=3, timeout_s=25,
                       notes={"template_id": template.id})
    if t is None:
        return None
    if t.output is None:
        return [], [], n, t
    by_id = {r["id"]: r for r in rows}
    changes, used, seen_fields = [], {}, set()
    titles = {str(i.get("title", "")).lower() for i in template.plan_items}
    milestones = {m["key"]: m for m in template.milestones}
    for c in t.output.changes:
        ev = [e for e in dict.fromkeys(c.evidence) if e in by_id]
        reason, why_not = t.text(c.reason, 300)
        field_ = c.field.strip()
        bad = None
        if not ev:
            bad = "it cited no evidence row"
        elif reason is None:
            bad = f"its reason failed a check ({why_not})"
        elif field_ in seen_fields and field_ != "plan_items":
            bad = "it repeated a field"
        change = None
        if bad is None and field_ == "plan_items":
            item = c.item
            if item is None or not item.name.strip() or item.name.strip().lower() in titles:
                bad = "its plan item is missing or already in the template"
            else:
                to = {"title": item.name.strip()[:200], "kind": item.kind, "due_day": max(0, min(T_BASE, int(item.due_day))),
                      "description": " ".join(item.description.split())[:400],
                      "acceptance_criteria": [x[:200] for x in item.acceptance_criteria[:3]] or ["Done as described"]}
                change = {"field": "plan_items", "from": None, "to": to, "reason": reason}
                titles.add(to["title"].lower())
        elif bad is None and field_.startswith("milestones.") and field_.endswith(".due_day"):
            key = field_.split(".")[1]
            m = milestones.get(key)
            if m is None or c.to_day is None or not 0 <= int(c.to_day) <= T_BASE or int(c.to_day) == int(m["due_day"]):
                bad = "its milestone or day is out of bounds"
            else:
                change = {"field": field_, "from": int(m["due_day"]), "to": int(c.to_day), "reason": reason}
        elif bad is None and field_ == "duration_days":
            if c.to_day is None or not 14 <= int(c.to_day) <= 365 or int(c.to_day) == template.duration_days:
                bad = "its length is out of bounds"
            else:
                change = {"field": field_, "from": template.duration_days, "to": int(c.to_day), "reason": reason}
        elif bad is None:
            bad = "its field is not one a template has"
        if bad or change is None:
            t.note("verify", f"Dropped a change: {bad}", shared="Dropped a change that failed a check")
            continue
        seen_fields.add(field_)
        changes.append(change)
        for e in ev:
            used[e] = by_id[e]
        if len(changes) == MAX_CHANGES:
            break
    evidence = [{"label": _evidence_label(r), "kind": "metric", "ref": {"evidence": r["id"]}, "n": r.get("of") or r.get("answers") or 0}
                for r in used.values()]
    t.note("final", f"Proposed {len(changes)} template change{'s' if len(changes) != 1 else ''}",
           shared=f"Proposed {len(changes)} template change(s)")
    return changes, evidence, n, t


T_BASE = 90  # template days are days of a 90-day program (app/lifecycle/templates.BASE_DAYS)


def _evidence_label(r: dict) -> str:
    """One evidence row in words, with its counts."""
    if r["id"].startswith("blocker:"):
        return f"{r['programs']} of {r['of']} {r['what']}"
    if r["id"].startswith("milestone:"):
        return f"{r['what']}: median day {r['median_day']}, planned for day {r['planned_day']} ({r['reached_by']} of {r['of']} programs)"
    if r["id"] == "duration":
        return f"Median {r['what']}: {r['median_days']} across {r['of']} programs, for a {r['template_days']}-day template"
    return f"{r['what']}: average {r['average']} of 5 ({r['answers']} answers, {r['floor']})"


def apply_template_change(deps, approval, user_id: str) -> dict:
    """Approve: a new version of the template with the changes (PATCH /api/programs/templates/{id}'s logic)."""
    lc, store = deps.lifecycle, deps.store
    p = approval.payload
    t = lc.get_template(p["template_id"])
    if t and not t.active:  # changed since the proposal: the changes apply to the version that is active now
        t = next((x for x in lc.templates(t.org_id) if x.key == t.key), None)
    if not t:
        return {"applied": False, "reason": "The template no longer exists."}
    fields: dict = {}
    for ch in p.get("changes", []):
        f = ch["field"]
        if f == "plan_items":
            items = fields.get("plan_items", [dict(i) for i in t.plan_items])
            if ch["to"]["title"] not in {i.get("title") for i in items}:
                items.append(dict(ch["to"]))
            fields["plan_items"] = sorted(items, key=lambda i: int(i.get("due_day", 7)))
        elif f.startswith("milestones.") and f.endswith(".due_day"):
            key = f.split(".")[1]
            ms = fields.get("milestones", [dict(m) for m in t.milestones])
            for m in ms:
                if m["key"] == key:
                    m["due_day"] = int(ch["to"])
            fields["milestones"] = sorted(ms, key=lambda m: int(m["due_day"]))
        elif f == "duration_days":
            fields["duration_days"] = max(14, min(365, int(ch["to"])))
    new = lc.new_template_version(t.id, **fields) if fields else None
    if not new:
        return {"applied": False, "reason": "Nothing to change, or the template changed meanwhile."}
    store.audit(t.org_id, user_id or None, "template.versioned", new.id,
                {"key": new.key, "version": new.version, "via": "template_change", "approval_id": approval.id})
    return {"applied": True, "template_id": new.id, "template_key": new.key, "version": new.version}


def _tell(store, user_id: str, text: str, link: str) -> None:
    try:
        store.notify(user_id, "approval", text, link)
    except Exception:  # noqa: BLE001 - a lost notice must not lose the proposal
        pass


# ---------------------------------------------------------------- the 30/60/90 plan of an enrolment
MEET_WORDS = frozenset({"review", "shadow", "shadowed", "sessions", "session", "met", "pairing", "coffee", "check", "check-in"})


def plan_30_60_90(deps, ob, proposed: list, template_tasks: list) -> dict:
    """ONE plan for an enrolled hire: the template's milestones (scaled to the program), the tasks the Task agent proposed (or
    the template's plan without a model), the template's plan items it left out, and a task for each milestone a person marks
    in the second and third phase; grouped into phases Days 1-30, 31-60, 61-90. Rules only: what the plan holds is decided
    here; the Task agent wrote its own tasks' words."""
    from app.agents.core.schemas import ProposedTask
    from app.lifecycle import enrolment
    from app.lifecycle import templates as T

    program = deps.lifecycle.get_program(ob.id)
    template = deps.lifecycle.get_template(program.template_id) if program else None
    if not template:
        return {"tasks": list(proposed), "milestones": [], "phases": []}
    duration = program.duration_days
    milestones = [{"key": m.key, "title": m.title, "due_day": m.due_day} for m in T.milestones_for(template, ob.id, duration)]
    tasks, titles = list(proposed), {t.title.lower() for t in proposed}
    for t in template_tasks:
        if t.title.lower() not in titles:
            tasks.append(t)
            titles.add(t.title.lower())
    first = enrolment.phases(duration)[0]["to_day"]
    for m in milestones:  # the later phases' milestones, each with the task that reaches it
        if m["due_day"] <= first or m["title"].lower() in titles:
            continue
        words = set(m["title"].lower().replace("-", " ").split())
        kind = "code" if m["key"] in {"area_contributed", "first_pr_merged"} else "meet" if words & MEET_WORDS else "learn"
        tasks.append(ProposedTask(title=m["title"], description=f"Reach the milestone '{m['title']}' by day {m['due_day']}.", kind=kind,
                                  due_in_days=max(1, m["due_day"]), acceptance_criteria=[f"The milestone '{m['title']}' is marked done"],
                                  source="plan"))
        titles.add(m["title"].lower())
    return {"tasks": tasks, "milestones": milestones, "phases": enrolment.phases(duration)}


def phase_view(phases: list[dict], milestones: list[dict], rows: list[dict], start) -> list[dict]:
    """Each phase with the milestones due in it and the tasks (created rows, by due date) that fall in it."""
    out = []
    for p in phases:
        def inside(day: int) -> bool:
            return p["from_day"] <= day <= p["to_day"] or (p is phases[-1] and day > p["to_day"])
        due = {r["id"]: (r["due_date"] - start).days if r.get("due_date") else 0 for r in rows}
        out.append({**p, "milestones": [m["key"] for m in milestones if inside(int(m["due_day"]))],
                    "tasks": [r["id"] for r in rows if inside(due[r["id"]])]})
    return out


# ---------------------------------------------------------------- the Planner's own 30/60/90 plan (agent loop)
def draft_plan(deps, ob, run_id: str | None = None):
    """An enrolment's plan, drafted by the Planner's loop: (its checked tasks, what the approval shows of its reasoning, the
    Thought), or None when no loop could run or it gave no usable task (the Task agent's or the template's plan stands in).
    The tasks then go through plan_30_60_90, which adds the template's milestones, the items it left out and a task per later
    milestone: the template's bounds. A manager approves the plan; nothing here approves anything."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.runtime import shared_steps
    from app.agents.core.schemas import PlanDraft, ProposedTask
    from app.agents.tools import REGISTRY
    from app.lifecycle import enrolment

    lc = deps.lifecycle
    program = lc.get_program(ob.id) if lc is not None else None
    template = lc.get_template(program.template_id) if program else None
    if not program or not template:
        return None
    duration = program.duration_days
    task = ("Draft the ONE 30/60/90 plan for this new hire. Start with program, then research the focus area.\n\n"
            + embed_data({"onboarding_id": ob.id, "role": ob.role_title, "focus_area": program.focus_area, "duration_days": duration,
                          "phases": enrolment.phases(duration), "template": template.title}))
    t = thinking.think(deps, KEY, org_id=program.org_id, project_id=ob.project_id, user=None, system=prompts.PLANNER_AGENT, task=task,
                       tools=REGISTRY.select(*PLAN_TOOLS), output=PlanDraft, run_id=run_id, max_steps=8, timeout_s=45,
                       notes={"onboarding_id": ob.id})
    if t is None or t.output is None:
        return None if t is None else ([], {"run_id": t.run_id, "steps": shared_steps(t.steps)}, t)
    from app.agents.core.thinking import known_paths, real_paths

    _, allowed = known_paths(deps, ob.project_id)
    issues = set()
    for repo in deps.store.repos_for(ob.project_id):
        try:
            issues |= {i["number"] for i in deps.repos.good_first_issues(repo.full_name, repo.installation_id)}
        except Exception:  # noqa: BLE001 - an unreachable repository: its issues cannot be cited
            pass
    keys = {m["key"] for m in template.milestones}
    existing = {x.title.lower() for x in deps.store.tasks(onboarding_id=ob.id) if x.status != "rejected"}
    tasks, reasons, seen = [], [], set(existing)
    for d in sorted(t.output.tasks, key=lambda x: x.due_day):
        title = " ".join(d.name.split())[:200]
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        issue = d.issue_number if d.issue_number in issues else None
        if d.issue_number and issue is None:
            t.note("verify", "Dropped an issue number that is not an open issue", shared="Dropped an issue that failed a check")
        why, _ = t.text(d.why, 300)
        desc, _ = t.text(d.description, 600, title)
        criteria = [c for c in (t.text(x, 200, title)[0] for x in d.acceptance_criteria[:4]) if c] or ["Done as described, and shown to your mentor"]
        tasks.append(ProposedTask(title=title, description=desc or title, kind=d.kind, due_in_days=max(1, min(duration, int(d.due_day))),
                                  acceptance_criteria=criteria, related_paths=real_paths(d.related_paths, allowed, 6),
                                  source="first_pr" if issue and d.kind == "code" else "plan", issue_number=issue))
        reasons.append({"title": title, "why": why or "", "milestone": d.milestone if d.milestone in keys else None,
                        "sources": t.sources(d.sources)})
        if len(tasks) == MAX_AGENT_TASKS:
            break
    if not tasks:
        t.note("final", "No task passed the checks: the template's plan stands.")
        return [], {"run_id": t.run_id, "steps": shared_steps(t.steps)}, t
    rationale, _ = t.text(t.output.rationale, 400)
    t.note("final", f"Drafted {len(tasks)} tasks of the plan", shared=f"Drafted {len(tasks)} tasks of the plan")
    return tasks, {"run_id": t.run_id, "rationale": rationale or "", "reasons": reasons, "steps": shared_steps(t.steps)}, t


# ---------------------------------------------------------------- ask.free: "plan a KT session with Ravi about webhooks"
def kt_session(deps, ev: dict, steps: list, on_step=None) -> dict | None:
    """A KT session proposal for a free-form request in Ask: the presenter (someone the people tools named), the files and an
    agenda, checked. Nothing is booked: the asker books it in KT sessions. None when no loop could run."""
    from app.agents.core import thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import KTSessionProposal
    from app.agents.core.thinking import known_paths, real_paths
    from app.agents.tools import REGISTRY

    user = deps.store.get_user(ev["user_id"])
    if user is None:
        return None
    task = ("A person asked for a KT session (their words are in the data). Find what it should cover and who should present it.\n\n"
            + embed_data({"request": ev.get("question", "")}))
    t = thinking.think(deps, KEY, org_id=user.org_id, project_id=ev["project_id"], user=user, system=prompts.PLANNER_SESSION_AGENT, task=task,
                       tools=REGISTRY.select(*SESSION_TOOLS), output=KTSessionProposal, max_steps=5, timeout_s=25, steps=steps,
                       on_step=on_step)
    if t is None or t.output is None:
        return None
    p = t.output
    presenter = deps.store.get_user(p.presenter_id) if p.presenter_id in t.ctx.people else None
    if p.presenter_id and presenter is None:
        t.note("verify", "Dropped a presenter no tool named", shared="Dropped a presenter that failed a check")
    files, _ = known_paths(deps, ev["project_id"])
    topic, _ = t.text(p.topic, 120)
    agenda = [x for x in (t.text(a, 200)[0] for a in p.agenda[:5]) if x]
    minutes = max(15, min(90, int(p.minutes or 30)))
    if not topic:
        return None
    who = f" presented by {presenter.name.split(' (')[0]}" if presenter else ""
    proposal = (f"The Planner proposes a {minutes}-minute KT session on {topic}{who}"
                + (": " + "; ".join(agenda) if agenda else "") + ". Nothing is booked: book it in KT sessions if it suits you.")
    why, _ = t.text(p.why, 300)
    return {"proposal": proposal[:800], "session": {"topic": topic, "presenter": {"id": presenter.id, "name": presenter.name} if presenter else None,
                                                    "paths": real_paths(p.paths, files, 8), "agenda": agenda, "minutes": minutes,
                                                    "why": why or ""},
            "actions": [{"label": "Book in KT sessions", "href": "#/meetings"}, {"label": "Open the KT planner", "href": "#/kt-planner"}]}
