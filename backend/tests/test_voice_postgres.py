"""Real PostgreSQL test of the consent gate, records, idempotency, and organisation isolation."""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import voice
from app.adapters.lifecycle_pg import PgLifecycleStore
from app.adapters.postgres import current_org
from app.config import Settings
from tests.test_lifecycle_postgres import _program
from tests.test_postgres import db, engine, store

FIXTURES = (db, engine, store)
pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_ADMIN_URL"), reason="scratch Postgres only")


def test_voice_actions_are_scoped_audited_and_execute_once(store, engine, monkeypatch):
    lc = PgLifecycleStore(engine)
    admin, project, hire, ob, _program_row, _team = _program(store, lc, "voice")
    store.add_member(project.id, hire.id)
    settings = Settings(mode="self_hosted", agora_app_id="a" * 32, agora_app_certificate="b" * 32,
                        agora_customer_id="c" * 32, agora_customer_secret="d" * 32,
                        murf_api_key="e", voice_tool_url="https://voice.example.org")
    c = SimpleNamespace(store=store, lifecycle=lc, settings=settings, extras={})
    sid = str(uuid.uuid4())
    store.one("""INSERT INTO voice_sessions(id, org_id, project_id, user_id, onboarding_id, rtc_channel, rtc_uid, status)
                 VALUES (:s,:o,:p,:u,:ob,:ch,1003,'active') RETURNING id""",
              s=sid, o=hire.org_id, p=project.id, u=hire.id, ob=ob.id, ch="test_" + uuid.uuid4().hex)
    bearer = "Bearer " + voice._signed(settings, sid, hire.org_id, hire.id)

    context = voice.tool_call("getEmployeeContext", voice.ToolIn(), bearer, c)
    assert context["profile"]["profile"]["hire"]["name"] == hire.name
    access = voice.tool_call("checkAccessStatus", voice.ToolIn(), bearer, c)
    assert "access" in access
    citation = {"label": "engine/validate.py:L20-L22", "path": "engine/validate.py"}
    monkeypatch.setattr(voice.routes, "ask", lambda body, user, container: {"status": "answered", "answer": "Validates rules [1].", "citations": [citation]})
    grounded = voice.tool_call("searchCompanyKnowledge", voice.ToolIn(question="How are rules validated?"), bearer, c)
    assert grounded["citations"] == [citation]
    assert store.one("SELECT sources FROM voice_sessions WHERE id = :s", s=sid)["sources"] == [citation]

    specs = [
        ("createAccessRequest", voice.ToolIn(system="VPN", tool_call_id="call-access"), "requested"),
        ("requestKTSession", voice.ToolIn(topic="Validation rules", tool_call_id="call-kt"), "unscheduled"),
        ("createOnboardingTask", voice.ToolIn(title="Read validation", description="Review the implementation", tool_call_id="call-task"), "proposed"),
    ]
    for name, body, expected in specs:
        first = voice.tool_call(name, body, bearer, c)
        again = voice.tool_call(name, body, bearer, c)
        assert first["action_id"] == again["action_id"] and first["confirmation_required"]
        assert voice._actions(c, sid)[-1]["status"] == "pending"
        with pytest.raises(HTTPException) as no_confirmation:
            voice.approve(first["action_id"], voice.DecideIn(confirm=False), hire, c)
        assert no_confirmation.value.status_code == 422
        done = voice.approve(first["action_id"], voice.DecideIn(confirm=True), hire, c)
        assert done["result"]["record_id"] and done["result"]["status"] == expected
        assert voice.approve(first["action_id"], voice.DecideIn(confirm=True), hire, c) == done
    assert len(voice._actions(c, sid)) == 3
    assert len(store.rows("SELECT id FROM voice_actions WHERE voice_session_id = :s AND status = 'completed'", s=sid)) == 3
    assert store.rows("SELECT action FROM audit_log WHERE action = 'voice.action.completed'")

    other, _other_project, _hire, _ob, _program2, _team2 = _program(store, lc, "voice_other")
    current_org.set(other.org_id)
    with pytest.raises(HTTPException) as isolated:
        voice._own(c, other, sid)
    assert isolated.value.status_code == 404
