"""The shared knowledge core (app/services/knowledge.py) that knowledge risk, the KT planner and the code tour build on.

Seed numbers are read from the real demo seed (MODE=demo): 4 code files, 60 lines, 18 recorded, pace 250 s / 18 lines.
"""
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from app.adapters.memory import MemoryIndex, MemoryStore
from app.agents.core.schemas import Chunk, Citation, Hit, KTLink, QAResult, Utterance
from app.config import Settings
from app.container import build_container
from app.ports import Onboarding, Project, TaskRow, User
from app.services import knowledge as K
from app.services.chunking import body_end, chunk_code, chunk_document, chunk_transcript
from app.services.codeowners import has_owners, owners_for, parse_codeowners, rule_for
from app.services.embeddings import HashEmbedder

from tests.conftest import SAMPLE

PID, ORG = "proj-demo", "org-demo"


@pytest.fixture
def demo():
    """A fresh seeded demo container per test (building one takes milliseconds), so tests that write stay isolated."""
    return build_container(Settings(mode="demo", gemini_api_key=""))


def _link(path, symbol=None, start_line=None, end_line=None, start_s=0.0, end_s=10.0, confidence=0.9, stale=False, meeting="m1"):
    return KTLink(meeting_id=meeting, meeting_title="KT #9: test", file_path=path, symbol=symbol, start_line=start_line, end_line=end_line,
                  start_s=start_s, end_s=end_s, confidence=confidence, method="path", code_changed_since=stale)


def _func(name, body_lines):
    return [f"def {name}():"] + [f"    x{i} = {i}" for i in range(body_lines - 1)]


# ---------------------------------------------------------------- Index.code_chunks
def test_code_chunks_memory_excludes_stale_and_content_flag():
    index = MemoryIndex(HashEmbedder())
    b = "\n".join(["import os", "", *_func("first", 5), "", "", "", *_func("second", 4)])  # symbols at lines 3 and 11
    a = "\n".join(_func("only", 3))
    chunks = chunk_code("p1", "pkg/b.py", b) + chunk_code("p1", "pkg/a.py", a)
    index.upsert(chunks)
    index.upsert(chunk_document("p1", "d1", "Guide", ["Run the tests before a PR."]))
    index.upsert(chunk_transcript("p1", "m1", "KT", [Utterance(start_s=0, end_s=5, speaker="Ravi", text="hello")]))
    index.upsert(chunk_code("p2", "pkg/z.py", a))  # another project
    stale = next(c for c in chunks if c.symbol == "second")
    index.chunks[stale.id] = stale.model_copy(update={"stale": True})

    lean = index.code_chunks("p1", content=False)
    assert [(c.ref["path"], c.symbol, c.ref["start_line"]) for c in lean] == [
        ("pkg/a.py", "only", 1), ("pkg/b.py", None, 1), ("pkg/b.py", "first", 3)]  # stale 'second', docs, meetings, p2 left out
    assert all(c.content == "" and c.source_type == "code" for c in lean)
    assert index.chunks[lean[0].id].content  # the stored chunk keeps its content: content=False copies
    full = index.code_chunks("p1")
    assert [c.id for c in full] == [c.id for c in lean]
    assert "def only():" in full[0].content


# ---------------------------------------------------------------- code_files
def test_symbol_spans_merge_split_function():
    index = MemoryIndex(HashEmbedder())
    text = "\n".join(_func("huge", 300) + ["", *_func("after", 3)])
    chunks = chunk_code("p1", "pkg/big.py", text)
    assert [(c.symbol, c.ref["start_line"], c.ref["end_line"]) for c in chunks] == [
        ("huge", 1, 120), ("huge", 121, 240), ("huge", 241, 300), ("after", 302, 304)]
    index.upsert(chunks)
    spans = K.code_files(index, "p1")["pkg/big.py"]
    assert [(s.symbol, s.start_line, s.end_line, s.lines) for s in spans] == [("huge", 1, 300, 300), ("after", 302, 304, 3)]
    assert spans[0].path == "pkg/big.py" and spans[0].key == "pkg/big.py::huge"


def test_code_files_ignore_root_docs_tests_and_symbolless(demo):
    files = K.code_files(demo.index, PID)
    # a span is the symbol's own lines: Rule stops before Policy's @dataclass (L15), do_POST before the __main__ guard (L33-34)
    assert {p: [(s.symbol, s.start_line, s.end_line) for s in spans] for p, spans in files.items()} == {
        "engine/mutate.py": [("apply_defaults", 6, 11)],
        "engine/policy.py": [("Rule", 8, 12), ("Policy", 16, 19), ("load_policy", 22, 26)],
        "engine/validate.py": [("ValidationError", 5, 6), ("validate_resource", 9, 17), ("check_labels", 20, 22), ("check_privileged", 25, 33)],
        "webhooks/server.py": [("handle_admission", 12, 21), ("Handler", 24, 24), ("do_POST", 25, 30)],
    }
    assert sum(s.lines for s in K.all_spans(files)) == 60

    index = MemoryIndex(HashEmbedder())
    py = "\n".join(_func("f", 3))
    for path in ("setup.py", "docs/conf.py", "tests/test_x.py", "src/test_y.py", "src/y_test.go", "src/__tests__/z.js", "src/app.py"):
        index.upsert(chunk_code("p1", path, py if path.endswith(".py") else "func F() {\n}\n" if path.endswith(".go") else "function f() {}\n"))
    index.upsert(chunk_code("p1", "deploy/app.yaml", "a: 1\nb: 2\n"))  # no symbols
    assert list(K.code_files(index, "p1")) == ["src/app.py"]


# ---------------------------------------------------------------- span state, pace, line totals
def test_span_state_matches_symbol_lines_or_whole_file():
    span = K.Span("check", 10, 20, path="a.py")
    assert span.lines == 11
    assert K.span_state([_link("a.py", "check", end_s=30)], span) == ("recorded", 30.0, [_link("a.py", "check", end_s=30)])
    assert K.span_state([_link("a.py", None, 18, 25)], span).state == "recorded"  # lines overlap
    assert K.span_state([_link("a.py", "other", 21, 25)], span).state == "none"  # another symbol, no overlap
    assert K.span_state([_link("a.py")], span).state == "recorded"  # file-wide
    assert K.span_state([_link("a.py", "other")], span).state == "none"  # another symbol without lines is not file-wide
    assert K.span_state([_link("a.py", "check", confidence=0.4)], span).state == "none"  # a guess explains nothing
    assert K.span_state([_link("b.py", "check")], span).state == "none"  # another file
    st = K.span_state([_link("a.py", "check", end_s=10, stale=True), _link("a.py", None, 12, 14, start_s=5, end_s=25, stale=True)], span)
    assert st.state == "stale" and st.kt_seconds == 30.0 and len(st.links) == 2
    assert K.span_state([_link("a.py", "check", stale=True), _link("a.py", "check")], span).state == "recorded"


def test_pace_measured_and_default(demo):
    assert K.pace_s_per_line(demo.store.kt_links(PID)) == (13.89, False)  # (35 + 127 + 88) s over (9 + 3 + 6) lines
    assert K.pace_s_per_line([_link("a.py", "f", end_s=60)]) == (12.0, True)  # no line range
    assert K.pace_s_per_line([_link("a.py", "f", 1, 10, end_s=600, confidence=0.3)]) == (12.0, True)  # too unsure to count
    assert K.pace_s_per_line([]) == (12.0, True)
    assert K.pace_s_per_line([_link("a.py", "f", 1, 10, end_s=300)]) == (30.0, False)


def test_line_totals_give_stale_half_credit(demo):
    files = K.code_files(demo.index, PID)
    totals = K.line_totals(files, demo.store.kt_links(PID))
    assert totals == (60, 18, 0) and round(totals.share, 2) == 0.3
    demo.store.mark_kt_code_changed(PID, ["engine/validate.py"])
    validate = {"engine/validate.py": files["engine/validate.py"]}
    v = K.line_totals(validate, demo.store.kt_links(PID))
    assert v == (23, 0, 12) and round(v.share, 2) == 0.26
    after = K.line_totals(validate, demo.store.kt_links(PID), assume_recorded=[files["engine/validate.py"][3]])  # + check_privileged
    assert after == (23, 9, 12)
    assert K.recorded_share(0, 0, 0) == 0.0


# ---------------------------------------------------------------- owners
def test_rule_for_returns_last_match():
    rules = parse_codeowners((SAMPLE / ".github/CODEOWNERS").read_text())
    assert rule_for(rules, "engine/validate.py") == ("/engine/", ["ravi-dev"])
    assert rule_for(rules, "README.md") == ("*", ["priya-mgr"])
    assert rule_for(parse_codeowners("/engine/ @a\n"), "webhooks/server.py") is None
    assert rule_for([], "a.py") is None


def test_ownerless_pattern_is_kept_and_unowns_its_paths():
    """GitHub: a pattern with no owners means "no code owner" for the paths it matches, and as the last match it
    overrides a broader rule. A later rule with owners takes the path back."""
    rules = parse_codeowners("# owners\n/engine/ @ravi-dev\n/engine/policy.py   # nobody yet\n/webhooks/ @meera-dev\n/engine/*.md\n/engine/README.md @priya\n")
    assert rules == [("/engine/", ["ravi-dev"]), ("/engine/policy.py", []), ("/webhooks/", ["meera-dev"]), ("/engine/*.md", []),
                     ("/engine/README.md", ["priya"])]
    assert owners_for(rules, "engine/policy.py") == [] and rule_for(rules, "engine/policy.py") == ("/engine/policy.py", [])
    assert owners_for(rules, "engine/validate.py") == ["ravi-dev"]  # the broader rule still owns its other files
    assert owners_for(rules, "engine/NOTES.md") == [] and owners_for(rules, "engine/README.md") == ["priya"]  # last match wins
    assert owners_for(rules, "webhooks/server.py") == ["meera-dev"] and owners_for(rules, "setup.py") == []
    assert has_owners(rules) and not has_owners(parse_codeowners("/engine/\n*.md\n")) and not has_owners([])
    assert parse_codeowners("") == [] and parse_codeowners(None) == []


def test_owners_index_leaves_an_ownerless_path_unowned(demo):
    store, calls = demo.store, []
    real = store.users_by_github
    store.users_by_github = lambda org, logins: calls.append(sorted(logins)) or real(org, logins)
    store.set_codeowners(PID, "* @priya-mgr\n/engine/ @ravi-dev\n/engine/policy.py\n")
    owners = K.owners_index(store, ORG, PID)
    assert owners("engine/policy.py") == []  # not Ravi's, and not the '*' owner's either
    assert [o.user_id for o in owners("engine/validate.py")] == ["u-ravi"] and [o.user_id for o in owners("README.md")] == ["u-priya"]
    assert calls == [["priya-mgr", "ravi-dev"]]


def test_owners_index_resolves_people_once_and_keeps_teams_as_labels(demo):
    store = demo.store
    owners = K.owners_index(store, ORG, PID)
    assert owners("engine/policy.py") == [K.Owner("ravi-dev", "u-ravi", "Ravi (Senior)", False, "/engine/ @ravi-dev")]
    assert owners("README.md")[0].user_id == "u-priya" and owners("README.md")[0].rule == "* @priya-mgr"

    calls = []
    real = store.users_by_github
    store.users_by_github = lambda org, logins: calls.append(sorted(logins)) or real(org, logins)
    store.set_codeowners(PID, "/engine/ @org/platform\n/webhooks/ @Meera-Dev @ghost dev@corp.com\n")
    owners = K.owners_index(store, ORG, PID)
    [team] = owners("engine/validate.py")
    assert team == K.Owner("org/platform", None, "@org/platform", True, "/engine/ @org/platform")
    meera, ghost, email = owners("webhooks/server.py")
    assert (meera.user_id, meera.team, meera.rule) == ("u-meera", False, "/webhooks/ @Meera-Dev @ghost dev@corp.com")
    assert (ghost.user_id, ghost.name, ghost.team) == (None, "@ghost", False)
    assert (email.login, email.user_id, email.name, email.team) == ("dev@corp.com", None, "dev@corp.com", False)
    assert owners("README.md") == []
    assert calls == [["Meera-Dev", "ghost"]]  # one lookup for every person handle; teams and emails are not looked up

    store.set_codeowners(PID, "")
    assert K.owners_index(store, ORG, PID)("engine/policy.py") == []


# ---------------------------------------------------------------- speakers
def test_dominant_speaker_maps_to_member(demo):
    store = demo.store
    members = store.members(PID)
    notes = store.meeting_notes("mtg-1")
    label = K.dominant_speaker(notes.utterances, 55, 182)  # check_labels: Ravi 40 + 42 s, Vijay 38 s
    assert label == "Ravi" and K.member_for_speaker(members, label).id == "u-ravi"
    assert K.member_for_speaker(members, "Speaker 1") is None
    assert K.member_for_speaker(members, "meera iyer").id == "u-meera"  # unique first token
    assert K.member_for_speaker(members, "ravi-dev").id == "u-ravi"  # GitHub login
    assert K.member_for_speaker(members, "") is None
    twins = members + [User(id="u-r2", org_id=ORG, email="r2@x", name="Ravi (Intern)", role="new_hire")]
    assert K.member_for_speaker(twins, "Ravi") is None  # two Ravis: not guessed
    assert K.dominant_speaker(notes.utterances, 1000, 1100) is None

    speakers = K.Speakers(store, members)
    links = store.kt_links(PID)
    link = K.longest_link(links, "engine/validate.py")
    assert link.symbol == "check_labels"  # 127 s, longer than validate_resource's 35 s
    label, user = speakers.for_link(link)
    assert label == "Ravi" and user.id == "u-ravi"
    assert K.longest_link(links, "engine/policy.py") is None  # never shown on a call
    assert K.longest_link(links, "engine/policy.py", same_module=True).symbol == "check_labels"
    assert K.longest_link(links, "webhooks/server.py", same_module=True) is None
    assert speakers.seconds_by_user(["mtg-1"]) == {"u-ravi": 204.0, "u-vijay": 38.0}  # Ravi: 3.4 minutes


def test_meeting_notes_read_once_per_request(demo):
    store, reads = demo.store, []
    real = store.meeting_notes
    store.meeting_notes = lambda mid: reads.append(mid) or real(mid)
    notes = K.NotesCache(store)
    speakers = K.Speakers(store, store.members(PID), notes)
    for link in store.kt_links(PID):
        speakers.for_link(link)
    K.demand(store, demo.index, PID, K.code_files(demo.index, PID), store.kt_links(PID), notes=notes)
    assert reads == ["mtg-1"]


# ---------------------------------------------------------------- demand
def _by_kind(reasons):
    out = {}
    for r in reasons:
        out.setdefault(r.kind, []).append(r)
    return out


def test_demand_signals_seed(demo):
    store, index = demo.store, demo.index
    files = K.code_files(index, PID)
    reasons = K.demand(store, index, PID, files, store.kt_links(PID))
    kinds = _by_kind(reasons)
    tasks = {t.origin: t for t in store.tasks(project_id=PID)}

    [first_pr] = kinds["first_pr"]
    assert (first_pr.path, first_pr.symbol, first_pr.weight, first_pr.start_line) == ("engine/validate.py", "check_privileged", 3, 25)
    assert first_pr.pending_approval is True and first_pr.due == tasks["first_pr"].due_date and first_pr.ref == {"task_id": tasks["first_pr"].id}
    assert first_pr.text == "First PR for Vijay: Validate privileged init containers (issue #12), 41% match"

    [task] = kinds["task"]
    assert (task.path, task.symbol, task.weight, task.pending_approval) == ("webhooks/server.py", None, 2, True)
    assert task.due == tasks["coverage_gap"].due_date
    assert task.text == f"Vijay's task Read and summarise the webhooks module, due {K.fmt_day(task.due)} (awaiting approval)"

    [action] = kinds["action_item"]
    assert (action.path, action.symbol, action.weight) == ("engine/policy.py", None, 2)
    assert action.ref == {"meeting_id": "mtg-1", "at_s": 232.0}
    assert action.text == "Action item from KT #1 at 3:52: Vijay to read engine/policy.py before Friday"

    [named] = kinds["named_not_shown"]
    assert (named.path, named.symbol, named.weight, named.ref) == ("engine/validate.py", "check_privileged", 1, {"meeting_id": "mtg-1"})
    assert named.text == "Named in the KT #1 summary but never shown on screen"

    [gap] = kinds["gap"]
    assert (gap.path, gap.symbol, gap.weight, gap.ref) == ("webhooks/server.py", None, 1, {"module": "webhooks"})
    assert gap.text == "Module webhooks has never been explained in a KT session"

    assert set(kinds) == {"first_pr", "task", "action_item", "named_not_shown", "gap"}  # no questions and nothing stale yet
    weights = {}
    for r in reasons:
        weights[r.path] = weights.get(r.path, 0) + r.weight
    assert weights == {"webhooks/server.py": 3, "engine/policy.py": 2, "engine/validate.py": 4}

    # how the two features attach reasons to spans
    check_privileged = files["engine/validate.py"][3]
    assert first_pr.on(check_privileged) and not first_pr.file_level
    assert task.file_level and all(task.applies_to(s) for s in files["webhooks/server.py"]) and not task.on(files["webhooks/server.py"][0])
    d = first_pr.as_dict()
    assert set(d) == {"path", "symbol", "kind", "text", "weight", "due", "pending_approval", "ref", "start_line"} and isinstance(d["due"], date)


def test_unanchored_question_not_attributed(demo):
    out = demo.handle({"type": "chat.question", "project_id": PID, "user_id": "u-vijay", "question": "How do I deploy to production?"})
    assert out["result"]["status"] == "not_enough_information"
    reasons = K.demand(demo.store, demo.index, PID, K.code_files(demo.index, PID), demo.store.kt_links(PID))
    assert [r for r in reasons if r.kind == "question"] == []  # its best code hit is below 0.2 similarity


def test_answered_question_without_clip_marks_the_cited_span(demo):
    q = "How does the admission webhook handle a request?"
    out = demo.handle({"type": "chat.question", "project_id": PID, "user_id": "u-vijay", "question": q})
    assert out["result"]["status"] == "answered" and out["result"]["kt_clips"] == []
    reasons = K.demand(demo.store, demo.index, PID, K.code_files(demo.index, PID), demo.store.kt_links(PID))
    [question] = [r for r in reasons if r.kind == "question"]  # it also cites the file header: no extra file-level reason
    assert (question.path, question.symbol, question.weight) == ("webhooks/server.py", "handle_admission", 1)
    assert question.ref == {"message_id": out["result"]["message_id"]}
    assert question.text == K.QUESTION_ANSWERED_TEXT and K.question_answered(question)  # never who asked, never the words
    assert "Vijay" not in question.text and "admission webhook" not in question.text and question.expert_text is None
    assert sum(r.weight for r in reasons if r.path == "webhooks/server.py") == 4


def test_unanswered_question_anchored_by_search_weighs_two(demo):
    store, index = demo.store, demo.index
    from app.agents.core.schemas import QAResult

    mid = store.save_chat(PID, "u-vijay", "Why does check_privileged skip init containers?", QAResult(status="not_enough_information", answer="?"))
    store.save_chat(PID, "u-priya", "Why does check_labels exist?", QAResult(status="not_enough_information", answer="?"))  # not a new hire
    files = K.code_files(index, PID)
    reasons = K.demand(store, index, PID, files, store.kt_links(PID))
    [question] = [r for r in reasons if r.kind == "question"]
    assert (question.symbol, question.weight, question.ref) == ("check_privileged", 2, {"message_id": mid})
    assert [r for r in K.demand(store, index, PID, files, store.kt_links(PID), max_question_searches=0) if r.kind == "question"] == []


def test_stale_link_gives_stale_reason(demo):
    demo.store.mark_kt_code_changed(PID, ["engine/mutate.py"])
    reasons = K.demand(demo.store, demo.index, PID, K.code_files(demo.index, PID), demo.store.kt_links(PID))
    [stale] = [r for r in reasons if r.kind == "stale"]
    assert (stale.path, stale.symbol, stale.weight, stale.ref) == ("engine/mutate.py", "apply_defaults", 1, {"meeting_id": "mtg-1", "at_s": 182.0})
    assert stale.text == "Explained in KT #1 at 3:02, but the code changed since"


def test_done_rejected_and_inactive_tasks_add_no_demand(demo):
    store = demo.store
    for t in store.tasks(project_id=PID):
        store.update_task(t.id, status="done" if t.origin == "first_pr" else "rejected")
    store.add_onboarding(Onboarding(id="ob-old", project_id=PID, new_hire_id="u-meera", manager_id="u-priya", start_date=date(2020, 1, 1),
                                    status="completed"))
    store.task_rows["t-old"] = TaskRow(id="t-old", onboarding_id="ob-old", title="Read engine", description="", kind="learn",
                                       status="approved", related_paths=["engine/"])
    assert K.open_tasks(store, PID) == []
    reasons = K.demand(store, demo.index, PID, K.code_files(demo.index, PID), store.kt_links(PID))
    assert not [r for r in reasons if r.kind in {"task", "first_pr"}]
    store.update_task(store.tasks(onboarding_id="ob-vijay")[0].id, status="approved")
    [(task, ob)] = K.open_tasks(store, PID)
    assert ob.id == "ob-vijay" and task.status == "approved"


def test_task_related_paths_prefix_match_files_only():
    store = MemoryStore()
    store.add_project(Project(id="p1", org_id="o1", name="P"))
    store.add_user(User(id="u-h", org_id="o1", email="h@x", name="Nia Rao", role="new_hire"), "p1")
    store.add_onboarding(Onboarding(id="ob1", project_id="p1", new_hire_id="u-h", manager_id="u-m", start_date=date(2026, 1, 1)))
    store.task_rows["t1"] = TaskRow(id="t1", onboarding_id="ob1", title="Learn the API", description="", kind="learn", status="approved",
                                    related_paths=["./api/", "web"], due_date=date(2026, 1, 9))
    index = MemoryIndex(HashEmbedder())
    for path in ("api/routes.py", "api_old/routes.py", "web/app.py", "webhooks/hook.py"):
        index.upsert(chunk_code("p1", path, "\n".join(_func("handler", 3))))
    files = K.code_files(index, "p1")
    tasks = [r for r in K.demand(store, index, "p1", files, []) if r.kind == "task"]
    assert [(r.path, r.pending_approval, r.text) for r in tasks] == [
        ("api/routes.py", False, "Nia's task Learn the API, due 9 Jan"), ("web/app.py", False, "Nia's task Learn the API, due 9 Jan")]


def _ask(store, user_id, question, at, status="not_enough_information"):
    """A chat row as the QA agent saves it, at a fixed time so ordering is exact."""
    mid = store.save_chat(PID, user_id, question, QAResult(status=status, answer=""))
    store.chats[mid]["created_at"] = at
    return mid


def test_newest_unanswered_orders_across_hires():
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    vijay = [{"id": "m1", "question": "a?", "status": "not_enough_information", "created_at": t0},
             {"id": "m2", "question": "b?", "status": "answered", "created_at": t0 + timedelta(hours=5)},  # answered: no search
             {"id": "m3", "question": "  ", "status": "not_enough_information", "created_at": t0 + timedelta(hours=6)},  # blank
             {"id": "m4", "question": "c?", "status": "not_enough_information", "created_at": (t0 + timedelta(hours=3)).isoformat()}]
    nina = [{"message_id": "m5", "question": "d?", "status": "not_enough_information", "created_at": t0 + timedelta(hours=2)},
            {"message_id": "m6", "question": "e?", "status": "not_enough_information", "created_at": None}]  # undated: oldest
    assert K.newest_unanswered([vijay, nina], 2) == {"m4", "m5"}
    assert K.newest_unanswered([nina, vijay, vijay], 3) == {"m4", "m5", "m1"}  # store order and a repeated history change nothing
    assert K.newest_unanswered([vijay, nina], 0) == set() and len(K.newest_unanswered([vijay, nina], 99)) == 4
    tie = [{"id": i, "question": "q?", "status": "not_enough_information", "created_at": t0} for i in ("m-a", "m-c", "m-b")]
    assert K.newest_unanswered([tie], 2) == {"m-c", "m-b"}  # equal times: the message id decides, the same way every time


def test_question_cap_is_newest_first_across_hires(demo):
    """demand() spends max_question_searches on the newest unanswered questions of ALL active hires, not hire by hire in
    whatever order the store returns the onboardings, and lists them newest first across hires in either order."""
    store = demo.store
    store.add_user(User(id="u-nina", org_id=ORG, email="nina@demo.dev", name="Nina Rao", role="new_hire", github_login="nina"), PID)
    store.add_onboarding(Onboarding(id="ob-a-nina", project_id=PID, new_hire_id="u-nina", manager_id="u-priya", start_date=date.today()))
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    q = "How does the admission webhook handle a request?"
    ids = {n: _ask(store, who, f"{q} ({n})", t0 + timedelta(minutes=n))
           for n, who in enumerate(["u-vijay", "u-nina", "u-vijay", "u-vijay", "u-nina", "u-vijay"])}
    files, links = K.code_files(demo.index, PID), store.kt_links(PID)
    asked, real_search, real_obs = [], demo.index.search, store.onboardings
    demo.index.search = lambda pid, query, k=8, st=None: asked.append(query) or real_search(pid, query, k, st)
    for order in (lambda obs: obs, lambda obs: list(reversed(obs))):
        store.onboardings = lambda pid, active_only=True, _o=order: _o(real_obs(pid, active_only))
        asked.clear()
        reasons = K.demand(store, demo.index, PID, files, links, max_question_searches=3)
        assert sorted(a for a in asked if a.startswith(q)) == [f"{q} ({n})" for n in (3, 4, 5)]  # Vijay's two and Nina's newest
        assert [r.ref["message_id"] for r in reasons if r.kind == "question"] == [ids[5], ids[4], ids[3]]  # Vijay, Nina, Vijay
        assert all(r.weight == K.UNANSWERED_QUESTION_WEIGHT for r in reasons if r.kind == "question")


# ---------------------------------------------------------------- SafeSearch
class _Searcher:
    """An index whose search can be made to fail, recording every live search."""

    def __init__(self):
        self.calls, self.fail = [], False

    def search(self, project_id, query, k=8, source_types=None):
        self.calls.append((project_id, query, k))
        if self.fail:
            raise ConnectionError("Vertex AI embeddings unavailable")
        return [f"hit:{project_id}:{query}"]

    def all_paths(self, project_id):
        return ["a.py"]


def test_safe_search_remembers_across_requests():
    index = _Searcher()
    first = K.SafeSearch(index, "p1", "fp-1")
    assert first.search("p1", "how?", 1, ["code"]) == ["hit:p1:how?"] and first.searched == 1
    again = K.SafeSearch(index, "p1", "fp-1")  # the next request, same code
    assert again.search("p1", "how?", 1, ["code"]) == ["hit:p1:how?"]
    assert (len(index.calls), again.searched, again.partial) == (1, 0, False)
    again.search("p1", "how?", 3, ["code"])  # k is part of the question
    again.search("p2", "how?", 1, ["code"])  # another project's search is never filed under this project's code
    assert len(index.calls) == 3
    K.SafeSearch(index, "p1", "fp-1").search("p2", "how?", 1, ["code"])
    assert len(index.calls) == 4
    K.SafeSearch(_Searcher(), "p1", "fp-1").search("p1", "how?", 1, ["code"])  # another index has its own memo
    assert len(index.calls) == 4
    assert again.all_paths("p1") == ["a.py"]  # every other Index method goes straight to the index


def test_safe_search_forgets_when_the_code_changes(monkeypatch):
    index = _Searcher()
    K.SafeSearch(index, "p1", "fp-1").search("p1", "q")
    K.SafeSearch(index, "p1", "fp-2").search("p1", "q")  # a push re-indexed the code
    assert len(index.calls) == 2
    K.SafeSearch(index, "p1", "fp-2").search("p1", "q")
    assert len(index.calls) == 2

    late = K.SafeSearch(index, "p1", "fp-2")  # a request that read the code before the next re-index...
    K.SafeSearch(index, "p1", "fp-3")  # ...and one that read it after
    late.search("p1", "new question")
    K.SafeSearch(index, "p1", "fp-3").search("p1", "new question")
    assert len(index.calls) == 4  # the late request's answer was not filed under the newer code

    K.SafeSearch(index, "p1", "fp-3").search("p1", "q")
    monkeypatch.setattr(time, "monotonic", lambda: 10.0 ** 9)  # an answer depends only on the code and the query: no expiry
    K.SafeSearch(index, "p1", "fp-3").search("p1", "q")
    assert len(index.calls) == 5


def test_safe_search_degrades_and_never_remembers_a_failure():
    index = _Searcher()
    K.SafeSearch(index, "p1", "fp").search("p1", "known")  # an earlier request's answer
    index.fail = True
    s = K.SafeSearch(index, "p1", "fp")
    assert s.search("p1", "a") == [] and (s.skipped, s.partial) == (1, True)
    assert s.search("p1", "b") == [] and s.skipped == 2  # the first failure turns live searches off for the request...
    assert [c[1] for c in index.calls] == ["known", "a"]
    assert s.search("p1", "known") == ["hit:p1:known"]  # ...but a remembered answer is still given
    index.fail = False
    t = K.SafeSearch(index, "p1", "fp")  # the next request tries again: the failure was not remembered
    assert t.search("p1", "a") == ["hit:p1:a"] and not t.partial


def test_safe_search_time_budget(monkeypatch):
    index = _Searcher()
    K.SafeSearch(index, "p1", "fp").search("p1", "known")
    s = K.SafeSearch(index, "p1", "fp", budget_s=0.0)
    assert s.search("p1", "new") == [] and s.partial and len(index.calls) == 1  # out of time: not even tried
    assert s.search("p1", "known") == ["hit:p1:known"]  # a remembered answer costs no time
    monkeypatch.setattr(K, "SEARCH_BUDGET_S", 0.0)  # the default budget is read when the request starts
    assert K.SafeSearch(index, "p1", "fp").search("p1", "other") == [] and len(index.calls) == 1


def test_safe_search_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(K, "SEARCH_MEMO_MAX", 3)
    index = _Searcher()
    s = K.SafeSearch(index, "p1", "fp")
    for q in "abcde":
        s.search("p1", q)
    for q in "cde":
        K.SafeSearch(index, "p1", "fp").search("p1", q)
    assert len(index.calls) == 5  # the three most recent answers are remembered
    K.SafeSearch(index, "p1", "fp").search("p1", "a")
    assert len(index.calls) == 6  # the least recently used was dropped
    for n in range(4):
        K.SafeSearch(index, f"p-{n}", "fp")  # projects are bounded too: p1 drops out, and its answers with it
    memo = K._search_memos[index]
    assert list(memo.code) == ["p-1", "p-2", "p-3"] and len(memo.hits) == 0


def test_safe_search_is_thread_safe(monkeypatch):
    monkeypatch.setattr(K, "SEARCH_MEMO_MAX", 16)
    index, errors = _Searcher(), []
    start = threading.Barrier(12)

    def request(n):
        try:
            pid = f"p{n % 3}"
            s = K.SafeSearch(index, pid, f"fp{n % 2}")  # fingerprints flip between requests: forgetting races with storing
            start.wait()
            for q in range(40):
                assert s.search(pid, f"q{q}") == [f"hit:{pid}:q{q}"]
        except Exception as e:  # noqa: BLE001 - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=request, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == [] and len(K._search_memos[index].hits) <= 16


class _SlowSearcher(_Searcher):
    """Like _Searcher, but each search takes `delay` seconds, as a Vertex call that hangs before its own timeout does."""

    def __init__(self, delay):
        super().__init__()
        self.delay = delay

    def search(self, project_id, query, k=8, source_types=None):
        out = super().search(project_id, query, k, source_types)
        time.sleep(self.delay)
        return out


def test_safe_search_stops_waiting_at_the_budget():
    """The budget bounds the wait, not only the start: a search already running when the budget ends is abandoned to its
    thread (VertexEmbedder's own 60 s timeout and retries would otherwise hold the request for minutes)."""
    index = _SlowSearcher(1.5)
    s = K.SafeSearch(index, "p1", "fp", budget_s=0.2)
    t0 = time.monotonic()
    assert s.search("p1", "slow") == [] and s.partial and s.reasons == {"budget"}
    assert s.search("p1", "next") == [] and s.skipped == 2  # slow means stop waiting for the rest of the request
    assert time.monotonic() - t0 < 0.6 and len(index.calls) == 1
    time.sleep(1.6)  # the abandoned search finishes on its thread and is remembered for the next request
    t = K.SafeSearch(index, "p1", "fp")
    assert t.search("p1", "slow") == ["hit:p1:slow"] and len(index.calls) == 1


def test_safe_search_breaker_skips_live_searches_after_consecutive_failures(monkeypatch):
    """Two failed requests in a row open the breaker: for BREAKER_OPEN_S every request skips live search at once instead
    of paying for the outage again. Remembered answers are still given; once it closes, one search probes."""
    index = _Searcher()
    K.SafeSearch(index, "p1", "fp").search("p1", "known")
    index.fail = True
    for q in ("a", "b"):
        assert K.SafeSearch(index, "p1", "fp").search("p1", q) == []
    assert len(index.calls) == 3
    s = K.SafeSearch(index, "p1", "fp")
    assert s.search("p1", "c") == [] and s.reasons == {"breaker"} and len(index.calls) == 3  # not tried
    assert s.search("p1", "known") == ["hit:p1:known"]
    index.fail = False
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now + K.BREAKER_OPEN_S + 1)
    probe = K.SafeSearch(index, "p1", "fp")
    assert probe.search("p1", "c") == ["hit:p1:c"] and not probe.partial  # closed again: the probe succeeded
    assert K.SafeSearch(index, "p1", "fp").search("p1", "d") == ["hit:p1:d"]


class _BatchSearcher(_Searcher):
    def __init__(self, delay=0.0):
        super().__init__()
        self.batches, self.delay = [], delay

    def search_many(self, project_id, queries, source_types=None):
        self.batches.append([q for q, _ in queries])
        time.sleep(self.delay)
        return [[f"hit:{project_id}:{q}"] for q, _ in queries]


def test_safe_search_prefetch_is_one_batch_and_concurrent_requests_share_it():
    """A cold page starts all of its searches as one search_many (one embedding call), and a second request that needs
    the same searches while they run waits for them instead of searching again."""
    index = _BatchSearcher(delay=0.3)
    queries = [(f"question {n}", 1) for n in range(6)]
    first, second = K.SafeSearch(index, "p1", "fp"), K.SafeSearch(index, "p1", "fp")
    first.prefetch(queries, ["code"])
    second.prefetch(queries, ["code"])  # already running: joined, not started again
    assert [first.search("p1", q, 1, ["code"]) for q, _ in queries] == [[f"hit:p1:{q}"] for q, _ in queries]
    assert [second.search("p1", q, 1, ["code"]) for q, _ in queries] == [[f"hit:p1:{q}"] for q, _ in queries]
    assert index.batches == [[q for q, _ in queries]] and index.calls == []
    assert first.searched == 6 and not first.partial
    third = K.SafeSearch(index, "p1", "fp")
    third.prefetch(queries, ["code"])
    assert third.search("p1", "question 0", 1, ["code"]) == ["hit:p1:question 0"] and len(index.batches) == 1  # remembered


def test_safe_search_remembers_hits_without_chunk_content():
    class Index:
        def search(self, project_id, query, k=8, source_types=None):
            return [Hit(chunk=Chunk(id="c1", project_id=project_id, source_type="code", symbol="f", content="File: a.py\n" + "x = 1\n" * 120,
                                    ref={"path": "a.py", "start_line": 1, "end_line": 120}), score=1.0, similarity=0.5)]

    index = Index()
    [hit] = K.SafeSearch(index, "p1", "fp").search("p1", "q", 1, ["code"])
    assert hit.chunk.content == "" and hit.chunk.symbol == "f" and hit.chunk.ref["end_line"] == 120 and hit.similarity == 0.5
    assert all(h.chunk.content == "" for hits in K._search_memos[index].hits.values() for h in hits)


def test_partial_request_logs_one_structured_line_and_tracebacks_are_rate_limited(caplog, monkeypatch):
    monkeypatch.setattr(K, "_last_traceback", 0.0)
    index = _Searcher()
    index.fail = True
    with caplog.at_level(logging.INFO, logger="knowledge"):
        for _ in range(3):
            s = K.SafeSearch(index, "p1", "fp")
            s.search("p1", "q")
            s.report("kt-plan")
        healthy = K.SafeSearch(_Searcher(), "p1", "fp")
        healthy.search("p1", "q")
        healthy.report("kt-plan")  # a complete response logs nothing
    partial = [r for r in caplog.records if getattr(r, "extra_fields", {}).get("event") == "knowledge.partial"]
    assert len(partial) == 3
    assert {k: partial[0].extra_fields[k] for k in ("page", "project_id", "searched", "skipped", "reason")} == {
        "page": "kt-plan", "project_id": "p1", "searched": 0, "skipped": 1, "reason": "failure"}
    assert partial[2].extra_fields["reason"] == "breaker" and "elapsed_ms" in partial[2].extra_fields
    assert len([r for r in caplog.records if r.exc_info]) == 1  # two failures, one traceback within the minute


def test_body_end_leaves_out_what_follows_the_symbol():
    lines = ["def f(", "    a,", "):", '    x = """', "text at column 0", '"""', "    # a comment inside", "    return x",
             "# a comment for what follows", "", "@decorator", "def g():", "    pass"]
    assert body_end(lines, 1, 11, "python") == 8  # a multi-line signature and a string at column 0 stay; the rest goes
    js = ["function a() {", "  if (x) { y(); }", "  return 1;", "}", "const z = 1;", "// for b", "@Dec()"]
    assert body_end(js, 1, 7, "javascript") == 4
    assert body_end(["const f = () => 1;", "", "// next", "@Dec"], 1, 4, "typescript") == 1
    chunks = chunk_code("p1", "webhooks/server.py", (SAMPLE / "webhooks/server.py").read_text())
    assert [(c.symbol, c.ref["end_line"], c.ref.get("body_end")) for c in chunks if c.symbol] == [
        ("handle_admission", 21, 21), ("Handler", 24, 24), ("do_POST", 30, 30)]
    # the module code after the last method is a chunk of its own: a citation of do_POST never quotes `if __name__ == ...`
    tail = chunks[-1]
    assert (tail.symbol, tail.ref["start_line"], tail.ref["end_line"]) == (None, 31, 34) and "__main__" in tail.content


def test_answered_question_counts_once_when_its_answer_cites_two_functions(demo):
    """An answer citing handle_admission and do_POST is one question: it lands on the function the question names, else on
    the first cited one, never on both."""
    cite = lambda start, end: Citation(chunk_id=None, source_type="code", label="webhooks/server.py",  # noqa: E731
                                       ref={"path": "webhooks/server.py", "start_line": start, "end_line": end})
    ids = {}
    for q, first in (("How does handle_admission in webhooks/server.py work?", (25, 34)), ("How are requests answered?", (25, 34))):
        ids[q] = demo.store.save_chat(PID, "u-vijay", q, QAResult(status="answered", answer="...", confidence=0.8,
                                                                  citations=[cite(*first), cite(12, 21)]))
    reasons = [r for r in K.demand(demo.store, demo.index, PID, K.code_files(demo.index, PID), demo.store.kt_links(PID)) if r.kind == "question"]
    by_message = {r.ref["message_id"]: (r.symbol, r.weight) for r in reasons}
    assert len(reasons) == 2 and by_message == {ids["How does handle_admission in webhooks/server.py work?"]: ("handle_admission", 1),
                                                ids["How are requests answered?"]: ("do_POST", 1)}


def test_code_fingerprint_reads_no_content_and_follows_the_code():
    index = MemoryIndex(HashEmbedder())
    text = "\n".join(_func("only", 3))
    index.upsert(chunk_code("p1", "pkg/a.py", text, "sha1"))
    before = K.code_fingerprint(index.code_chunks("p1", content=False))
    assert before == K.code_fingerprint(index.code_chunks("p1"))  # bodies are never needed
    index.upsert(chunk_code("p1", "pkg/a.py", text, "sha2"))  # the same lines re-indexed at a new commit
    assert K.code_fingerprint(index.code_chunks("p1", content=False)) != before


def test_mentions_read_paths_and_code_like_symbols():
    files = {
        "engine/policy.py": [K.Span("Policy", 1, 4, path="engine/policy.py"), K.Span("load_policy", 6, 9, path="engine/policy.py")],
        "a/run.py": [K.Span("run_job", 1, 3, path="a/run.py")],
        "b/run.py": [K.Span("run_job", 1, 3, path="b/run.py")],
    }
    paths = sorted(files) + ["README.md"]
    assert K.names_in("Policy loading uses load_policy and `Policy`; Policy( too", ["Policy", "load_policy"]) == ["load_policy", "Policy"]
    assert K.names_in("Policy loading is slow", ["Policy"]) == []  # a plain word must be marked as code
    assert K.mentions("Read engine/policy.py before Friday", paths, files) == [("engine/policy.py", None)]
    assert K.mentions("Read policy.py and README.md", paths, files) == [("engine/policy.py", None)]  # README is not a code file
    assert K.mentions("Walk through load_policy", paths, files) == [("engine/policy.py", files["engine/policy.py"][1])]
    assert K.mentions("Explain run_job", paths, files) == []  # defined in two files and no path says which
    assert K.mentions("Explain run_job in b/run.py", paths, files) == [("b/run.py", files["b/run.py"][0])]  # the symbol replaces the file


def test_text_helpers():
    assert K.fmt_ts(232) == "3:52" and K.fmt_ts(3725) == "1:02:05" and K.fmt_ts(None) == "0:00"
    assert K.short_title("KT #1: How validation works") == "KT #1" and K.short_title("Weekly sync") == "Weekly sync"
    assert K.first_name("Ravi (Senior)") == "Ravi" and K.first_name("Priya Sharma") == "Priya"
    assert K.task_query("First PR: Validate privileged init containers") == "Validate privileged init containers"
    assert K.under_prefixes("webhooks/server.py", ["webhooks"]) and not K.under_prefixes("webhooks_old/x.py", ["webhooks"])
    assert K.is_code_path("engine/validate.py") and not K.is_code_path("pkg/foo_test.py") and not K.is_code_path("docs/x.py")
