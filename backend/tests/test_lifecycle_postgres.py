"""The LifecycleStore on PostgreSQL, as ww_app under row-level security (the setup of tests/test_postgres.py, whose
fixtures this module reuses): every method round-trips, one organisation never sees another's lifecycle data, the
pre-boarding link resolves before the tenant is known, the services run on it end to end, and an organisation's export
and deletion cover every table. Skipped without TEST_PG_ADMIN_URL."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.adapters.lifecycle_pg import PgLifecycleStore
from app.adapters.memory import InlineBus, LocalBlobStorage, LocalRepoSource
from app.adapters.postgres import PgIndex, current_org
from app.agents.core.deps import Deps
from app.agents.core.llm import OfflineLLM
from app.agents.core.schemas import ReviewResult
from app.agents.supervisor.graph import build_supervisor
from app.config import Settings
from app.container import Container
from app.lifecycle import enrolment, hires, preboarding, visibility
from app.lifecycle import templates as T
from app.lifecycle.clock import Clock
from app.lifecycle.context import Ctx
from app.ports import (AccessItem, AreaProgress, BlockerAlert, CheckinReview, DocFeedback, EnvironmentCheck, ExpertPreferences, Handover,
                       OnboardingProgram, ProgressSummary, SurveyResponse, VerifiedAnswer)
from app.services.embeddings import HashEmbedder
from app.services.redaction import RegexRedactor
from tests.test_postgres import ADMIN, SAMPLE, _org, db, engine, store

FIXTURES = (db, engine, store)  # test_postgres's database fixtures, reused here by name

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")
NOW = datetime.now(timezone.utc)


@pytest.fixture
def lc(engine):
    return PgLifecycleStore(engine)


def _program(store, lc, label: str):
    """An organisation with a project, a hire who has not joined yet (an unbound user row) and their program."""
    admin, project = _org(store, label)
    hire = lc.ensure_user(admin.org_id, f"hire-{label}-{uuid.uuid4().hex[:6]}@example.com", "Nia", "nia")
    assert lc.ensure_user(admin.org_id, hire.email.upper(), "Other", None).id == hire.id  # one row per email
    ob = store.create_onboarding(project.id, hire.id, admin.id, date.today() - timedelta(days=10), "Engineer")
    t = T.list_for(lc, admin.org_id)[0]
    team = lc.create_team(admin.org_id, project.id, "Core")
    program = lc.create_program(
        OnboardingProgram(onboarding_id=ob.id, org_id=admin.org_id, project_id=project.id, template_id=t.id, template_version=t.version,
                          mentor_id=admin.id, team_id=team.id, focus_area="engine"),
        T.milestones_for(t, ob.id, 90), T.access_for(t, ob.id, {"manager": admin.id}), T.acknowledgements_for(t, ob.id))
    return admin, project, hire, ob, program, team


def test_every_method_round_trips(store, lc):
    admin, project, hire, ob, program, team = _program(store, lc, "rt")
    org, pid = admin.org_id, project.id
    assert lc.get_program(ob.id) == program and [p.onboarding_id for p in lc.programs(org, pid)] == [ob.id]
    # templates: 16 defaults, a new version replaces the active one
    templates = lc.templates(org)
    assert len(templates) == 16 and len({t.key for t in templates}) == 16
    old = templates[0]
    new = lc.new_template_version(old.id, duration_days=120)
    assert (new.version, new.duration_days, new.active) == (2, 120, True) and lc.new_template_version(old.id, title="x") is None
    assert [t.version for t in lc.templates(org, include_inactive=True) if t.key == old.key] == [2, 1]
    # settings, teams
    assert lc.org_settings(org).minutes_per_interruption == 15
    assert lc.update_org_settings(org, minutes_per_interruption=20, setup={"skipped": ["teams"]}).setup == {"skipped": ["teams"]}
    assert lc.update_team(team.id, name="Platform").name == "Platform" and [t.name for t in lc.teams(org, pid)] == ["Platform"]
    # the timeline
    keys = [m.key for m in lc.milestones(ob.id)]
    assert keys[0] == "preboarding_complete" and "checkin_90" in keys
    done = lc.update_milestone(ob.id, "meet_team", status="done", done_at=NOW, evidence={"by": admin.id})
    assert done.status == "done" and done.evidence == {"by": admin.id}
    item = lc.add_access_item(AccessItem(id="", onboarding_id=ob.id, system="VPN", owner_id=admin.id))
    assert lc.update_access_item(item.id, status="granted", granted_at=NOW).status == "granted" and lc.get_access_item(item.id).granted_at
    ack = lc.acknowledgements(ob.id)[0]
    assert lc.acknowledge(ob.id, ack.doc_key, "h", NOW).acknowledged_at and lc.acknowledge(ob.id, "nope", "h", NOW) is None
    lc.add_environment_check(EnvironmentCheck(id="", onboarding_id=ob.id, ran_at=NOW, ok=False, results=[{"name": "git", "ok": False}]))
    assert lc.environment_checks(ob.id)[0].results == [{"name": "git", "ok": False}]
    # progress, blockers, summaries, check-ins
    lc.save_area_progress(AreaProgress(onboarding_id=ob.id, area="engine", evidence={"steps": [{"title": "a"}]}))
    assert lc.save_area_progress(AreaProgress(onboarding_id=ob.id, area="engine", self_level="working")).self_level == "working"
    first = lc.open_blocker(BlockerAlert(id="", onboarding_id=ob.id, rule="environment", subject="env"))
    assert lc.open_blocker(BlockerAlert(id="", onboarding_id=ob.id, rule="environment", subject="env")).id == first.id  # one open
    lc.update_blocker(first.id, resolved_at=NOW, resolved_by="system")
    again = lc.open_blocker(BlockerAlert(id="", onboarding_id=ob.id, rule="environment", subject="env"))
    assert again.id != first.id and [b.id for b in lc.blockers(ob.id, open_only=True)] == [again.id]
    week = date.today() - timedelta(days=date.today().weekday())
    lc.save_summary(ProgressSummary(id="", onboarding_id=ob.id, week_start=week, body={"lines": [1]}))
    assert lc.save_summary(ProgressSummary(id="", onboarding_id=ob.id, week_start=week, body={"lines": [2]})).body == {"lines": [2]}
    assert len(lc.summaries(ob.id)) == 1
    lc.save_checkin(CheckinReview(id="", onboarding_id=ob.id, day=30, author_role="hire", author_id=hire.id, answers={"a": 1}))
    assert lc.save_checkin(CheckinReview(id="", onboarding_id=ob.id, day=30, author_role="hire", author_id=hire.id,
                                         answers={"a": 2})).answers == {"a": 2}
    # feedback, verified answers, preferences, surveys, handovers, metrics
    fb = lc.add_feedback(DocFeedback(id="", project_id=pid, target_kind="code", target_ref="a.py", kind="unclear", owner_id=admin.id,
                                     created_by=hire.id))
    assert lc.update_feedback(fb.id, status="resolved").status == "resolved" and lc.feedback(pid)[0].id == fb.id
    va = lc.add_verified_answer(VerifiedAnswer(id="", project_id=pid, question="Q?", answer="A.", verified_by=admin.id,
                                               source_request_id="not-a-uuid"))
    lc.bump_reuse(va.id)
    assert lc.verified_answers(pid)[0].reuse_count == 1
    assert lc.expert_preferences(admin.id).max_interruptions_per_day == 3
    lc.save_expert_preferences(org, ExpertPreferences(user_id=admin.id, max_interruptions_per_day=1, office_hours={"days": ["Tue"]}))
    assert lc.expert_preferences(admin.id).office_hours == {"days": ["Tue"]}
    lc.add_survey_response(SurveyResponse(id="", org_id=org, team_id=team.id, cohort="2026-Q4", day=30, answers={"clarity": 4}))
    assert [r.answers for r in lc.survey_responses(org)] == [{"clarity": 4}]
    h = lc.add_handover(Handover(id="", org_id=org, project_id=pid, user_id=admin.id, last_day=date.today(), plan={"items": []},
                                 created_by=admin.id))
    assert lc.update_handover(h.id, status="done").status == "done" and lc.handovers(org)[0].id == h.id
    lc.record_metric(org, pid, "interruption", admin.id, {"n": 1})
    assert [m.props for m in lc.metrics(org, ["interruption"], NOW - timedelta(minutes=5))] == [{"n": 1}]
    lc.update_program(ob.id, survey_days=[30], first_task={"title": "x"})
    assert lc.get_program(ob.id).survey_days == [30]
    lc.set_onboarding_status(ob.id, "paused")
    assert store.get_onboarding(ob.id).status == "paused"


def test_reads_of_agent_tables(store, lc):
    admin, project, hire, ob, *_ = _program(store, lc, "ag")
    from app.agents.core.schemas import ProposedTask
    [task] = store.create_tasks(ob.id, [ProposedTask(title="Fix", description="d", kind="code", due_in_days=3)], date.today())
    assert task.id in lc.task_activity([task.id, "nope"])
    store.save_review(task.id, 7, "https://x/7", "sha", ReviewResult(recommendation="needs_work", summary="s"))
    [r] = lc.reviews_for_tasks([task.id])
    assert (r["head_sha"], r["recommendation"], r["decision"]) == ("sha", "needs_work", None)
    rid = store.create_expert_request(project.id, "Why?", admin.id, hire.id, None)
    store.answer_expert_request(rid, "Because.")
    assert [x["answer"] for x in lc.answered_requests(project.id)] == ["Because."]


def test_one_organisation_never_sees_anothers_lifecycle(store, lc):
    a_admin, a_project, _, a_ob, *_ = _program(store, lc, "ta")
    lc.set_preboarding(a_ob.id, preboarding.token_hash("tok-a"), NOW + timedelta(days=1))
    b_admin, b_project = _org(store, "tb")  # tenant is now B
    assert lc.get_program(a_ob.id) is None and lc.programs(b_admin.org_id) == [] and lc.milestones(a_ob.id) == []
    assert lc.teams(b_admin.org_id) == [] and lc.templates(b_admin.org_id) == [] and lc.survey_responses(b_admin.org_id) == []
    assert lc.blockers(a_ob.id) == [] and lc.update_milestone(a_ob.id, "meet_team", status="done") is None
    current_org.set(None)  # the pre-boarding portal: no tenant yet
    found = lc.program_by_token(preboarding.token_hash("tok-a"))
    assert found[:2] == (a_ob.id, a_admin.org_id)
    assert lc.program_by_token(preboarding.token_hash("other")) is None
    assert lc.programs(a_admin.org_id) == []  # and nothing else is readable without one
    current_org.set(a_admin.org_id)
    assert preboarding.resolve(lc, "tok-a") == (a_ob.id, a_admin.org_id)
    lc.set_preboarding(a_ob.id, preboarding.token_hash("tok-a"), NOW - timedelta(seconds=1))
    assert preboarding.resolve(lc, "tok-a") is None  # expired


def _container(engine, store, lc):
    settings = Settings(mode="demo", agent_retries=1, retry_sleep_s=0)
    deps = Deps(settings=settings, llm=OfflineLLM(), index=PgIndex(engine, HashEmbedder(768)), store=store,
                repos=LocalRepoSource(str(SAMPLE), issues=[{"number": 12, "title": "Validate privileged init containers", "labels": []}]),
                blobs=LocalBlobStorage("/tmp/ww-it"), redactor=RegexRedactor(), bus=InlineBus())
    saver = MemorySaver()
    c = Container(settings, deps, build_supervisor(deps, saver), build_supervisor(deps, None), saver, lifecycle=lc)
    deps.bus.handler = c.handle
    return c


def test_enrolment_and_every_visible_field_on_postgres(engine, store, lc):
    from app.agents.supervisor.graph import run_event

    admin, project = _org(store, "en")
    c = _container(engine, store, lc)
    repo = store.create_repo(project.id, "demo/mini-policy-engine", None, "main")
    run_event(c.graph, {"type": "repo.connected", "project_id": project.id, "repository_id": repo.id, "ref": "main"},
              f"en:{uuid.uuid4().hex}")
    ctx = Ctx(c, Clock(), admin)
    template = T.list_for(lc, admin.org_id)[0]
    out = enrolment.enrol(ctx, project.id, "new@example.com", "New", None, date.today(), template, None, None, "engine", None)
    assert out["invitation"] is None and out["plan_event"]["enrolment"]["invite"]  # a draft until the plan is approved
    c.handle(out["plan_event"])
    hc = hires.refresh(hires.load(ctx, out["onboarding"].id))
    assert visibility.FIELDS["plan"].build(hc)["status"] == "awaiting_approval"
    assert lc.milestones(hc.ob.id) == [] and lc.access_items(hc.ob.id) == []
    approval = next(a for a in store.approvals(project.id) if a.kind == "task_plan")
    plan = approval.payload["plans"][0]
    assert [p["title"] for p in plan["phases"]] == ["Days 1–30", "Days 31–60", "Days 61–90"]
    keep = [m["key"] for m in plan["milestones"]][:-1]
    [done] = c.decide(approval, {"approve": [t["id"] for t in plan["tasks"]], "milestones": keep, "user_id": admin.id})["result"]["enrolments"]
    assert done["invitation"]["email"] == "new@example.com" and done["milestones"] == len(keep)
    assert [m.key for m in lc.milestones(hc.ob.id)] == keep  # set_milestones, under row-level security
    hc = hires.refresh(hires.load(ctx, out["onboarding"].id))
    data = visibility.visibility(hc)["data"]  # every builder, against the Postgres adapters
    assert set(data) == set(visibility.FIELDS) and data["profile"]["value"]["hire"]["name"] == "New"
    assert {a["owner"]["id"] for a in data["access"]["value"]} == {admin.id}


def test_export_and_delete_cover_every_table(store, lc):
    a_admin, a_project, hire, a_ob, *_ = _program(store, lc, "da")
    lc.set_preboarding(a_ob.id, preboarding.token_hash("tok-da"), NOW + timedelta(days=1))
    b_admin, _ = _org(store, "db")
    lc.create_team(b_admin.org_id, store.projects_for(b_admin)[0].id, "B team")
    current_org.set(a_admin.org_id)
    data = lc.export_org(a_admin.org_id)
    assert {"users", "projects", "onboardings", "onboarding_programs", "onboarding_milestones", "access_items", "teams",
            "program_templates", "audit_log"} <= set(data)
    assert [o["id"] for o in data["organizations"]] == [a_admin.org_id]
    assert all(r["org_id"] == a_admin.org_id for table in data.values() for r in table if "org_id" in r)
    assert not any(k for rows in data.values() for r in rows for k in r if "token" in k or k in {"firebase_uid", "embedding", "ip_hash"})
    counts = lc.delete_org(a_admin.org_id)
    assert counts["onboarding_programs"] == 1 and counts["users"] >= 2
    assert all(rows == [] for rows in lc.export_org(a_admin.org_id).values())
    current_org.set(None)
    assert lc.program_by_token(preboarding.token_hash("tok-da")) is None
    current_org.set(b_admin.org_id)
    assert [t.name for t in lc.teams(b_admin.org_id)] == ["B team"]  # the other organisation is untouched


def test_seed_shaped_namespace_runs_the_services(engine, store, lc):
    """The lifecycle services take a container-like object: the seed builds one from parts, as this does on Postgres."""
    admin, project, hire, ob, *_ = _program(store, lc, "ns")
    ns = SimpleNamespace(lifecycle=lc, store=store, index=PgIndex(engine, HashEmbedder(768)), deps=None, settings=Settings(mode="demo"))
    hc = hires.load(Ctx(ns, Clock(), admin), ob.id)
    assert visibility.view(hc, ["milestones", "access", "acknowledgements"])["milestones"][0]["key"] == "preboarding_complete"


def test_the_agents_storage_on_postgres(engine, store, lc):
    """014_agents.sql as ww_app: agent modes and the completion draft round-trip, approved doc edits are one per approval and
    stay in their organisation, the new approval kinds are accepted, a question carries its draft, and the daily tick finds
    every organisation with a program in progress before any tenant is set."""
    from app.ports import DocEdit

    admin, project, hire, ob, program, team = _program(store, lc, "ag")
    org, pid = admin.org_id, project.id
    assert lc.update_org_settings(org, agent_modes={"coach": "rules"}).agent_modes == {"coach": "rules"}
    assert lc.org_settings(org).agent_modes == {"coach": "rules"} and lc.org_settings(org).minutes_per_interruption == 15
    lc.update_program(ob.id, completion_draft={"decision": "extend", "extend_days": 14, "note": "n", "fingerprint": "f", "ai": False})
    assert lc.get_program(ob.id).completion_draft["fingerprint"] == "f"
    kinds = {k: store.create_approval(pid, k, f"t:{k}", {"explain": {"agent": "keeper"}}) for k in ("rerecord", "doc_edit", "template_change")}
    assert {store.get_approval(a.id).kind for a in kinds.values()} == set(kinds)
    edit = DocEdit(id="", org_id=org, project_id=pid, approval_id=kinds["doc_edit"].id, target_kind="code", target_ref="engine/x.py",
                   title="engine/x.py", proposed_text="Say what it returns.", feedback_ids=["f1"], owner_id=admin.id, accepted_by=admin.id)
    first = lc.add_doc_edit(edit)
    assert lc.add_doc_edit(edit).id == first.id  # a retried decision stores it once
    assert [e.id for e in lc.doc_edits(pid, "code", "engine/x.py")] == [first.id] and lc.doc_edits(pid, "doc") == []
    assert first.feedback_ids == ["f1"] and first.status == "suggested"
    rid = store.create_expert_request(pid, "Why?", admin.id, hire.id, None, draft="The draft answer.")
    assert store.get_expert_request(rid)["draft"] == "The draft answer."
    other, other_project = _org(store, "ag2")
    current_org.set(other.org_id)
    assert lc.doc_edits(pid) == [] and lc.org_settings(org).agent_modes == {}  # another tenant sees none of it
    current_org.set(None)
    assert org in lc.lifecycle_orgs() and other.org_id not in lc.lifecycle_orgs()  # the definer function, no tenant set
    current_org.set(org)
    lc.update_program(ob.id, completed_at=NOW)
    current_org.set(None)
    assert org not in lc.lifecycle_orgs()


def test_the_daily_tick_on_postgres_runs_once_per_organisation_and_day(engine, store, lc):
    admin, project, hire, ob, program, team = _program(store, lc, "tk")
    c = _container(engine, store, lc)
    current_org.set(admin.org_id)
    first = c.tick(admin.org_id)
    assert first["ran"][0] == "coach" and "skipped" not in first
    assert c.tick(admin.org_id)["skipped"] == "duplicate"  # processed_events: the claim
    runs = store.list_runs(project.id)
    assert any(r["agent"] == "coach" and r["detail"]["summary"].startswith("Daily check of 1 program") for r in runs)
    current_org.set(None)
