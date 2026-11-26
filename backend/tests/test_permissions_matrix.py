"""Every route of the app against docs/PERMISSIONS.md.

The test lists every route the running app serves (routers included), fails if docs/PERMISSIONS.md has no row for one,
then calls each route as each demo user: `no` must be refused (401, 403 or 404); `yes` and `own` must not be (404 counts
as allowed only on rows marked 404-ok, and on `own` rows called on someone else's data); `public` must work without a
sign-in; `n/a` (webhooks, schedulers, Firebase sign-up) must refuse a demo user. Requests carry valid bodies, so a check
made inside the route (after validation) is reached, and use the demo's real ids: Vijay's onboarding, his mentor Ravi.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

MATRIX = Path(__file__).resolve().parents[2] / "docs" / "PERMISSIONS.md"
ROLES = {"admin": "u-admin", "manager": "u-priya", "expert": "u-ravi", "new_hire": "u-vijay", "executive": "u-exec"}
PID = "proj-demo"
REFUSED = {401, 403, 404}


def walk(routes, prefix=""):
    """(method, path) of every APIRoute, through included routers."""
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            yield from walk(r.original_router.routes, prefix + (r.include_context.prefix or ""))
        elif isinstance(r, APIRoute):
            for m in r.methods - {"HEAD"}:
                yield m, prefix + r.path


def matrix() -> dict[tuple[str, str], dict]:
    rows = {}
    for line in MATRIX.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 8 and cells[0] in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
            method, path, *roles, notes = cells
            rows[(method, path)] = {"roles": dict(zip(ROLES, roles)), "notes": notes}
    return rows


@pytest.fixture
def client(monkeypatch):
    """A fresh demo per test: the matrix test ends with everyone leaving the organisation."""
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")  # the question limit is per process, and these tests ask many
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _ids(c) -> dict:
    """Real ids of the demo's data for every path parameter, and a few things to act on."""
    lc, store = c.app.state.container.lifecycle, c.app.state.container.store
    h = {"X-Demo-User": "u-vijay"}
    routed = c.post("/api/ask/route", json={"project_id": PID, "question": "How is the mutate module benchmarked?", "ask_person": True},
                    headers=h).json()
    run = c.post("/api/ask/route", json={"project_id": PID, "question": "Where does check_labels report missing labels?",
                                         "run_id": "6f1c2b9a-0d4e-4c55-9a7e-3b8f2d1c0e01"}, headers=h).json()["run_id"]
    code_task = next(t for t in store.tasks(onboarding_id="ob-vijay") if t.kind == "code")
    plan = next(a for a in store.approvals(PID) if a.kind == "task_plan")
    return {
        "project_id": PID, "onboarding_id": "ob-vijay", "task_id": code_task.id, "approval_id": plan.id, "meeting_id": "mtg-1",
        "message_id": routed["message_id"], "request_id": routed["request_id"], "review_id": "no-review", "document_id": "no-document",
        "invitation_id": "no-invitation", "team_id": lc.teams("org-demo")[0].id, "template_id": lc.templates("org-demo")[0].id,
        "key": "meet_team", "item_id": next(a.id for a in lc.access_items("ob-vijay") if a.status == "requested"), "area": "engine",
        "blocker_id": lc.blockers("ob-vijay", open_only=True)[0].id,
        "feedback_id": next(f.id for f in lc.feedback(PID) if f.owner_id == "u-ravi"),
        "handover_id": lc.handovers("org-demo")[0].id, "token": "demo-welcome-arjun",
        "user_id": "u-noah",  # a member from the previous cohort: deactivating or reactivating them changes no later call
        "agent_key": "coach",
        "run_id": run,  # Vijay's own Guide run: he and the project's managers read it, his mentor does not
    }


DIFF = "diff --git a/engine/validate.py b/engine/validate.py\n@@ -1,1 +1,2 @@\n x = 1\n+y = 2\n"
BODIES = {  # valid bodies, chosen so an allowed call changes nothing that a later call needs
    ("POST", "/api/voice/sessions"): {"project_id": PID},
    ("POST", "/api/voice/actions/{action_id}/approve"): {"confirm": True},
    ("POST", "/api/voice/sessions/{session_id}/transcript"): {"speaker": "employee", "text": "Matrix turn"},
    ("POST", "/api/projects"): {"name": "Matrix"},
    ("POST", "/api/projects/{project_id}/invitations"): {"email": "matrix@example.com", "role": "new_hire"},
    ("POST", "/api/github/callback"): {"code": "x", "installation_id": "1", "state": "bad"},
    ("POST", "/api/projects/{project_id}/repositories"): {"full_name": "demo/mini-policy-engine"},
    ("POST", "/api/projects/{project_id}/onboardings"): {"new_hire_id": "u-vijay", "start_date": "2026-01-05"},
    ("POST", "/api/chat"): {"project_id": PID, "question": "Where does check_labels report missing labels?"},
    ("POST", "/api/chat/{message_id}/feedback"): {"value": 1},
    ("POST", "/api/meetings"): {"project_id": PID, "title": "Matrix check"},
    ("POST", "/api/meetings/{meeting_id}/schedule"): {"starts_at": "2030-01-07T10:00:00Z", "duration_min": 30},
    ("POST", "/api/meetings/{meeting_id}/recording/upload"): {"filename": "matrix.mp4", "size": 8, "duration_s": 1},
    ("POST", "/api/meetings/{meeting_id}/recording/complete"): {"upload_id": "invalid-upload-id"},
    ("POST", "/api/meetings/{meeting_id}/recording/consent"): {"confirm": True},  # KT #1 has its consent already: nothing changes
    ("POST", "/api/meetings/{meeting_id}/recording/removal-requests"): {"reason": "Matrix check"},  # a request removes nothing
    ("POST", "/api/meetings/{meeting_id}/recording/removal-requests/{request_id}/decline"): {"note": ""},
    ("PATCH", "/api/tasks/{task_id}"): {"status": "in_progress"},
    ("POST", "/api/approvals/{approval_id}/decide"): {"approve": ["not-a-task"]},
    ("POST", "/api/experts/requests/{request_id}/answer"): {"answer": "With pytest-benchmark."},
    ("POST", "/api/notifications/read"): {"ids": []},
    ("POST", "/api/legal/accept"): {"documents": ["terms", "privacy", "recording"]},  # accepted in the seed already: nothing changes
    ("POST", "/api/org/legal/dpa"): {},  # accepted in the seed already: the first acceptance stands
    ("POST", "/api/kt-plan/schedule"): {"project_id": PID, "key": "000000000000"},
    ("PUT", "/api/integrations/calendly/link"): {"url": "https://calendly.com/matrix/kt"},
    ("POST", "/api/demo/login"): {"email": "priya@demo.dev", "password": "welcome-wave"},
    ("POST", "/api/client-errors"): {"id": "k2f9q", "kind": "error", "name": "TypeError", "message": "x is undefined", "page": "/app"},
    ("POST", "/api/demo/invitations/accept"): {"project_id": PID, "invitation_id": "none"},
    ("POST", "/api/demo/pull-request"): {"task_id": "{task_id}"},
    ("POST", "/api/demo/advance"): {"days": 1},
    ("PATCH", "/api/setup"): {"minutes_per_interruption": 15},
    ("POST", "/api/teams"): {"project_id": PID, "name": "Matrix team"},
    ("PATCH", "/api/teams/{team_id}"): {"name": "Policy Engine"},
    ("POST", "/api/programs/templates"): {"key": "backend-mid", "title": "Duplicate", "role_track": "Backend", "level": "Mid"},
    ("PATCH", "/api/programs/templates/{template_id}"): {"title": "Renamed"},
    ("POST", "/api/onboardings/enrol"): {"project_id": PID, "email": "vijay@demo.dev", "name": "Vijay", "start_date": "2026-01-05",
                                         "template_id": "{template_id}"},
    ("PATCH", "/api/onboardings/{onboarding_id}/milestones/{key}"): {"status": "done"},
    # the milestone board: {task_id} is a task waiting in Approvals, so moves and edits of it answer 409 and change nothing
    ("POST", "/api/onboardings/{onboarding_id}/board/tasks/{task_id}/move"): {"column": "in_progress"},
    ("POST", "/api/onboardings/{onboarding_id}/board/tasks"): {"title": "Matrix task"},
    ("PATCH", "/api/onboardings/{onboarding_id}/board/tasks/{task_id}"): {"title": "Matrix"},
    ("POST", "/api/onboardings/{onboarding_id}/board/milestones"): {"title": "Matrix milestone", "due_day": 70},
    ("PUT", "/api/onboardings/{onboarding_id}/board/milestones/order"): {"keys": ["settle_in"]},
    ("PATCH", "/api/onboardings/{onboarding_id}/board/milestones/{milestone_key}"): {"title": "Renamed"},
    ("POST", "/api/onboardings/{onboarding_id}/access"): {"system": "Matrix dashboard"},
    ("PATCH", "/api/onboardings/{onboarding_id}/access/{item_id}"): {"status": "requested"},
    ("POST", "/api/preboarding/{token}/acknowledge"): {"doc_key": "security_policy"},
    ("POST", "/api/preboarding/{token}/env-check"): {"results": [{"name": "git", "ok": True, "detail": "git version 2"}]},
    ("POST", "/api/onboardings/{onboarding_id}/first-task"): {"candidate_id": "issue:999999"},
    ("POST", "/api/tasks/{task_id}/presubmit-review"): {"diff": DIFF},
    ("POST", "/api/ask/route"): {"project_id": PID, "question": "Where does check_labels report missing labels?"},
    ("POST", "/api/ask/free"): {"project_id": PID, "question": "Where does check_labels report missing labels?"},
    ("POST", "/api/verified-answers"): {"project_id": PID, "question": "How do I run the tests?", "answer": "pytest -q"},
    ("PATCH", "/api/experts/preferences"): {"digest": True},
    ("PATCH", "/api/onboardings/{onboarding_id}/areas/{area}"): {"self_level": "exploring"},
    ("POST", "/api/blockers/{blocker_id}/resolve"): {"note": "Docker restarted"},
    ("POST", "/api/feedback"): {"project_id": PID, "target_kind": "code", "target_ref": "engine/validate.py", "kind": "unclear"},
    ("PATCH", "/api/feedback/{feedback_id}"): {"status": "open"},
    ("POST", "/api/onboardings/{onboarding_id}/checkins"): {"day": 30, "answers": {}},
    ("POST", "/api/surveys"): {"onboarding_id": "ob-vijay", "day": 30, "answers": {"clarity": 4}},
    ("POST", "/api/onboardings/{onboarding_id}/completion"): {"decision": "extend", "extend_days": 0},
    ("POST", "/api/handovers"): {"project_id": PID, "user_id": "u-meera", "last_day": "2030-01-31"},
    ("PATCH", "/api/handovers/{handover_id}"): {"status": "in_progress"},
    ("DELETE", "/api/org"): {"confirm": "not the name"},
    ("PATCH", "/api/org/members/{user_id}"): {"role": "new_hire"},  # Noah's role already: nothing changes
    ("PATCH", "/api/agents/{agent_key}"): {"mode": "ai"},  # the Coach's mode already: nothing changes
}
QUERIES = {"/api/github/install-url": {"project_id": PID}, "/api/github/repositories": {"installation_id": "1"},
           "/api/chat/history": {"project_id": PID}, "/api/meetings": {"project_id": PID},
           "/api/code/files": {"project_id": PID}, "/api/code/explanations": {"project_id": PID, "path": "engine/validate.py"},
           "/api/code/file": {"project_id": PID, "path": "engine/validate.py"}, "/api/coverage": {"project_id": PID},
           "/api/tasks": {"project_id": PID}, "/api/approvals": {"project_id": PID}, "/api/dashboard": {"project_id": PID},
           "/api/runs": {"project_id": PID}, "/api/knowledge-risk": {"project_id": PID}, "/api/kt-plan": {"project_id": PID},
           "/api/tour": {"project_id": PID}, "/api/scheduling/links": {"project_id": PID}, "/api/teams": {"project_id": PID},
           "/api/verified-answers": {"project_id": PID}, "/api/feedback": {"project_id": PID},
           "/api/agents": {"project_id": PID}, "/api/agents/runs": {"project_id": PID}, "/api/agents/tick": {"project_id": PID},
           "/api/board/hires": {"project_id": PID}, "/api/recording-removal-requests": {"project_id": PID}}
QUERIES["/api/voice/sessions"] = {"project_id": PID}


def _fill(value, ids):
    if isinstance(value, str):
        return re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    return value


def row_key(method: str, path: str) -> tuple[str, str]:
    return method, path.replace("{area:path}", "{area}")


def call(c, method: str, path: str, ids: dict, user: str | None):
    url = re.sub(r"\{(\w+)(?::path)?\}", lambda m: ids.get(m.group(1), "x"), path)
    body = _fill(BODIES.get(row_key(method, path)), ids)
    headers = {"X-Demo-User": user} if user else {}
    return c.request(method, url, params=QUERIES.get(path), json=body if method != "GET" else None, headers=headers)


def test_every_route_has_a_matrix_row(client):
    served = {row_key(m, p) for m, p in walk(client.app.routes)}
    rows = matrix()
    assert not served - set(rows), f"routes without a row in docs/PERMISSIONS.md: {sorted(served - set(rows))}"
    assert not set(rows) - served, f"rows for routes that do not exist: {sorted(set(rows) - served)}"


def test_every_role_gets_what_the_matrix_says(client):
    ids = _ids(client)
    rows = matrix()
    def order(r):  # 'last' rows at the end, 'final' ones (the demo reset) after them
        notes = rows[row_key(*r)]["notes"]
        return ("last" in notes) + 2 * ("final" in notes)

    served = sorted(set(walk(client.app.routes)), key=order)
    wrong = []
    for method, path in served:
        row = rows[row_key(method, path)]
        notes = row["notes"]
        for role, user in ROLES.items():
            cell = row["roles"][role]
            if cell in {"yes", "own"} and "stream" in notes:
                continue
            r = call(client, method, path, ids, None if cell == "public" else user)
            ok_404 = "404-ok" in notes or (cell == "own" and role != "new_hire" and ("{message_id}" in path or "{run_id}" in path))
            if "demo-off" in notes:  # off in the demo: refused, or stopped at validation, never served
                good = r.status_code in REFUSED | {422}
            elif cell == "no":
                good = r.status_code in REFUSED
            elif cell == "n/a":
                good = r.status_code >= 400
            elif cell == "public":
                good = r.status_code not in {401, 403}
            else:
                good = r.status_code not in {401, 403} and (r.status_code != 404 or ok_404)
            if not good:
                wrong.append(f"{method} {path} as {role} ({cell}): {r.status_code} {r.text[:120]}")
    assert not wrong, "\n".join(wrong)


def test_nobody_signed_out_gets_past_a_signed_in_route(client):
    ids = _ids(client)
    rows = matrix()
    for method, path in sorted(set(walk(client.app.routes))):
        row = rows[row_key(method, path)]
        if set(row["roles"].values()) & {"public", "n/a"}:
            continue
        r = call(client, method, path, ids, None)
        assert r.status_code == 401, f"{method} {path} without a sign-in: {r.status_code}"


# ---------------------------------------------------------------- "own" is ownership, not just a role
# The matrix test above calls an `own` row as the role's demo user, who IS the related person (Vijay, his mentor Ravi). That
# proves the role, not the ownership. Here every `own` row is called as someone of the same role who is NOT related to the data
# (another hire, an expert who is not the mentor): it must be refused as if the data did not exist.
UNRELATED = {"new_hire": "u-arjun", "expert": "u-meera"}


def _with_a_real_review(c, ids: dict) -> dict:
    """Approve Vijay's plan and open his pull request, so GET /api/reviews/{id} has a real review to refuse."""
    h = {"X-Demo-User": "u-priya"}
    plan = next(a for a in c.get(f"/api/approvals?project_id={PID}", headers=h).json() if a["kind"] == "task_plan")
    c.post(f"/api/approvals/{plan['id']}/decide", json={"approve": [t["id"] for t in plan["payload"]["plans"][0]["tasks"]]}, headers=h)
    code = next(t for t in c.get(f"/api/tasks?project_id={PID}", headers={"X-Demo-User": "u-vijay"}).json() if t["kind"] == "code")
    review = c.post("/api/demo/pull-request", json={"task_id": code["id"]}, headers={"X-Demo-User": "u-vijay"}).json()["review_id"]
    assert review and c.get(f"/api/reviews/{review}", headers={"X-Demo-User": "u-ravi"}).status_code == 200  # his mentor reads it
    return {**ids, "review_id": review, "task_id": code["id"]}


def test_every_own_row_refuses_an_unrelated_user_of_the_same_role(client):
    ids = _with_a_real_review(client, _ids(client))
    rows = matrix()
    tried, wrong = [], []
    for (method, path), row in sorted(rows.items()):
        notes = row["notes"]
        for role, who in UNRELATED.items():
            if row["roles"][role] != "own" or "stream" in notes:
                continue
            r = call(client, method, path, ids, who)
            tried.append((method, path, role))
            if r.status_code not in REFUSED:
                wrong.append(f"{method} {path} as {who} (an unrelated {role}): {r.status_code} {r.text[:120]}")
    assert not wrong, "\n".join(wrong)
    # not vacuous: the routes that read or change one hire's data are among them, for both roles
    assert {("GET", "/api/reviews/{review_id}", "expert"), ("GET", "/api/reviews/{review_id}", "new_hire"),
            ("GET", "/api/onboardings/{onboarding_id}/program", "expert"), ("PATCH", "/api/tasks/{task_id}", "new_hire"),
            ("PATCH", "/api/feedback/{feedback_id}", "expert"), ("POST", "/api/blockers/{blocker_id}/resolve", "new_hire")} <= set(tried)
    assert len(tried) >= 20


def test_an_unrelated_hire_and_expert_get_nothing_of_vijays_data_from_the_list_routes(client):
    ids = _with_a_real_review(client, _ids(client))
    for who in UNRELATED.values():
        h = {"X-Demo-User": who}
        seen = client.get(f"/api/tasks?project_id={PID}", headers=h).json()
        assert not any(t["id"] == ids["task_id"] for t in seen), who
        assert client.get(f"/api/reviews/{ids['review_id']}", headers=h).status_code == 404


def test_a_manager_of_another_project_reads_nothing_of_a_hire_in_this_one(client):
    """The role passes `require("manager")`; being a manager OF THIS PROJECT is the ownership."""
    from app.ports import Project, User

    store = client.app.state.container.store
    store.add_project(Project(id="proj-two", org_id="org-demo", name="Another project", description=""))
    store.add_user(User(id="u-mgr2", org_id="org-demo", email="pat@demo.dev", name="Pat (Manager)", role="manager"), "proj-two")
    ids, rows, wrong = _ids(client), matrix(), []
    for (method, path), row in sorted(rows.items()):
        if "{onboarding_id}" not in path or row["roles"]["manager"] != "yes" or "stream" in row["notes"]:
            continue
        r = call(client, method, path, ids, "u-mgr2")
        if r.status_code not in REFUSED:
            wrong.append(f"{method} {path}: {r.status_code} {r.text[:120]}")
    assert not wrong, "\n".join(wrong)
