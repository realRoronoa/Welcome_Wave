"""Tests for ClaudeLLM, HybridLLM, and production DEMO_SHOWCASE accounts."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from app import showcase
from app.adapters.lifecycle_memory import MemoryLifecycleStore
from app.adapters.memory import InlineBus, LocalBlobStorage, LocalRepoSource, MemoryIndex, MemoryStore
from app.agents.core.deps import Deps
from app.agents.core.llm import FINAL, ClaudeLLM, FakeLLM, FunctionDecl, HybridLLM, LLMError, MediaRef, OfflineLLM
from app.agents.supervisor.graph import build_supervisor
from app.config import Settings, get_settings
from app.container import Container
from app.main import create_app
from app.seed import DEMO_ISSUES, DEMO_PR_FILES
from app.services.embeddings import HashEmbedder
from app.services.redaction import RegexRedactor


class _Out(BaseModel):
    answer: str
    score: int


def test_claude_llm_structured_and_fallback():
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode())
        seen.append(body["model"])
        assert req.headers["x-api-key"] == "sk-test-claude"
        assert req.headers["anthropic-version"] == "2023-06-01"
        if body["model"] == "claude-haiku-4-5-20251001":
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json={
            "id": "msg_1",
            "model": body["model"],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 22, "output_tokens": 14},
            "content": [{"type": "tool_use", "id": "toolu_1", "name": FINAL, "input": {"answer": "from claude", "score": 9}}],
        })

    settings = Settings(
        _env_file=None,
        anthropic_api_key="sk-test-claude",
        anthropic_base_url="https://api.fableaiapi.com/v1",
        claude_model_fast="claude-haiku-4-5-20251001",
        claude_model_fallback="claude-sonnet-4-5-20250929",
        llm_retries=1,
        retry_sleep_s=0.0,
    )
    llm = ClaudeLLM(settings, sleep=lambda _: None, client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = llm.structured("Check validation", _Out, system="Be concise.")
    assert out == _Out(answer="from claude", score=9)
    assert seen == ["claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929"]


def test_claude_llm_agent_turn_round_trip():
    turn_num = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal turn_num
        turn_num += 1
        body = json.loads(req.content.decode())
        if turn_num == 1:
            assert body["tool_choice"] == {"type": "any"}
            return httpx.Response(200, json={
                "id": "msg_a",
                "model": body["model"],
                "usage": {"input_tokens": 30, "output_tokens": 12},
                "content": [
                    {"type": "text", "text": "Checking files first."},
                    {"type": "tool_use", "id": "toolu_search", "name": "find_file", "input": {"name": "validate.py"}},
                ],
            })
        assert body["messages"][-1]["role"] == "user"
        assert body["messages"][-1]["content"][0]["type"] == "tool_result"
        assert body["messages"][-1]["content"][0]["tool_use_id"] == "toolu_search"
        return httpx.Response(200, json={
            "id": "msg_b",
            "model": body["model"],
            "usage": {"input_tokens": 45, "output_tokens": 18},
            "content": [{"type": "tool_use", "id": "toolu_done", "name": FINAL, "input": {"answer": "engine/validate.py", "score": 10}}],
        })

    settings = Settings(_env_file=None, anthropic_api_key="sk-test", anthropic_base_url="https://api.fableaiapi.com")
    llm = ClaudeLLM(settings, sleep=lambda _: None, client=httpx.Client(transport=httpx.MockTransport(handler)))
    tools = [FunctionDecl(name="find_file", description="Find file", parameters={"type": "object", "properties": {"name": {"type": "string"}}})]

    t1 = llm.agent_turn("Sys", [{"role": "user", "text": "Where is validate?"}], tools, _Out, agent="guide")
    assert t1.final is None and len(t1.tool_calls) == 1 and t1.tool_calls[0].name == "find_file"

    msgs = [
        {"role": "user", "text": "Where is validate?"},
        {"role": "model", "thought": t1.thought, "calls": [c.model_dump() for c in t1.tool_calls], "raw": t1.raw},
        {"role": "tool", "results": [{"id": "toolu_search", "name": "find_file", "text": "engine/validate.py"}]},
    ]
    t2 = llm.agent_turn("Sys", msgs, tools, _Out, agent="guide", final_only=True)
    assert t2.final == _Out(answer="engine/validate.py", score=10)


def test_hybrid_llm_routes_media_and_falls_back():
    class _BrokenPrimary:
        def structured(self, *args, **kwargs):
            raise LLMError("gateway down")

        def agent_turn(self, *args, **kwargs):
            raise LLMError("gateway down")

    secondary = FakeLLM(responders={"_Out": lambda _p, media: _Out(answer="video" if media else "fallback", score=5)})
    hybrid = HybridLLM(_BrokenPrimary(), secondary)
    assert hybrid.structured("hi", _Out) == _Out(answer="fallback", score=5)
    assert hybrid.structured("clip", _Out, media=[MediaRef(uri="gs://b/c.mp4")]) == _Out(answer="video", score=5)


def test_showcase_demo_login_and_voice_readiness_outside_demo_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("MODE", "self_hosted")
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("DEMO_SHOWCASE", "true")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "ww-test")
    monkeypatch.setenv("FIREBASE_API_KEY", "fake-web-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AGORA_APP_ID", "app-id")
    monkeypatch.setenv("AGORA_APP_CERTIFICATE", "app-cert")
    monkeypatch.setenv("AGORA_CUSTOMER_ID", "cust-id")
    monkeypatch.setenv("AGORA_CUSTOMER_SECRET", "cust-secret")
    monkeypatch.setenv("MURF_API_KEY", "murf-key")
    monkeypatch.setenv("VOICE_TOOL_URL", "https://welcome-wave.example.com")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    get_settings.cache_clear()
    showcase._login_attempts.hits.clear()
    try:
        settings = get_settings()
        assert settings.ai_ready is True
        assert settings.voice_ready is True

        repo_root = Path(__file__).resolve().parents[1] / "sample_repo"
        store = MemoryStore()
        lc = MemoryLifecycleStore(store)
        bus = InlineBus()
        deps = Deps(
            settings=settings,
            llm=OfflineLLM(),
            index=MemoryIndex(HashEmbedder(settings.embedding_dim)),
            store=store,
            repos=LocalRepoSource(str(repo_root), issues=DEMO_ISSUES, default_pr=DEMO_PR_FILES),
            blobs=LocalBlobStorage(str(tmp_path / "blobs")),
            redactor=RegexRedactor(),
            bus=bus,
            lifecycle=lc,
        )
        saver = MemorySaver()
        cont = Container(settings, deps, build_supervisor(deps, saver), build_supervisor(deps, None), saver, lifecycle=lc)
        bus.handler = cont.handle

        # Swap build_container so TestClient exercises create_app(MODE=self_hosted, DEMO_SHOWCASE=true) without needing Postgres
        monkeypatch.setattr("app.main.build_container", lambda s, live=None: cont)
        monkeypatch.setattr("app.selfhost.start", lambda _c: [])
        monkeypatch.setattr("app.live.PgListener.start", lambda self: None)
        monkeypatch.setattr("app.live.PgListener.stop", lambda self: None)

        with TestClient(create_app()) as client:
            cfg = client.get("/api/config").json()
            assert cfg["mode"] == "self_hosted"
            assert cfg["demo_password"] is None  # invariant from test_demo_login.py preserved
            assert cfg["showcase"] == {"enabled": True, "password": "welcome-wave"}
            assert client.get("/api/demo/people").status_code == 404

            people = client.get("/api/showcase/people").json()
            emails = {m["email"] for m in people["members"]}
            assert {"admin@demo.dev", "priya@demo.dev", "elena@demo.dev", "ravi@demo.dev", "meera@demo.dev", "vijay@demo.dev"} <= emails

            bad = client.post("/api/showcase/login", json={"email": "vijay@demo.dev", "password": "wrong"})
            assert bad.status_code == 401

            res = client.post("/api/showcase/login", json={"email": "vijay@demo.dev", "password": "welcome-wave"})
            assert res.status_code == 200, res.text
            token = res.json()["token"]
            assert token.startswith("wwdemo.")

            headers = {"Authorization": f"Bearer {token}"}
            me = client.get("/api/me", headers=headers).json()
            assert me["email"] == "vijay@demo.dev" and me["role"] == "new_hire"
            pid = me["projects"][0]["id"]
            assert pid == showcase.SHOWCASE_PROJECT_ID

            files = client.get(f"/api/code/files?project_id={pid}", headers=headers).json()
            assert any(f["path"] == "engine/validate.py" for f in files)

            vcfg = client.get(f"/api/voice/config?project_id={pid}", headers=headers).json()
            assert vcfg == {"available": True, "reason": None}

            tampered = client.get("/api/me", headers={"Authorization": f"Bearer {token}x"})
            assert tampered.status_code == 401
    finally:
        showcase._login_attempts.hits.clear()
        get_settings.cache_clear()
