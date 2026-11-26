"""FE-07: POST /api/client-errors, where the web app's crash reports go (frontend/src/report.ts, navigator.sendBeacon).

No sign-in (a beacon carries none, and the sign-in page crashes too), rate-limited per address, a 16 KB body, a strict
schema, one redacted log line, nothing stored, 204."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import JsonFormatter, create_app
from app.ratelimit import CLIENT_ERROR_BYTES, body_limit, group_for

ROOT = Path(__file__).resolve().parents[2]
REPORT = {"id": "k2f9q1", "kind": "error", "name": "TypeError", "message": "Cannot read properties of undefined (reading 'id')",
          "stack": "TypeError: Cannot read properties of undefined\n    at Home (https://app.example.com/assets/index-3f2a.js:12:345)",
          "component": "", "page": "/app#/projects", "build": "/assets/index-3f2a.js", "at": "2026-11-18T09:00:00.000Z"}


@pytest.fixture
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _lines(caplog) -> list[dict]:
    """The route's log lines as the app writes them: one JSON object each."""
    fmt = JsonFormatter()
    return [json.loads(fmt.format(r)) for r in caplog.records if r.name == "client"]


def test_a_report_needs_no_sign_in_and_answers_204(client, caplog):
    with caplog.at_level(logging.INFO, logger="client"):
        r = client.post("/api/client-errors", json=REPORT)
    assert r.status_code == 204 and r.content == b""
    [line] = _lines(caplog)
    assert line["severity"] == "WARNING" and line["message"] == "client error"
    assert line["ref"] == "k2f9q1" and line["kind"] == "error" and line["page"] == "/app#/projects"
    assert line["error_message"] == REPORT["message"] and "index-3f2a.js:12:345" in line["stack"]


def test_a_beacon_sent_as_plain_text_counts_too(client):
    r = client.post("/api/client-errors", content=json.dumps(REPORT), headers={"Content-Type": "text/plain;charset=UTF-8"})
    assert r.status_code == 204


@pytest.mark.parametrize("body", [
    {**REPORT, "password": "x"},  # unknown fields are refused
    {**REPORT, "kind": "anything"},
    {**REPORT, "message": {"nested": True}},
    {k: v for k, v in REPORT.items() if k != "kind"},
    [REPORT], "just text", None,
])
def test_only_a_crash_report_is_taken(client, body):
    r = client.post("/api/client-errors", content=b"" if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_not_json_is_refused(client):
    assert client.post("/api/client-errors", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 422


def test_long_text_is_cut_and_nothing_secret_is_logged(client, caplog):
    secret = {
        **REPORT,
        "id": "<script>",  # not a reference id: left out
        "message": "Failed for priya@demo.dev with token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1LXByaXlhIn0.c2lnbmF0dXJlc2ln " + "x" * 2000,
        "stack": "Error\n    at go (https://app.example.com/app?q=salary%20bands&token=abc123#frag:1:2)\n"
                 "    at fetch (/api/chat/history?project_id=p1:3:4)\n    at load (/api/preboarding/tok-SECRET-123/acknowledge:5:6)",
        "page": "/welcome/tok-SECRET-456?utm=1#?search=bonus",
        "build": "api_key=AIzaSyA" + "b" * 32,
    }
    with caplog.at_level(logging.INFO, logger="client"):
        assert client.post("/api/client-errors", json=secret).status_code == 204
    [line] = _lines(caplog)
    text = json.dumps(line)
    for leaked in ("priya@demo.dev", "eyJhbGciOiJIUzI1NiJ9", "salary", "token=abc123", "frag", "project_id=p1", "tok-SECRET-123",
                   "tok-SECRET-456", "utm", "bonus", "AIzaSyA", "<script>"):
        assert leaked not in text, leaked
    assert len(line["error_message"]) <= 500 and line["ref"] == ""
    assert line["page"] == "/welcome/[redacted]"
    assert "https://app.example.com/app" in line["stack"] and "/api/preboarding/[redacted]" in line["stack"]


def test_nothing_is_stored(client, caplog):
    """The route never reaches the container: no store, no audit row, no queue."""
    real = client.app.state.container
    client.app.state.container = None
    try:
        assert client.post("/api/client-errors", json=REPORT).status_code == 204
    finally:
        client.app.state.container = real


def test_the_body_is_small():
    s = Settings(max_json_kb=1024)
    assert body_limit("POST", "/api/client-errors", s) == CLIENT_ERROR_BYTES == 16 * 1024
    assert body_limit("POST", "/api/chat", s) == 1024 * 1024  # others keep theirs


def test_an_oversized_report_is_refused_before_it_is_read(client):
    big = json.dumps({**REPORT, "stack": "x" * (CLIENT_ERROR_BYTES + 1)})
    assert client.post("/api/client-errors", content=big, headers={"Content-Type": "application/json"}).status_code == 413

    def chunks():  # no Content-Length: counted as it arrives
        for _ in range(4):
            yield b"x" * 8192

    assert client.post("/api/client-errors", content=chunks(), headers={"Content-Type": "application/json"}).status_code == 413


def test_reports_are_limited_per_address_and_a_header_does_not_escape_it(monkeypatch):
    g = group_for("POST", "/api/client-errors")
    assert g.name == "client_errors" and not g.by_person
    assert group_for("GET", "/api/client-errors").name == "default"
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_CLIENT_ERRORS_PER_MINUTE", "2")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            codes = [c.post("/api/client-errors", json=REPORT, headers={"X-Demo-User": f"u-fake-{i}"}).status_code for i in range(3)]
            assert codes == [204, 204, 429]
            assert c.post("/api/client-errors", json=REPORT, headers={"X-Demo-User": "u-priya"}).status_code == 429  # a real one too
            assert c.get("/api/config").status_code == 200  # its own bucket: the rest of the app is untouched
    finally:
        get_settings.cache_clear()


def test_the_web_app_sends_to_this_route_on_its_own_origin():
    """report.ts posts to a relative path; the web app's CSP allows 'self' in connect-src (which governs sendBeacon), nginx
    and the Vite dev server pass /api/ on to the API."""
    report = (ROOT / "frontend/src/report.ts").read_text()
    assert 'const URL_PATH = "/api/client-errors";' in report and "sendBeacon" in report
    nginx = (ROOT / "frontend/nginx.conf.template").read_text()
    connect = next(line for line in nginx.splitlines() if "connect-src" in line and "set $csp" in line)
    assert "connect-src 'self'" in connect
    assert "location /api/ {" in nginx
    assert '"/api": target' in (ROOT / "frontend/vite.config.ts").read_text()
    assert "/api/client-errors" in (ROOT / "docs/PERMISSIONS.md").read_text()
