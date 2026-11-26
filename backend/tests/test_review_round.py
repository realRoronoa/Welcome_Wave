"""Fixes from the final review round: what executives may see, what the hire's visibility page lists, logs without
credentials, the audit trail of the pre-boarding portal, the dashboard's clock, and the leadership report by team."""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.logsafe import AccessLogFilter, redact
from app.main import create_app
from app.ports import SurveyResponse

PID = "proj-demo"
ADMIN, PRIYA, VIJAY, RAVI, MEERA, EXEC, ARJUN = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay", "u-ravi", "u-meera", "u-exec",
                                                                              "u-arjun"))
TOKEN = "demo-welcome-arjun"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    monkeypatch.setenv("AGENT_RETRIES", "1")
    monkeypatch.setenv("RETRY_SLEEP_S", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _c(client):
    return client.app.state.container


# ---------------------------------------------------------------- R12: executives get counts, never a hire's words
def test_an_executive_gets_feedback_counts_only(client):
    sent = client.post("/api/feedback", json={"project_id": PID, "target_kind": "code", "target_ref": "webhooks/server.py", "kind": "unclear",
                                              "note": "I could not tell what the timeout is for"}, headers=VIJAY)
    assert sent.status_code == 201
    mine = client.get(f"/api/feedback?project_id={PID}", headers=EXEC)
    assert mine.status_code == 200
    body = mine.json()
    assert body["counts_only"] is True and isinstance(body["counts"], list)
    assert "I could not tell" not in mine.text and "created_by" not in mine.text and "u-vijay" not in mine.text
    assert all(set(row) == {"target_kind", "kind", "open", "resolved", "dismissed"} for row in body["counts"])
    manager_rows = client.get(f"/api/feedback?project_id={PID}", headers=PRIYA).json()
    assert body["total"] == len(manager_rows) == sum(r["open"] + r["resolved"] + r["dismissed"] for r in body["counts"])
    assert any(f["note"].startswith("I could not tell") and f["created_by"] == "u-vijay" for f in manager_rows)  # managers still read them
    code = next(r for r in body["counts"] if (r["target_kind"], r["kind"]) == ("code", "unclear"))
    assert code["open"] >= 1


# ---------------------------------------------------------------- R13: the hire's page lists what others can read
def test_the_visibility_page_lists_what_the_inbox_the_log_and_the_reviews_hold(client):
    asked = client.post("/api/ask/route", json={"project_id": PID, "question": "How is the mutate module benchmarked?", "ask_person": True},
                        headers=VIJAY).json()
    client.post("/api/feedback", json={"project_id": PID, "target_kind": "code", "target_ref": "engine/validate.py", "kind": "outdated",
                                       "note": "The docstring is out of date"}, headers=VIJAY)
    plan = next(a for a in client.get(f"/api/approvals?project_id={PID}", headers=PRIYA).json() if a["kind"] == "task_plan")
    client.post(f"/api/approvals/{plan['id']}/decide", json={"approve": [t["id"] for t in plan["payload"]["plans"][0]["tasks"]]}, headers=PRIYA)
    code = next(t for t in client.get(f"/api/tasks?project_id={PID}", headers=VIJAY).json() if t["kind"] == "code")
    review_id = client.post("/api/demo/pull-request", json={"task_id": code["id"]}, headers=VIJAY).json()["review_id"]
    page = client.get("/api/onboardings/ob-vijay/visibility", headers=VIJAY).json()
    data = page["data"]
    assert {"requests", "reviews", "feedback", "audit"} <= set(data)
    assert set(data["requests"]["visible_to"]) == {"hire", "expert"} and set(data["audit"]["visible_to"]) == {"hire", "admin"}
    assert "feedback" in data and set(data["feedback"]["visible_to"]) == {"hire", "manager", "owner"}
    assert set(data["reviews"]["visible_to"]) == {"hire", "mentor", "manager"}
    assert "dashboard" not in data  # the dashboard holds no per-hire row any more

    # the question, in full, with the name of the person it went to: what that person's inbox shows
    sent = next(r for r in data["requests"]["value"] if r["id"] == asked["request_id"])
    inbox = next(r for r in client.get("/api/experts/requests", headers=RAVI).json() if r["id"] == asked["request_id"])
    assert sent["question"] == inbox["question"] == "How is the mutate module benchmarked?"
    assert sent["routed_to"]["name"].startswith("Ravi") and inbox["asked_by_name"].startswith("Vijay")
    # the audit trail of his own actions, as admins read it
    audit = data["audit"]["value"]
    assert audit["total"] >= 2 and {"question.routed", "feedback.submitted"} <= {e["action"] for e in audit["latest"]}
    log = client.get("/api/audit?actor=u-vijay", headers=ADMIN).json()
    assert log["total"] >= audit["total"]  # the page the hire sees is the log's own entries for him
    # the review of his pull request and the manager's decision
    [rv] = [r for r in data["reviews"]["value"] if r["id"] == review_id]
    assert rv["task_id"] == code["id"] and rv["decision"] in {None, "approved", "rejected", "changes_requested"}
    # and the note he left, with who owns what it is about
    note = next(f for f in data["feedback"]["value"] if f["note"] == "The docstring is out of date")
    assert note["owner"]["id"] and note["target_ref"] == "engine/validate.py"


def test_the_statement_names_everyone_who_reads(client):
    page = client.get("/api/onboardings/ob-vijay/visibility", headers=VIJAY).json()
    assert "admins" in page["statement"] and "answer your questions" in page["statement"]
    assert any("handed on to a person" in line for line in page["private"])


# ---------------------------------------------------------------- R20: the pre-boarding token never reaches a log
def test_the_preboarding_token_is_redacted_from_every_path_logged():
    assert redact(f"/welcome/{TOKEN}") == "/welcome/[redacted]"
    assert redact(f"/api/preboarding/{TOKEN}/env-check.sh") == "/api/preboarding/[redacted]/env-check.sh"
    assert redact("/api/blobs/abc.def") == "/api/blobs/[redacted]"
    assert redact("/api/integrations/google/callback?code=4/0AX&state=s.t&x=1") == "/api/integrations/google/callback?code=[redacted]&state=[redacted]&x=1"
    assert redact("/api/tasks") == "/api/tasks"


def test_requests_for_a_preboarding_link_log_no_token(client, caplog):
    with caplog.at_level(logging.INFO):
        assert client.get(f"/api/preboarding/{TOKEN}").status_code == 200
        client.get(f"/api/preboarding/{TOKEN}/env-check.sh")
        client.post(f"/api/preboarding/{TOKEN}/acknowledge", json={"doc_key": "security_policy"})
    lines = [r for r in caplog.records if r.name == "api" and r.getMessage() == "request"]
    assert len(lines) == 3
    ours = [r for r in caplog.records if not r.name.startswith("httpx")]  # (the test client's own log line is not the server's)
    assert ours and not any(TOKEN in str(r.__dict__) for r in ours)  # no field of any record the app wrote, whatever the logger
    assert any(r.extra_fields["path"] == "/api/preboarding/[redacted]" for r in lines)


def test_the_server_access_log_is_filtered_too():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("10.0.0.1:5000", "GET", f"/api/preboarding/{TOKEN}?code=abc", "1.1", 200), None)
    assert AccessLogFilter().filter(record)
    assert TOKEN not in record.getMessage() and "abc" not in record.getMessage() and "[redacted]" in record.getMessage()


# ---------------------------------------------------------------- R22: portal actions belong to the hire
def test_a_portal_acknowledgement_is_audited_as_the_hire(client):
    assert client.post(f"/api/preboarding/{TOKEN}/acknowledge", json={"doc_key": "security_policy"}).status_code == 200
    rows = [a for a in _c(client).store.audit_rows if a["action"] == "preboarding.acknowledged" and a["detail"].get("doc_key") == "security_policy"]
    live = rows[-1]
    assert live["actor_id"] == "u-arjun" and live["detail"]["via"] == "preboarding" and live["actor_id"] is not None
    log = client.get("/api/audit?action=preboarding.acknowledged&limit=200", headers=ADMIN).json()
    assert log["items"] and all(e["actor_id"] and e["actor_name"] for e in log["items"])  # an actor on every entry: never "System"
    assert any(e["actor_name"].startswith("Arjun") and e["detail"] == {"doc_key": "security_policy", "via": "preboarding"} for e in log["items"])


def test_the_seed_audits_the_acknowledgements_it_seeds(client):
    c = _c(client)
    seeded = {(a["target"], a["detail"].get("doc_key")) for a in c.store.audit_rows if a["action"] == "preboarding.acknowledged"}
    for ob in ("ob-vijay", "ob-sara", "ob-tom"):
        for ack in c.lifecycle.acknowledgements(ob):
            if ack.acknowledged_at:
                assert (ob, ack.doc_key) in seeded, (ob, ack.doc_key)
    assert all(a["actor_id"] for a in c.store.audit_rows if a["action"] == "preboarding.acknowledged")


# ---------------------------------------------------------------- the dashboard: lifecycle clock, no per-hire activity
def test_the_dashboard_agrees_with_home_and_has_no_per_hire_activity(client):
    dash = client.get(f"/api/dashboard?project_id={PID}", headers=PRIYA).json()
    vijay = next(p for p in dash["people"] if p["onboarding_id"] == "ob-vijay")
    program = client.get("/api/onboardings/ob-vijay/program", headers=PRIYA).json()
    assert vijay["day"] == program["profile"]["day"] == 12  # not 13: the lifecycle's day, not date.today() + 1
    home = client.get("/api/home", headers=PRIYA).json()
    card = next(c for c in home["sections"]["hires"] if c["profile"]["onboarding_id"] == "ob-vijay")
    assert vijay["on_track"] == card["status"]["on_track"] and vijay["why"] == card["status"]["why"]
    forbidden = {"tasks_total", "tasks_done", "progress", "overdue", "changes_requested", "questions_asked", "questions_unanswered", "at_risk"}
    assert not any(forbidden & set(p) for p in dash["people"])
    assert dash["coverage"] and all({"module", "files", "files_explained", "kt_minutes"} <= set(m) for m in dash["coverage"])
    assert "kt_gaps" in dash and "pending_approvals" in dash


def test_the_dashboard_follows_the_demo_clock_and_the_first_task(client):
    client.post("/api/demo/advance", json={"days": 5}, headers=PRIYA)
    dash = client.get(f"/api/dashboard?project_id={PID}", headers=PRIYA).json()
    assert next(p for p in dash["people"] if p["onboarding_id"] == "ob-vijay")["day"] == 17
    # a first task the program selected is the first PR the dashboard reports (it said "Not assigned" before)
    c = _c(client)
    code = next(t for t in c.store.tasks(onboarding_id="ob-vijay") if t.kind == "code")
    c.lifecycle.update_program("ob-vijay", first_task={"title": "x", "task_id": code.id})
    row = next(p for p in client.get(f"/api/dashboard?project_id={PID}", headers=PRIYA).json()["people"] if p["onboarding_id"] == "ob-vijay")
    assert row["first_pr_status"] == code.status


def test_repeated_questions_need_two_different_hires(client):
    for who in (VIJAY, ARJUN):
        client.post("/api/chat", json={"project_id": PID, "question": "Where is the escalation policy documented?"}, headers=who)
    client.post("/api/chat", json={"project_id": PID, "question": "How is quantum flux calibrated?"}, headers=VIJAY)
    client.post("/api/chat", json={"project_id": PID, "question": "How is quantum flux calibrated?"}, headers=VIJAY)  # one hire, twice
    topics = [r["topic"] for r in client.get(f"/api/dashboard?project_id={PID}", headers=PRIYA).json()["repeated_questions"]]
    assert not any("quantum" in t for t in topics)


# ---------------------------------------------------------------- R4: the leadership report by team
def _survey_team(client, name):
    return client.post("/api/teams", json={"project_id": PID, "name": name}, headers=PRIYA).json()["id"]


def test_the_report_groups_figures_by_team_with_the_same_floors(client):
    report = client.get("/api/reports/onboarding", headers=EXEC).json()
    assert report["team_id"] is None and {f["key"] for f in report["figures"]} >= {"hires", "days_to_first_pr"}  # still every old field
    teams = {t["team"]: t for t in report["teams"]}
    assert {"Policy Engine", "Admission Webhooks"} <= set(teams)
    engine = teams["Policy Engine"]
    keys = {f["key"] for f in engine["figures"]}
    assert "single_owner_critical_areas" not in keys and "hires" in keys
    hires = next(f for f in engine["figures"] if f["key"] == "hires")
    assert hires["current"]["n"] >= 1
    for team in report["teams"]:  # floors hold inside a team: a median of fewer than three hires says so, never a number
        for f in team["figures"]:
            if f["key"] == "days_to_first_merge" and f["current"]["n"] < 3:
                assert f["current"]["value"] is None and f["current"]["display"] == "not enough data"


def test_the_report_can_be_narrowed_to_one_team(client):
    report = client.get("/api/reports/onboarding", headers=EXEC).json()
    team = next(t for t in report["teams"] if t["team"] == "Policy Engine")
    one = client.get("/api/reports/onboarding", params={"team_id": team["team_id"]}, headers=EXEC).json()
    assert one["team_id"] == team["team_id"] and one["teams"] == []
    assert next(f for f in one["figures"] if f["key"] == "hires") == next(f for f in team["figures"] if f["key"] == "hires")
    assert [t["team"] for t in one["surveys"]["teams"]] in (["Policy Engine"], [])
    csv_one = client.get("/api/reports/onboarding.csv", params={"team_id": team["team_id"]}, headers=EXEC)
    assert csv_one.status_code == 200 and "hires" in csv_one.text
    assert client.get("/api/reports/onboarding", params={"team_id": "no-such-team"}, headers=EXEC).status_code == 404


def test_the_csv_has_the_team_rows_and_the_key_person_figure(client):
    text = client.get("/api/reports/onboarding.csv", headers=EXEC).text
    assert "single_owner_critical_areas" in text and "team." in text and "Policy Engine: Hires in the cohort" in text


def test_executives_get_the_key_person_count_without_names(client):
    report = client.get("/api/reports/onboarding", headers=EXEC).json()
    fig = next(f for f in report["figures"] if f["key"] == "single_owner_critical_areas")
    assert fig["current"]["value"] == 2 and fig["current"]["display"] == "2"  # engine/ and webhooks/ each hold a critical file one person owns
    assert fig["previous"]["value"] is None
    assert client.get(f"/api/knowledge-risk?project_id={PID}", headers=EXEC).status_code == 403  # no access to the page itself
    for name in ("Ravi", "Meera", "engine/", "webhooks/", "policy.py", "server.py"):
        assert name not in str(fig)


def test_a_small_team_survey_says_not_enough_data_in_the_report_by_team(client):
    team = _survey_team(client, "Tiny")
    for day in (30, 60, 90):
        for _ in range(3):
            _c(client).lifecycle.add_survey_response(SurveyResponse(id="", org_id="org-demo", team_id=team, cohort="2026-Q4", day=day,
                                                                    answers={"clarity": 5}))
    one = client.get("/api/reports/onboarding", params={"team_id": team}, headers=EXEC).json()
    [row] = one["surveys"]["teams"]
    assert row["status"] == "not_enough_data" and row["scores"] is None and row["n"] == 3


# ---------------------------------------------------------------- an expert's answer saved as a verified answer: one source
def test_saving_an_expert_answer_as_a_verified_answer_does_not_index_it_twice(client):
    q = "How is the mutate module benchmarked?"
    asked = client.post("/api/chat", json={"project_id": PID, "question": q}, headers=VIJAY).json()
    [req] = [r for r in client.get("/api/experts/requests", headers=RAVI).json() if r["message_id"] == asked["message_id"]]
    assert client.post(f"/api/experts/requests/{req['id']}/answer", json={"answer": "With pytest-benchmark."}, headers=RAVI).status_code == 200
    index = _c(client).index
    assert f"verified-{req['id']}" in index.chunks
    body = {"project_id": PID, "question": q, "answer": "With pytest-benchmark in CI.", "source_request_id": req["id"]}
    first = client.post("/api/verified-answers", json=body, headers=RAVI)
    assert first.status_code == 201
    va = first.json()
    again = client.post("/api/verified-answers", json=body, headers=RAVI)  # a second click, or a retry: the same verified answer
    assert again.json()["id"] == va["id"] and len([a for a in client.get(f"/api/verified-answers?project_id={PID}", headers=RAVI).json()
                                                  if a["source_request_id"] == req["id"]]) == 1
    holding = [c for c in index.chunks.values() if c.content.startswith(f"Q: {q}")]
    assert [c.id for c in holding] == [f"verified-{req['id']}"]  # one chunk, not 'verified-…' and 'va-…'
    assert f"va-{va['id']}" not in index.chunks and holding[0].ref["verified_answer_id"] == va["id"]
    assert "pytest-benchmark in CI" in holding[0].content  # the verified wording replaced the first answer's

    # asked again, it is answered from that one source: cited once, by the chunk that exists, and the verified answer counts the reuse
    out = client.post("/api/ask/route", json={"project_id": PID, "question": q}, headers=ARJUN).json()
    assert out["status"] in {"answered", "related"}
    turn = client.get(f"/api/chat/history?project_id={PID}", headers=ARJUN).json()[-1]
    cited = [c["chunk_id"] for c in turn["citations"] if c["source_type"] == "verified_answer"]
    assert cited == [f"verified-{req['id']}"] and cited[0] in index.chunks  # one citation, not a pair
    assert next(a for a in client.get(f"/api/verified-answers?project_id={PID}", headers=RAVI).json() if a["id"] == va["id"])["reuse_count"] == 1


def test_a_verified_answer_with_no_request_keeps_its_own_chunk(client):
    va = client.post("/api/verified-answers", json={"project_id": PID, "question": "How do I run the tests?", "answer": "pytest -q"},
                     headers=RAVI).json()
    assert f"va-{va['id']}" in _c(client).index.chunks


# ---------------------------------------------------------------- the pre-boarding portal's first week
def _portal(client, token=TOKEN):
    r = client.get(f"/api/preboarding/{token}")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_portal_shows_the_templates_first_week_and_the_kt_sessions_booked_in_it(client):
    from datetime import datetime, time, timedelta, timezone

    from app.ports import Attendee

    c = _c(client)
    ob = c.store.get_onboarding("ob-arjun")
    start = ob.start_date
    week = _portal(client)["first_week"]
    assert week and all(set(i) <= {"date", "title", "text", "done", "kind"} and {"date", "title"} <= set(i) for i in week)
    assert [i["date"] for i in week] == sorted(i["date"] for i in week)  # oldest first
    template = c.lifecycle.get_template(c.lifecycle.get_program("ob-arjun").template_id)
    due = {m["title"]: m["due_day"] for m in template.milestones if m["due_day"] <= 5}
    for title, day in due.items():  # every first-week milestone, on its due date
        assert next(i for i in week if i["title"] == title)["date"] == (start + timedelta(days=day)).isoformat()
    assert {"Set up your development environment", "Meet your mentor and the team"} <= {i["title"] for i in week}  # the plan items too
    assert not any(m["title"] in {i["title"] for i in week} for m in template.milestones if m["due_day"] > 5)  # nothing later
    assert {i["kind"] for i in week} <= {"welcome", "setup", "meet", "kt", "learn", "task"}
    assert next(i for i in week if i["title"] == "Set up your development environment")["done"] is True  # Arjun finished that task

    # KT sessions: only the ones he is invited to, held in his first five working days, as a title and a day (no link)
    def book(title, days, who=("u-arjun",), status="scheduled"):
        m = c.store.create_meeting(PID, title, "u-priya", provider="manual", starts_at=datetime.combine(start + timedelta(days=days), time(10), tzinfo=timezone.utc),
                                   ends_at=datetime.combine(start + timedelta(days=days), time(11), tzinfo=timezone.utc),
                                   attendees=[Attendee(email=f"{u[2:]}@demo.dev", name=u, user_id=u) for u in who],
                                   join_url="https://meet.google.com/abc-defg-hij")
        c.store.update_meeting(m.id, status=status)

    book("KT: how admission webhooks work", 1)
    book("KT: for someone else", 1, who=("u-sara",))
    book("KT: cancelled one", 2, status="cancelled")
    book("KT: three weeks in", 21)
    week = _portal(client)["first_week"]
    kt = [i for i in week if i["kind"] == "kt"]
    assert [i["title"] for i in kt] == ["KT: how admission webhooks work"]
    assert kt[0]["date"] == (start + timedelta(days=1)).isoformat() and kt[0]["text"] == "KT session, 10:00 UTC" and kt[0]["done"] is False
    text = json.dumps(_portal(client))
    assert "meet.google.com" not in text and "for someone else" not in text and "u-sara" not in text and "kt-" not in text  # no link, no room


def test_the_first_week_comes_only_from_the_template_never_from_the_hires_own_tasks(client):
    c = _c(client)
    from app.agents.core.schemas import ProposedTask

    c.store.create_tasks("ob-arjun", [ProposedTask(title="Fix the secret payment bug in billing/charge.py", description="x", kind="code",
                                                   due_in_days=2, related_paths=["billing/charge.py"])], c.store.get_onboarding("ob-arjun").start_date)
    assert "billing" not in json.dumps(_portal(client))


def test_the_portal_has_no_first_week_when_the_template_holds_nothing_for_it(client):
    c = _c(client)
    tid = c.lifecycle.get_program("ob-arjun").template_id
    c.lifecycle.template_rows[tid] = c.lifecycle.template_rows[tid].model_copy(update={"milestones": [], "plan_items": []})
    c.lifecycle.set_milestones("ob-arjun", [])  # no plan approved yet: nothing is rows, nothing is template
    assert "first_week" not in _portal(client)  # the page shows its generic list


def test_first_week_scales_with_the_program_length(client):
    c = _c(client)
    c.lifecycle.update_program("ob-arjun", duration_days=45)  # half as long: the template's day 5 is day 2 or 3
    c.lifecycle.set_milestones("ob-arjun", [])
    start = c.store.get_onboarding("ob-arjun").start_date
    week = _portal(client)["first_week"]
    template = c.lifecycle.get_template(c.lifecycle.get_program("ob-arjun").template_id)
    from datetime import timedelta
    from app.lifecycle import templates as T

    expected = {m["title"]: (start + timedelta(days=T.scale(m["due_day"], 45))).isoformat() for m in template.milestones if T.scale(m["due_day"], 45) <= 5}
    assert {i["title"]: i["date"] for i in week if i["title"] in expected} == expected
    assert expected["Met the mentor and the team"] == (start + timedelta(days=2)).isoformat()  # day 5 of 90 is day 2 of 45
