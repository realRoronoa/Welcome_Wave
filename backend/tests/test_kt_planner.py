"""KT planner (app/services/kt_planner.py, GET /api/kt-plan, POST /api/kt-plan/schedule).

Seed numbers come from the real demo seed (MODE=demo): 4 code files, 60 lines, 18 recorded, pace 13.89 s per line.
Every test builds its own seeded container (milliseconds), so tests that book calls or add links stay isolated.
"""
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.adapters.memory import MemoryIndex, MemoryStore
from app.agents.schemas import KTLink, QAResult
from app.config import Settings, get_settings
from app.container import build_container
from app.main import create_app
from app.ports import Onboarding, Project, TaskRow, User
from app.services import knowledge as K
from app.services import kt_planner as P
from app.services.chunking import chunk_code
from app.services.embeddings import HashEmbedder

PID, ORG = "proj-demo", "org-demo"
MANAGER, HIRE = {"X-Demo-User": "u-priya"}, {"X-Demo-User": "u-vijay"}
RAVI, MEERA = {"X-Demo-User": "u-ravi"}, {"X-Demo-User": "u-meera"}


@pytest.fixture
def demo():
    return build_container(Settings(mode="demo", gemini_api_key=""))


def plan(c, who="u-priya", presenter=None):
    return P.build_plan(c.store, c.index, PID, ORG, c.store.get_user(who), presenter)


def symbols(session):
    return [i["symbol"] for i in session["items"]]


def kinds(item):
    return {r["kind"] for r in item["reasons"]}


def _link(path, symbol, start_line, end_line, start_s=0.0, end_s=60.0, meeting="mtg-9", stale=False):
    return KTLink(meeting_id=meeting, meeting_title="KT #9: test", file_path=path, symbol=symbol, start_line=start_line, end_line=end_line,
                  start_s=start_s, end_s=end_s, confidence=0.95, method="path", code_changed_since=stale)


# ---------------------------------------------------------------- the seeded plan
def test_plan_seed_sessions(demo):
    out = plan(demo)
    assert (out["indexed"], out["scope"], out["presenter_filter"], out["partial"]) == (True, "team", None, False)
    assert (out["pace_s_per_line"], out["pace_is_default"]) == (13.89, False)
    meera, ravi = out["sessions"]
    webhooks_task = next(t for t in demo.store.tasks(project_id=PID) if t.origin == "coverage_gap")
    first_pr = next(t for t in demo.store.tasks(project_id=PID) if t.origin == "first_pr")

    assert meera["title"] == "KT with Meera: webhooks/server.py"
    assert (meera["presenter"], meera["presenter_source"]) == ({"user_id": "u-meera", "name": "Meera (Senior)", "login": "meera-dev"}, "codeowners")
    assert symbols(meera) == ["handle_admission", "Handler", "do_POST"]
    assert [(i["start_line"], i["end_line"], i["est_minutes"]) for i in meera["items"]] == [(12, 21, 2.3), (24, 24, 0.2), (25, 30, 1.4)]
    assert (meera["est_minutes"], meera["deadline"], meera["part"], meera["booked"]) == (4.0, webhooks_task.due_date, 1, None)
    assert [r["text"] for r in meera["items"][0]["reasons"]] == [
        f"Vijay's task Read and summarise the webhooks module, due {K.fmt_day(webhooks_task.due_date)} (awaiting approval)",
        "Module webhooks has never been explained in a KT session"]
    assert meera["items"][0]["reasons"][0]["pending_approval"] is True  # a proposed task: the UI says 'awaiting approval'
    assert meera["impact"] == {"files_newly_covered": ["webhooks/server.py"], "gaps_closed": ["webhooks"], "tasks_unblocked": 1,
                               "questions_addressed": 0, "recorded_share_before": 0.3, "recorded_share_after": 0.58}

    assert ravi["title"] == "KT with Ravi: engine/validate.py, engine/policy.py"
    assert (ravi["presenter"]["user_id"], ravi["est_minutes"], ravi["deadline"]) == ("u-ravi", 5.5, first_pr.due_date)
    assert symbols(ravi) == ["check_privileged", "Rule", "Policy", "load_policy"]
    assert {"first_pr", "named_not_shown"} <= kinds(ravi["items"][0])
    assert ravi["items"][0]["reasons"][0]["text"] == "First PR for Vijay: Validate privileged init containers (issue #12), 41% match"
    assert all(kinds(i) == {"action_item"} for i in ravi["items"][1:])
    assert ravi["items"][1]["reasons"][0]["ref"] == {"meeting_id": "mtg-1", "at_s": 232.0}
    assert ravi["impact"]["gaps_closed"] == [] and ravi["impact"]["tasks_unblocked"] == 1


def test_plan_totals_and_backlog(demo):
    out = plan(demo)
    assert out["totals"] == {"sessions": 2, "est_minutes": 9.5, "items": 7, "recorded_share_before": 0.3, "recorded_share_after": 0.97,
                             "gaps_before": ["webhooks"], "gaps_after": []}
    assert out["backlog"] == {"unrecorded_symbols": 1, "lines": 2, "symbols": ["ValidationError"]}
    assert out["rerecord"] == []


def test_keys_and_titles_are_deterministic(demo):
    a, b = plan(demo), plan(demo)
    assert [s["key"] for s in a["sessions"]] == [s["key"] for s in b["sessions"]]
    s = a["sessions"][0]
    spans = [K.Span(i["symbol"], i["start_line"], i["end_line"], path=i["path"]) for i in s["items"]]
    assert s["key"] == P.session_key("u-meera", spans) and len(s["key"]) == 12
    long = P.session_title("Ravi (Senior)", [f"pkg/module_{n}/file.py" for n in range(20)], 2, True)
    assert len(long) <= P.TITLE_MAX and long.endswith("… (part 2)")  # the part suffix survives truncation
    assert P.session_title("Ravi (Senior)", ["a.py"], 1, False) == "KT with Ravi: a.py"


def test_session_share_after_matches_line_totals(demo):
    """The planner's arithmetic 'after' share equals the core's line_totals(assume_recorded=...) for every session."""
    demo.store.mark_kt_code_changed(PID, ["engine/mutate.py"])  # a stale item too: it loses its half credit and gains a full one
    files, links = K.code_files(demo.index, PID), demo.store.kt_links(PID)
    for s in plan(demo)["sessions"]:
        spans = [K.span_at(files, i["path"], i["start_line"], i["symbol"]) for i in s["items"]]
        assert s["impact"]["recorded_share_after"] == round(K.line_totals(files, links, assume_recorded=spans).share, 2)


def test_pace_drives_estimates(demo):
    before = plan(demo)
    # a long walkthrough of code that is already explained: nothing leaves the plan, but the team's pace slows
    demo.store.add_kt_links(PID, [_link("engine/validate.py", "validate_resource", 9, 17, end_s=900)])
    after = plan(demo)
    assert after["pace_s_per_line"] > before["pace_s_per_line"]
    assert [symbols(s) for s in after["sessions"]] == [symbols(s) for s in before["sessions"]]
    for b, a in zip(before["sessions"], after["sessions"]):
        assert a["est_minutes"] > b["est_minutes"]
        assert all(ai["est_minutes"] >= bi["est_minutes"] for bi, ai in zip(b["items"], a["items"]))
    assert after["totals"]["est_minutes"] > before["totals"]["est_minutes"]


def test_default_pace_is_flagged(demo):
    demo.store.links[PID] = []  # no KT link has a line range yet
    out = plan(demo)
    assert (out["pace_s_per_line"], out["pace_is_default"]) == (12.0, True)
    assert out["sessions"][0]["items"][0]["est_minutes"] == 2.0  # 10 lines at 12 s


# ---------------------------------------------------------------- scope and presenters
def test_expert_scope_is_mine(demo):
    out = plan(demo, "u-ravi", presenter="u-meera")  # the presenter param is ignored for experts
    assert (out["scope"], out["presenter_filter"]) == ("mine", "u-ravi")
    assert [s["title"] for s in out["sessions"]] == ["KT with Ravi: engine/validate.py, engine/policy.py"]
    assert out["totals"]["sessions"] == 2 and out["totals"]["est_minutes"] == 9.5  # totals still describe the team plan


def test_manager_presenter_filter(demo):
    out = plan(demo, presenter="u-meera")
    assert (out["scope"], out["presenter_filter"]) == ("team", "u-meera")
    assert [s["presenter"]["user_id"] for s in out["sessions"]] == ["u-meera"]
    assert plan(demo, presenter="u-nobody")["sessions"] == []


def test_empty_presenter_filter_is_the_team_plan(demo):
    out = plan(demo, presenter="")  # '?presenter=' from a form or a URL builder
    assert out["presenter_filter"] is None and len(out["sessions"]) == 2


def _questions(out):
    return [(i["symbol"], r) for s in out["sessions"] for i in s["items"] for r in i["reasons"] if r["kind"] == "question"]


def test_new_hire_questions_are_counts_for_managers_and_experts(demo):
    """A new hire's chat is theirs (GET /chat/history is owner-only): the plan says how many times new hires asked about a
    function and how it went, never who asked, the words or the message, for managers (their visibility page lists only
    counts) and experts alike."""
    q = "I'm lost and worried about my probation - how does the admission webhook handle a request?"
    demo.handle({"type": "chat.question", "project_id": PID, "user_id": "u-vijay", "question": q})
    for who, text in (("u-vijay", "Why does do_POST read Content-Length in the webhook server?"), ("u-arjun", "Why does do_POST read the body?")):
        demo.store.save_chat(PID, who, text, QAResult(status="not_enough_information", answer=""))

    team, meera = plan(demo), plan(demo, "u-meera")
    for out in (team, meera):
        asked = {symbol: (r["text"], r["asked"], r["unanswered"], r["weight"], r["ref"]) for symbol, r in _questions(out)}
        assert asked == {
            "handle_admission": ("Asked once by a new hire about handle_admission: answered from code, no recording", 1, 0, 1, {}),
            "do_POST": ("Asked twice by new hires about do_POST: not answered", 2, 2, 2 * K.UNANSWERED_QUESTION_WEIGHT, {}),
        }
        assert "probation" not in str(out) and "Content-Length" not in str(out) and "asked \"" not in str(out)
        assert out["sessions"][0]["impact"]["questions_addressed"] == 3  # still counted, one per question


def test_expert_reads_no_hire_name_and_no_plan_awaiting_approval(demo):
    """The seeded plan waits for Priya's approval: an expert's queue says planned work is coming, not what or for whom.
    Once it is approved, the expert reads the task without the hire's name; managers keep the name throughout."""
    def tasks(out):
        return [r for s in out["sessions"] for i in s["items"] for r in i["reasons"] if r["kind"] in {"task", "first_pr"}]

    team, meera = plan(demo), plan(demo, "u-meera")
    assert all(r["pending_approval"] for r in tasks(team)) and any("Vijay" in r["text"] for r in tasks(team))
    assert {(r["text"], json.dumps(r["ref"])) for r in tasks(meera)} == {(K.PLANNED_WORK_TEXT, "{}")}
    assert "Vijay" not in str(meera["sessions"]) and "summarise" not in str(meera["sessions"])
    booked = P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-meera"), meera["sessions"][0]["key"])
    assert "Vijay" not in str(booked["session"])  # the booking response is the expert's view too

    for t in demo.store.tasks(project_id=PID):
        if t.status == "proposed":
            demo.store.update_task(t.id, status="approved")
    approved = [r["text"] for r in tasks(plan(demo, "u-meera"))]
    assert approved and all(t.startswith("A new hire's task Read and summarise the webhooks module, due ") for t in approved)
    assert all(r["text"].startswith("Vijay's task") for r in tasks(plan(demo, presenter="u-meera")))


def test_question_searches_are_capped_and_remembered(demo, monkeypatch):
    for n in range(P.QUESTION_SEARCHES + 5):
        demo.store.save_chat(PID, "u-vijay", f"how does load_policy parse rule number {n}?", QAResult(status="not_enough_information", answer=""))
    queries = []
    real = demo.index.search
    monkeypatch.setattr(demo.index, "search", lambda pid, q, k=8, st=None: queries.append(q) or real(pid, q, k, st))

    plan(demo)
    asked = [q for q in queries if q.startswith("how does load_policy")]
    assert len(asked) == P.QUESTION_SEARCHES  # the newest questions, at most QUESTION_SEARCHES per plan
    first = len(queries)
    plan(demo)
    assert len(queries) == first  # a reload repeats no search (no embedding call)
    P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-priya"), plan(demo)["sessions"][0]["key"])
    assert len(queries) == first  # nor does a booking

    demo.index.upsert(chunk_code(PID, "engine/extra.py", "def extra():\n    return 1\n"))  # the code changed: search again
    plan(demo)
    assert len(queries) == 2 * first


def test_search_outage_degrades_to_partial(demo):
    """An embedding outage costs the searched anchors (the path-less first PR, unanswered questions), not the plan."""
    calls = []

    def down(*a, **kw):
        calls.append(a)
        raise TimeoutError("Vertex embeddings unavailable")

    demo.store.save_chat(PID, "u-vijay", "Why does do_POST read Content-Length?", QAResult(status="not_enough_information", answer=""))
    demo.index.search = down
    out = plan(demo)
    assert out["partial"] is True and len(calls) == 1  # the first failure turns the remaining searches off
    meera, ravi = out["sessions"]
    assert symbols(meera) == ["handle_admission", "Handler", "do_POST"]
    # the first-PR anchor needed a search: check_privileged keeps its other reason, and without the first PR's due date
    # files go in path order
    assert symbols(ravi) == ["Rule", "Policy", "load_policy", "check_privileged"] and ravi["deadline"] is None
    assert kinds(ravi["items"][-1]) == {"named_not_shown"} and all(kinds(i) == {"action_item"} for i in ravi["items"][:-1])
    assert not [r for s in out["sessions"] for i in s["items"] for r in i["reasons"] if r["kind"] == "question"]
    booked = P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-priya"), meera["key"])  # booking still works
    assert booked["session"]["key"] == meera["key"]


def test_question_cap_is_shared_across_hires(demo):
    """QUESTION_SEARCHES older questions from Vijay must not crowd out a newer one from a second hire."""
    store = demo.store
    store.add_user(User(id="u-nina", org_id=ORG, email="nina@demo.dev", name="Nina Rao", role="new_hire", github_login="nina"), PID)
    store.add_onboarding(Onboarding(id="ob-zz-nina", project_id=PID, new_hire_id="u-nina", manager_id="u-priya", start_date=date.today()))
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for n in range(P.QUESTION_SEARCHES):
        mid = store.save_chat(PID, "u-vijay", f"How do I deploy to production? ({n})", QAResult(status="not_enough_information", answer=""))
        store.chats[mid]["created_at"] = t0 + timedelta(minutes=n)
    nina = store.save_chat(PID, "u-nina", "How does the admission webhook handle a request?", QAResult(status="not_enough_information", answer=""))
    store.chats[nina]["created_at"] = t0 + timedelta(days=1)
    meera = plan(demo)["sessions"][0]
    handle = next(i for i in meera["items"] if i["symbol"] == "handle_admission")
    [question] = [r for r in handle["reasons"] if r["kind"] == "question"]  # Nina's: the newest, though Vijay asked 20 before
    assert (question["asked"], question["unanswered"], question["weight"]) == (1, 1, K.UNANSWERED_QUESTION_WEIGHT)
    assert nina and "admission webhook" not in question["text"] and meera["impact"]["questions_addressed"] == 1


def test_ownerless_codeowners_line_hands_the_file_to_the_next_presenter(demo):
    """GitHub: '/engine/policy.py' with no owners makes policy.py unowned inside '/engine/ @ravi-dev'. Ravi still
    presents it, as the engine's speaker on KT #1 rather than as its CODEOWNERS owner."""
    demo.store.set_codeowners(PID, "/engine/ @ravi-dev\n/webhooks/ @meera-dev\n/engine/policy.py\n")
    ravi = next(s for s in plan(demo)["sessions"] if s["presenter"]["user_id"] == "u-ravi")
    assert {i["path"]: i["presenter_source"] for i in ravi["items"]} == {"engine/validate.py": "codeowners", "engine/policy.py": "speaker"}
    assert ravi["presenter_source"] == "codeowners"


def test_presenter_fallbacks(demo):
    demo.store.set_codeowners(PID, "")
    ravi_session = next(s for s in plan(demo)["sessions"] if s["presenter"]["user_id"] == "u-ravi")
    # engine/policy.py has no clip of its own: the longest clip in the engine module is Ravi's walkthrough of check_labels
    assert ravi_session["presenter_source"] == "speaker" and {i["presenter_source"] for i in ravi_session["items"]} == {"speaker"}
    assert {i["path"] for i in ravi_session["items"]} == {"engine/validate.py", "engine/policy.py"}
    webhooks = next(s for s in plan(demo)["sessions"] if s["items"][0]["path"] == "webhooks/server.py")
    assert (webhooks["presenter"]["user_id"], webhooks["presenter_source"]) == ("u-priya", "manager")
    assert webhooks["title"] == "KT with Priya: webhooks/server.py"

    demo.store.managers = lambda pid: []
    nobody = next(s for s in plan(demo)["sessions"] if s["items"][0]["path"] == "webhooks/server.py")
    assert (nobody["presenter"], nobody["presenter_source"]) == (None, "none")


def test_owner_must_be_an_eligible_member(demo):
    # Vijay owns the webhooks in CODEOWNERS, but a new hire never presents: the session falls back to the manager
    demo.store.set_codeowners(PID, "/engine/ @ravi-dev\n/webhooks/ @vijay @org/platform\n")
    webhooks = plan(demo)["sessions"][0]
    assert (webhooks["presenter"]["user_id"], webhooks["presenter_source"]) == ("u-priya", "manager")


# ---------------------------------------------------------------- splitting, live demand, coverage changes
def _world(functions: int, lines: int):
    """A project with one file of `functions` functions of `lines` lines, owned by one expert and needed by a task."""
    store, index = MemoryStore(), MemoryIndex(HashEmbedder())
    store.add_project(Project(id="p1", org_id="o1", name="Big"))
    store.add_user(User(id="u-m", org_id="o1", email="m@x", name="Mona Manager", role="manager"), "p1")
    store.add_user(User(id="u-e", org_id="o1", email="e@x", name="Esha Expert", role="expert", github_login="esha"), "p1")
    store.add_user(User(id="u-h", org_id="o1", email="h@x", name="Hari Hire", role="new_hire"), "p1")
    store.add_onboarding(Onboarding(id="ob1", project_id="p1", new_hire_id="u-h", manager_id="u-m", start_date=date(2026, 1, 1)))
    store.set_codeowners("p1", "/core/ @esha\n")
    src = []
    for n in range(functions):
        src += [f"def step_{n}():"] + [f"    x{i} = {i}" for i in range(lines - 1)] + [""]
    index.upsert(chunk_code("p1", "core/big.py", "\n".join(src)))
    return store, index


def test_session_split_over_30_minutes():
    store, index = _world(3, 60)
    store.task_rows["t1"] = TaskRow(id="t1", onboarding_id="ob1", title="Learn core", description="", kind="learn", status="approved",
                                    related_paths=["core/"], due_date=date(2026, 1, 9))
    out = P.build_plan(store, index, "p1", "o1", store.get_user("u-m"))
    assert out["pace_is_default"] is True  # 12 s per line: 12 minutes per function
    one, two = out["sessions"]
    assert (one["part"], two["part"], one["parts"]) == (1, 2, 2)
    assert (symbols(one), symbols(two)) == (["step_0", "step_1"], ["step_2"])
    assert (one["est_minutes"], two["est_minutes"]) == (24.0, 12.0)
    assert one["title"] == "KT with Esha: core/big.py (part 1)" and two["title"] == "KT with Esha: core/big.py (part 2)"
    assert one["key"] != two["key"] and one["deadline"] == date(2026, 1, 9)


def test_no_demand_gives_empty_plan_and_full_backlog():
    store, index = _world(2, 5)
    store.add_kt_links("p1", [_link("core/big.py", None, None, None, meeting="m-old")])  # explains the whole file
    out = P.build_plan(store, index, "p1", "o1", store.get_user("u-m"))
    assert out["sessions"] == [] and out["totals"]["sessions"] == 0 and out["backlog"]["unrecorded_symbols"] == 0

    # big.py was never explained, but nobody needs it (no task, and a call covered core/small.py, so 'core' is no gap)
    store2, index2 = _world(2, 5)
    index2.upsert(chunk_code("p1", "core/small.py", "def tiny():\n    return 1\n"))
    store2.add_kt_links("p1", [_link("core/small.py", None, None, None, meeting="m-old")])
    out = P.build_plan(store2, index2, "p1", "o1", store2.get_user("u-m"))
    assert out["sessions"] == [] and out["totals"]["gaps_before"] == []
    assert out["backlog"] == {"unrecorded_symbols": 2, "lines": 10, "symbols": ["step_0", "step_1"]}


def test_not_indexed_project():
    store, index = MemoryStore(), MemoryIndex(HashEmbedder())
    store.add_project(Project(id="p1", org_id="o1", name="Empty"))
    store.add_user(User(id="u-m", org_id="o1", email="m@x", name="Mona", role="manager"), "p1")
    out = P.build_plan(store, index, "p1", "o1", store.get_user("u-m"))
    assert out["indexed"] is False and out["sessions"] == [] and out["totals"]["sessions"] == 0 and out["backlog"]["lines"] == 0


def test_question_reason_appears_live(demo):
    q = "How does the admission webhook handle a request?"
    res = demo.handle({"type": "chat.question", "project_id": PID, "user_id": "u-vijay", "question": q})["result"]
    meera = plan(demo)["sessions"][0]
    handle = next(i for i in meera["items"] if i["symbol"] == "handle_admission")
    [question] = [r for r in handle["reasons"] if r["kind"] == "question"]
    assert res["message_id"] and question["ref"] == {} and question["text"].endswith("answered from code, no recording")
    assert meera["impact"]["questions_addressed"] == 1


def test_recorded_items_leave_plan(demo):
    demo.store.add_kt_links(PID, [_link("engine/validate.py", "check_privileged", 25, 33, meeting="mtg-2")])
    ravi = next(s for s in plan(demo)["sessions"] if s["presenter"]["user_id"] == "u-ravi")
    assert symbols(ravi) == ["Rule", "Policy", "load_policy"]
    assert ravi["title"] == "KT with Ravi: engine/policy.py"


def test_stale_link_goes_to_rerecord(demo):
    demo.store.mark_kt_code_changed(PID, ["engine/mutate.py"])
    out = plan(demo)
    [row] = out["rerecord"]
    assert (row["meeting_id"], row["file_path"], row["symbol"], row["start_s"]) == ("mtg-1", "engine/mutate.py", "apply_defaults", 182.0)
    assert row["presenter"]["user_id"] == "u-ravi"  # the engine owner
    item = next(i for s in out["sessions"] for i in s["items"] if i["symbol"] == "apply_defaults")
    assert item["state"] == "stale" and kinds(item) == {"stale"}
    assert plan(demo, "u-meera")["rerecord"] == []  # an expert sees only their own re-records


def test_fresh_clip_clears_rerecord(demo):
    demo.store.mark_kt_code_changed(PID, ["engine/mutate.py"])
    assert [r["symbol"] for r in plan(demo)["rerecord"]] == ["apply_defaults"]
    demo.store.add_kt_links(PID, [_link("engine/mutate.py", "apply_defaults", 6, 11, meeting="mtg-2")])  # re-recorded
    out = plan(demo)
    assert out["rerecord"] == []
    assert not [i for s in out["sessions"] for i in s["items"] if i["symbol"] == "apply_defaults"]  # agrees with the sessions


def test_deleted_file_leaves_rerecord(demo):
    demo.store.mark_kt_code_changed(PID, ["engine/mutate.py"])  # ingestion marks a removed file as changed...
    demo.index.delete_paths(PID, ["engine/mutate.py"])  # ...and drops it from the index
    assert plan(demo)["rerecord"] == []


# ---------------------------------------------------------------- schedule (service level)
def test_schedule_errors_at_service_level(demo):
    store, index, priya = demo.store, demo.index, demo.store.get_user("u-priya")
    with pytest.raises(P.PlanChanged):
        P.schedule(store, index, PID, ORG, priya, "0" * 12)
    store.set_codeowners(PID, "/engine/ @ravi-dev\n")
    store.managers = lambda pid: []
    orphan = next(s for s in plan(demo)["sessions"] if s["presenter"] is None)
    with pytest.raises(P.NoPresenter):
        P.schedule(store, index, PID, ORG, priya, orphan["key"])
    with pytest.raises(P.NotYourSession):  # an expert never books a session without a presenter either
        P.schedule(store, index, PID, ORG, store.get_user("u-ravi"), orphan["key"])


def test_schedule_notification_names_the_agenda(demo):
    key = plan(demo)["sessions"][0]["key"]
    out = P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-priya"), key)
    [n] = [n for n in demo.store.notifications if n["kind"] == "kt_request"]
    assert n["text"] == ("Priya (Manager) booked a KT session for you to present: 'KT with Meera: webhooks/server.py', about 4.0 min. "
                         "Walk through handle_admission, Handler and do_POST in webhooks/server.py. It is in KT sessions.")
    assert n["link"] == f"/meetings?call={out['meeting'].id}&project={PID}"  # names the call, so cancelling it can find the notice
    [audit] = [a for a in demo.store.audit_rows if a["action"] == "kt_plan.scheduled"]
    assert audit["target"] == out["meeting"].id and audit["detail"]["presenter_id"] == "u-meera" and audit["detail"]["est_minutes"] == 4.0
    assert audit["detail"]["items"] == ["webhooks/server.py::handle_admission", "webhooks/server.py::Handler", "webhooks/server.py::do_POST"]
    with pytest.raises(P.AlreadyBooked):  # a second click never books a second call
        P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-priya"), key)


def test_processing_call_stays_booked(demo):
    priya = demo.store.get_user("u-priya")
    key = plan(demo)["sessions"][0]["key"]
    m = P.schedule(demo.store, demo.index, PID, ORG, priya, key)["meeting"]
    demo.store.update_meeting(m.id, status="processing")  # the call ended; the Meeting agent is still on the recording
    s = plan(demo)["sessions"][0]
    assert s["key"] == key and s["booked"] == {"meeting_id": m.id, "status": "processing", "created_by": "u-priya"}
    with pytest.raises(P.AlreadyBooked):
        P.schedule(demo.store, demo.index, PID, ORG, priya, key)
    demo.store.update_meeting(m.id, status="failed")  # a recording that failed can be booked again
    assert plan(demo)["sessions"][0]["booked"] is None


def test_simultaneous_bookings_make_one_call(demo, monkeypatch):
    priya = demo.store.get_user("u-priya")
    session = plan(demo)["sessions"][0]
    real = demo.store.create_meeting

    def slow_create(*a, **kw):  # widen the window between the booked check and the insert
        time.sleep(0.15)
        return real(*a, **kw)

    monkeypatch.setattr(demo.store, "create_meeting", slow_create)
    start, results = threading.Barrier(2), []

    def book():
        start.wait()
        try:
            results.append(P.schedule(demo.store, demo.index, PID, ORG, priya, session["key"])["meeting"].id)
        except P.AlreadyBooked:
            results.append("already_booked")

    threads = [threading.Thread(target=book) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(results) == 2 and results.count("already_booked") == 1
    assert len([m for m in demo.store.list_meetings(PID) if m.title == session["title"]]) == 1
    assert len([n for n in demo.store.notifications if n["kind"] == "kt_request"]) == 1


def test_booking_refused_by_the_store_is_already_booked(demo, monkeypatch):
    """The database keeps one live call per session key (Postgres: a partial unique index, create_meeting returns None):
    a booking that loses that race is AlreadyBooked, and writes no audit row and sends no notification."""
    monkeypatch.setattr(demo.store, "create_meeting", lambda *a, **kw: None)
    key = plan(demo)["sessions"][0]["key"]
    with pytest.raises(P.AlreadyBooked):
        P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-priya"), key)
    assert not [a for a in demo.store.audit_rows if a["action"] == "kt_plan.scheduled"]
    assert not [n for n in demo.store.notifications if n["kind"] == "kt_request"]


def test_booking_takes_no_lock_and_refuses_an_unknown_key_before_any_write(demo, monkeypatch):
    """A booking never holds a lock (on Postgres an advisory lock held a pooled connection while the plan was rebuilt),
    and a key that is not in the plan is refused before anything is written."""
    def no_lock(name):
        raise AssertionError(f"booking took a lock: {name}")

    monkeypatch.setattr(demo.store, "ingest_lock", no_lock)
    created = []
    real = demo.store.create_meeting
    monkeypatch.setattr(demo.store, "create_meeting", lambda *a, **kw: created.append(a) or real(*a, **kw))
    with pytest.raises(P.PlanChanged):
        P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-ravi"), "0123456789ab")
    assert created == []
    out = P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-priya"), plan(demo)["sessions"][0]["key"])
    assert out["meeting"].kt_key == out["session"]["key"] and len(created) == 1


def test_a_call_with_the_same_title_books_nothing(demo):
    """Booked means booked from the planner (by session key), not 'a call has this title': a new hire's call named after a
    session neither marks it booked nor stops the manager from booking it."""
    session = plan(demo)["sessions"][0]
    hires_call = demo.store.create_meeting(PID, session["title"], "u-vijay")
    again = next(s for s in plan(demo)["sessions"] if s["key"] == session["key"])
    assert hires_call.kt_key is None and again["booked"] is None
    out = P.schedule(demo.store, demo.index, PID, ORG, demo.store.get_user("u-priya"), session["key"])
    assert out["meeting"].id != hires_call.id and out["notified_user_id"] == "u-meera"
    assert next(s for s in plan(demo)["sessions"] if s["key"] == session["key"])["booked"]["meeting_id"] == out["meeting"].id


def test_cancelled_call_frees_its_session(demo):
    priya = demo.store.get_user("u-priya")
    key = plan(demo)["sessions"][0]["key"]
    m = P.schedule(demo.store, demo.index, PID, ORG, priya, key)["meeting"]
    assert demo.store.cancel_meeting(m.id) is True and demo.store.get_meeting(m.id).status == "cancelled"
    assert demo.store.cancel_meeting(m.id) is False  # only a scheduled call can be cancelled
    assert plan(demo)["sessions"][0]["booked"] is None
    again = P.schedule(demo.store, demo.index, PID, ORG, priya, key)["meeting"]
    assert again.id != m.id and again.status == "scheduled"


def test_session_key_ignores_display_order_so_an_outage_booking_stays_booked(demo):
    """During a search outage the first PR's due date is missing, so the Ravi session lists policy.py before validate.py.
    The key hashes the agenda in (path, line) order, so the booking made then is found again once search recovers, and
    the healthy plan cannot book a second call for the same agenda."""
    priya = demo.store.get_user("u-priya")
    real = demo.index.search

    def down(*a, **kw):
        raise ConnectionError("embedding service unavailable")

    healthy = next(s for s in plan(demo)["sessions"] if s["presenter"]["user_id"] == "u-ravi")
    K.clear_search_memo()
    P.clear_cache()
    demo.index.search = down
    degraded = next(s for s in plan(demo)["sessions"] if s["presenter"]["user_id"] == "u-ravi")
    assert [i["path"] for i in degraded["items"]] != [i["path"] for i in healthy["items"]]  # another display order...
    assert sorted(symbols(degraded)) == sorted(symbols(healthy)) and degraded["key"] == healthy["key"]  # ...one key
    booked = P.schedule(demo.store, demo.index, PID, ORG, priya, degraded["key"])["meeting"]
    demo.index.search = real
    K.clear_search_memo()
    recovered = next(s for s in plan(demo)["sessions"] if s["presenter"]["user_id"] == "u-ravi")
    assert recovered["title"] == healthy["title"] and recovered["booked"]["meeting_id"] == booked.id
    with pytest.raises(P.AlreadyBooked):
        P.schedule(demo.store, demo.index, PID, ORG, priya, recovered["key"])
    assert len([m for m in demo.store.list_meetings(PID) if m.kt_key]) == 1


def test_planning_work_is_linear_cached_and_scoped(demo, monkeypatch):
    """coverage_map runs once per plan, not once per session; a reload and a booking right after reuse the cached plan
    (no demand, no search); anything the plan reads changing rebuilds it; an expert's response builds only their own
    sessions."""
    store = demo.store
    for n in range(5):  # more sessions: one more presenter per module
        store.task_rows[f"t-extra-{n}"] = next(t for t in store.tasks(project_id=PID)).model_copy(
            update={"id": f"t-extra-{n}", "title": f"Extra {n}", "related_paths": ["engine/"], "origin": "agent", "status": "approved"})
    maps, demands, dicts = [], [], []
    real_map, real_demand, real_dict = P.coverage_map, K.demand, P._session_dict
    monkeypatch.setattr(P, "coverage_map", lambda *a, **kw: maps.append(1) or real_map(*a, **kw))
    monkeypatch.setattr(K, "demand", lambda *a, **kw: demands.append(1) or real_demand(*a, **kw))
    monkeypatch.setattr(P, "_session_dict", lambda s, pace, expert=False: dicts.append(s.key) or real_dict(s, pace, expert))
    team = plan(demo)
    assert len(maps) == 1 and len(demands) == 1
    assert plan(demo) == team and len(demands) == 1  # reload: cached
    ravi = plan(demo, "u-ravi")
    assert len(demands) == 1 and dicts[-len(ravi["sessions"]):] == [s["key"] for s in ravi["sessions"]]
    assert len(ravi["sessions"]) < team["sessions_total"]  # only Ravi's sessions were turned into JSON
    P.schedule(store, demo.index, PID, ORG, store.get_user("u-priya"), team["sessions"][0]["key"])
    assert len(demands) == 1  # the booking reused the cached plan
    after = plan(demo)
    assert len(demands) == 2 and after["sessions"][0]["booked"]  # the new call changed what the plan reads
    store.save_chat(PID, "u-vijay", "Why does do_POST read Content-Length?", QAResult(status="not_enough_information", answer=""))
    plan(demo)
    assert len(demands) == 3  # so does a new question


def test_response_carries_at_most_max_sessions(demo, monkeypatch):
    monkeypatch.setattr(P, "MAX_SESSIONS", 1)
    out = plan(demo)
    assert len(out["sessions"]) == 1 and out["sessions_total"] == 2 and out["totals"]["sessions"] == 2
    assert [(p["user_id"], p["sessions"]) for p in out["presenters"]] == [("u-meera", 1), ("u-ravi", 1)]
    ravi = plan(demo, presenter="u-ravi")
    assert [s["presenter"]["user_id"] for s in ravi["sessions"]] == ["u-ravi"] and ravi["sessions_total"] == 1
    assert plan(demo, "u-ravi")["presenters"] == []  # an expert's queue lists no one else's share


# ---------------------------------------------------------------- HTTP API
@pytest.fixture
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def _plan(client, headers, **params):
    r = client.get("/api/kt-plan", params={"project_id": PID, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_kt_plan_roles(client):
    team = _plan(client, MANAGER)
    assert team["scope"] == "team" and len(team["sessions"]) == 2 and team["sessions"][0]["deadline"]  # dates serialize as YYYY-MM-DD
    assert _plan(client, RAVI)["scope"] == "mine"
    assert client.get(f"/api/kt-plan?project_id={PID}", headers=HIRE).status_code == 403
    assert client.get("/api/kt-plan?project_id=proj-other", headers=MANAGER).status_code == 404
    assert client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": "0" * 12}, headers=HIRE).status_code == 403
    assert _plan(client, MANAGER, presenter="")["presenter_filter"] is None  # an empty filter is the team plan


def test_kt_plan_search_outage_is_not_a_500(client, monkeypatch):
    def down(*a, **kw):
        raise ConnectionError("embedding service unavailable")

    K.clear_search_memo()  # an outage with nothing remembered yet
    monkeypatch.setattr(client.app.state.container.index, "search", down)
    body = _plan(client, MANAGER)
    assert body["partial"] is True and len(body["sessions"]) == 2
    assert _plan(client, RAVI)["partial"] is True  # the expert's queue says so too


def test_kt_plan_other_project_of_the_same_org_is_404(client):
    """Membership, not only the organisation, guards the plan: same-org people outside a project get 404."""
    store = client.app.state.container.store
    store.add_project(Project(id="proj-x", org_id=ORG, name="Other team"))
    store.add_member("proj-x", "u-meera")
    for who in (MANAGER, RAVI):
        assert client.get("/api/kt-plan", params={"project_id": "proj-x"}, headers=who).status_code == 404
        r = client.post("/api/kt-plan/schedule", json={"project_id": "proj-x", "key": "0" * 12}, headers=who)
        assert r.status_code == 404
    assert _plan(client, MEERA, project_id="proj-x")["indexed"] is False  # a member gets the (unindexed) plan


def test_schedule_books_and_notifies(client):
    store = client.app.state.container.store
    session = _plan(client, MANAGER)["sessions"][0]
    r = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": session["key"]}, headers=MANAGER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meeting"]["title"] == session["title"] == "KT with Meera: webhooks/server.py"
    assert body["meeting"]["status"] == "scheduled" and body["notified_user_id"] == "u-meera"
    assert body["session"]["booked"] == {"meeting_id": body["meeting"]["id"], "status": "scheduled", "created_by": "u-priya"}
    [n] = [n for n in store.notifications if n["user_id"] == "u-meera" and n["kind"] == "kt_request"]
    assert n["link"] == f"/meetings?call={body['meeting']['id']}&project={PID}"
    assert any(a["action"] == "kt_plan.scheduled" for a in store.audit_rows)

    again = _plan(client, MANAGER)["sessions"][0]
    assert again["key"] == session["key"] and again["booked"]["status"] == "scheduled"
    meera = _plan(client, MEERA)
    assert [s["key"] for s in meera["sessions"]] == [session["key"]] and meera["sessions"][0]["booked"]
    dup = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": session["key"]}, headers=MANAGER)
    assert (dup.status_code, dup.json()["detail"]) == (409, "already_booked")
    assert any(m["title"] == session["title"] for m in client.get(f"/api/meetings?project_id={PID}", headers=MEERA).json())


def test_schedule_stale_key_409(client):
    r = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": "abcdef123456"}, headers=MANAGER)
    assert (r.status_code, r.json()["detail"]) == (409, "plan_changed")
    assert client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": "NOT-A-KEY"}, headers=MANAGER).status_code == 422


def test_expert_cannot_book_others_session_403(client):
    meera_key = _plan(client, MANAGER)["sessions"][0]["key"]
    r = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": meera_key}, headers=RAVI)
    assert r.status_code == 403
    assert not [m for m in client.get(f"/api/meetings?project_id={PID}", headers=RAVI).json() if m["title"].startswith("KT with")]


def test_new_hires_call_with_a_session_title_does_not_block_it_and_a_manager_can_cancel(client):
    """POST /meetings lets any member name a call anything: a new hire naming one after a planned session books nothing.
    A booked call nobody joins can be cancelled by a manager, which frees the session to be booked again."""
    session = _plan(client, MANAGER)["sessions"][0]
    r = client.post("/api/meetings", json={"project_id": PID, "title": session["title"]}, headers=HIRE)
    assert r.status_code == 200
    assert next(s for s in _plan(client, MANAGER)["sessions"] if s["key"] == session["key"])["booked"] is None
    booked = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": session["key"]}, headers=MANAGER)
    assert booked.status_code == 200 and booked.json()["notified_user_id"] == "u-meera"
    mid = booked.json()["meeting"]["id"]
    assert client.post(f"/api/meetings/{mid}/cancel", headers=HIRE).status_code == 403
    assert client.post(f"/api/meetings/{mid}/cancel", headers=RAVI).status_code == 403
    cancelled = client.post(f"/api/meetings/{mid}/cancel", headers=MANAGER)
    assert cancelled.status_code == 200 and cancelled.json()["meeting"]["status"] == "cancelled"
    assert client.post(f"/api/meetings/{mid}/cancel", headers=MANAGER).status_code == 409
    assert client.post(f"/api/meetings/{mid}/schedule", json={"starts_at": "2030-01-07T10:00:00Z"}, headers=MANAGER).status_code == 409
    assert next(s for s in _plan(client, MANAGER)["sessions"] if s["key"] == session["key"])["booked"] is None
    again = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": session["key"]}, headers=MANAGER)
    assert again.status_code == 200 and again.json()["meeting"]["id"] != mid


def test_expert_books_own_session_without_notification(client):
    store = client.app.state.container.store
    [mine] = _plan(client, RAVI)["sessions"]
    r = client.post("/api/kt-plan/schedule", json={"project_id": PID, "key": mine["key"]}, headers=RAVI)
    assert r.status_code == 200 and r.json()["notified_user_id"] is None
    assert not [n for n in store.notifications if n["kind"] == "kt_request"]
    assert _plan(client, MANAGER)["sessions"][1]["booked"]["status"] == "scheduled"
