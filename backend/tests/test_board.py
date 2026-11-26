"""The milestone board (app/lifecycle/board.py, app/api/board.py) in demo mode: who sees and changes what, the column rules,
moves between milestones with their history, the milestones themselves, templates' milestones, audit entries, and the
organisation's export and deletion. The seed's boards: Vijay (day 12), Arjun (day 3), Sara (day 45, with a milestone Priya
added on day 75) and Tom (day 88)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.adapters.lifecycle_memory import MemoryLifecycleStore
from app.adapters.memory import MemoryStore
from app.config import get_settings
from app.lifecycle import board as B
from app.lifecycle import templates as T
from app.main import create_app
from app.ports import Phase, TaskMove, TaskPlacement, TaskRow

PID = "proj-demo"
ADMIN, MANAGER, MENTOR, HIRE, MEERA, EXEC, ARJUN = ({"X-Demo-User": u} for u in (
    "u-admin", "u-priya", "u-ravi", "u-vijay", "u-meera", "u-exec", "u-arjun"))
URL = "/api/onboardings/ob-vijay/board"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _c(client):
    return client.app.state.container


def _card(board: dict, title: str) -> dict:
    return next(t for t in board["tasks"] if t["title"] == title)


def _move(client, title: str, who=HIRE, url=URL, **body):
    task = _card(client.get(url, headers=MANAGER).json(), title)
    return client.post(f"{url}/tasks/{task['id']}/move", json=body, headers=who)


def _audits(client, action: str) -> list[dict]:
    return [a for a in _c(client).store.audit_rows if a["action"] == action]


# ---------------------------------------------------------------- what the board holds
def test_a_board_has_the_default_milestones_and_every_approved_task(client):
    b = client.get(URL, headers=HIRE).json()
    assert [(m["key"], m["due_day"]) for m in b["milestones"]] == [
        ("settle_in", 15), ("first_change", 30), ("on_your_own", 45), ("own_an_area", 60), ("fully_on_board", 90)]
    assert [c["key"] for c in b["columns"]] == ["todo", "in_progress", "done"] and b["current"] == "settle_in" and b["day"] == 12
    store = _c(client).store
    approved = [t for t in store.tasks(onboarding_id="ob-vijay") if t.status in B.COLUMN_OF]
    assert {t["id"] for t in b["tasks"]} == {t.id for t in approved}  # proposed ones wait in Approvals
    assert b["waiting_approval"] == sum(t.status == "proposed" for t in store.tasks(onboarding_id="ob-vijay")) > 0
    first = b["milestones"][0]
    assert first["total"] == sum(t["milestone"] == "settle_in" for t in b["tasks"]) and first["done"] == 2
    assert {"Pre-boarding documents acknowledged", "First pull request opened"} <= {c["title"] for c in first["checkpoints"]}
    # columns are statuses: a submitted task is Done, waiting for sign-off; an overdue one is marked; KT tasks are marked
    watch = _card(b, "Walk through KT #1 with Ravi")
    assert (watch["column"], watch["badge"], watch["kt"]) == ("done", "Waiting for sign-off", True)
    assert _card(b, "Fix the Docker check on your laptop")["overdue"] is True
    # the seeded move: "Moved from Settled in", and the milestone it left lists it
    moved = _card(b, "Trace one request through the engine")
    assert moved["milestone"] == "first_change" and moved["moved"]["from_key"] == "settle_in" and moved["moved"]["by"]["id"] == "u-priya"
    assert [g["title"] for g in first["moved_out"]] == ["Trace one request through the engine"]


def test_a_task_without_a_placement_sits_in_the_milestone_its_due_date_falls_in():
    ps = [Phase(onboarding_id="o", key=k, title=k, due_day=d, position=i) for i, (k, d) in enumerate([("a", 15), ("b", 30), ("c", 90)])]
    assert [B._phase_for_day(ps, d).key for d in (None, 0, 15, 16, 30, 89, 400)] == ["a", "a", "a", "b", "b", "c", "c"]
    assert B._window(ps, ps[1]) == (16, 30) and B._window(ps, ps[0]) == (0, 15)


def test_default_milestones_scale_with_the_program_and_templates_carry_their_own(client):
    assert [p["due_day"] for p in T.default_phases(90)] == [15, 30, 45, 60, 90]
    assert [p["due_day"] for p in T.default_phases(120)] == [20, 40, 60, 80, 120]
    assert [p["due_day"] for p in T.default_phases(14)] == [2, 5, 7, 9, 14]  # strictly increasing, within the program
    t = next(t for t in client.get("/api/programs/templates", headers=MANAGER).json() if t["key"] == "backend-mid")
    assert [p["due_day"] for p in t["phases"]] == [15, 30, 45, 60, 90]
    phases = [{"key": "week_two", "title": "Two weeks in", "due_day": 14}, {"key": "month_one", "title": "A month in", "due_day": 30},
              {"key": "quarter", "title": "A quarter in", "due_day": 90}]
    r = client.patch(f"/api/programs/templates/{t['id']}", json={"phases": phases}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["phases"] == phases and r.json()["version"] == t["version"] + 1
    dup = client.patch(f"/api/programs/templates/{r.json()['id']}", json={"phases": phases[:1] * 2}, headers=ADMIN)
    assert dup.status_code == 422
    assert client.patch(f"/api/programs/templates/{r.json()['id']}", json={"phases": phases}, headers=MANAGER).status_code == 403
    # a program started from the new version gets its milestones the first time its board is read
    from app.lifecycle import hires
    from app.lifecycle.context import Ctx

    c = _c(client)
    c.lifecycle.update_program("ob-arjun", template_id=r.json()["id"])
    c.lifecycle.phase_rows.pop("ob-arjun", None)
    hc = hires.load(Ctx.of(c, c.store.get_user("u-priya")), "ob-arjun")
    assert [(p.key, p.due_day) for p in B.phases(hc)] == [("week_two", 14), ("month_one", 30), ("quarter", 90)]


# ---------------------------------------------------------------- who sees what
def test_only_the_hire_and_the_projects_managers_open_a_board(client):
    assert client.get(URL, headers=HIRE).status_code == 200
    assert client.get(URL, headers=MANAGER).status_code == 200 and client.get(URL, headers=ADMIN).status_code == 200
    assert client.get(URL, headers=MENTOR).status_code == 403  # Ravi mentors Vijay: the program page, not the board
    assert client.get(URL, headers=MEERA).status_code == 404  # unrelated: as if it did not exist
    assert client.get(URL, headers=ARJUN).status_code == 404  # another hire
    assert client.get(URL, headers=EXEC).status_code == 404
    assert client.get(URL).status_code == 401
    rows = client.get("/api/board/hires", params={"project_id": PID}, headers=MANAGER).json()
    assert {r["onboarding_id"] for r in rows["hires"]} == {"ob-vijay", "ob-arjun", "ob-sara", "ob-tom"} and rows["can_manage"]
    own = client.get("/api/board/hires", params={"project_id": PID}, headers=HIRE).json()
    assert [r["onboarding_id"] for r in own["hires"]] == ["ob-vijay"] and not own["can_manage"]
    assert client.get("/api/board/hires", params={"project_id": PID}, headers=MENTOR).status_code == 403
    assert client.get("/api/board/hires", params={"project_id": PID}, headers=EXEC).status_code == 403


def test_the_hire_cannot_change_milestones_tasks_or_another_board(client):
    task = _card(client.get(URL, headers=HIRE).json(), "Read the policy module before Friday")
    for method, path, body in (("post", f"{URL}/tasks", {"title": "Mine"}), ("patch", f"{URL}/tasks/{task['id']}", {"title": "x"}),
                               ("delete", f"{URL}/tasks/{task['id']}", None), ("post", f"{URL}/milestones", {"title": "x", "due_day": 70}),
                               ("patch", f"{URL}/milestones/settle_in", {"title": "x"}), ("delete", f"{URL}/milestones/settle_in", None),
                               ("put", f"{URL}/milestones/order", {"keys": ["fully_on_board"]})):
        r = client.request(method, path, json=body, headers=HIRE)
        assert r.status_code == 403, (method, path, r.text)
        assert client.request(method, path, json=body, headers=MENTOR).status_code == 403
    r = client.post(f"{URL}/tasks/{task['id']}/move", json={"milestone": "first_change"}, headers=HIRE)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "manager_only"
    sara = _card(client.get("/api/onboardings/ob-sara/board", headers=MANAGER).json(), "Document the admission flow")
    assert client.post(f"/api/onboardings/ob-sara/board/tasks/{sara['id']}/move", json={"column": "in_progress"},
                       headers=HIRE).status_code == 404
    assert client.post(f"{URL}/tasks/{sara['id']}/move", json={"column": "in_progress"}, headers=HIRE).status_code == 404


# ---------------------------------------------------------------- columns
def test_the_hire_works_a_card_through_the_columns_and_the_manager_signs_it_off(client):
    title = "Read the policy module before Friday"
    r = _move(client, title, column="in_progress")
    assert r.status_code == 200 and _card(r.json(), title)["status"] == "in_progress"
    assert _card(_move(client, title, column="todo").json(), title)["status"] == "approved"  # back to To do
    done = _card(_move(client, title, column="done").json(), title)
    assert (done["status"], done["column"], done["badge"]) == ("submitted", "done", "Waiting for sign-off")
    store = _c(client).store
    assert any(n["user_id"] == "u-priya" and n["kind"] == "board" and "finished" in n["text"] for n in store.notifications)
    assert _card(_move(client, title, column="in_progress").json(), title)["status"] == "in_progress"  # taken back
    _move(client, title, column="done")
    # the manager asks for changes, then signs off
    changed = _card(_move(client, title, who=MANAGER, column="in_progress", changes_requested=True).json(), title)
    assert (changed["status"], changed["badge"]) == ("changes_requested", "Changes requested")
    assert _move(client, title, column="todo").status_code == 409  # with changes asked for, not back to To do
    _move(client, title, column="done")
    assert _card(_move(client, title, who=MANAGER, column="done").json(), title)["status"] == "submitted"  # a new place, no sign-off
    assert _move(client, title, sign_off=True).status_code == 403
    signed = _card(_move(client, title, who=MANAGER, sign_off=True).json(), title)
    assert signed["status"] == "done" and signed["moves"] == ["todo", "in_progress", "done"]
    assert any(n["user_id"] == "u-vijay" and "signed off" in n["text"] for n in store.notifications)
    # a done task is the manager's to reopen
    hire_view = _card(client.get(URL, headers=HIRE).json(), title)
    assert hire_view["moves"] == ["done"]
    r = _move(client, title, column="in_progress")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "done"
    assert _card(_move(client, title, who=MANAGER, column="todo").json(), title)["status"] == "approved"
    moved = [a["detail"] for a in _audits(client, "board.task.moved")]
    assert {"from": "approved", "to": "in_progress", "title": title, "onboarding_id": "ob-vijay", "project_id": "proj-demo"} in moved
    assert len(moved) >= 9


def test_only_managers_ask_for_changes_and_a_pending_review_is_decided_in_approvals(client):
    title = "Write down three questions about validation"
    r = _move(client, title, column="in_progress", changes_requested=True)
    assert r.status_code == 403
    c = _c(client)
    task = _card(client.get(URL, headers=MANAGER).json(), title)
    c.store.update_task(task["id"], status="submitted", pr_number=7, pr_url="https://github.com/demo/mini-policy-engine/pull/7")
    c.store.create_approval(PID, "review", "t-review", {"task_id": task["id"]})
    board = client.get(URL, headers=MANAGER).json()
    assert _card(board, title)["badge"] == "In review" and _card(board, title)["review_pending"]
    assert _card(board, title)["moves"] == ["done"]
    r = _move(client, title, who=MANAGER, column="done")  # the same column: only its place changes
    assert r.status_code == 200
    r = _move(client, title, who=MANAGER, column="todo")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "review_pending"
    assert client.delete(f"{URL}/tasks/{task['id']}", headers=MANAGER).status_code == 409


def test_a_card_waiting_in_approvals_or_on_a_finished_program_does_not_move(client):
    c = _c(client)
    proposed = next(t for t in c.store.tasks(onboarding_id="ob-vijay") if t.status == "proposed")
    r = client.post(f"{URL}/tasks/{proposed.id}/move", json={"column": "in_progress"}, headers=HIRE)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "awaiting_approval"
    assert client.post(f"{URL}/tasks/nope/move", json={"column": "done"}, headers=HIRE).status_code == 404
    c.lifecycle.set_onboarding_status("ob-vijay", "completed")
    r = _move(client, "Pair with Ravi on the label rules", column="in_progress")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "inactive"
    assert _move(client, "Pair with Ravi on the label rules", who=MANAGER, column="in_progress").status_code == 200


def test_a_card_dropped_before_another_takes_its_place_in_the_column(client):
    board = client.get(URL, headers=MANAGER).json()
    todo = [t for t in board["tasks"] if t["milestone"] == "first_change" and t["column"] == "todo"]
    assert len(todo) >= 2
    last, first = todo[-1], todo[0]
    r = client.post(f"{URL}/tasks/{last['id']}/move", json={"column": "todo", "before_id": first["id"]}, headers=HIRE)
    after = [t["id"] for t in r.json()["tasks"] if t["milestone"] == "first_change" and t["column"] == "todo"]
    assert after[0] == last["id"] and set(after) == {t["id"] for t in todo}
    # the order holds on the next read, and a card moved to another column goes to its end
    assert [t["id"] for t in client.get(URL, headers=HIRE).json()["tasks"]
            if t["milestone"] == "first_change" and t["column"] == "todo"] == after
    assert client.post(f"{URL}/tasks/{last['id']}/move", json={"before_id": "not-a-card"}, headers=HIRE).status_code == 422


# ---------------------------------------------------------------- moves between milestones
def test_a_manager_moves_a_task_to_another_milestone_and_both_keep_the_history(client):
    title = "Pair with Ravi on the label rules"
    before = _card(client.get(URL, headers=MANAGER).json(), title)
    assert before["milestone"] == "first_change" and not before["pinned"]
    r = _move(client, title, who=MANAGER, milestone="own_an_area")
    assert r.status_code == 200
    b = r.json()
    card = _card(b, title)
    start = date.fromisoformat(b["start_date"])
    assert card["milestone"] == "own_an_area" and card["pinned"] and card["moved"]["from_key"] == "first_change"
    assert date.fromisoformat(card["due_date"]) == start + timedelta(days=60)  # due inside the milestone it moved to
    assert card["history"][-1]["from_due"] == before["due_date"]
    left = next(m for m in b["milestones"] if m["key"] == "first_change")
    assert title in [g["title"] for g in left["moved_out"]]
    assert _audits(client, "board.task.rescheduled")[-1]["detail"]["to"] == "own_an_area"
    assert any(n["user_id"] == "u-vijay" and "moved" in n["text"] for n in _c(client).store.notifications)
    # back again: no longer "moved out" of first_change, and the history keeps both moves
    card = _card(_move(client, title, who=MANAGER, milestone="first_change").json(), title)
    assert card["moved"]["from_key"] == "own_an_area" and len(card["history"]) == 2
    assert _move(client, title, who=MANAGER, milestone="nope").status_code == 404


# ---------------------------------------------------------------- managers: tasks and milestones
def test_managers_add_edit_and_remove_tasks(client):
    r = client.post(f"{URL}/tasks", json={"title": "Shadow a release", "kind": "meet", "milestone": "on_your_own"}, headers=MANAGER)
    assert r.status_code == 201
    card = _card(r.json(), "Shadow a release")
    assert (card["milestone"], card["status"], card["kind"], card["origin"]) == ("on_your_own", "approved", "meet", "manager")
    assert date.fromisoformat(card["due_date"]) == date.fromisoformat(r.json()["start_date"]) + timedelta(days=45)
    due = (date.fromisoformat(r.json()["start_date"]) + timedelta(days=40)).isoformat()
    r = client.patch(f"{URL}/tasks/{card['id']}", json={"title": "Shadow the next release", "due_date": due}, headers=ADMIN)
    assert _card(r.json(), "Shadow the next release")["due_date"] == due
    assert client.post(f"{URL}/tasks", json={"title": "x", "milestone": "nope"}, headers=MANAGER).status_code == 404
    r = client.delete(f"{URL}/tasks/{card['id']}", headers=MANAGER)
    assert r.status_code == 200 and card["id"] not in {t["id"] for t in r.json()["tasks"]}
    assert _c(client).store.get_task(card["id"]).status == "rejected"
    assert [a["action"] for a in _c(client).store.audit_rows if a["target"] == card["id"]] == [
        "board.task.added", "board.task.changed", "board.task.removed"]
    # without a milestone, a new task goes into the one the hire is in now
    assert _card(client.post(f"{URL}/tasks", json={"title": "Now"}, headers=MANAGER).json(), "Now")["milestone"] == "settle_in"


def test_managers_add_rename_redate_reorder_and_delete_milestones(client):
    r = client.post(f"{URL}/milestones", json={"title": "Leads a design review", "due_day": 75}, headers=MANAGER)
    assert r.status_code == 201
    keys = [m["key"] for m in r.json()["milestones"]]
    assert keys == ["settle_in", "first_change", "on_your_own", "own_an_area", "leads_a_design_review", "fully_on_board"]
    assert client.post(f"{URL}/milestones", json={"title": "Again", "due_day": 75}, headers=MANAGER).status_code == 409
    assert client.post(f"{URL}/milestones", json={"title": "x", "due_day": 0}, headers=MANAGER).status_code == 422
    # a re-dated milestone re-flows the tasks placed by their due date (Vijay's day-44 task falls in day 45 until it is day 40)
    r = client.patch(f"{URL}/milestones/on_your_own", json={"title": "On your own", "due_day": 40}, headers=MANAGER)
    m = next(m for m in r.json()["milestones"] if m["key"] == "on_your_own")
    assert (m["title"], m["due_day"]) == ("On your own", 40)
    assert _card(r.json(), "Present the validation flow at team sync")["milestone"] == "own_an_area"
    assert client.patch(f"{URL}/milestones/on_your_own", json={"due_day": 30}, headers=MANAGER).status_code == 409  # day taken
    r = client.put(f"{URL}/milestones/order", json={"keys": ["fully_on_board", "settle_in"]}, headers=ADMIN)
    assert [m["key"] for m in r.json()["milestones"]][:3] == ["fully_on_board", "settle_in", "first_change"]
    assert client.put(f"{URL}/milestones/order", json={"keys": ["nope"]}, headers=ADMIN).status_code == 422
    # deleting one moves its tasks to the next by day, each move recorded
    board = client.get(URL, headers=MANAGER).json()
    leaving = {t["id"] for t in board["tasks"] if t["milestone"] == "first_change"}
    r = client.delete(f"{URL}/milestones/first_change", headers=MANAGER)
    assert r.status_code == 200 and "first_change" not in [m["key"] for m in r.json()["milestones"]]
    for t in r.json()["tasks"]:
        if t["id"] in leaving:
            assert t["milestone"] == "on_your_own" and t["moved"]["from_key"] == "first_change"
    assert [a["action"] for a in _audits(client, "board.milestone.removed")] and _audits(client, "board.milestone.removed")[-1]["detail"][
        "tasks_moved"] == len(leaving)
    for key in ("settle_in", "on_your_own", "own_an_area", "leads_a_design_review"):
        client.delete(f"{URL}/milestones/{key}", headers=MANAGER)
    r = client.delete(f"{URL}/milestones/fully_on_board", headers=MANAGER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "last_one"
    assert {a["action"] for a in _c(client).store.audit_rows if a["action"].startswith("board.milestone")} == {
        "board.milestone.added", "board.milestone.changed", "board.milestone.removed", "board.milestones.reordered"}


def test_the_seeded_boards_are_at_different_stages(client):
    sara = client.get("/api/onboardings/ob-sara/board", headers=MANAGER).json()
    assert "design_review" in [m["key"] for m in sara["milestones"]] and sara["current"] == "on_your_own"
    statuses = {t["status"] for t in sara["tasks"]}
    assert {"done", "submitted", "in_progress", "changes_requested", "approved"} <= statuses
    tom = client.get("/api/onboardings/ob-tom/board", headers=MANAGER).json()
    assert tom["current"] == "fully_on_board" and sum(m["state"] == "complete" for m in tom["milestones"]) >= 3
    arjun = client.get("/api/onboardings/ob-arjun/board", headers=MANAGER).json()
    assert arjun["day"] == 3 and any(t["moved"] for t in arjun["tasks"])


# ---------------------------------------------------------------- stores: export, deletion, the first read
def test_the_memory_store_keeps_the_first_milestones_and_moves_placements_off_a_deleted_one():
    lc = MemoryLifecycleStore(MemoryStore())
    first = [Phase(onboarding_id="o", key="a", title="A", due_day=15, position=0), Phase(onboarding_id="o", key="b", title="B", due_day=30, position=1)]
    assert [p.key for p in lc.ensure_phases("o", first)] == ["a", "b"]
    assert [p.key for p in lc.ensure_phases("o", [Phase(onboarding_id="o", key="z", title="Z", due_day=9)])] == ["a", "b"]
    assert lc.add_phase(Phase(onboarding_id="o", key="a", title="Again", due_day=40)) is None
    lc.save_placements("o", [TaskPlacement(task_id="t1", onboarding_id="o", phase_key="a", position=3)])
    lc.save_placements("o", [TaskPlacement(task_id="t1", onboarding_id="o", phase_key="a", position=0)])  # one per task
    assert [(p.phase_key, p.position) for p in lc.placements("o")] == [("a", 0)]
    assert lc.delete_phase("o", "a", "b") and not lc.delete_phase("o", "a", "b")
    assert [(p.phase_key, p.position) for p in lc.placements("o")] == [("b", None)]
    lc.add_task_move(TaskMove(onboarding_id="o", task_id="t1", from_phase="a", to_phase="b", from_title="A", to_title="B"))
    assert [m.to_phase for m in lc.task_moves("o")] == ["b"] and lc.task_moves("o")[0].id


def test_export_and_deletion_cover_the_board(client):
    c = _c(client)
    _move(client, "Pair with Ravi on the label rules", who=MANAGER, milestone="own_an_area")
    data = c.lifecycle.export_org("org-demo")
    assert data["onboarding_phases"] and data["task_placements"] and data["task_moves"]
    assert {p["onboarding_id"] for p in data["onboarding_phases"]} >= {"ob-vijay", "ob-sara"}
    counts = c.lifecycle.delete_org("org-demo")
    assert counts["onboarding_phases"] >= 20 and counts["task_moves"] >= 4
    assert not c.lifecycle.phase_rows and not c.lifecycle.placement_rows and not c.lifecycle.move_rows


def test_target_status_rules():
    t = TaskRow(id="t", onboarding_id="o", title="t", description="", kind="learn", status="approved")
    assert B.target_status(t, "todo", False, False) is None
    assert B.target_status(t, "in_progress", False, False) == "in_progress"
    assert B.target_status(t, "done", False, False) == "submitted"
    assert B.target_status(t, "done", True, False) == "done"
    sub = t.model_copy(update={"status": "submitted", "pr_number": 3})
    with pytest.raises(B.BoardError) as e:
        B.target_status(sub, "in_progress", False, False)
    assert e.value.code == "in_review"
    assert B.allowed_columns(sub, False, False) == ["done"] and B.allowed_columns(sub, True, False) == ["todo", "in_progress", "done"]


# ---------------------------------------------------------------- the hire never manages their own board (security review)
def _hire_is_a_manager_by_role(client):
    """Vijay is onboarded as an engineering manager: his own role is 'manager', and he is the hire of ob-vijay."""
    inner = _c(client).store.__dict__.get("_store", _c(client).store)
    inner.users["u-vijay"] = inner.users["u-vijay"].model_copy(update={"role": "manager"})


def test_a_manager_role_hire_cannot_add_remove_or_redate_their_own_tasks(client):
    _hire_is_a_manager_by_role(client)
    b = client.get(URL, headers=HIRE).json()
    assert b["can"]["edit"] is False  # the view and the routes agree
    todo = next(t for t in b["tasks"] if t["status"] in {"approved", "in_progress"})
    assert client.delete(f"{URL}/tasks/{todo['id']}", headers=HIRE).status_code == 403
    late = next(t for t in b["tasks"] if t["overdue"])
    assert client.patch(f"{URL}/tasks/{late['id']}", json={"due_date": "2030-01-01"}, headers=HIRE).status_code == 403
    assert client.post(f"{URL}/tasks", json={"title": "Self-assigned"}, headers=HIRE).status_code == 403
    assert _c(client).store.get_task(todo["id"]).status == todo["status"] and not _audits(client, "board.task.removed")


def test_a_manager_role_hire_cannot_change_their_own_milestones_or_checkpoints(client):
    _hire_is_a_manager_by_role(client)
    assert client.patch(f"{URL}/milestones/settle_in", json={"due_day": 200}, headers=HIRE).status_code == 403
    assert client.post(f"{URL}/milestones", json={"title": "Mine", "due_day": 70}, headers=HIRE).status_code == 403
    assert client.delete(f"{URL}/milestones/settle_in", headers=HIRE).status_code == 403
    keys = [m["key"] for m in client.get(URL, headers=HIRE).json()["milestones"]]
    assert client.put(f"{URL}/milestones/order", json={"keys": keys[::-1]}, headers=HIRE).status_code == 403
    # the older checkpoint route goes through the same relation (app/api/lifecycle.py _hire)
    key = _c(client).lifecycle.milestones("ob-vijay")[0].key
    assert client.patch(f"/api/onboardings/ob-vijay/milestones/{key}", json={"status": "done"}, headers=HIRE).status_code == 403
    assert not [a for a in _c(client).store.audit_rows if a["actor_id"] == "u-vijay" and a["action"].startswith(("board.", "milestone."))]
    # he still reads and works his own board as its hire
    assert client.get(URL, headers=HIRE).status_code == 200


def test_board_entries_name_the_task_its_board_and_project(client):
    title = "Pair with Ravi on the label rules"
    _move(client, title, who=MANAGER, milestone="own_an_area")
    [entry] = _audits(client, "board.task.rescheduled")[-1:]
    d = entry["detail"]
    assert d["title"] == title and d["onboarding_id"] == "ob-vijay" and d["project_id"] == PID
    assert d["to_day"] == 60 and d["to_title"] and d["from_title"] and d["from_day"]
