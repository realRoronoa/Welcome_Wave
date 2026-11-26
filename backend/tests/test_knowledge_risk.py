"""Knowledge risk (app/services/knowledge_risk.py and GET /api/knowledge-risk).

Seed numbers come from the real demo seed (MODE=demo): 4 code files, 60 lines, 18 recorded, pace 250 s / 18 lines.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.core.schemas import KTLink, QAResult
from app.api import knowledge_risk as risk_api
from app.config import Settings, get_settings
from app.container import build_container
from app.main import create_app
from app.ports import Onboarding, Project, User
from app.services import knowledge as K
from app.services import knowledge_risk as KR
from app.services.knowledge_risk import band, build_knowledge_risk, demand_factor, holder_factor

PID, ORG = "proj-demo", "org-demo"


@pytest.fixture
def demo():
    """A fresh seeded demo container per test, so tests that write stay isolated."""
    return build_container(Settings(mode="demo", gemini_api_key=""))


def risk(c):
    return build_knowledge_risk(c.store, c.index, PID, ORG)


def by_path(r):
    return {f["path"]: f for f in r["files"]}


def by_user(r):
    return {p["user_id"]: p for p in r["people"]}


def _owner(login, user_id=None, team=False):
    return K.Owner(login, user_id, login, team, f"/x/ @{login}")


# ---------------------------------------------------------------- scoring rules
def test_holder_demand_and_band_rules():
    assert holder_factor([]) == 1.0  # nobody accountable is as risky as one person
    assert holder_factor([_owner("ravi-dev", "u-ravi")]) == 1.0
    assert holder_factor([_owner("org/platform", team=True)]) == 0.6
    assert holder_factor([_owner("a", "u-a"), _owner("b", "u-b")]) == 0.6
    assert holder_factor([_owner("a", "u-a"), _owner("b", "u-b"), _owner("c")]) == 0.4
    assert holder_factor([_owner("Ravi-Dev", "u-ravi"), _owner("ravi-dev", "u-ravi")]) == 1.0  # two handles, one person
    assert holder_factor([_owner("Ghost"), _owner("ghost")]) == 1.0  # unresolved handles compare case-insensitively
    assert [demand_factor(w) for w in (0, 2, 3, 4, 9)] == [0.5, 0.75, 0.875, 1.0, 1.0]
    assert [band(s) for s in (100, 70, 69, 40, 39, 15, 14, 0)] == ["critical", "critical", "high", "high", "medium", "medium", "low", "low"]


# ---------------------------------------------------------------- the seed
def test_risk_seed_summary_and_order(demo):
    r = risk(demo)
    assert r["indexed"] is True and r["has_codeowners"] is True
    assert (r["pace_s_per_line"], r["pace_is_default"]) == (13.89, False)
    s = r["summary"]
    assert (s["code_files"], s["code_lines"], s["owned_lines"], s["recorded_lines"], s["recorded_line_share"]) == (4, 60, 60, 18, 0.3)
    assert (s["single_owner_files"], s["unowned_files"], s["critical"], s["high"], s["medium"], s["low"]) == (4, 0, 2, 1, 0, 1)
    assert [(f["path"], f["risk_score"], f["risk"]) for f in r["files"]] == [
        ("webhooks/server.py", 88, "critical"), ("engine/policy.py", 75, "critical"), ("engine/validate.py", 48, "high"), ("engine/mutate.py", 0, "low")]
    assert s["highest_exposure"] == {"user_id": "u-meera", "name": "Meera (Senior)", "recorded_share": 0.0, "handover_minutes": 3.9}


def test_file_rows_explain_their_score(demo):
    files = by_path(risk(demo))
    server = files["webhooks/server.py"]
    assert server["owners"] == [{"login": "meera-dev", "user_id": "u-meera", "name": "Meera (Senior)", "team": False, "rule": "/webhooks/ @meera-dev"}]
    assert (server["lines_total"], server["lines_recorded"], server["recorded_share"], server["voices"]) == (17, 0, 0.0, [])
    assert [s["symbol"] for s in server["unrecorded_symbols"]] == ["handle_admission", "Handler", "do_POST"]
    assert [(d["kind"], d["weight"], d["pending_approval"]) for d in server["demand"]] == [("task", 2, True), ("gap", 1, False)]
    assert server["demand_weight"] == 3
    assert server["score_parts"] == {"unrecorded": 1.0, "holder_factor": 1.0, "demand_factor": 0.875}

    policy = files["engine/policy.py"]
    [action] = policy["demand"]
    assert (action["kind"], action["ref"], action["meeting"]) == ("action_item", {"meeting_id": "mtg-1", "at_s": 232.0}, "KT #1")
    assert action["text"] == "Action item from KT #1 at 3:52: Vijay to read engine/policy.py before Friday"

    validate = files["engine/validate.py"]
    assert (validate["lines_recorded"], validate["lines_total"], validate["recorded_share"]) == (12, 23, 0.52)
    assert [s["symbol"] for s in validate["unrecorded_symbols"]] == ["ValidationError", "check_privileged"]
    first_pr, named = validate["demand"]
    assert (first_pr["kind"], first_pr["symbol"], first_pr["weight"]) == ("first_pr", "check_privileged", 3)
    assert first_pr["text"].endswith("41% match") and isinstance(first_pr["due"], date)
    assert (named["kind"], named["symbol"]) == ("named_not_shown", "check_privileged")
    assert validate["voices"] == [{"speaker": "Ravi (Senior)", "user_id": "u-ravi", "seconds": 162.0}]
    states = {s["symbol"]: (s["state"], s["kt_seconds"]) for s in validate["symbols"]}
    assert states == {"ValidationError": ("none", 0.0), "validate_resource": ("recorded", 35.0), "check_labels": ("recorded", 127.0),
                      "check_privileged": ("none", 0.0)}
    clip = next(s["clip"] for s in validate["symbols"] if s["symbol"] == "check_labels")
    assert clip == {"meeting_id": "mtg-1", "meeting": "KT #1", "at_s": 55.0}  # the UI links the segment to this moment

    mutate = files["engine/mutate.py"]
    assert (mutate["recorded_share"], mutate["risk_score"], mutate["unrecorded_symbols"]) == (1.0, 0, [])
    assert mutate["voices"] == [{"speaker": "Ravi (Senior)", "user_id": "u-ravi", "seconds": 88.0}]


def test_people_exposure_seed(demo):
    r = risk(demo)
    assert [p["user_id"] for p in r["people"]] == ["u-meera", "u-ravi"]  # Priya owns only '*' (root, docs, tests): not listed
    meera, ravi = r["people"]
    assert (meera["files_owned"], meera["sole_owner_files"], meera["lines_owned"], meera["recorded_share"], meera["kt_minutes_spoken"]) == (1, 1, 17, 0.0, 0.0)
    assert meera["exposure"] == {"orphaned_files": ["webhooks/server.py"], "orphaned_count": 1, "unrecorded_lines": 17, "unrecorded_symbols": 3,
                                 "stale_lines": 0, "handover_minutes": 3.9, "risk_sum": 88}
    assert (meera["role"], meera["login"], meera["open_expert_requests"], meera["onboardings_managed"]) == ("expert", "meera-dev", 0, 0)
    assert (ravi["files_owned"], ravi["lines_owned"], ravi["lines_recorded"], ravi["recorded_share"], ravi["kt_minutes_spoken"]) == (3, 43, 18, 0.42, 3.4)
    assert ravi["exposure"]["orphaned_files"] == ["engine/policy.py"]  # validate.py is 52% recorded: not orphaned
    assert (ravi["exposure"]["unrecorded_lines"], ravi["exposure"]["handover_minutes"], ravi["exposure"]["risk_sum"]) == (25, 5.8, 75)


# ---------------------------------------------------------------- changes to the data
def test_stale_links_get_half_credit(demo):
    demo.store.mark_kt_code_changed(PID, ["engine/validate.py"])
    r = risk(demo)
    validate = by_path(r)["engine/validate.py"]
    assert (validate["lines_recorded"], validate["lines_stale"], validate["recorded_share"]) == (0, 12, 0.26)
    assert {s["symbol"]: s["state"] for s in validate["symbols"]}["check_labels"] == "stale"
    assert validate["demand_weight"] == 4 and "stale" not in {d["kind"] for d in validate["demand"]}  # staleness is charged once
    assert (validate["risk_score"], validate["risk"]) == (74, "critical")
    assert (r["summary"]["recorded_lines"], r["summary"]["stale_lines"], r["summary"]["recorded_line_share"]) == (6, 12, 0.2)
    ravi = by_user(r)["u-ravi"]
    assert ravi["exposure"]["orphaned_files"] == ["engine/policy.py", "engine/validate.py"]
    assert ravi["exposure"]["stale_lines"] == 12 and ravi["exposure"]["unrecorded_lines"] == 25  # stale lines are not "unrecorded"


def test_no_codeowners_marks_unowned(demo):
    demo.store.set_codeowners(PID, "")
    r = risk(demo)
    s = r["summary"]
    assert r["has_codeowners"] is False and r["people"] == [] and s["highest_exposure"] is None
    assert (s["unowned_files"], s["single_owner_files"], s["owned_lines"], s["recorded_line_share"]) == (4, 0, 0, 0.3)
    assert all(f["owners"] == [] and f["score_parts"]["holder_factor"] == 1.0 for f in r["files"])
    assert [f["risk_score"] for f in r["files"]] == [88, 75, 48, 0]  # unowned is as risky as one owner


def test_team_handle_counts_as_shared(demo):
    demo.store.set_codeowners(PID, "/engine/ @org/platform\n/webhooks/ @meera-dev\n")
    r = risk(demo)
    files = by_path(r)
    validate = files["engine/validate.py"]
    assert [(o["login"], o["user_id"], o["team"]) for o in validate["owners"]] == [("org/platform", None, True)]
    assert validate["score_parts"]["holder_factor"] == 0.6 and validate["risk_score"] == 29  # 47.8 * 0.6
    assert files["engine/policy.py"]["risk_score"] == 45
    assert [p["user_id"] for p in r["people"]] == ["u-meera"]  # a team is never one person
    assert r["summary"]["single_owner_files"] == 1


def test_duplicate_handles_and_unresolved_logins(demo):
    demo.store.set_codeowners(PID, "/engine/ @ravi-dev @Ravi-Dev\n/webhooks/ @ghost\n")
    r = risk(demo)
    files = by_path(r)
    assert files["engine/policy.py"]["score_parts"]["holder_factor"] == 1.0  # one person under two spellings
    ravi = by_user(r)["u-ravi"]
    assert (ravi["files_owned"], ravi["sole_owner_files"], ravi["exposure"]["orphaned_files"]) == (3, 3, ["engine/policy.py"])
    server = files["webhooks/server.py"]
    assert server["owners"][0]["user_id"] is None and server["owners"][0]["name"] == "@ghost"
    assert list(by_user(r)) == ["u-ravi"]  # a login nobody in the organisation has stays on the file row
    assert r["summary"]["single_owner_files"] == 4


def test_owner_outside_the_project_is_still_a_holder(demo):
    demo.store.users["u-ana"] = User(id="u-ana", org_id=ORG, email="ana@demo.dev", name="Ana Lopez", role="expert", github_login="ana-dev")
    demo.store.set_codeowners(PID, "/engine/ @ravi-dev\n/webhooks/ @ana-dev\n")
    r = risk(demo)
    ana = by_user(r)["u-ana"]  # CODEOWNERS is resolved across the organisation, not just the project's members
    assert (ana["name"], ana["files_owned"], ana["exposure"]["orphaned_files"]) == ("Ana Lopez", 1, ["webhooks/server.py"])
    assert r["summary"]["highest_exposure"]["user_id"] == "u-ana"


def test_clip_shared_by_two_symbols_counts_its_seconds_once(demo):
    store = demo.store
    labels = next(l for l in store.kt_links(PID) if l.symbol == "check_labels")
    twin = KTLink(**{**labels.model_dump(), "symbol": "ValidationError", "start_line": 5, "end_line": 6})
    store.add_kt_links(PID, [*store.kt_links(PID), twin])
    validate = by_path(risk(demo))["engine/validate.py"]
    assert validate["voices"] == [{"speaker": "Ravi (Senior)", "user_id": "u-ravi", "seconds": 162.0}]  # not 289
    assert {s["symbol"]: s["state"] for s in validate["symbols"]}["ValidationError"] == "recorded"


def test_everything_recorded_has_no_highest_exposure(demo):
    store = demo.store
    files = K.code_files(demo.index, PID)
    store.add_kt_links(PID, [KTLink(meeting_id="mtg-1", meeting_title="KT #1", file_path=p, start_s=0, end_s=60, confidence=0.9, method="path")
                             for p in files])
    r = risk(demo)
    assert {f["risk_score"] for f in r["files"]} == {0} and r["summary"]["recorded_line_share"] == 1.0
    assert r["summary"]["highest_exposure"] is None  # no one's departure orphans anything
    assert all(p["exposure"]["orphaned_files"] == [] and p["exposure"]["handover_minutes"] == 0 for p in r["people"])


def test_question_raises_risk_live(demo):
    q = "How does the admission webhook handle a request?"
    out = demo.handle({"type": "chat.question", "project_id": PID, "user_id": "u-vijay", "question": q})
    assert out["result"]["status"] == "answered" and out["result"]["kt_clips"] == []
    server = by_path(risk(demo))["webhooks/server.py"]
    [question] = [d for d in server["demand"] if d["kind"] == "question"]
    assert (question["symbol"], question["weight"], question["ref"]) == ("handle_admission", 1, {"message_id": out["result"]["message_id"]})
    assert question["answered"] is True
    assert question["text"] == KR.QUESTION_ANSWERED_TEXT  # a new hire's chat is theirs: never the words or who asked
    assert "Vijay" not in question["text"] and "admission webhook" not in question["text"]
    assert (server["demand_weight"], server["risk_score"]) == (4, 100)


def test_unanchored_question_goes_to_the_expert_not_a_file(demo):
    out = demo.handle({"type": "chat.question", "project_id": PID, "user_id": "u-vijay", "question": "How do I deploy to production?"})
    assert out["result"]["status"] == "not_enough_information"
    r = risk(demo)
    assert not [d for f in r["files"] for d in f["demand"] if d["kind"] == "question"]  # top code hit is below 0.2 similarity
    assert [f["risk_score"] for f in r["files"]] == [88, 75, 48, 0]
    assert by_user(r)["u-meera"]["open_expert_requests"] == 1
    demo.store.create_expert_request("proj-other", "Unrelated", "u-meera", "u-vijay", "m-x")  # another project's request
    assert by_user(risk(demo))["u-meera"]["open_expert_requests"] == 1


def test_not_indexed_returns_empty_lists(world):
    r = build_knowledge_risk(world.store, world.index, "p1", "o1")
    assert r["indexed"] is False and r["files"] == [] and r["people"] == []
    assert r["repository"] == {"full_name": "demo/mini-policy-engine", "status": "pending"}
    assert r["summary"]["code_files"] == 0 and r["summary"]["highest_exposure"] is None
    assert r["pace_is_default"] is True and r["pace_s_per_line"] == 12.0


def test_reads_each_table_once_whatever_the_team_size_and_writes_nothing(demo):
    """Every Store read is one call per page, however many hires, meetings and owners: the hires' chats, the ready
    meetings' notes and the open expert requests are one batched read each (Reads), never one per person or meeting."""
    store, calls = demo.store, []
    for n in range(5):  # more hires: still one chat read
        store.add_user(User(id=f"u-h{n}", org_id=ORG, email=f"h{n}@demo.dev", name=f"Hire {n}", role="new_hire"), PID)
        store.add_onboarding(Onboarding(id=f"ob-h{n}", project_id=PID, new_hire_id=f"u-h{n}", manager_id="u-priya", start_date=date.today()))
    names = ("meeting_notes", "meeting_notes_many", "users_by_github", "list_meetings", "codeowners", "get_user", "expert_requests_for",
             "open_expert_requests", "chat_histories", "kt_links", "members", "tasks", "repos_for", "managers")  # (MemoryStore.tasks reads onboardings itself)
    for name in names:
        real = getattr(store, name)
        setattr(store, name, lambda *a, _n=name, _r=real, **kw: calls.append(_n) or _r(*a, **kw))
    before = (len(store.runs), len(store.notifications), len(store.expert_requests), [l.model_dump() for l in store.kt_links(PID)])
    calls.clear()
    first = risk(demo)
    assert {n: calls.count(n) for n in names} == {**{n: 1 for n in names}, "meeting_notes": 0, "get_user": 0, "expert_requests_for": 0}
    assert (len(store.runs), len(store.notifications), len(store.expert_requests), [l.model_dump() for l in store.kt_links(PID)]) == before
    assert risk(demo) == first  # deterministic


def _ask(store, user_id, question, at, status="not_enough_information"):
    """A chat row as the QA agent saves it, at a fixed time so ordering is exact."""
    mid = store.save_chat(PID, user_id, question, QAResult(status=status, answer=""))
    store.chats[mid]["created_at"] = at
    return mid


def test_search_outage_degrades_to_partial(demo):
    calls = []

    def down(*a, **kw):
        calls.append(a)
        raise TimeoutError("Vertex embeddings unavailable")

    _ask(demo.store, "u-vijay", "How does the admission webhook handle a request?", datetime.now(timezone.utc))
    demo.index.search = down
    r = risk(demo)
    assert r["partial"] is True and len(calls) == 1  # the first failure turns the remaining searches off
    files = by_path(r)
    validate = files["engine/validate.py"]
    assert [d["kind"] for d in validate["demand"]] == ["named_not_shown"]  # the first-PR anchor needed a search
    assert [d["kind"] for d in files["webhooks/server.py"]["demand"]] == ["task", "gap"]  # no unanswered-question anchor either
    assert [d["kind"] for d in files["engine/policy.py"]["demand"]] == ["action_item"]  # every other signal still counts
    assert [f["path"] for f in r["files"]] == ["webhooks/server.py", "engine/policy.py", "engine/validate.py", "engine/mutate.py"]


def test_search_time_budget(demo, monkeypatch):
    calls = []
    real = demo.index.search
    demo.index.search = lambda *a, **kw: calls.append(a) or real(*a, **kw)
    monkeypatch.setattr(K, "SEARCH_BUDGET_S", 0.0)
    r = risk(demo)
    assert r["partial"] is True and calls == []  # out of time before the first search: none made
    assert "first_pr" not in {d["kind"] for d in by_path(r)["engine/validate.py"]["demand"]}
    monkeypatch.setattr(K, "SEARCH_BUDGET_S", 4.0)
    assert risk(demo)["partial"] is False and len(calls) == 1
    monkeypatch.setattr(K, "SEARCH_BUDGET_S", 0.0)
    r = risk(demo)  # out of time again, but the first-PR search is remembered: nothing is missing
    assert r["partial"] is False and len(calls) == 1
    assert "first_pr" in {d["kind"] for d in by_path(r)["engine/validate.py"]["demand"]}


def test_searches_are_remembered_until_the_code_changes(demo):
    from app.services.chunking import chunk_code

    calls = []
    real = demo.index.search
    demo.index.search = lambda *a, **kw: calls.append(a) or real(*a, **kw)
    _ask(demo.store, "u-vijay", "How does the admission webhook handle a request?", datetime.now(timezone.utc))
    first = risk(demo)
    assert len(calls) == 2  # the first-PR anchor and the unanswered question
    assert risk(demo) == first and len(calls) == 2  # a reload answers both from memory: no embedding call
    demo.index.upsert(chunk_code(PID, "engine/extra.py", "def extra():\n    return 1\n"))  # a push changed the code
    risk(demo)
    assert len(calls) == 4


def test_question_cap_is_shared_across_hires(demo):
    """50 older unanswered questions from Vijay must not crowd out a newer one from a second hire, whatever order the
    store returns the onboardings in."""
    store = demo.store
    store.add_user(User(id="u-nina", org_id=ORG, email="nina@demo.dev", name="Nina Rao", role="new_hire", github_login="nina"), PID)
    store.add_onboarding(Onboarding(id="ob-zz-nina", project_id=PID, new_hire_id="u-nina", manager_id="u-priya", start_date=date.today()))
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for i in range(K.MAX_QUESTION_SEARCHES):
        _ask(store, "u-vijay", f"How do I deploy to production? ({i})", t0 + timedelta(minutes=i))
    nina = _ask(store, "u-nina", "How does the admission webhook handle a request?", t0 + timedelta(days=1))
    calls = []
    real = demo.index.search
    demo.index.search = lambda *a, **kw: calls.append(a) or real(*a, **kw)
    real_obs = store.onboardings
    for order in (lambda obs: obs, lambda obs: list(reversed(obs))):
        store.onboardings = lambda pid, active_only=True, _o=order: _o(real_obs(pid, active_only))
        calls.clear()
        K.clear_search_memo()  # count every search of both orders, not the remembered ones
        server = by_path(risk(demo))["webhooks/server.py"]
        [q] = [d for d in server["demand"] if d["kind"] == "question"]
        assert (q["symbol"], q["answered"], q["ref"]) == ("handle_admission", False, {"message_id": nina})
        assert q["text"] == KR.QUESTION_UNANSWERED_TEXT
        assert len(calls) == 1 + K.MAX_QUESTION_SEARCHES  # the first-PR anchor, then the 50 newest questions across both hires


def test_ownerless_codeowners_line_makes_a_path_unowned(demo):
    """GitHub: a pattern with no owners makes that path unowned, even inside a broader rule."""
    demo.store.set_codeowners(PID, "/engine/ @ravi-dev\n/webhooks/ @meera-dev\n/engine/policy.py\n")
    r = risk(demo)
    files = by_path(r)
    assert files["engine/policy.py"]["owners"] == [] and files["engine/policy.py"]["risk_score"] == 75  # unowned: as risky as one owner
    ravi = by_user(r)["u-ravi"]
    assert (ravi["files_owned"], ravi["exposure"]["orphaned_files"], ravi["exposure"]["unrecorded_lines"]) == (2, [], 11)
    assert (r["summary"]["unowned_files"], r["summary"]["single_owner_files"]) == (1, 3)
    assert r["has_codeowners"] is True


def test_codeowners_of_ownerless_patterns_only_is_no_codeowners(demo):
    demo.store.set_codeowners(PID, "/engine/\n/webhooks/\n")
    r = risk(demo)
    assert r["has_codeowners"] is False and r["people"] == [] and r["summary"]["unowned_files"] == 4  # the 'add a CODEOWNERS' banner


def test_chunk_cap_drops_whole_files_and_marks_the_response_truncated(demo, monkeypatch):
    """The index's cap (ports.CODE_CHUNK_CAP, the one source) cuts whole files: no file is scored on half its symbols."""
    from app import ports

    full = risk(demo)
    assert (full["truncated"], full["index_truncated"], full["files_total"]) == (False, False, 4)
    monkeypatch.setattr(ports, "CODE_CHUNK_CAP", 7)  # README (1), engine/__init__ (0) ... the seed indexes 19 code chunks
    cut = risk(demo)
    assert cut["truncated"] is cut["index_truncated"] is True
    kept = by_path(cut)
    assert kept and set(kept) < set(by_path(full))
    for path, f in kept.items():  # every file that is in is whole
        assert [s["symbol"] for s in f["symbols"]] == [s["symbol"] for s in by_path(full)[path]["symbols"]]
    assert "webhooks/server.py" not in kept  # late in path order: left out entirely


def test_response_is_bounded_but_counts_everything(demo, monkeypatch):
    monkeypatch.setattr(KR, "MAX_FILES", 2)
    monkeypatch.setattr(KR, "MAX_SYMBOLS", 2)
    r = risk(demo)
    assert [f["path"] for f in r["files"]] == ["webhooks/server.py", "engine/policy.py"] and r["files_total"] == 4
    assert r["summary"]["code_files"] == 4 and r["summary"]["code_lines"] == 60  # the summary still counts every file
    server = r["files"][0]
    assert (len(server["symbols"]), server["symbols_total"], len(server["unrecorded_symbols"]), server["unrecorded_total"]) == (2, 3, 2, 3)
    assert by_user(r)["u-meera"]["exposure"]["unrecorded_lines"] == 17  # computed before the lists were cut


# ---------------------------------------------------------------- HTTP
@pytest.fixture(scope="module")
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def test_knowledge_risk_roles(client):
    url = f"/api/knowledge-risk?project_id={PID}"
    r = client.get(url, headers={"X-Demo-User": "u-priya"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"indexed", "pace_s_per_line", "pace_is_default", "summary", "files", "people"}
    assert [f["risk_score"] for f in body["files"]] == [88, 75, 48, 0]
    task = next(d for d in body["files"][0]["demand"] if d["kind"] == "task")
    assert len(task["due"]) == 10 and task["due"][4] == "-"  # 'YYYY-MM-DD'
    assert client.get(url, headers={"X-Demo-User": "u-vijay"}).status_code == 403  # new hire
    assert client.get(url, headers={"X-Demo-User": "u-ravi"}).status_code == 403  # expert
    assert client.get("/api/knowledge-risk?project_id=proj-nope", headers={"X-Demo-User": "u-priya"}).status_code == 404
    assert client.get("/api/knowledge-risk", headers={"X-Demo-User": "u-priya"}).status_code == 422

    store = client.app.state.container.store
    store.add_project(Project(id="proj-elsewhere", org_id="org-elsewhere", name="Elsewhere"))
    store.add_user(User(id="u-mona", org_id=ORG, email="mona@demo.dev", name="Mona", role="manager"))  # same org, not a member
    store.add_user(User(id="u-otto", org_id="org-elsewhere", email="otto@x.dev", name="Otto", role="manager"), "proj-elsewhere")
    store.add_user(User(id="u-ada", org_id=ORG, email="ada@demo.dev", name="Ada", role="admin"))  # admin, not a member
    assert client.get(url, headers={"X-Demo-User": "u-mona"}).status_code == 404
    assert client.get(url, headers={"X-Demo-User": "u-otto"}).status_code == 404  # another organisation's manager
    admin = client.get(url, headers={"X-Demo-User": "u-ada"})
    assert admin.status_code == 200 and [f["risk_score"] for f in admin.json()["files"]] == [88, 75, 48, 0]


def test_endpoint_uses_the_canonical_project_id(client, monkeypatch):
    """Postgres parses a uuid in any case, so check_project accepts 'ABC…' for 'abc…'; the service must get the stored id."""
    store, seen = client.app.state.container.store, []
    real_get, real_member = store.get_project, store.is_member
    monkeypatch.setattr(store, "get_project", lambda pid: real_get(pid.lower()))
    monkeypatch.setattr(store, "is_member", lambda pid, uid: real_member(pid.lower(), uid))
    real_build = risk_api.build_knowledge_risk
    monkeypatch.setattr(risk_api, "build_knowledge_risk", lambda st, ix, pid, org: seen.append(pid) or real_build(st, ix, pid, org))
    r = client.get(f"/api/knowledge-risk?project_id={PID.upper()}", headers={"X-Demo-User": "u-priya"})
    assert r.status_code == 200 and seen == [PID]


def test_search_outage_is_not_a_500(client, monkeypatch):
    def down(*a, **kw):
        raise ConnectionError("embedding service unavailable")

    K.clear_search_memo()  # earlier requests remembered the first-PR search: start from an outage with nothing remembered
    monkeypatch.setattr(client.app.state.container.index, "search", down)
    r = client.get(f"/api/knowledge-risk?project_id={PID}", headers={"X-Demo-User": "u-priya"})
    assert r.status_code == 200 and r.json()["partial"] is True and r.json()["files"]
