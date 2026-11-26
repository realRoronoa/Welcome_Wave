"""Voice boundary checks: provider shape, scoped credentials and unavailable-mode behaviour."""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import voice
from app.config import Settings
from app.main import create_app
from app.voice import platform


def test_tool_token_is_signed_scoped_and_expires(monkeypatch):
    settings = Settings(mode="demo", agora_app_certificate="a" * 32)
    sid, oid, uid = (str(uuid.uuid4()) for _ in range(3))
    bearer = "Bearer " + voice._signed(settings, sid, oid, uid)
    assert voice._verified(settings, bearer)["s"] == sid
    with pytest.raises(HTTPException) as bad:
        voice._verified(settings, bearer[:-1] + ("a" if bearer[-1] != "a" else "b"))
    assert bad.value.status_code == 401
    monkeypatch.setattr(time, "time", lambda: 99999999999)
    with pytest.raises(HTTPException) as expired:
        voice._verified(settings, bearer)
    assert expired.value.status_code == 401


def test_agora_platform_body_has_six_https_tools_and_murf_secret_reference():
    from agora_agent.agentkit.session_backend import prepare_start_body

    settings = Settings(mode="demo", agora_app_id="a" * 32, agora_app_certificate="b" * 32,
                        agora_customer_id="c" * 32, agora_customer_secret="d" * 32,
                        murf_api_key="server-only", voice_tool_url="https://voice.example.org")
    agent = platform.agent(settings, "Bearer signed-session-token")
    session = agent.create_ephemeral_session(channel="test", agent_uid="1002", remote_uids=["1003"])
    body = prepare_start_body(session)
    stages = body["agent"]["pipeline"]
    assert stages["tts"]["vendor"] == "murf"
    assert stages["tts"]["params"]["model"] == "FALCON"
    assert stages["tts"]["credential"]["fields"]["api_key"] == "$secrets.welcome_wave_voice.murf"
    tools = stages["llm"]["tools"]
    assert {t["name"] for t in tools} == {"getEmployeeContext", "searchCompanyKnowledge", "checkAccessStatus",
                                               "createAccessRequest", "requestKTSession", "createOnboardingTask"}
    assert all(t["server"]["url"].startswith("https://") and t["server"]["headers"]["Authorization"].startswith("Bearer ") for t in tools)
    assert "server-only" not in str(body)


def test_demo_does_not_offer_a_fake_voice_call():
    with TestClient(create_app()) as client:
        headers = {"X-Demo-User": "u-vijay"}
        config = client.get("/api/voice/config", headers=headers)
        assert config.status_code == 200 and config.json()["available"] is False
        history = client.get("/api/voice/sessions", params={"project_id": "proj-demo"}, headers=headers)
        assert history.status_code == 200 and history.json() == []  # the in-memory demo store has no voice tables
        start = client.post("/api/voice/sessions", json={"project_id": "proj-demo"}, headers=headers)
        assert start.status_code == 503
        tool = client.post("/api/voice/tools/getEmployeeContext", json={}, headers={"Authorization": "Bearer fake"})
        assert tool.status_code == 503


def test_summary_carries_actions_sources_and_outstanding_work():
    row = {"transcript": [{"speaker": "employee", "text": "Explain validate.py"}], "sources": [{"label": "validate.py:L20-L22"}]}
    actions = [{"kind": "kt_request", "status": "completed", "result": {"record_id": "m1"}, "payload": {}},
               {"kind": "access_request", "status": "pending", "result": {}, "payload": {"system": "VPN"}}]
    summary = voice._summary(row, actions)
    assert summary["turns"] == 1
    assert summary["sources"][0]["label"] == "validate.py:L20-L22"
    assert summary["completed_actions"][0]["record"]["record_id"] == "m1"
    assert summary["outstanding_actions"][0]["payload"]["system"] == "VPN"
