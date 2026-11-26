"""The product-framed role homes (GET /api/home): a hire's milestones and current tasks, a manager's or admin's pending
decisions and recent activity. Demo mode: the seeded organisation, in-memory stores."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.lifecycle import home
from app.main import create_app

ADMIN, MANAGER, MENTOR, HIRE = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-ravi", "u-vijay"))
SECURITY_EVENTS = {"audit.viewed", "audit.exported", "demo.signed_in", "member.role_changed", "organization.exported"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _home(client, who):
    r = client.get("/api/home", headers=who)
    assert r.status_code == 200
    return r.json()


def _store(client):
    return client.app.state.container.store


# ---------------------------------------------------------------- the new hire

def test_a_hire_sees_their_milestones_with_one_current(client):
    s = _home(client, HIRE)["sections"]
    phases = s["phases"]
    assert [p["due_day"] for p in phases] == sorted(p["due_day"] for p in phases) and len(phases) >= 3
    assert [p["state"] for p in phases].count("current") == 1
    first_open = next(i for i, p in enumerate(phases) if p["state"] != "done")
    assert phases[first_open]["state"] == "current"  # the first milestone with work left is the current one
    assert all(p["state"] in {"upcoming", "done"} for p in phases[first_open + 1:])  # a later one is done only if its work is
    assert all(0 <= p["tasks_done"] <= p["tasks_total"] for p in phases)


def test_the_current_milestone_lists_only_approved_work_in_progress_first(client):
    s = _home(client, HIRE)["sections"]
    cur = s["current"]
    assert cur and cur["key"] == next(p["key"] for p in s["phases"] if p["state"] == "current")
    assert 0 < len(cur["tasks"]) <= home.MAX_CURRENT_TASKS
    assert not {t["status"] for t in cur["tasks"]} & {"proposed", "rejected"}  # a hire never sees an unapproved plan
    order = [t["status"] for t in cur["tasks"]]
    assert order.index("in_progress") < order.index("done") if {"in_progress", "done"} <= set(order) else True
    total = sum(p["tasks_total"] for p in s["phases"])
    visible = [t for t in _store(client).tasks(onboarding_id="ob-vijay") if t.status not in {"proposed", "rejected"}]
    assert total == len(visible)  # every visible task is in exactly one milestone


def test_a_task_sits_in_the_first_milestone_due_on_or_after_it():
    class Lc:
        def phases(self, _):
            return []

        def placements(self, _):
            return []

    class Clock:
        def today(self):
            return date(2026, 1, 10)

    class Ctx:
        lc, clock = Lc(), Clock()

    class Task:
        def __init__(self, id, due, status="approved"):
            self.id, self.due_date, self.status, self.kind, self.title = id, due, status, "learn", id

    class Program:
        duration_days = 90

    class Ob:
        id, start_date = "ob-1", date(2026, 1, 1)

    class Hc:
        ctx, ob, program = Ctx(), Ob(), Program()
        tasks = [Task("early", date(2026, 1, 5)), Task("day-20", date(2026, 1, 21)), Task("undated", None),
                 Task("hidden", date(2026, 1, 3), "proposed")]

    phases, current = home._board(Hc())
    by_key = {p["key"]: p for p in phases}
    first, second, last = phases[0]["key"], phases[1]["key"], phases[-1]["key"]
    assert by_key[first]["tasks_total"] == 1 and by_key[second]["tasks_total"] == 1 and by_key[last]["tasks_total"] == 1
    assert current["key"] == first and [t["id"] for t in current["tasks"]] == ["early"]  # the proposed task is left out


# ---------------------------------------------------------------- managers and admins

def test_pending_decisions_are_counted_and_the_oldest_lead(client):
    s = _home(client, MANAGER)["sections"]
    pending = [a for a in _store(client).approvals("proj-demo")]
    assert s["approvals"]["count"] == len(pending)
    items = s["approvals"]["items"]
    assert len(items) == min(len(pending), home.MAX_APPROVALS)
    assert [i["created_at"] for i in items] == sorted(i["created_at"] for i in items)
    assert all(i["title"] and i["link"].startswith("/approvals/") for i in items)


def test_activity_is_newest_first_product_events_only(client):
    client.get("/api/audit", headers=ADMIN)  # writes audit.viewed: a security event, never activity
    for who in (MANAGER, ADMIN):
        rows = _home(client, who)["sections"]["activity"]
        assert 0 < len(rows) <= home.MAX_ACTIVITY
        assert [r["at"] for r in rows] == sorted((r["at"] for r in rows), reverse=True)
        assert not {r["action"] for r in rows} & SECURITY_EVENTS
        assert all(r["text"] and r["actor"]["name"] for r in rows)
        assert not any("(" in r["actor"]["name"] for r in rows)  # "Priya", not "Priya (Manager)"


def test_a_managers_activity_is_people_on_their_projects(client):
    members = {m.id for m in _store(client).members("proj-demo")}
    assert {r["actor"]["id"] for r in _home(client, MANAGER)["sections"]["activity"]} <= members


def test_a_hire_writing_their_own_check_in_reads_naturally(client):
    texts = [r["text"] for r in _home(client, ADMIN)["sections"]["activity"]]
    assert not any("Sara wrote Sara's" in t for t in texts)
    line = home._activity_line({"action": "checkin.written", "target": "ob-1", "actor_id": "u-1", "detail": {"day": 30}},
                               lambda ob: "Sara", lambda uid: None, lambda ob: "u-1")
    assert line == ("wrote their day-30 check-in", "/program/ob-1?tab=checkins")


def test_reading_the_home_does_not_write_to_the_audit_log(client):
    rows = _store(client).audit_rows
    before = len(rows)
    _home(client, ADMIN)
    _home(client, MANAGER)
    assert len(rows) == before


def test_the_home_still_loads_when_the_audit_log_cannot_be_read(client, monkeypatch):
    from app.adapters import audit_store

    def broken(*a, **k):
        raise RuntimeError("database away")

    monkeypatch.setattr(audit_store, "page", broken)
    s = _home(client, ADMIN)["sections"]
    assert s["activity"] == [] and s["setup"]


def test_an_admin_sees_the_hires_and_their_stage(client):
    s = _home(client, ADMIN)["sections"]
    assert s["hires"] and all({"profile", "status"} <= set(c) for c in s["hires"])
    assert {c["profile"]["onboarding_id"] for c in s["hires"]} >= {"ob-vijay"}


def test_hires_and_experts_get_no_activity_or_approvals(client):
    for who in (HIRE, MENTOR):
        s = _home(client, who)["sections"]
        assert "activity" not in s and "approvals" not in s


# ---------------------------------------------------------------- activity: scoped by project, board events read as the board
def _inner(client):
    s = _store(client)
    return s.__dict__.get("_store", s)


def test_a_managers_activity_keeps_to_their_own_projects(client):
    """Someone on two projects: what they do in the other one never reaches a manager who is not on it (security review)."""
    inner = _inner(client)
    p2 = inner.create_project("org-demo", "Confidential project", "", "u-admin")
    inner.add_member(p2.id, "u-ravi")
    store = _store(client)
    store.audit("org-demo", "u-ravi", "document.uploaded", "doc-secret", {"title": "Q4 restructuring plan", "project_id": p2.id})
    store.audit("org-demo", "u-ravi", "repo.connected", "repo-secret", {"full_name": "acme/stealth-product"})  # cannot be placed: left out
    store.audit("org-demo", "u-ravi", "meeting.recording_uploaded", "mtg-1", {})  # proj-demo's meeting: stays
    assert client.get(f"/api/projects/{p2.id}", headers=MANAGER).status_code in {403, 404}
    texts = [a["text"] for a in _home(client, MANAGER)["sections"]["activity"]]
    assert not any("Q4 restructuring plan" in t or "stealth-product" in t for t in texts), texts
    assert "added a KT recording" in texts
    admin = [a["text"] for a in _home(client, ADMIN)["sections"]["activity"]]
    assert any("Q4 restructuring plan" in t for t in admin)  # admins keep the organisation's feed


def test_board_moves_reach_the_home_feed_in_the_boards_words(client):
    board = client.get("/api/onboardings/ob-vijay/board", headers=MANAGER).json()
    card = next(t for t in board["tasks"] if t["status"] == "approved")
    r = client.post(f"/api/onboardings/ob-vijay/board/tasks/{card['id']}/move", json={"column": "in_progress"}, headers=MANAGER)
    assert r.status_code == 200
    for who in (MANAGER, ADMIN):
        top = _home(client, who)["sections"]["activity"][0]
        assert top["action"] == "board.task.moved" and top["link"] == "/board/ob-vijay"
        assert top["text"] == f"moved '{card['title']}' to In progress on Vijay's board", top["text"]


def test_a_run_of_board_moves_reads_as_one_line(client):
    board = client.get("/api/onboardings/ob-vijay/board", headers=MANAGER).json()
    cards = [t for t in board["tasks"] if t["status"] == "approved"][:3]
    assert len(cards) == 3
    for card in cards:
        client.post(f"/api/onboardings/ob-vijay/board/tasks/{card['id']}/move", json={"column": "in_progress"}, headers=MANAGER)
    rows = _home(client, MANAGER)["sections"]["activity"]
    assert rows[0]["text"] == "moved 3 tasks on Vijay's board" and rows[0]["link"] == "/board/ob-vijay"
    assert sum(r["action"] == "board.task.moved" for r in rows) == 1


def test_board_lines_read_naturally():
    line = lambda action, d: home._activity_line({"action": action, "target": "t1", "actor_id": "u-1", "detail": {"onboarding_id": "ob-1", **d}},  # noqa: E731
                                                lambda ob: "Sara", lambda uid: None, lambda ob: "u-2")
    assert line("board.task.moved", {"from": "submitted", "to": "done", "title": "Ship it"}) == ("signed off 'Ship it' on Sara's board", "/board/ob-1")
    assert line("board.task.moved", {"from": "in_progress", "to": "changes_requested", "title": "T"})[0] == "asked for changes to 'T' on Sara's board"
    assert line("board.task.rescheduled", {"title": "T", "to_day": 30, "to_title": "First change shipped"})[0] == (
        "moved 'T' to Day 30 · First change shipped on Sara's board")
    assert line("board.task.rescheduled", {"from": "a", "to": "b"})[0] == "moved a task to another milestone on Sara's board"  # older entries
    assert line("board.task.added", {"title": "T"})[0] == "added 'T' to Sara's board"
    assert line("board.milestones.reordered", {})[0] == "updated the milestones on Sara's board"


def test_a_hires_overdue_checkpoint_says_so_and_comes_first(client):
    focus = _home(client, HIRE)["focus"]
    checkpoint = [f for f in focus if f["key"].startswith("milestone:")]
    for f in checkpoint:
        assert f["text"].startswith("Next checkpoint: ")
        if "overdue since" in f["text"]:
            assert f["priority"] == 2
    assert not any(f["text"].startswith("Next milestone") for f in focus)


def test_the_admins_people_figure_says_what_it_counts(client):
    fig = _home(client, ADMIN)["figure"]
    assert fig["label"] == "People on projects" and "at least one project" in fig["how"]
