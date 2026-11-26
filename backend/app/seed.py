"""Demo data so `MODE=demo` opens on a working project: one repo, one recorded KT call, and an onboarding lifecycle with
something to show on every role's home: hires at days 3, 12, 45 and 88, a completed cohort before them, two teams."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.agents.core.schemas import ActionItem, MeetingResult, ScreenMoment, Term, Utterance
from app.agents.supervisor.graph import run_event
from app.ports import Meeting, Onboarding, Project, Repository, User
from app.services.chunking import chunk_transcript
from app.services import kt_review
from app.services.kt_linker import link_screen_moments

ORG, PROJECT = "org-demo", "proj-demo"
VIJAY_DAY = 12  # Vijay is twelve days into his program

DEMO_ISSUES = [
    {"number": 12, "title": "Validate privileged init containers", "labels": ["good first issue"]},
    {"number": 15, "title": "Return a clear error when a policy has no name", "labels": ["good first issue"]},
]

# What a pull request changes, for the demo (no GitHub App): the files GitHub's "list pull request files" would return. Every
# PR in the demo gets this diff, so the Review agent has real lines to check: a new `raise` in validation (the team decided
# validation returns violations and never raises) and no test.
DEMO_PR_FILES = [
    {
        "filename": "engine/validate.py",
        "status": "modified",
        "additions": 3,
        "deletions": 0,
        "patch": "@@ -26,6 +26,9 @@ def check_privileged(resource: dict, rule: Rule) -> list[str]:\n"
                 "     if not rule.deny_privileged:\n"
                 "         return []\n"
                 "     containers = resource.get(\"spec\", {}).get(\"containers\", [])\n"
                 "+    for c in containers + resource.get(\"spec\", {}).get(\"initContainers\", []):\n"
                 "+        if c.get(\"securityContext\") is None:\n"
                 "+            raise ValueError(\"missing securityContext\")\n"
                 "     return [\n",
    }
]

TRANSCRIPT = [
    (4, 18, "Ravi", "Welcome! Today we walk through how a resource gets validated against a policy."),
    (20, 52, "Ravi", "Everything starts in validate_resource. It loops over every rule that matches the resource kind."),
    (55, 95, "Ravi", "check_labels compares the required labels with what the resource has. Each missing label becomes a violation string."),
    (98, 140, "Ravi", "Important decision: we never raise inside validation. We return a list of violations so the webhook can report all of them at once."),
    (142, 180, "Vijay", "Where do the default labels come from?"),
    (182, 230, "Ravi", "From apply_defaults in mutate.py. It runs before validation and adds managed-by."),
    (232, 260, "Ravi", "Vijay, please read policy.py before Friday, and look at issue twelve, it is a good first PR."),
]
MOMENTS = [
    ScreenMoment(at_s=20, description="Editor showing the validate module", visible_paths=["engine/validate.py"], visible_symbols=["validate_resource"]),
    ScreenMoment(at_s=55, description="Scrolled to label checks", visible_paths=["engine/validate.py"], visible_symbols=["check_labels"]),
    ScreenMoment(at_s=182, description="Opened mutate.py", visible_paths=["mutate.py"], visible_symbols=["apply_defaults"]),
]


def seed(deps, graph, lifecycle=None) -> None:
    """The demo's data, written by its agents on the offline model whatever key the server has: with GEMINI_API_KEY set, the
    seed's agents (plans, the handover, summaries) would otherwise spend about 60 model calls, and the free tier's
    per-minute quota, before anyone asks anything. The real model is back for every request after the seed."""
    from app.agents.core.llm import OfflineLLM

    real, deps.llm = deps.llm, OfflineLLM()
    try:
        _seed(deps, graph, lifecycle)
    finally:
        deps.llm = real


def _seed(deps, graph, lifecycle=None) -> None:
    store = deps.store
    store.add_project(Project(id=PROJECT, org_id=ORG, name="Mini Policy Engine", description="Kubernetes admission policy engine (demo)"))
    store.add_user(User(id="u-admin", org_id=ORG, email="admin@demo.dev", name="Alex (Admin)", role="admin", github_login="alex-admin"), PROJECT)
    store.add_user(User(id="u-priya", org_id=ORG, email="priya@demo.dev", name="Priya (Manager)", role="manager", github_login="priya-mgr"), PROJECT)
    store.add_user(User(id="u-vijay", org_id=ORG, email="vijay@demo.dev", name="Vijay (New hire)", role="new_hire", github_login="vijay"), PROJECT)
    store.add_user(User(id="u-ravi", org_id=ORG, email="ravi@demo.dev", name="Ravi (Senior)", role="expert", github_login="ravi-dev"), PROJECT)
    store.add_user(User(id="u-meera", org_id=ORG, email="meera@demo.dev", name="Meera (Senior)", role="expert", github_login="meera-dev"), PROJECT)
    store.add_onboarding(Onboarding(id="ob-vijay", project_id=PROJECT, new_hire_id="u-vijay", manager_id="u-priya",
                                    start_date=date.today() - timedelta(days=VIJAY_DAY)))
    store.add_repo(Repository(id="repo-demo", project_id=PROJECT, full_name="demo/mini-policy-engine"))
    run_event(graph, {"type": "repo.connected", "project_id": PROJECT, "repository_id": "repo-demo", "ref": "main"}, "seed:repo")

    m = store.add_meeting(Meeting(id="mtg-1", project_id=PROJECT, title="KT #1: How validation works", room="kt-demo-1", status="processing",
                                  duration_s=270, recording_uri=None, started_at=datetime.now(timezone.utc) - timedelta(days=2)))
    utterances = [Utterance(start_s=a, end_s=b, speaker=s, text=t) for a, b, s, t in TRANSCRIPT]
    links = link_screen_moments(PROJECT, m.id, m.title, MOMENTS, utterances, deps.index, meeting_end_s=270)
    result = MeetingResult(
        meeting_id=m.id, project_id=PROJECT, title=m.title,
        summary="Ravi explained the validation flow: validate_resource loops over matching rules, check_labels and "
                "check_privileged return violation strings, and apply_defaults adds labels before validation runs.",
        decisions=["Validation returns a list of violations and never raises"],
        action_items=[ActionItem(text="Vijay to read engine/policy.py before Friday", owner_hint="Vijay", at_s=232),
                      ActionItem(text="Vijay to look at issue #12 as a first PR", owner_hint="Vijay", at_s=245)],
        terms=[Term(term="violation", definition="A message describing a rule the resource breaks")],
        utterances=utterances, screen_moments=MOMENTS, kt_links=links,
    )
    store.save_meeting_result(result)
    store.add_kt_links(PROJECT, links)
    deps.index.upsert(chunk_transcript(PROJECT, m.id, m.title, utterances))
    _recording_records(store, m)
    kt_review.open_review(store, PROJECT, m.id, m.title)  # Priya reviews the transcript and books the follow-ups

    # The Task agent proposes Vijay's plan; it waits in Priya's approvals until she decides.
    run_event(graph, {"type": "onboarding.started", "project_id": PROJECT, "onboarding_id": "ob-vijay"}, "seed:plan")
    if lifecycle is not None:
        seed_lifecycle(deps, lifecycle, graph)
        seed_agents(deps, graph, lifecycle)
        seed_board(deps, lifecycle)
    seed_legal(store)


def seed_legal(store) -> None:
    """Everyone in the demo organisation has accepted the current terms, privacy policy and recording notice, and its admin
    the data-processing agreement, so the demo opens on the product and not on a prompt. They are written as what they
    stand for: people who were members before the documents existed, asked once at a sign-in earlier today. Someone who
    joins the demo from an invitation afterwards is asked like anyone else."""
    from app import legal

    now = datetime.now(timezone.utc)
    people = sorted((u for u in store.users.values() if u.org_id == ORG), key=lambda u: (u.id != "u-admin", u.id))
    for i, u in enumerate(people):
        at = now - timedelta(hours=3) + timedelta(minutes=2 * i)
        for document in legal.PERSONAL:
            store.record_legal_acceptance(ORG, u.id, document, legal.VERSIONS[document], "prompt", at=at)
        _audit_at(store, u.id, "legal.accepted", u.id, {"documents": {d: legal.VERSIONS[d] for d in legal.PERSONAL}, "method": "prompt"}, at)
    at = now - timedelta(hours=3) + timedelta(minutes=1)
    store.record_legal_acceptance(ORG, "u-admin", "dpa", legal.VERSIONS["dpa"], "organisation", at=at)
    _audit_at(store, "u-admin", "legal.dpa_accepted", ORG, {"version": legal.VERSIONS["dpa"], "method": "organisation"}, at)


# ---------------------------------------------------------------- the onboarding lifecycle
# Arjun's pre-boarding page in the demo (MODE=demo only): /welcome/demo-welcome-arjun. Real links are random and shown once.
DEMO_PREBOARDING_TOKEN = "demo-welcome-arjun"
# (id, name, github, day, template, team, mentor, focus area)
CURRENT = [
    ("u-arjun", "Arjun (New hire)", "arjun-dev", 3, "backend-junior", "webhooks", "u-meera", "webhooks"),
    ("u-sara", "Sara (New hire)", "sara-dev", 45, "backend-senior", "webhooks", "u-meera", "webhooks"),
    ("u-tom", "Tom (New hire)", "tom-dev", 88, "sre-mid", "engine", "u-ravi", "engine"),
]
# The cohort before: started five to six months ago and completed; they have moved on to other projects since.
PREVIOUS = [  # (id, name, started days ago, template, team, first PR day, merged day, completed day)
    ("u-lina", "Lina Park", 176, "backend-mid", "engine", 11, 18, 92),
    ("u-noah", "Noah Kim", 168, "backend-junior", "webhooks", 16, 24, 97),
    ("u-iris", "Iris Shah", 160, "backend-mid", "engine", 9, 15, 90),
    ("u-kofi", "Kofi Mensah", 152, "sre-mid", "engine", 13, 21, 94),
]
SURVEY_SCORES = [(4, 4, 3, 5, 4), (5, 4, 4, 4, 5), (3, 4, 3, 4, 4), (4, 5, 4, 3, 4), (5, 5, 4, 4, 5)]


def _audit_at(store, actor: str | None, action: str, target: str, detail: dict, at: datetime) -> None:
    """An audit entry in the demo's past (the in-memory log keeps the time it is given), so the log has a history to read."""
    store.audit_rows.append({"org_id": ORG, "actor_id": actor, "action": action, "target": target, "detail": detail, "created_at": at})


def _at(start: date, day: float, hour: int = 10) -> datetime:
    return datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=day, hours=hour)


class _IndexView:
    """The index, under another identity: caches keyed by the index object do not keep what the seed read."""

    def __init__(self, index):
        self._index = index

    def __getattr__(self, name):
        return getattr(self._index, name)


def seed_lifecycle(deps, lc, graph) -> None:
    """Programs, timelines, progress, blockers, check-ins, surveys, feedback, a verified answer and a handover, written
    through the LifecycleStore (and the Store for people, onboardings and tasks) with dates relative to today."""
    from types import SimpleNamespace

    from app.agents.core.schemas import Citation, ProposedTask, QAResult
    from app.lifecycle import handover, preboarding, summaries
    from app.lifecycle import templates as T
    from app.lifecycle.clock import Clock, business_days, week_start
    from app.lifecycle.context import Ctx, HireCtx
    from app.ports import (AreaProgress, BlockerAlert, CheckinReview, DocFeedback, EnvironmentCheck, ExpertPreferences,
                           OnboardingProgram, ProgressSummary, SurveyResponse, VerifiedAnswer)

    store, today, now = deps.store, date.today(), datetime.now(timezone.utc)
    # The seed reads the code through a throwaway view of the index: knowledge risk and the KT planner remember their
    # searches and plans per index object, and the demo's first requests should start as cold as a fresh server's.
    index = _IndexView(deps.index)
    ctx = Ctx(SimpleNamespace(lifecycle=lc, store=store, index=index, deps=deps, settings=deps.settings), Clock(),
              store.get_user("u-priya"))
    tpl = {t.key: t for t in T.list_for(lc, ORG)}

    def audit(actor: str | None, action: str, target: str, detail: dict, at: datetime) -> None:
        _audit_at(store, actor, action, target, detail, at)

    def history(hc_or_ob) -> bool:
        """The log has the current cohort's history; the cohort before them left it long ago."""
        ob = getattr(hc_or_ob, "ob", hc_or_ob)
        return ob.status == "active"

    teams = {"engine": lc.create_team(ORG, PROJECT, "Policy Engine"), "webhooks": lc.create_team(ORG, PROJECT, "Admission Webhooks")}
    store.add_user(User(id="u-exec", org_id=ORG, email="elena@demo.dev", name="Elena (Executive)", role="executive"), PROJECT)
    # no office hours: routing honours them (a question outside them waits for the digest), and the demo's mentor answers any day
    lc.save_expert_preferences(ORG, ExpertPreferences(user_id="u-ravi", max_interruptions_per_day=3, digest=True))

    def program(ob: Onboarding, key: str, team: str, mentor: str | None, focus: str, done: dict[str, float]) -> HireCtx:
        t = tpl[key]
        lc.create_program(OnboardingProgram(onboarding_id=ob.id, org_id=ORG, project_id=PROJECT, template_id=t.id,
                                            template_version=t.version,
                                            duration_days=t.duration_days, mentor_id=mentor, team_id=teams[team].id, focus_area=focus,
                                            plan_approved_at=_at(ob.start_date, -5), created_at=_at(ob.start_date, -7)),
                          T.milestones_for(t, ob.id, t.duration_days),
                          T.access_for(t, ob.id, {"manager": "u-priya", "mentor": mentor, "admin": "u-admin"}),
                          T.acknowledgements_for(t, ob.id))
        for a in lc.access_items(ob.id):  # requested at enrolment, a week before the start date
            lc.update_access_item(a.id, requested_at=_at(ob.start_date, -7))
            if history(ob):
                audit("u-priya", "access.requested", a.id, {"system": a.system, "owner_id": a.owner_id}, _at(ob.start_date, -7, 11))
        if history(ob):
            audit("u-priya", "onboarding.enrolled", ob.id, {"template": t.key, "version": t.version, "invite_on_approval": False},
                  _at(ob.start_date, -7, 9))
            if ob.id != "ob-vijay":  # his plan still waits for Priya in Approvals
                audit("u-priya", "approval.task_plan.decided", ob.id, {"decision": "approved"}, _at(ob.start_date, -5, 15))
        for k, day in done.items():
            lc.update_milestone(ob.id, k, status="done", done_at=_at(ob.start_date, day),
                                evidence={"auto": True} if k in T.AUTO_MILESTONES else {"by": "u-priya", "note": "Seeded"})
            if k not in T.AUTO_MILESTONES and history(ob):
                audit("u-priya", "milestone.updated", f"{ob.id}:{k}", {"status": "done"}, _at(ob.start_date, day, 14))
        return HireCtx(ctx, ob, lc.get_program(ob.id))

    def acknowledge_all(hc: HireCtx, day: float, keep: int = 0) -> None:
        for a in lc.acknowledgements(hc.ob.id)[:len(lc.acknowledgements(hc.ob.id)) - keep]:
            lc.acknowledge(hc.ob.id, a.doc_key, "", _at(hc.ob.start_date, day))
            # the hire did it through the pre-boarding link: the entry names them and says how, never "System"
            if history(hc):
                audit(hc.ob.new_hire_id, "preboarding.acknowledged", hc.ob.id, {"doc_key": a.doc_key, "via": "preboarding"},
                      _at(hc.ob.start_date, day))

    def grant(hc: HireCtx, day: float, keep: int = 0) -> None:
        items = lc.access_items(hc.ob.id)
        for a in items[:len(items) - keep]:
            lc.update_access_item(a.id, status="granted", granted_at=_at(hc.ob.start_date, day))
            if history(hc):
                audit(a.owner_id or "u-priya", "access.updated", a.id, {"status": "granted"}, _at(hc.ob.start_date, day, 15))

    def env(hc: HireCtx, at: datetime, failing: dict[str, str] | None = None) -> None:
        failing = failing or {}
        results = [{"name": n, "ok": n not in failing, "detail": failing.get(n, "found")} for n, _ in preboarding.tools_for(hc)]
        lc.add_environment_check(EnvironmentCheck(id="", onboarding_id=hc.ob.id, ran_at=at, ok=not failing, results=results))
        if history(hc):
            audit(hc.ob.new_hire_id, "preboarding.env_checked", hc.ob.id, {"ok": not failing, "via": "preboarding"}, at)

    def tasks(hc: HireCtx, rows: list[tuple]) -> None:
        """rows: (title, kind, due day, status, related paths, pr number)"""
        made = store.create_tasks(hc.ob.id, [ProposedTask(title=t, description=t, kind=k, due_in_days=max(1, min(60, d)),
                                                          related_paths=paths)
                                             for t, k, d, _, paths, _ in rows], hc.ob.start_date)
        for row, (_, _, day, status, _, pr) in zip(made, rows):
            store.update_task(row.id, status=status, **({"pr_number": pr,
                                                         "pr_url": f"https://github.com/demo/mini-policy-engine/pull/{pr}"} if pr else {}))
            if pr and status == "done":  # merged on GitHub (a merge, not an approved review, is what counts as contributed)
                lc.record_metric(ORG, PROJECT, "pr.merged", row.id, {"pr": pr}, _at(hc.ob.start_date, day + 5))

    def checkin(hc: HireCtx, day: int, late: int = 1) -> None:
        for role, author, answers in (("hire", hc.ob.new_hire_id, {"going_well": "Pairing sessions help.", "confidence": 4}),
                                      ("manager", "u-priya", {"strengths": "Careful reviews.", "on_track": 4})):
            lc.save_checkin(CheckinReview(id="", onboarding_id=hc.ob.id, day=day, author_role=role, author_id=author, answers=answers,
                                          created_at=_at(hc.ob.start_date, day + late)))
            if history(hc):
                audit(author, "checkin.written", hc.ob.id, {"day": day, "role": role}, _at(hc.ob.start_date, day + late))

    def survey(team: str, start: date, day: int, n: int) -> None:
        scores = SURVEY_SCORES[n % len(SURVEY_SCORES)]
        lc.add_survey_response(SurveyResponse(id="", org_id=ORG, team_id=teams[team].id,
                                              cohort=f"{start.year}-Q{(start.month - 1) // 3 + 1}",
                                              day=day, answers=dict(zip(("clarity", "support", "docs", "tools", "belonging"), scores))))

    def resolved_blocker(hc: HireCtx, rule: str, subject: str, opened: float, closed: float, evidence: dict) -> None:
        a = lc.open_blocker(BlockerAlert(id="", onboarding_id=hc.ob.id, rule=rule, subject=subject, evidence=evidence,
                                         opened_at=_at(hc.ob.start_date, opened)))
        lc.update_blocker(a.id, resolved_at=_at(hc.ob.start_date, closed), resolved_by=hc.ob.new_hire_id, stage="mentor",
                          notified_stage="mentor")

    # ---- Vijay, day 12: plan approved, two documents acknowledged, three of four access items granted, an environment
    # check failing again since yesterday (an open blocker his mentor was told about), progress in engine, feedback sent.
    vijay = program(store.get_onboarding("ob-vijay"), "backend-mid", "engine", "u-ravi", "engine",
                    {"preboarding_complete": -1, "environment_ready": 2, "meet_team": 4})
    acknowledge_all(vijay, -2)
    grant(vijay, 1, keep=1)
    opened = today - timedelta(days=1)
    while business_days(opened, today) < 1:
        opened -= timedelta(days=1)
    env(vijay, _at(vijay.ob.start_date, 1), {"docker": "not found"})
    env(vijay, _at(vijay.ob.start_date, 2))
    env(vijay, _at(opened, -1, 16), {"docker": "Cannot connect to the Docker daemon"})
    env(vijay, now - timedelta(hours=3), {"docker": "Cannot connect to the Docker daemon"})
    lc.open_blocker(BlockerAlert(id="", onboarding_id="ob-vijay", rule="environment", subject="env", stage="mentor",
                                 notified_stage="mentor",
                                 opened_at=_at(opened, 0, 9), evidence={"failing_since": _at(opened, -1, 16).isoformat(),
                                                                        "failing": ["docker"],
                                                                        "checks": 2, "hours": 40}))
    lc.save_area_progress(AreaProgress(onboarding_id="ob-vijay", area="engine", self_level="exploring", updated_at=now - timedelta(days=2),
                                       evidence={"steps": [{"title": "Code tour: engine/policy.py, load_policy",
                                                            "at": (now - timedelta(days=2)).isoformat()}]}))
    for fb in (DocFeedback(id="", project_id=PROJECT, target_kind="code", target_ref="engine/policy.py", kind="outdated",
                                owner_id="u-ravi", note="The module docstring still describes the old YAML loader.", created_by="u-vijay",
                                created_at=now - timedelta(days=3)),
               DocFeedback(id="", project_id=PROJECT, target_kind="meeting", target_ref="mtg-1", kind="unclear", owner_id="u-priya",
                                note="The part about default labels (around 3:00) goes by fast.", created_by="u-vijay",
                                created_at=now - timedelta(days=1))):
        made = lc.add_feedback(fb)
        audit("u-vijay", "feedback.submitted", made.id, {"target_kind": fb.target_kind, "kind": fb.kind}, fb.created_at)
    for back in (14, 7):
        start = week_start(today - timedelta(days=back))
        if start + timedelta(days=7) > vijay.ob.start_date:
            lc.save_summary(ProgressSummary(id="", onboarding_id="ob-vijay", week_start=start, body=summaries.build(vijay, start),
                                                      created_at=_at(start, 6, 17)))

    # ---- Arjun, Sara and Tom
    hires = {}
    for uid, name, gh, day, key, team, mentor, focus in CURRENT:
        store.add_user(User(id=uid, org_id=ORG, email=f"{uid[2:]}@demo.dev", name=name, role="new_hire", github_login=gh), PROJECT)
        ob = store.add_onboarding(Onboarding(id=f"ob-{uid[2:]}", project_id=PROJECT, new_hire_id=uid, manager_id="u-priya",
                                             start_date=today - timedelta(days=day), role_title=tpl[key].title))
        hires[uid] = ob
    arjun = program(hires["u-arjun"], "backend-junior", "webhooks", "u-meera", "webhooks", {})
    acknowledge_all(arjun, -1, keep=1)
    grant(arjun, 0, keep=3)
    env(arjun, _at(arjun.ob.start_date, 0, 11))
    lc.set_preboarding(arjun.ob.id, preboarding.token_hash(DEMO_PREBOARDING_TOKEN), now + timedelta(days=preboarding.LINK_DAYS))
    tasks(arjun, [("Set up your development environment", "learn", 2, "done", [], None),
                  ("Meet your mentor and the team", "meet", 3, "approved", [], None),
                  ("Read the README and the architecture notes", "learn", 5, "approved", [], None)])

    sara = program(hires["u-sara"], "backend-senior", "webhooks", "u-meera", "webhooks",
                   {"preboarding_complete": -2, "environment_ready": 1, "access_ready": 2, "meet_team": 3, "first_pr_opened": 12,
                    "service_traced": 18, "first_pr_merged": 19, "checkin_30": 31})
    acknowledge_all(sara, -3)
    grant(sara, 1)
    env(sara, _at(sara.ob.start_date, 1))
    tasks(sara, [("Read the README and the architecture notes", "learn", 5, "done", [], None),
                 ("Add a request size limit to the webhook server", "code", 14, "done", ["webhooks/server.py"], 101),
                 ("Write a runbook for webhook timeouts", "learn", 50, "in_progress", [], None)])
    checkin(sara, 30)
    survey("webhooks", sara.ob.start_date, 30, 1)
    lc.update_program(sara.ob.id, survey_days=[30])  # the program remembers the days answered, never the answers

    tom = program(hires["u-tom"], "sre-mid", "engine", "u-ravi", "engine",
                  {"preboarding_complete": -1, "environment_ready": 2, "access_ready": 4, "meet_team": 5, "first_pr_opened": 9,
                   "first_pr_merged": 16, "checkin_30": 31, "oncall_shadow": 38, "area_working": 44, "checkin_60": 62,
                   "area_contributed": 70})
    acknowledge_all(tom, -2)
    grant(tom, 2)
    env(tom, _at(tom.ob.start_date, 2))
    tasks(tom, [("Shadow an on-call shift", "meet", 20, "done", [], None),
                ("Add a metric for rejected resources", "code", 14, "done", ["engine/validate.py"], 98)])
    checkin(tom, 30)
    checkin(tom, 60)
    resolved_blocker(tom, "stalled_task", "oncall-shadow", 23, 25, {"title": "Shadow an on-call shift", "n": 3})
    for d, n in ((30, 2), (60, 3)):
        survey("engine", tom.ob.start_date, d, n)
    lc.update_program(tom.ob.id, survey_days=[30, 60])

    # ---- the previous cohort, completed
    for uid, name, ago, key, team, pr_day, merge_day, done_day in PREVIOUS:
        store.add_user(User(id=uid, org_id=ORG, email=f"{uid[2:]}@demo.dev", name=name, role="new_hire"))  # not on this project any more
        ob = store.add_onboarding(Onboarding(id=f"ob-{uid[2:]}", project_id=PROJECT, new_hire_id=uid, manager_id="u-priya",
                                             start_date=today - timedelta(days=ago), role_title=tpl[key].title, status="completed"))
        t = tpl[key]
        late = 3 if uid in {"u-noah", "u-kofi"} else 0
        done = {m["key"]: T.scale(m["due_day"], t.duration_days) + (late if m["key"].startswith("checkin") else 0) for m in t.milestones}
        done.update(first_pr_opened=pr_day, first_pr_merged=merge_day)
        hc = program(ob, key, team, "u-ravi" if team == "engine" else "u-meera", team, done)
        acknowledge_all(hc, -2)
        grant(hc, 2)
        env(hc, _at(ob.start_date, 1))
        tasks(hc, [("Read the README and the architecture notes", "learn", 5, "done", [], None),
                   (f"First change: {'validation messages' if team == 'engine' else 'webhook errors'}", "code", 14, "done",
                    ["engine/validate.py" if team == "engine" else "webhooks/server.py"], 60 + pr_day)])
        for d in T.checkin_days(t.duration_days):
            checkin(hc, d, late=1 + late)
            if not (uid == "u-noah" and d == 90):
                survey(team, ob.start_date, d, len(uid) + d)
        resolved_blocker(hc, "environment", "env", 1, 2 + late, {"failing": ["docker"], "hours": 30})
        lc.update_program(ob.id, completed_at=_at(ob.start_date, done_day),
                          completion={"decision": "complete", "note": "Signed off at the completion review.", "by": "u-priya",
                                      "at": _at(ob.start_date, done_day).isoformat(), "unmet": []})
        q = "How does check_labels report missing labels?" if team == "engine" else "Where does the webhook server read the request body?"
        path = "engine/validate.py" if team == "engine" else "webhooks/server.py"
        for i in range(2):
            mid = store.save_chat(PROJECT, uid, q, QAResult(status="answered", answer="Answered from the code.", confidence=0.7,
                                                            citations=[Citation(chunk_id=None, source_type="code", label=path,
                                                                                ref={"path": path})]))
            lc.record_metric(ORG, PROJECT, "answer.reused", uid, {"message_id": mid}, _at(ob.start_date, 20 + i))
        store.save_chat(PROJECT, uid, "Who decides the on-call rota?", QAResult(status="not_enough_information",
                                                                                answer="Handed to a person."))

    # ---- knowledge: a verified answer routing reuses (tier 2), and a handover for Meera, who is leaving
    va = lc.add_verified_answer(VerifiedAnswer(id="", project_id=PROJECT, question="Who decides the on-call rota?",
                                               answer="Priya sets it each month; swaps are agreed in the team channel and noted in the rota.",
                                               verified_by="u-ravi", reuse_count=2, created_at=now - timedelta(days=20)))
    # indexed as a source the way POST /api/verified-answers indexes one (api/lifecycle._index_verified), so the Guide finds
    # and cites it ("how do I swap my on-call week?"), not only routing's tier 2
    from app.agents.core.schemas import Chunk

    verifier = store.get_user(va.verified_by)
    name = verifier.name if verifier else "an expert"
    deps.index.upsert([Chunk(id=f"va-{va.id}", project_id=PROJECT, source_type="verified_answer",
                             ref={"question": va.question, "expert_id": va.verified_by, "expert_name": name, "verified_answer_id": va.id},
                             content=f"Q: {va.question}\nA (verified by {name}): {va.answer}")])
    real = deps.index
    deps.index = index  # the Keeper reads the code through the seed's view too: the knowledge caches start cold
    try:  # the Keeper (handover.requested), through the Supervisor like any request: the plan, its KT sessions and a note
        out = run_event(graph, {"type": "handover.requested", "project_id": PROJECT, "user_id": "u-priya", "person_id": "u-meera",
                                "last_day": (today + timedelta(days=21)).isoformat()}, "seed:handover")
    finally:
        deps.index = real
    h = lc.get_handover(out["result"]["id"])
    items = h.plan.get("items", [])
    handover.update(ctx, h, "in_progress", [i["key"] for i in items[:1]], [], None, False)
    audit("u-priya", "handover.created", h.id, {"user_id": "u-meera", "last_day": (today + timedelta(days=21)).isoformat()}, now - timedelta(days=3))
    seed_conversations(deps, lc, audit, now, today)


def seed_conversations(deps, lc, audit, now: datetime, today: date) -> None:
    """What the new hires asked, a question waiting in Ravi's inbox with the assistant's draft, Ravi's booking link and an audit
    history beyond sign-ins, so every page opens with something to read. The questions are generic (no names, no addresses). The
    answered ones cite a KT clip, a document or a verified answer, not code, so they add no demand to the knowledge views."""
    from app.agents.core.schemas import Citation, KTLink, QAResult

    store = deps.store

    def ask(uid: str, question: str, result: QAResult, when: datetime) -> str:
        mid = store.save_chat(PROJECT, uid, question, result)
        store.chats[mid]["created_at"] = when
        audit(uid, "question.routed", mid, {"tier": 1 if result.status == "answered" else 3, "status": result.status}, when)
        return mid

    clip = KTLink(meeting_id="mtg-1", meeting_title="KT #1: How validation works", file_path="engine/mutate.py", symbol="apply_defaults",
                  start_s=182, end_s=230, confidence=0.9, method="symbol")
    kt = Citation(chunk_id=None, source_type="transcript", label="KT #1: How validation works, 3:02", ref={"meeting_id": "mtg-1", "start_s": 182})
    guide = Citation(chunk_id=None, source_type="doc", label="docs/CONTRIBUTING.md", ref={"path": "docs/CONTRIBUTING.md"})
    ask("u-vijay", "Where do the default labels come from?",
        QAResult(status="answered", answer="They are added by apply_defaults before validation runs; Ravi walks through it in the first KT session.",
                 citations=[kt], kt_clips=[clip], confidence=0.82), now - timedelta(days=4, hours=3))
    ask("u-arjun", "How do I run the tests locally?",
        QAResult(status="answered", answer="Run pytest from the repository root, as the contributing guide says.", citations=[guide],
                 confidence=0.74), now - timedelta(hours=26))
    ask("u-sara", "Which environment variables does the webhook server read at startup?",
        QAResult(status="answered", answer="The contributing guide lists them under 'Running the webhook server'.", citations=[guide],
                 confidence=0.7), now - timedelta(days=9))
    reused = ask("u-tom", "Who decides the on-call rota?",
                 QAResult(status="answered", answer="The team lead sets it each month; swaps are agreed in the team channel.",
                          citations=[Citation(chunk_id=None, source_type="verified_answer", label="Verified by a senior engineer",
                                              ref={"question": "Who decides the on-call rota?"})], confidence=0.9), now - timedelta(days=30))
    lc.record_metric(ORG, PROJECT, "answer.reused", "u-tom", {"message_id": reused}, now - timedelta(days=30))
    # a question the assistant could not answer from its sources: handed to Ravi, with the draft it wrote (his inbox shows both).
    # It has no chat turn of its own here, so the knowledge views (which anchor unanswered turns with a code search) see no new demand.
    question = "Who signs off a change to the staging deploy checklist?"
    draft = ("I found nothing about the staging deploy checklist in the code, the documents or the KT sessions. CODEOWNERS gives the "
             "engine folder to one person, so the engine owner may be the one to ask. This draft is unverified.")
    asked_at = now - timedelta(hours=20)
    rid = store.create_expert_request(PROJECT, question, "u-ravi", "u-vijay", None, draft=draft)
    store.expert_requests[rid]["created_at"] = asked_at
    store.notify("u-ravi", "expert_request", f"Vijay asked: {question}", f"/experts?project={PROJECT}")
    # never "today's" count: today as the expert's daily budget counts it, in UTC (routing._today_start), not the server's
    # local date, which runs ahead of UTC in the hours after local midnight east of Greenwich
    told = min(asked_at, now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=1))
    lc.record_metric(ORG, PROJECT, "interruption", "u-ravi", {"request_id": rid, "urgent": False}, told)
    lc.record_metric(ORG, PROJECT, "route.escalated", "u-vijay",
                     {"tier": 3, "request_id": rid, "target": "u-ravi", "draft": draft, "delivery": "now", "urgent": False,
                      "cited_paths": []}, told)
    audit("u-vijay", "question.routed", rid, {"tier": 3, "status": "routed"}, asked_at)
    # Ravi's own booking link for KT sessions
    store.set_scheduling_link(ORG, "u-ravi", "calendly", "https://calendly.com/ravi-demo/kt-session")
    # the log's earlier history
    for actor, action, target, detail, days in (
            ("u-admin", "project.created", PROJECT, {"name": "Mini Policy Engine"}, 31), ("u-admin", "repo.connected", "repo-demo", {}, 31),
            ("u-admin", "member.added", "u-ravi", {"role": "expert"}, 30), ("u-admin", "member.added", "u-meera", {"role": "expert"}, 30),
            ("u-admin", "member.added", "u-priya", {"role": "manager"}, 30), ("u-priya", "meeting.created", "mtg-1", {"via": "manual"}, 3),
            ("u-ravi", "recording.consent_confirmed", "mtg-1", {"notice_version": _notice_version(), "via": "upload"}, 2),
            ("u-ravi", "meeting.recording_uploaded", "mtg-1", {}, 2), ("u-ravi", "expert.preferences", "u-ravi", {"max_interruptions_per_day": 3}, 12),
            ("u-ravi", "answer.verified", "va-on-call-rota", {}, 20), ("u-priya", "preboarding.link_created", "ob-arjun", {}, 2),
            ("u-priya", "setup.updated", ORG, {"minutes_per_interruption": 15}, 8)):
        audit(actor, action, target, detail, now - timedelta(days=days))


# ---------------------------------------------------------------- the milestone board (app/lifecycle/board.py)
# (title, kind, due day, status, from a KT session, what it asks). Statuses: approved = To do, in_progress, submitted (waiting
# for sign-off), changes_requested, done. Kept out of what other views count: no open code task (knowledge risk, the KT planner
# and the first-task shortlist read those), no file or symbol in the words, and Vijay's learn tasks before his plan's are To do.
BOARD = {
    "ob-arjun": [
        ("Run the test suite locally", "learn", 6, "in_progress", False, "Run it from the repository root until it passes."),
        ("Watch KT #1: How validation works", "learn", 8, "approved", True, "Ravi's walk-through of how a resource is checked."),
        ("Pick a first issue with Meera", "meet", 12, "approved", False, "Go through the good first issues together."),
        ("Pair on a small fix with Meera", "meet", 28, "approved", False, "A small change to the webhook server, reviewed together."),
        ("Write a short guide to the webhook tests", "learn", 42, "approved", False, "What each test covers, for the next hire."),
        ("Own the webhook timeout alerts", "meet", 58, "approved", False, "Be the first responder for one alert, with Meera as backup."),
        ("Retro: what would have helped in week one", "learn", 88, "approved", False, "Three things, written down for the team."),
    ],
    "ob-vijay": [
        ("Meet your mentor and the team", "meet", 3, "done", False, "A 30-minute introduction with Ravi and the team."),
        ("Pair on the local setup with Ravi", "meet", 5, "done", False, "Get the services running on your laptop together."),
        ("Walk through KT #1 with Ravi", "meet", 7, "submitted", True, "Replay the recorded session and ask about the parts that went fast."),
        ("Sit in on the team's planning meeting", "meet", 9, "in_progress", False, "Listen first; ask one question at the end."),
        ("Fix the Docker check on your laptop", "learn", 10, "approved", False, "The environment check fails on Docker since yesterday."),
        ("Read the policy module before Friday", "learn", 14, "approved", True, "Asked for in KT #1, before your first pull request."),
        ("Trace one request through the engine", "learn", 9, "approved", False, "Follow the code tour from loading a policy to its violations."),
        ("Pair with Ravi on the label rules", "meet", 18, "approved", False, "One hour, on how missing labels are reported."),
        ("Write down three questions about validation", "learn", 24, "approved", False, "Ask them in the chat or bring them to Ravi."),
        ("Present the validation flow at team sync", "meet", 44, "approved", False, "Ten minutes, with one diagram."),
        ("Review two pull requests in the engine", "learn", 55, "approved", False, "Leave at least one useful comment on each."),
        ("Write down one thing to improve in onboarding", "learn", 85, "approved", False, "For the next hire's program."),
    ],
    "ob-sara": [
        ("Trace a request through the webhook server", "learn", 10, "done", False, "From the HTTP handler to the policy engine."),
        ("Pair with Meera on admission timeouts", "meet", 24, "done", False, "Why the server answers within ten seconds."),
        ("Review three pull requests in webhooks", "learn", 36, "submitted", False, "Leave a review on each, then tell Priya."),
        ("Document the admission flow", "learn", 40, "approved", False, "One page, with the order of the checks."),
        ("Write the retry design note", "learn", 42, "changes_requested", False, "When the client retries, and when it gives up."),
        ("Draft the admission flow diagram", "learn", 43, "in_progress", False, "One diagram the next hire can follow."),
        ("Lead the webhooks design review", "meet", 72, "approved", False, "The retry design, with Meera and Ravi."),
        ("Mentor the next hire's first week", "meet", 80, "approved", False, "Arjun's environment, access and first issue."),
    ],
    "ob-tom": [
        ("Set up the cloud console", "learn", 5, "done", False, "Read-only access first, then the staging project."),
        ("Write the on-call checklist", "learn", 26, "done", False, "What to check in the first five minutes."),
        ("Run a game day for the engine", "meet", 38, "done", False, "Break the policy reload on staging, then fix it."),
        ("Automate the policy reload alert", "code", 50, "done", False, "Page when a reload fails twice."),
        ("Present the engine's SLOs at team sync", "meet", 66, "done", False, "Availability and latency, with this quarter's numbers."),
        ("Hand over the pager rota notes", "learn", 78, "submitted", False, "Everything the rota taught you, for the next person."),
        ("Complete the 90-day check-in", "meet", 86, "in_progress", False, "With Priya: what went well, what to change."),
        ("Write down one thing to improve in onboarding", "learn", 89, "approved", False, "For the next hire's program."),
    ],
}
# (onboarding, task title, to milestone, to due day, days ago): Priya moved them to a later milestone
BOARD_MOVES = [("ob-vijay", "Trace one request through the engine", "first_change", 20, 2),
               ("ob-sara", "Document the admission flow", "own_an_area", 55, 6),
               ("ob-arjun", "Write a short guide to the webhook tests", "own_an_area", 52, 1)]


def seed_board(deps, lc) -> None:
    """Four boards at four stages: Arjun on day 3, Vijay on day 12, Sara on day 45 (with a milestone Priya added on day 75)
    and Tom on day 88. Tasks in every column, a few overdue, some waiting for sign-off, one with changes asked for, and tasks
    Priya moved to a later milestone (the board's "Moved" state, with its history). Written after the agents' seeded runs, so
    those read the same data as before."""
    from types import SimpleNamespace

    from app.agents.core.schemas import ProposedTask
    from app.lifecycle import board, hires
    from app.lifecycle.clock import Clock
    from app.lifecycle.context import Ctx
    from app.ports import Phase, TaskMove, TaskPlacement

    store, now = deps.store, datetime.now(timezone.utc)
    ctx = Ctx(SimpleNamespace(lifecycle=lc, store=store, index=deps.index, deps=deps, settings=deps.settings), Clock(),
              store.get_user("u-priya"))
    for oid, rows in BOARD.items():
        hc = hires.load(ctx, oid)
        board.phases(hc)  # the template's milestones, as the first read of the board writes them
        start = hc.ob.start_date
        made = store.create_tasks(oid, [ProposedTask(title=t, description=d, kind=k, due_in_days=day, source="meeting" if kt else "plan")
                                        for t, k, day, _, kt, d in rows], start)
        for row, (title, _, _, status, _, _) in zip(made, rows):
            store.update_task(row.id, status=status)
            _audit_at(store, "u-priya", "board.task.added", row.id, {"title": title, "onboarding_id": oid, "project_id": hc.ob.project_id},
                      _at(start, -3, 9))
    # Sara's board: a milestone Priya added for the design review she leads
    lc.add_phase(Phase(onboarding_id="ob-sara", key="design_review", title="Leading a design review", due_day=75, position=4))
    lc.update_phase("ob-sara", "fully_on_board", position=5)
    _audit_at(store, "u-priya", "board.milestone.added", "ob-sara:design_review", {"title": "Leading a design review", "due_day": 75,
                                                                                    "onboarding_id": "ob-sara", "project_id": PROJECT},
              now - timedelta(days=4))
    for oid, title, to, day, ago in BOARD_MOVES:
        ob = store.get_onboarding(oid)
        task = next(t for t in store.tasks(onboarding_id=oid) if t.title == title)
        phases = {p.key: p for p in lc.phases(oid)}
        old_day = (task.due_date - ob.start_date).days
        frm = next(p for p in sorted(phases.values(), key=lambda p: p.due_day) if p.due_day >= old_day)
        due = ob.start_date + timedelta(days=day)
        store.update_task(task.id, due_date=due)
        lc.save_placements(oid, [TaskPlacement(task_id=task.id, onboarding_id=oid, phase_key=to)])
        at = now - timedelta(days=ago, hours=3)
        lc.add_task_move(TaskMove(onboarding_id=oid, task_id=task.id, from_phase=frm.key, to_phase=to, from_title=frm.title,
                                  to_title=phases[to].title, from_due=task.due_date, to_due=due, actor_id="u-priya", at=at))
        _audit_at(store, "u-priya", "board.task.rescheduled", task.id, {  # as app/lifecycle/board.py writes it
            "from": frm.key, "to": to, "title": title, "from_title": frm.title, "to_title": phases[to].title, "from_day": frm.due_day,
            "to_day": phases[to].due_day, "due_date": due.isoformat(), "onboarding_id": oid, "project_id": ob.project_id}, at)
    lc.save_placements("ob-sara", [TaskPlacement(task_id=next(t.id for t in store.tasks(onboarding_id="ob-sara")
                                                              if t.title == "Lead the webhooks design review"),
                                                 onboarding_id="ob-sara", phase_key="design_review")])


# ---------------------------------------------------------------- the agents (docs/AGENTS.md)
# Recent runs for the Agents page: what each agent did in the week before the demo starts. Marked demo_history: they are the
# demo's past, not runs of this process (they have no trace).
HISTORY = [  # (agent run name, trigger, status, summary, days ago)
    ("supervisor", "lifecycle.tick", "succeeded", "Daily tick: ran the Coach and the Planner", 1),
    ("ingestion", "repo.pushed", "succeeded", "Re-indexed the files a push changed", 2),
    ("ingestion", "repo.pushed", "succeeded", "Re-indexed the files a push changed", 5),
    ("meeting", "meeting.recording_ready", "succeeded", "Wrote the notes of 'KT #1: How validation works'", 2),
    ("qa", "chat.question", "succeeded", "Answered a question in chat from 2 sources", 1),
    ("guide", "ask.route", "succeeded", "Routed a question: tier 3, handed to a person (the mentor)", 3),
    ("guide", "ask.route", "succeeded", "Routed a question: tier 1, answered from sources", 4),
    ("task", "onboarding.started", "succeeded", "Drafted a 30/60/90 plan for approval", 6),
    ("scout", "first_task.requested", "succeeded", "Shortlisted 1 first task (1 excluded)", 3),
    ("review", "pr.updated", "succeeded", "Reviewed a pull request: needs work (2 rule findings)", 4),
    ("reviewer", "presubmit.requested", "succeeded", "A private review ran.", 5),
    ("coach", "lifecycle.tick", "succeeded", "Daily check of 4 programs: 1 open alert, 1 nudge sent", 1),
    ("coach", "week.closed", "succeeded", "Wrote 4 weekly summaries", 5),
    ("keeper", "handover.requested", "succeeded", "Drafted a handover plan for Meera", 3),
    ("keeper", "repo.pushed", "failed", "Keeper could not finish", 6),
]


def seed_agents(deps, graph, lc) -> None:
    """The agents at work on the demo data, through the Supervisor like any event: two flags on a KT call and on a document
    make the Keeper propose a re-recording and a doc fix; the previous cohort's completion makes the Planner propose a
    template change; today's tick runs the Coach (nudges, weekly summaries) and the Planner (Tom's completion draft). The
    proposals wait in Approvals, decidable like any other. Then a week of run history for the Agents page."""
    from app.ports import DocFeedback

    store, now = deps.store, datetime.now(timezone.utc)
    # the Keeper: "possibly outdated" on the KT call that explains engine/validate.py (a rerecord with its speaker, Ravi)
    flag = lc.add_feedback(DocFeedback(id="", project_id=PROJECT, target_kind="meeting", target_ref="mtg-1", kind="outdated",
                                       owner_id="u-priya", created_by="u-sara", created_at=now - timedelta(hours=20),
                                       note="check_labels in engine/validate.py was rewritten since: the clip at 0:55 shows the old loop."))
    _audit_at(store, "u-sara", "feedback.submitted", flag.id, {"target_kind": "meeting", "kind": "outdated"}, flag.created_at)
    run_event(graph, {"type": "feedback.created", "project_id": PROJECT, "feedback_id": flag.id}, "seed:keeper-rerecord")
    # the Keeper: two readers found the contributing guide unclear (a doc fix for its owner; never pushed)
    for who, note, hours in (("u-arjun", "It does not say where the test for a new engine module goes, or what to call it.", 30),
                             ("u-sara", "Run pytest from where? The repository root or engine/?", 6)):
        flag = lc.add_feedback(DocFeedback(id="", project_id=PROJECT, target_kind="code", target_ref="docs/CONTRIBUTING.md", kind="unclear",
                                           owner_id="u-priya", created_by=who, note=note, created_at=now - timedelta(hours=hours)))
        _audit_at(store, who, "feedback.submitted", flag.id, {"target_kind": "code", "kind": "unclear"}, flag.created_at)
    run_event(graph, {"type": "feedback.created", "project_id": PROJECT, "feedback_id": flag.id}, "seed:keeper-doc-edit")
    # the Planner: the previous Backend cohort's completions suggest a change to the Mid Backend template
    run_event(graph, {"type": "program.completed", "project_id": PROJECT, "onboarding_id": "ob-iris"}, "seed:planner-template")
    # today's tick: the Coach and the Planner
    run_event(graph, {"type": "lifecycle.tick", "org_id": ORG, "day": date.today().isoformat()}, "seed:tick")
    for name, trigger, status, summary, ago in HISTORY:
        rid = store.record_run(PROJECT, name, trigger, status, {"summary": summary, "demo_history": True})
        row = next((r for r in reversed(getattr(store, "runs", [])) if r.get("id") == rid), None)
        if row is not None:  # the in-memory store: give the run its day in the past week
            row["at"] = now - timedelta(days=ago, hours=ago * 2)


def _notice_version() -> str:
    from app import legal

    return legal.VERSIONS["recording"]


def _recording_records(store, m) -> None:
    """What a recorded session carries (app/services/recordings.py): the consent Ravi confirmed when he uploaded it, and
    one removal request that Priya has already answered, so the demo shows both without a pending item nobody made."""
    from app.adapters import recording_store
    from app.ports import RecordingConsent

    at = (m.started_at or datetime.now(timezone.utc)) + timedelta(hours=1)
    store.update_meeting(m.id, recording_consent=RecordingConsent(by="u-ravi", by_name=store.get_user("u-ravi").name, at=at,
                                                                  notice_version=_notice_version()))
    row, _ = recording_store.create_removal_request(store, ORG, PROJECT, m.id, "u-meera", "I name a customer near the end. Can this come out?")
    recording_store.close_removal_requests(store, m.id, recording_store.DECLINED, "u-priya",
                                           "Checked with Meera: that customer is already public. The recording stays.", row["id"])
