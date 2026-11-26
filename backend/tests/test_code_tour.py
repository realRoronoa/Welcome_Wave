"""Code tour: the call graph (app/services/code_tour.build_graph), the per-viewer tour (build_tour) and GET /api/tour.

Seed expectations come from the real demo (MODE=demo, sample_repo + KT #1); synthetic repositories cover the
resolution rules, cycles, split functions, strings, imports and the cache.
"""
from collections import Counter
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.adapters.memory import MemoryIndex, MemoryStore
from app.agents.core.schemas import ActionItem, MeetingResult, ProposedTask
from app.config import Settings, get_settings
from app.container import build_container
from app.main import create_app
from app.ports import Meeting, Onboarding, Project, Repository, User
from app.services import code_tour as T
from app.services import knowledge as K
from app.services.chunking import chunk_code
from app.services.embeddings import HashEmbedder

PID, ORG = "proj-demo", "org-demo"
VALIDATE = "engine/validate.py"


@pytest.fixture(autouse=True)
def fresh_cache():
    T.clear_cache()
    yield
    T.clear_cache()


@pytest.fixture
def demo():
    """A fresh seeded demo container per test, so tests that write stay isolated."""
    return build_container(Settings(mode="demo", gemini_api_key=""))


def tour(c, who="u-priya", order="execution"):
    return T.build_tour(c.store, c.index, PID, ORG, c.store.get_user(who), order)


def stops_of(t, chapter=0):
    return t["chapters"][chapter]["stops"]


def stop(t, key):
    return next(s for ch in t["chapters"] for s in ch["stops"] if s["key"] == key and not s["ref_chapter"])


def mini(files: dict[str, str]):
    """A one-project store and index holding just these files, for build_tour on synthetic code."""
    store, index = MemoryStore(), MemoryIndex(HashEmbedder())
    store.add_project(Project(id="p1", org_id="o1", name="Mini"))
    store.add_user(User(id="u1", org_id="o1", email="m@x.dev", name="Mia", role="manager"), "p1")
    store.add_repo(Repository(id="r1", project_id="p1", full_name="x/mini", last_indexed_sha="abc", status="ready"))
    index.upsert([ch for path, text in files.items() for ch in chunk_code("p1", path, text)])
    return store, index, store.get_user("u1")


def graph_of(files: dict[str, str]) -> T.Graph:
    return T.build_graph([ch for path, text in files.items() for ch in chunk_code("p1", path, text)])


def add_hire(c, uid="u-asha", name="Asha (New hire)", title="First PR: Validate privileged init containers", status="approved", days_ago=1):
    """A second new hire with an active onboarding and one first-PR task."""
    c.store.add_user(User(id=uid, org_id=ORG, email=f"{uid}@demo.dev", name=name, role="new_hire"), PID)
    ob = c.store.add_onboarding(Onboarding(id=f"ob-{uid}", project_id=PID, new_hire_id=uid, manager_id="u-priya",
                                           start_date=date.today() - timedelta(days=days_ago)))
    (task,) = c.store.create_tasks(ob.id, [ProposedTask(title=title, description="d", kind="code", due_in_days=7, source="first_pr",
                                                        issue_number=13)], ob.start_date)
    c.store.update_task(task.id, status=status)
    return task


class Spy:
    """Counts the calls made through it to a store or an index."""

    def __init__(self, target):
        self._target, self.calls = target, Counter()

    def __getattr__(self, name):
        attr = getattr(self._target, name)
        if not callable(attr):
            return attr

        def counted(*args, **kwargs):
            label = name
            if name == "code_chunks":
                label = f"code_chunks(content={kwargs.get('content', args[1] if len(args) > 1 else True)})"
            self.calls[label] += 1
            return attr(*args, **kwargs)
        return counted


# ---------------------------------------------------------------- the seed
def test_graph_edges_follow_call_order(demo):
    g = T.build_graph(demo.index.code_chunks(PID))
    assert g.calls["webhooks/server.py::handle_admission"] == ("engine/mutate.py::apply_defaults", f"{VALIDATE}::validate_resource")
    assert g.calls[f"{VALIDATE}::validate_resource"] == (f"{VALIDATE}::check_labels", f"{VALIDATE}::check_privileged")
    assert g.calls["engine/policy.py::load_policy"] == ("engine/policy.py::Rule", "engine/policy.py::Policy")
    assert g.calls["webhooks/server.py::do_POST"] == ("webhooks/server.py::handle_admission",)  # not Handler: the __main__ tail is module code
    assert g.called_by["engine/policy.py::load_policy"] == ()  # `POLICY = load_policy(...)` at server.py L9 is module-level code
    assert g.called_by[f"{VALIDATE}::validate_resource"] == ("webhooks/server.py::handle_admission",)  # the test's call is not a caller


def test_tour_seed_chapters(demo):
    t = tour(demo)
    assert t["indexed"] and t["sha"] and t["order"] == "execution" and not t["truncated"] and len(t["chapters"]) == 2
    ch1, ch2 = t["chapters"]
    assert ch1["id"] == "ch-1" and ch1["entry"] == "webhooks/server.py::do_POST"
    assert ch1["title"] == "Admission webhook: receives AdmissionReview requests and answers allow/deny."
    assert [s["symbol"] for s in ch1["stops"]] == ["do_POST", "handle_admission", "apply_defaults", "validate_resource", "check_labels", "check_privileged"]
    assert [s["n"] for s in ch1["stops"]] == [1, 2, 3, 4, 5, 6]
    assert [s["depth"] for s in ch1["stops"]] == [0, 1, 2, 2, 3, 3]  # the call tree under do_POST
    assert ch1["stops"][4]["trail"] == ["webhooks/server.py::do_POST", "webhooks/server.py::handle_admission", f"{VALIDATE}::validate_resource"]
    do_post, handle = ch1["stops"][0], ch1["stops"][1]
    assert do_post["container"] == "Handler" and do_post["kind"] == "method"
    assert (do_post["start_line"], do_post["end_line"]) == (25, 30)  # the `if __name__ == "__main__"` tail is not do_POST's
    assert do_post["code"][0].strip() == "def do_POST(self):" and do_post["code"][-1].strip() == "self.wfile.write(out)"
    assert handle["summary"] == "Mutate, then validate. Denies with all violation messages joined."
    assert ch1["stops"][2]["summary"] is None and ch1["stops"][2]["file_summary"] == "Mutation: adds default labels before validation runs."
    assert handle["calls"] == ["engine/mutate.py::apply_defaults", f"{VALIDATE}::validate_resource"]
    assert handle["called_by"] == ["webhooks/server.py::do_POST"]
    assert do_post["owner"] == {"user_id": "u-meera", "name": "Meera (Senior)", "login": "meera-dev", "rule": "/webhooks/ @meera-dev", "team": False}

    assert ch2["title"] == "Policy model and loader."
    assert [s["symbol"] for s in ch2["stops"]] == ["load_policy", "Rule", "Policy"]
    assert [(p["meeting_id"], p["at_s"], p["text"]) for p in ch2["pinned"]] == [("mtg-1", 232.0, "Vijay to read engine/policy.py before Friday")]
    load = ch2["stops"][0]
    assert "action_item" in load["tags"] and load["action_item"]["at_s"] == 232.0 and load["action_item"]["meeting_title"].startswith("KT #1")
    assert not ch1["pinned"] and all(s["action_item"] is None for s in ch2["stops"][1:])


def test_kt_clips_name_the_dominant_speaker(demo):
    t = tour(demo)
    clips = {s["symbol"]: s["kt"] for s in stops_of(t)}
    assert clips["do_POST"] == [] and clips["handle_admission"] == [] and clips["check_privileged"] == []
    (apply,) = clips["apply_defaults"]
    assert (apply["meeting_id"], apply["start_s"], apply["end_s"], apply["speaker"], apply["speaker_user_id"]) == ("mtg-1", 182.0, 270.0, "Ravi", "u-ravi")
    assert apply["meeting_title"] == "KT #1: How validation works" and apply["confidence"] >= 0.5 and not apply["code_changed_since"]
    assert apply["meeting_duration_s"] == 270
    assert [(k["start_s"], k["end_s"]) for k in clips["validate_resource"]] == [(20.0, 55.0)]
    (labels,) = clips["check_labels"]
    assert (labels["start_s"], labels["end_s"], labels["speaker"]) == (55.0, 182.0, "Ravi")  # Vijay asks inside the window; Ravi still dominates


def test_tour_stats_and_unreached(demo):
    t = tour(demo)
    s = t["stats"]
    assert (s["stops"], s["explained"], s["kt_seconds"]) == (9, 3, 250.0)
    assert [(o["user_id"], o["count"]) for o in s["unexplained_by_owner"]] == [("u-ravi", 4), ("u-meera", 2)]
    assert [u["key"] for u in t["unreached"]] == [f"{VALIDATE}::ValidationError"]  # never raised; Handler is folded into do_POST
    assert [(x["key"], x["calls"]) for x in t["tests"]] == [(
        "tests/test_validate.py::test_missing_label_is_reported",
        ["engine/policy.py::Policy", "engine/policy.py::Rule", f"{VALIDATE}::validate_resource"])]
    assert (t["files"], t["symbols"], t["tests_total"], t["unreached_total"]) == (4, 11, 1, 1) and t["hint"] is None


def test_reading_order_is_postorder(demo):
    t = tour(demo, order="reading")
    assert t["order"] == "reading"
    assert [s["symbol"] for s in stops_of(t)] == ["apply_defaults", "check_labels", "check_privileged", "validate_resource", "handle_admission", "do_POST"]
    assert [s["symbol"] for s in stops_of(t, 1)] == ["Rule", "Policy", "load_policy"]
    assert stop(t, "engine/policy.py::load_policy")["action_item"] is not None  # the pin stays on the file's first stop in execution order
    assert t["stats"] == tour(demo)["stats"]


def test_first_pr_tag_visibility(demo):
    target = f"{VALIDATE}::check_privileged"
    hire = tour(demo, "u-vijay")
    assert not any("first_pr_target" in s["tags"] for ch in hire["chapters"] for s in ch["stops"])  # proposed: hidden, like GET /tasks

    mgr = stop(tour(demo, "u-priya"), target)  # a manager sees it while the plan still waits for approval
    assert mgr["tags"] == ["first_pr_target"]
    assert {k: mgr["first_pr"][k] for k in ("issue", "new_hire", "new_hire_id", "status")} == {"issue": 12, "new_hire": "Vijay", "new_hire_id": "u-vijay", "status": "proposed"}
    assert mgr["first_pr"]["match"] >= 0.2
    assert "first_pr_target" in stop(tour(demo, "u-ravi"), target)["tags"]  # experts too

    demo.store.update_task(mgr["first_pr"]["task_id"], status="approved")
    mine = stop(tour(demo, "u-vijay"), target)
    assert mine["tags"] == ["first_pr_target"] and mine["first_pr"]["title"] == "First PR: Validate privileged init containers"

    demo.store.update_task(mgr["first_pr"]["task_id"], status="rejected")
    assert stop(tour(demo, "u-priya"), target)["first_pr"] is None


def test_first_pr_hidden_from_other_hires(demo):
    """One hire never sees another hire's first-PR target, approved or not; managers and experts see both."""
    target = f"{VALIDATE}::check_privileged"
    asha_task = add_hire(demo)  # approved, and it lands where Vijay's proposed one does
    vijay = tour(demo, "u-vijay")
    assert not any(s["first_prs"] or s["first_pr"] or "first_pr_target" in s["tags"] for ch in vijay["chapters"] for s in ch["stops"])

    asha = stop(tour(demo, "u-asha"), target)
    assert [(p["new_hire_id"], p["task_id"], p["status"]) for p in asha["first_prs"]] == [("u-asha", asha_task.id, "approved")]
    assert asha["first_pr"]["new_hire"] == "Asha" and asha["first_pr"]["issue"] == 13

    for who in ("u-priya", "u-ravi"):
        s = stop(tour(demo, who), target)  # both hires are named, the earlier onboarding first, on every request
        assert [p["new_hire"] for p in s["first_prs"]] == ["Vijay", "Asha"] and s["first_pr"]["new_hire"] == "Vijay"
        assert s["tags"] == ["first_pr_target"]

    vijay_task = next(t for t in demo.store.tasks(onboarding_id="ob-vijay") if t.origin == "first_pr")
    demo.store.update_task(vijay_task.id, status="approved")
    mine = stop(tour(demo, "u-vijay"), target)
    assert [p["new_hire_id"] for p in mine["first_prs"]] == ["u-vijay"]  # his own now, still never Asha's


def test_first_prs_follow_onboarding_order(demo):
    add_hire(demo, uid="u-dev", name="Dev Patel", days_ago=30)  # started before Vijay: first in onboarding order
    vijay_task = next(t for t in demo.store.tasks(onboarding_id="ob-vijay") if t.origin == "first_pr")
    demo.store.update_task(vijay_task.id, status="approved")
    target = f"{VALIDATE}::check_privileged"
    assert [p["new_hire"] for p in stop(tour(demo, "u-priya"), target)["first_prs"]] == ["Dev", "Vijay"]
    assert [p["new_hire"] for p in stop(tour(demo, "u-dev"), target)["first_prs"]] == ["Dev"]


def test_same_day_hires_are_ordered_by_name_not_id(demo):
    """A cohort that starts on one day is listed by name: onboarding ids are random (12 hex digits here, UUIDs in
    Postgres), so ordering by id would list the same people differently on each adapter."""
    add_hire(demo, uid="u-1", name="Zara Khan", days_ago=20)  # ob-u-1 sorts first by id... (both started before Vijay, day 12)
    add_hire(demo, uid="u-2", name="Amir Shah", days_ago=20)  # ...but Amir comes first by name
    target = f"{VALIDATE}::check_privileged"
    assert [p["new_hire"] for p in stop(tour(demo, "u-priya"), target)["first_prs"]] == ["Amir", "Zara", "Vijay"]


def test_search_outage_drops_only_the_first_pr_target(demo):
    target = f"{VALIDATE}::check_privileged"
    healthy = tour(demo)
    assert healthy["partial"] is False and stop(healthy, target)["first_pr"]["new_hire_id"] == "u-vijay"

    def down(*a, **kw):
        raise ConnectionError("Vertex AI embeddings unavailable")

    K.clear_search_memo()  # an outage with nothing remembered: the first-PR wording must be searched
    real, demo.index.search = demo.index.search, down
    t = tour(demo)
    assert t["partial"] is True and stop(t, target)["first_pr"] is None and stop(t, target)["tags"] == []
    assert t["stats"] == healthy["stats"] and [len(ch["stops"]) for ch in t["chapters"]] == [len(ch["stops"]) for ch in healthy["chapters"]]
    demo.index.search = real
    assert tour(demo) == healthy  # the next request searches again: a failure is never remembered


def test_ownerless_codeowners_line_leaves_stops_without_owner(demo):
    demo.store.set_codeowners(PID, "*  @priya-mgr\n/engine/ @ravi-dev\n/webhooks/ @meera-dev\n/engine/policy.py\n")
    t = tour(demo)
    assert stop(t, "engine/policy.py::load_policy")["owner"] is None  # neither Ravi's nor the '*' owner's: GitHub's rule
    assert stop(t, f"{VALIDATE}::check_privileged")["owner"]["user_id"] == "u-ravi"
    assert [(o["user_id"], o["count"]) for o in t["stats"]["unexplained_by_owner"]] == [("u-meera", 2), ("u-ravi", 1)]


def test_owner_logins_are_case_insensitive(demo):
    demo.store.set_codeowners(PID, "*  @priya-mgr\n/engine/ @ravi-dev\n/engine/policy.py @Ravi-Dev\n/webhooks/ @meera-dev\n")
    rows = tour(demo)["stats"]["unexplained_by_owner"]
    assert [(o["user_id"], o["count"]) for o in rows] == [("u-ravi", 4), ("u-meera", 2)]  # one row per person, however the rule spells them

    demo.store.set_codeowners(PID, "/engine/validate.py @Ghost\n/engine/policy.py @ghost\n")
    rows = tour(demo)["stats"]["unexplained_by_owner"]
    assert [(o["user_id"], o["login"].lower(), o["count"]) for o in rows] == [(None, "ghost", 4)]  # an unknown login too


def test_warm_request_reads_each_table_once(demo):
    """A cached graph means no code bodies, no code search and no meeting notes; every table is read once."""
    add_hire(demo)
    for n in range(4):
        mid = f"mtg-x{n}"
        demo.store.add_meeting(Meeting(id=mid, project_id=PID, title=f"KT #{n + 2}: Extra", room=f"kt-x{n}", status="processing", duration_s=60))
        demo.store.save_meeting_result(MeetingResult(meeting_id=mid, project_id=PID, title=f"KT #{n + 2}: Extra", summary=f"s{n}", decisions=[],
                                                     action_items=[ActionItem(text=f"Read engine/mutate.py, part {n}", at_s=10)],
                                                     terms=[], utterances=[], screen_moments=[], kt_links=[]))
    store, index = Spy(demo.store), Spy(demo.index)
    viewer = demo.store.get_user("u-priya")
    cold = T.build_tour(store, index, PID, ORG, viewer)
    # one search per first-PR wording: Asha's task reads like Vijay's, so knowledge.SafeSearch answers it from the first
    assert index.calls["code_chunks(content=True)"] == 1 and index.calls["search"] == 1

    store.calls.clear()
    index.calls.clear()
    warm = T.build_tour(store, index, PID, ORG, viewer)
    assert warm == cold
    assert dict(index.calls) == {"code_version": 1}  # the cache key is one aggregate read: no chunk at all
    assert store.calls["meeting_notes"] == 0 and store.calls["get_user"] == 0
    assert all(store.calls[m] == 1 for m in ("tasks", "onboardings", "list_meetings", "members", "kt_links", "repos_for", "codeowners"))
    assert [p["text"] for p in warm["chapters"][0]["pinned"]] == [f"Read engine/mutate.py, part {n}" for n in range(4)]

    demo.store.save_meeting_result(MeetingResult(meeting_id="mtg-x0", project_id=PID, title="KT #2: Extra", summary="redone", decisions=[],
                                                 action_items=[], terms=[], utterances=[], screen_moments=[], kt_links=[]))
    redone = T.build_tour(store, index, PID, ORG, viewer)  # a re-processed meeting's new notes are read again
    assert [p["text"] for p in redone["chapters"][0]["pinned"]] == [f"Read engine/mutate.py, part {n}" for n in range(1, 4)]


def test_stale_clip_tag(demo):
    assert "stale_clip" not in stop(tour(demo), "engine/mutate.py::apply_defaults")["tags"]
    demo.store.mark_kt_code_changed(PID, ["engine/mutate.py"])
    s = stop(tour(demo), "engine/mutate.py::apply_defaults")
    assert s["tags"] == ["stale_clip"] and s["kt"][0]["code_changed_since"] is True


def test_code_chunks_content_true_has_body(demo):
    chunks = demo.index.code_chunks(PID, content=True)
    body = next(c for c in chunks if c.symbol == "check_privileged").content.split("\n", 2)[-1]
    assert body.startswith("def check_privileged(") and all(c.content for c in chunks)


def test_cache_rebuilds_on_new_sha(demo):
    sha = demo.store.repos_for(PID)[0].last_indexed_sha
    first = T.graph_for(demo.index, PID, sha)
    assert T.graph_for(demo.index, PID, sha) is first  # same commit, same code: served from the cache

    text = (T.__file__.replace("\\", "/").rsplit("/app/", 1)[0] + "/sample_repo/engine/validate.py")
    source = open(text).read().replace("        return []\n", "        return check_labels(resource, rule)\n")
    demo.index.upsert(chunk_code(PID, VALIDATE, source, commit="x"))  # same line ranges, so the same chunk ids: a content change
    demo.store.update_repo("repo-demo", last_indexed_sha="x")
    t = tour(demo)
    assert stop(t, f"{VALIDATE}::check_privileged")["calls"] == [f"{VALIDATE}::check_labels"] and t["sha"] == "x"
    assert T.graph_for(demo.index, PID, "x") is not first


def test_cache_key_reads_no_code_but_sees_a_same_commit_reindex(demo):
    sha = demo.store.repos_for(PID)[0].last_indexed_sha
    index = Spy(demo.index)
    first = T.graph_for(index, PID, sha)
    assert T.graph_for(index, PID, sha) is first
    assert index.calls["code_chunks(content=True)"] == 1 and index.calls["code_chunks(content=False)"] == 0  # bodies read once
    assert first.calls[f"{VALIDATE}::check_privileged"] == ()

    # a push handled after a later one re-indexes its file at the commit already recorded: same sha, same chunk ids
    source = open(T.__file__.replace("\\", "/").rsplit("/app/", 1)[0] + "/sample_repo/engine/validate.py").read()
    demo.index.upsert(chunk_code(PID, VALIDATE, source.replace("        return []\n", "        return check_labels(resource, rule)\n"), commit="later"))
    assert demo.store.repos_for(PID)[0].last_indexed_sha == sha
    assert stop(tour(demo), f"{VALIDATE}::check_privileged")["calls"] == [f"{VALIDATE}::check_labels"]


def test_concurrent_cold_requests_build_the_graph_once(demo, monkeypatch):
    """Single-flight: four cold requests at once share one build (each used to build, and hold, its own copy)."""
    import threading
    import time

    builds, real = [], T.build_graph

    def slow_build(chunks):
        builds.append(1)
        time.sleep(0.3)
        return real(chunks)

    monkeypatch.setattr(T, "build_graph", slow_build)
    start, out, errors = threading.Barrier(4), [], []

    def request():
        try:
            start.wait()
            out.append(tour(demo))
        except Exception as e:  # noqa: BLE001 - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=request) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == [] and len(out) == 4 and len(builds) == 1 and all(t == out[0] for t in out)


def test_graph_cache_is_bounded_by_symbols(demo, monkeypatch):
    sha = demo.store.repos_for(PID)[0].last_indexed_sha
    big = T.graph_for(demo.index, PID, sha)
    monkeypatch.setattr(T, "CACHE_SYMBOLS", len(big.symbols) + 1)  # room for the demo graph, not for it and two more symbols
    _, index, _ = mini({"pkg/a.py": "def a():\n    return b()\n\n\ndef b():\n    return 1\n"})
    T.graph_for(index, "p1", "abc")
    assert len(T._CACHE) == 1 and next(iter(T._CACHE.values())) is not big  # the older graph made room
    assert T.graph_for(demo.index, PID, sha) is not big  # rebuilt, not served from the cache


def test_only_stops_keep_their_code(demo):
    g = T.graph_for(demo.index, PID, demo.store.repos_for(PID)[0].last_indexed_sha)
    stops = {key for ch in g.chapters for key, ref in ch.execution if ref is None}
    assert stops and all(g.symbols[k].code for k in stops)
    assert all(s.code == () for k, s in g.symbols.items() if k not in stops)  # tests and unreached symbols carry no lines


def test_entry_point_ranking_memory_is_linear():
    """Ranking 20,000 symbols (200 call chains of 100) with reach sets sized by the symbol count took tens of MiB and
    grew with its square (334 MiB at 50,000); fixed-width passes keep it to a few MiB."""
    import tracemalloc

    class Info:
        def __init__(self, n):
            self.span = K.Span(f"f{n}", n % 100 + 1, n % 100 + 1, path=f"pkg/m{n // 100}.py")

    nodes = [f"n{i}" for i in range(20_000)]
    edges = {v: ((nodes[i + 1],) if (i + 1) % 100 else ()) for i, v in enumerate(nodes)}
    by_key = {v: Info(i) for i, v in enumerate(nodes)}
    tracemalloc.start()
    entries, reached = T._entry_points(nodes, edges, by_key)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(entries) == 200 and len(reached) == 20_000
    assert peak < 12 * 2**20, f"{peak / 2**20:.1f} MiB"


def test_hub_lists_a_bounded_number_of_callers():
    callers = "".join(f"def handler_{n}():\n    return helper()\n\n\n" for n in range(30))
    store, index, viewer = mini({"pkg/hub.py": "def helper():\n    return 1\n\n\n" + callers})
    t = T.build_tour(store, index, "p1", "o1", viewer)
    helper = stop(t, "pkg/hub.py::helper")
    assert len(helper["called_by"]) == T.MAX_EDGES and helper["called_by_total"] == 30
    refs = [s for ch in t["chapters"] for s in ch["stops"] if s["ref_chapter"]]
    assert refs and all(s["calls"] == [] and s["called_by"] == [] for s in refs)  # a pointer carries no edge lists


def test_index_cap_is_reported(demo, monkeypatch):
    from app import ports

    assert tour(demo)["index_truncated"] is False
    monkeypatch.setattr(ports, "CODE_CHUNK_CAP", 7)
    t = tour(demo)
    assert t["index_truncated"] is True and not t["truncated"]  # chapters were not cut: files were left out of the index read


def test_no_codeowners_no_meetings():
    store, index, viewer = mini({"app/main.py": "def main():\n    return helper()\n\n\ndef helper():\n    return 1\n"})
    t = T.build_tour(store, index, "p1", "o1", viewer)
    (ch,) = t["chapters"]
    assert ch["pinned"] == [] and [s["symbol"] for s in ch["stops"]] == ["main", "helper"]
    assert all(s["owner"] is None and s["kt"] == [] and s["first_pr"] is None and s["first_prs"] == [] and s["tags"] == [] for s in ch["stops"])
    assert t["stats"] == {"stops": 2, "explained": 0, "kt_seconds": 0.0, "unexplained_by_owner": []}


def test_not_indexed_and_no_entry_point():
    store, index, viewer = mini({})
    t = T.build_tour(store, index, "p1", "o1", viewer)
    assert t["indexed"] is False and t["chapters"] == [] and t["stats"]["stops"] == 0

    store, index, viewer = mini({"lib/util.py": "def slug(s):\n    return s.lower()\n\n\ndef pad(s):\n    return s.ljust(8)\n"})
    t = T.build_tour(store, index, "p1", "o1", viewer)
    assert t["indexed"] and t["chapters"] == [] and t["hint"]
    assert [u["key"] for u in t["unreached"]] == ["lib/util.py::slug", "lib/util.py::pad"]


# ---------------------------------------------------------------- resolution rules
def test_ambiguous_name_needs_import():
    both = {"pkg/a.py": "def run():\n    return 1\n", "pkg/b.py": "def run():\n    return 2\n"}
    g = graph_of({**both, "app/main.py": "def main():\n    return run()\n"})
    assert g.calls["app/main.py::main"] == ()  # two files define run(), nothing says which

    g = graph_of({**both, "app/main.py": "from pkg.a import run\n\n\ndef main():\n    return run()\n"})
    assert g.calls["app/main.py::main"] == ("pkg/a.py::run",)
    g = graph_of({**both, "app/main.py": "from pkg.b import run as launch\n\n\ndef main():\n    return launch()\n"})
    assert g.calls["app/main.py::main"] == ("pkg/b.py::run",)  # an alias resolves to the name it imports
    g = graph_of({**both, "app/main.py": "from pkg import b\n\n\ndef main():\n    return b.run()\n"})
    assert g.calls["app/main.py::main"] == ("pkg/b.py::run",)  # module.func() goes to that module
    g = graph_of({**both, "app/main.py": "def main(x):\n    return x.run()\n"})
    assert g.calls["app/main.py::main"] == ()  # a method call on something not imported stays unresolved


def test_name_defined_in_too_many_files_is_skipped():
    files = {f"m{i}/x.py": "def handle():\n    return 1\n" for i in range(4)}
    g = graph_of({**files, "app/main.py": "def main():\n    return handle()\n"})
    assert g.calls["app/main.py::main"] == ()


def test_cycles_terminate():
    g = graph_of({"app/loop.py": "def ping(n):\n    return pong(n - 1)\n\n\ndef pong(n):\n    return ping(n - 1)\n"})
    assert [[k for k, _ in ch.execution] for ch in g.chapters] == [["app/loop.py::ping", "app/loop.py::pong"]]  # a cycle nothing calls: one entry

    g = graph_of({"app/loop.py": "def main():\n    ping(3)\n\n\ndef ping(n):\n    return pong(n)\n\n\ndef pong(n):\n    return ping(n)\n"})
    (ch,) = g.chapters
    assert [k.split("::")[1] for k, _ in ch.execution] == ["main", "ping", "pong"]
    assert [k.split("::")[1] for k, _ in ch.reading] == ["pong", "ping", "main"]


def test_earlier_chapter_symbol_is_a_reference_stop():
    g = graph_of({"app/cli.py": "def big():\n    step()\n    shared()\n\n\ndef step():\n    return 1\n\n\ndef small():\n    shared()\n\n\ndef shared():\n    return 2\n"})
    big, small = g.chapters
    assert big.entry == "app/cli.py::big" and [r for _, r in big.execution] == [None, None, None]
    assert small.execution == (("app/cli.py::small", None), ("app/cli.py::shared", "ch-1"))


def test_split_long_function_merged_into_one_stop():
    body = [f"    x{i} = {i}" for i in range(297)]
    files = {"app/big.py": "\n".join(["def huge():", *body, "    helper()", "    return x0", "", "", "def helper():", "    return 1"]) + "\n"}
    store, index, viewer = mini(files)
    assert len([c for c in index.code_chunks("p1") if c.symbol == "huge"]) == 3  # chunk_code split it at 120 lines
    t = T.build_tour(store, index, "p1", "o1", viewer)
    (ch,) = t["chapters"]
    huge = ch["stops"][0]
    assert [s["symbol"] for s in ch["stops"]] == ["huge", "helper"]
    assert (huge["start_line"], huge["end_line"]) == (1, 300) and huge["calls"] == ["app/big.py::helper"]  # the call on line 299 is seen
    assert len(huge["code"]) == T.MAX_CODE_LINES and huge["code_truncated"] is True
    assert ch["stops"][1]["code_truncated"] is False


def test_strings_signatures_and_module_code_do_not_confuse_attribution():
    src = '''"""Reports."""
import db


def report(
    month,
    team=None,
):
    """Builds the report. Call render() afterwards."""
    sql = """
SELECT *
FROM sales
"""
    rows = fetch(sql)  # format(rows) is only a comment
    return summarize(rows)


def fetch(q):
    return db.run(q)


def summarize(rows):
    return len(rows)


def render():
    return "render(x) is text"


if __name__ == "__main__":
    report(1)
'''
    g = graph_of({"app/report.py": src})
    k = "app/report.py::"
    assert g.calls[k + "report"] == (k + "fetch", k + "summarize")  # not render(): that is the docstring
    assert g.symbols[k + "report"].doc == "Builds the report. Call render() afterwards."
    assert g.symbols[k + "report"].shown_end == 15
    assert g.called_by[k + "report"] == ()  # the __main__ block is module code
    assert g.calls[k + "render"] == ()


def test_methods_containers_and_duplicate_names():
    src = '''class Reader:
    def run(self):
        return self.parse()

    def parse(self):
        return 1


class Writer:
    def run(self):
        return self.flush()

    def flush(self):
        return 2


def main():
    Reader().run()
    Writer().run()
'''
    g = graph_of({"app/io.py": src})
    k = "app/io.py::"
    assert {key for key in g.symbols if "run" in key} == {k + "Reader.run", k + "Writer.run"}  # one file, two run(): qualified keys
    assert g.symbols[k + "Reader.run"].container == "Reader" and g.symbols[k + "Reader.run"].kind == "method"
    assert g.calls[k + "Reader.run"] == (k + "parse",) and g.calls[k + "Writer.run"] == (k + "flush",)
    # instantiation counts as a call, and Name().method() goes to that class's method
    assert g.calls[k + "main"] == (k + "Reader", k + "Reader.run", k + "Writer", k + "Writer.run")
    g2 = graph_of({"app/io.py": src.replace("Reader().run()", "make().run()")})
    assert k + "Reader.run" not in g2.calls[k + "main"]  # x.run() with run in two classes: ambiguous, no edge
    assert g.unreached == ()


def test_nested_function_in_method_is_called():
    src = '''class Service:
    def run(self):
        def score(x):
            return weigh(x)
        total = score(1)
        return total


def weigh(x):
    return x


def other():
    return score(2)


def main():
    Service().run()
'''
    g = graph_of({"app/svc.py": src})
    k = "app/svc.py::"
    score = g.symbols[k + "score"]
    assert (score.container, score.kind, score.label) == (None, "function", "score")  # a local function of run, not a method
    assert g.calls[k + "run"] == (k + "score",) and g.calls[k + "score"] == (k + "weigh",)
    assert g.calls[k + "other"] == ()  # score is local to run: nothing outside it can call it by name
    (ch,) = [c for c in g.chapters if c.entry == k + "main"]
    assert [key.split("::")[1] for key, _ in ch.execution] == ["main", "Service", "run", "score", "weigh"]  # one path, not split
    run = g.symbols[k + "run"]
    assert (run.start_line, run.shown_end) == (2, 6)  # the method runs past its nested def, though its chunk stops there
    assert run.code[-1].strip() == "return total"
    assert k + "score" not in g.unreached


def test_closure_in_function():
    src = '''def outer(items):
    def keep(x):
        return check(x)
    def check(x):
        return x > 0
    return [x for x in items if keep(x)]


def keep(x):
    return False
'''
    g = graph_of({"app/f.py": src})
    k = "app/f.py::"
    outer = g.symbols[k + "outer"]
    assert (outer.start_line, outer.end_line, outer.shown_end) == (1, 1, 6)  # the chunk is the def line; the function is not
    assert g.calls[k + "outer"] == (k + "outer.keep",)  # the local keep shadows the module-level one
    assert g.calls[k + "outer.keep"] == (k + "check",)  # a nested def sees its siblings
    assert k + "outer.keep" not in g.unreached and k + "check" not in g.unreached
    assert g.unreached == (k + "keep@9",)  # the module-level keep: nothing calls it


def test_builtin_method_names_on_unknown_receivers_do_not_link():
    files = {"app/store.py": "class Store:\n    def get(self, k):\n        return k\n\n    def items(self):\n        return self.get(1)\n",
             "app/view.py": "from app.store import Store\n\n\ndef show(row):\n    return row.get('a') or Store().get('b')\n"}
    g = graph_of(files)
    assert g.calls["app/view.py::show"] == ("app/store.py::Store", "app/store.py::get")  # Store().get(): the class's own, row.get(): a dict
    assert g.calls["app/store.py::items"] == ("app/store.py::get",)  # self.get() still resolves


def test_jsx_tags_render_components():
    files = {"web/Card.tsx": "export function Card() {\n  return <div className=\"card\" />;\n}\n",
             "web/Page.tsx": "import { Card } from './Card';\n\nexport function Page({ items }: { items: Array<string> }) {\n"
                             "  const n = items.length < 3 ? 1 : 2;\n  return <main><Card />{n}</main>;\n}\n"}
    g = graph_of(files)
    assert g.calls["web/Page.tsx::Page"] == ("web/Card.tsx::Card",)  # Array<string> and `<` comparisons are not tags
    assert [k for k, _ in g.chapters[0].execution] == ["web/Page.tsx::Page", "web/Card.tsx::Card"]


def test_typescript_relative_imports_break_ties():
    files = {
        "src/rules/a.ts": "export function validate(x: string) {\n  return x.length > 0;\n}\n",
        "src/rules/b.ts": "export function validate(x: string) {\n  return x === 'ok';\n}\n",
        "src/app.ts": "import { validate } from './rules/b';\nimport * as rules from './rules/a';\n\n"
                      "export function check(x: string) {\n  // validate(x) in a comment is not a call\n  return validate(x) && rules.validate(x);\n}\n",
    }
    g = graph_of(files)
    assert g.calls["src/app.ts::check"] == ("src/rules/b.ts::validate", "src/rules/a.ts::validate")
    assert g.symbols["src/app.ts::check"].doc == "validate(x) in a comment is not a call"


def test_typescript_method_definitions_are_not_calls():
    db = "export function persist(k: string) {\n  return write(k);\n}\n\nexport function write(k: string) {\n  return k;\n}\n"
    cache = ("export class Cache {\n  constructor(private size: number) {\n    this.size = size;\n  }\n\n"
             "  persist(key: string) {\n    return key;\n  }\n\n  async load(\n    key: string,\n  ): Promise<string> {\n    return refresh(key);\n  }\n}\n")
    g = graph_of({"src/db.ts": db, "src/cache.ts": cache})
    assert g.calls["src/cache.ts::Cache"] == ()  # persist(...) { and load(...): T { define methods; refresh is defined nowhere
    assert g.calls["src/db.ts::persist"] == ("src/db.ts::write",)  # same-file names still resolve
    g = graph_of({"src/db.ts": db + "\nexport class Writer {\n  static write(k: string): string {\n    return k;\n  }\n}\n"})
    assert g.calls["src/db.ts::Writer"] == ()  # a method named like a function of the same file defines, not calls

    g = graph_of({"src/db.ts": db, "src/app.ts": "export function save(k: string) {\n  return persist(k);\n}\n"})
    assert g.calls["src/app.ts::save"] == ()  # defined in one other file but never imported: ES modules have no globals
    g = graph_of({"src/db.ts": db, "src/app.ts": "import { persist } from './db';\n\nexport function save(k: string) {\n  return persist(k);\n}\n"})
    assert g.calls["src/app.ts::save"] == ("src/db.ts::persist",)


# ---------------------------------------------------------------- HTTP
@pytest.fixture(scope="module")
def client():
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c


def test_tour_any_member(client):
    for who in ("u-vijay", "u-priya", "u-ravi", "u-meera", "u-admin"):
        r = client.get(f"/api/tour?project_id={PID}", headers={"X-Demo-User": who})
        assert r.status_code == 200 and len(r.json()["chapters"]) == 2
    r = client.get(f"/api/tour?project_id={PID}&order=reading", headers={"X-Demo-User": "u-vijay"})
    assert r.status_code == 200 and r.json()["order"] == "reading"
    assert client.get(f"/api/tour?project_id={PID}&order=bad", headers={"X-Demo-User": "u-vijay"}).status_code == 422
    client.app.state.container.store.add_project(Project(id="proj-solo", org_id=ORG, name="Solo"))  # nobody is a member
    assert client.get("/api/tour?project_id=proj-solo", headers={"X-Demo-User": "u-vijay"}).status_code == 404
    assert client.get("/api/tour?project_id=nope", headers={"X-Demo-User": "u-priya"}).status_code == 404


def test_tour_response_shape(client):
    body = client.get(f"/api/tour?project_id={PID}", headers={"X-Demo-User": "u-priya"}).json()
    assert set(body) >= {"indexed", "sha", "order", "truncated", "partial", "stats", "chapters", "tests", "unreached"}
    s = body["chapters"][0]["stops"][0]
    assert set(s) >= {"key", "n", "path", "symbol", "container", "start_line", "end_line", "summary", "code", "code_truncated", "calls",
                      "called_by", "ref_chapter", "kt", "owner", "tags", "first_pr", "first_prs", "action_item"}
    assert set(body["chapters"][0]) >= {"id", "title", "entry", "pinned", "stops"}
