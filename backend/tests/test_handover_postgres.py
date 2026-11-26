"""The handover's two store writes on PostgreSQL, as ww_app under row-level security (tests/test_postgres.py's fixtures):
moving the hires a leaver manages and the questions routed to them, never across organisations; and documents name who
uploaded them, which the handover lists. Skipped without TEST_PG_ADMIN_URL."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.adapters.lifecycle_pg import PgLifecycleStore
from app.adapters.postgres import current_org
from tests.test_postgres import ADMIN, _org, db, engine, store

FIXTURES = (db, engine, store)  # test_postgres's database fixtures, reused here by name

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")


@pytest.fixture
def lc(engine):
    return PgLifecycleStore(engine)


def test_a_transfer_moves_managed_hires_and_open_questions_within_the_organisation_only(store, lc):
    admin, project = _org(store, "ho")
    hire = lc.ensure_user(admin.org_id, f"hire-{uuid.uuid4().hex[:6]}@example.com", "Nia", "nia")
    successor = lc.ensure_user(admin.org_id, f"next-{uuid.uuid4().hex[:6]}@example.com", "Sam", "sam")
    store.add_member(project.id, successor.id)
    ob = store.create_onboarding(project.id, hire.id, admin.id, date.today() - timedelta(days=5), "Engineer")
    rid = store.create_expert_request(project.id, "Why does the webhook retry?", admin.id, hire.id, None)
    doc = store.create_document(project.id, "Runbook", "gs://b/runbook.pdf", "application/pdf", admin.id)

    # another organisation sees none of it and changes none of it
    other, _ = _org(store, "ho-other")
    current_org.set(other.org_id)
    lc.set_onboarding_manager(ob.id, other.id)
    assert lc.reassign_expert_request(rid, other.id) is False
    current_org.set(admin.org_id)
    assert store.get_onboarding(ob.id).manager_id == admin.id
    assert str(store.get_expert_request(rid)["expert_id"]) == admin.id

    # within the organisation
    lc.set_onboarding_manager(ob.id, successor.id)
    assert store.get_onboarding(ob.id).manager_id == successor.id
    assert lc.reassign_expert_request(rid, successor.id) is True
    assert str(store.get_expert_request(rid)["expert_id"]) == successor.id
    assert [str(r["id"]) for r in store.expert_requests_for(successor.id)] == [rid] and store.expert_requests_for(admin.id) == []
    store.answer_expert_request(rid, "Because the API server retries on timeout.")
    assert lc.reassign_expert_request(rid, admin.id) is False  # an answered question stays with whoever answered it
    assert lc.reassign_expert_request("not-a-uuid", admin.id) is False

    # documents name who uploaded them (the handover lists a leaver's uploads)
    assert str(store.get_document(doc)["uploaded_by"]) == admin.id
    assert [str(d["uploaded_by"]) for d in store.documents(project.id)] == [admin.id]
