"""Organisation export and deletion on Postgres (TEST_PG_ADMIN_URL, as tests/test_postgres.py): the stored-file uris and the
LangGraph thread ids are found before the rows go, the outbox events of the organisation's projects go with it and no other
organisation's do, the survey is aggregates only, and a survey day is claimed once however many submit at once."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.adapters.lifecycle_pg import PgLifecycleStore
from app.adapters.postgres import current_org
from app.lifecycle import orgdata
from app.ports import SurveyResponse
from tests.test_lifecycle_postgres import _container, _program
from tests.test_postgres import ADMIN, _org, db, engine, store

FIXTURES = (db, engine, store)

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")


@pytest.fixture
def lc(engine):
    return PgLifecycleStore(engine)


def test_a_survey_day_is_claimed_once_even_when_many_submit_at_once(store, lc):
    admin, project, hire, ob, program, team = _program(store, lc, "sv")
    assert lc.claim_survey_day(ob.id, 30) is True and lc.claim_survey_day(ob.id, 30) is False
    assert lc.claim_survey_day("not-a-uuid", 30) is False
    ctx_org = admin.org_id

    def race(_):
        current_org.set(ctx_org)  # each worker thread is its own request, under the same organisation
        return lc.claim_survey_day(ob.id, 60)

    with ThreadPoolExecutor(8) as pool:
        wins = list(pool.map(race, range(8)))
    assert wins.count(True) == 1
    assert sorted(lc.get_program(ob.id).survey_days) == [30, 60]


def test_requests_asked_by_a_hire_come_back_with_the_experts_name(store, lc):
    admin, project, hire, ob, *_ = _program(store, lc, "rq")
    current_org.set(admin.org_id)
    rid = store.create_expert_request(project.id, "Who signs off a change?", admin.id, hire.id, None, draft="unverified")
    [row] = store.expert_requests_by(project.id, hire.id)
    assert row["id"] == rid and row["expert_name"] == admin.name and row["draft"] == "unverified" and row["status"] == "open"
    assert store.expert_requests_by(project.id, admin.id) == [] and store.expert_requests_by("nope", hire.id) == []


def test_the_export_is_survey_aggregates_only_and_delete_finds_files_threads_and_outbox(engine, store, lc, tmp_path):
    from app.adapters.memory import LocalBlobStorage

    a_admin, a_project, hire, a_ob, program, team = _program(store, lc, "od")
    b_admin, _ = _org(store, "ob")  # another organisation, with its own outbox event
    b_project = store.projects_for(b_admin)[0]
    store.outbox_add("reviews", {"type": "feedback.created", "project_id": b_project.id, "dedupe": f"od-b-{uuid.uuid4().hex}"})
    current_org.set(a_admin.org_id)
    for _i in range(5):
        lc.add_survey_response(SurveyResponse(id="", org_id=a_admin.org_id, team_id=team.id, cohort="2026-Q4", day=30, answers={"clarity": 4}))
    for _i in range(3):
        lc.add_survey_response(SurveyResponse(id="", org_id=a_admin.org_id, team_id=team.id, cohort="2026-Q4", day=60, answers={"clarity": 1}))
    lc.update_program(a_ob.id, survey_days=[30, 60])
    key = f"od-a-{uuid.uuid4().hex}"
    store.outbox_add("reviews", {"type": "feedback.created", "project_id": a_project.id, "dedupe": key})
    store.outbox_add("reviews", {"type": "lifecycle.tick", "org_id": a_admin.org_id, "dedupe": key + "-tick"})
    doc = store.create_document(a_project.id, "Guide", "gs://local/docs/od/guide.md", "text/markdown", a_admin.id)
    meeting = store.create_meeting(a_project.id, "KT", a_admin.id)
    store.update_meeting(meeting.id, recording_uri="gs://local/recordings/od/a.mp4")
    approval = store.create_approval(a_project.id, "task_plan", f"thread-{uuid.uuid4().hex}", {"plans": []})

    c = _container(engine, store, lc)
    blobs = LocalBlobStorage(str(tmp_path))
    c.deps.blobs = blobs
    for uri in ("gs://local/docs/od/guide.md", "gs://local/recordings/od/a.mp4"):
        blobs.put(uri, b"x")
    threads: list[str] = []
    c.checkpointer = SimpleNamespace(delete_thread=threads.append)

    data = orgdata.export(c, a_admin.org_id)
    assert "survey_responses" not in data and all("survey_days" not in p for p in data["onboarding_programs"])
    assert {(a["day"], a["respondents"], a["status"]) for a in data["survey_aggregates"]} == {(30, 5, "ok"), (60, 3, "not_enough_data")}
    assert doc and not any(k for rows in data.values() for r in rows for k in r if "token" in k or k == "cohort")

    assert set(lc.org_blob_uris(a_admin.org_id)) == {"gs://local/docs/od/guide.md", "gs://local/recordings/od/a.mp4"}
    assert lc.org_thread_ids(a_admin.org_id) == [approval.thread_id]
    counts = orgdata.delete(c, a_admin.org_id)
    assert counts["stored_files"] == 2 and counts["checkpoint_threads"] == 1 and threads == [approval.thread_id]
    assert counts["outbox"] == 2 and counts["onboarding_programs"] == 1
    assert not (tmp_path / "local" / "docs").exists()  # the files and the folders they left empty
    current_org.set(None)
    assert store.rows("SELECT id FROM outbox WHERE dedupe LIKE 'od-a-%'") == []
    assert len(store.rows("SELECT id FROM outbox WHERE event->>'project_id' = :p", p=b_project.id)) == 1  # the other organisation's stays
