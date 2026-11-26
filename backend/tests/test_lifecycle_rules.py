"""The lifecycle's rules on their own: the citation check, question similarity, business days and escalation, area levels,
first-task weights, diff parsing, template scaling, flags, the demo clock and the report's floors."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.agents.core.schemas import Chunk
from app.lifecycle import first_task, flags, progress, reporting, routing
from app.lifecycle import templates as T
from app.lifecycle.blockers import stage_for
from app.lifecycle.clock import Clock, business_days, program_day, week_start
from app.lifecycle.presubmit import files_from_diff

CODE = Chunk(id="c1", project_id="p", source_type="code", symbol="check_labels",
             ref={"path": "engine/validate.py", "start_line": 20, "end_line": 22},
             content="def check_labels(resource, rule):\n    labels = resource.get('metadata', {}).get('labels', {})\n"
                     "    return [f'{rule.name}: missing label {key}' for key in rule.require_labels if key not in labels]")
MEETING = Chunk(id="m1", project_id="p", source_type="meeting", ref={"meeting_id": "m", "title": "KT #1", "start_s": 98, "end_s": 140},
                content="Ravi: we never raise inside validation. We return a list of violations so the webhook reports all of them.")


def test_every_sentence_must_map_to_a_cited_source():
    ok = routing.citation_check("check_labels in engine/validate.py returns a missing label message for each required label. "
                                "Validation never raises: it returns a list of violations.", [CODE, MEETING])
    assert ok["ok"] and 1 in ok["sentences"][0]["sources"] and 2 in ok["sentences"][1]["sources"]
    bad = routing.citation_check("check_labels returns a missing label message. It also retries failed requests three times.", [CODE])
    assert not bad["ok"] and bad["sentences"][1]["sources"] == []
    unknown = routing.citation_check("check_labels calls apply_defaults first.", [CODE])  # an identifier the source does not contain
    assert not unknown["ok"]
    assert not routing.citation_check("", [CODE])["ok"]


RETRY = Chunk(id="r1", project_id="p", source_type="code", symbol="send", ref={"path": "webhooks/client.py", "start_line": 1, "end_line": 4},
              content="RETRIES = 3\n\ndef send(request):\n    # retries the request, then raises ValueError when every attempt failed\n"
                      "    raise ValueError('admission request failed')")


def test_a_sentence_needs_one_source_that_says_it_numbers_and_negation_included():
    # numbers must be the source's: 5 is not 3
    wrong = routing.citation_check("send retries 5 times and never raises ValueError.", [RETRY])
    assert not wrong["ok"] and wrong["sentences"][0]["sources"] == []
    assert not routing.citation_check("send retries the request 5 times.", [RETRY])["ok"]
    assert routing.citation_check("RETRIES is 3: send retries the request, then raises ValueError.", [RETRY])["ok"]
    # negation must agree: the source raises, so "never raises" is not supported, and the meeting's "never raise" is
    assert not routing.citation_check("send never raises ValueError.", [RETRY])["ok"]
    assert routing.citation_check("Validation never raises: it returns a list of violations.", [MEETING])["ok"]
    assert not routing.citation_check("Validation raises on the first violation.", [MEETING])["ok"]
    # one source on its own: words found only by pooling two sources are not support
    pooled = "check_labels retries the request three times and raises ValueError."
    assert not routing.citation_check(pooled, [CODE, RETRY])["ok"]


def test_the_retrieval_gate_is_similarity_or_a_named_identifier():
    from app.agents.core.schemas import Hit

    weak, strong = Hit(chunk=CODE, score=1, similarity=0.3), Hit(chunk=CODE, score=1, similarity=0.7)
    assert routing.retrieval_gate("How does validation work?", [strong])["rule"] == "similarity"
    assert not routing.retrieval_gate("How does validation work?", [weak])["ok"]  # the model's confidence is not the gate
    named = routing.retrieval_gate("What does check_labels return?", [weak])
    assert named["ok"] and named["rule"] == "identifier" and named["names"] == ["check_labels"]
    assert not routing.retrieval_gate("What does apply_defaults return?", [weak])["ok"]  # a name the source does not contain


def test_office_hours_and_paraphrases():
    monday_noon = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    assert routing.in_office_hours({}, monday_noon)
    assert routing.in_office_hours({"days": ["Mon"], "start": "11:00", "end": "13:00"}, monday_noon)
    assert not routing.in_office_hours({"days": ["Tue", "Thu"]}, monday_noon)
    assert not routing.in_office_hours({"days": ["Mon"], "start": "15:00", "end": "16:00"}, monday_noon)
    p = routing.paraphrase("Why does Arjun's validate_resource loop crash for me?")
    assert p.startswith("A similar question about") and "Why does" not in p


def test_the_offline_models_notice_is_not_a_claim():
    out = routing.citation_check("Offline demo answer (connect Gemini for a written explanation). The closest match is "
                                 "engine/validate.py:L20-L22 (check_labels):\n\ndef check_labels(resource, rule):", [CODE])
    assert out["ok"] and not any(s["text"].startswith("Offline") for s in out["sentences"])


def test_question_similarity():
    assert routing.similarity("Who decides the on-call rota?", "who decides the on-call rota") == 1.0
    assert routing.similarity("Who decides the on-call rota?", "Who decides on the on-call rota each month?") >= 0.8
    assert routing.similarity("Who decides the on-call rota?", "Where does check_labels report missing labels?") < 0.8
    assert routing.similarity("?", "Anything") == 0.0


def test_business_days_and_the_escalation_order():
    mon = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)
    assert [business_days(mon, mon + timedelta(days=d)) for d in range(8)] == [0, 1, 2, 3, 4, 4, 4, 5]
    assert [stage_for(mon, mon + timedelta(days=d)) for d in (0, 1, 2, 3, 4)] == ["hire", "mentor", "mentor", "manager", "manager"]
    sat = datetime(2026, 11, 14, 9, tzinfo=timezone.utc)
    assert stage_for(sat, sat + timedelta(days=1)) == "hire" and stage_for(sat, sat + timedelta(days=2)) == "mentor"


def test_area_levels_count_kinds_of_evidence_not_amounts():
    assert progress.level_for({}) == "not_started"
    assert progress.level_for({"questions": [1, 2, 3, 4]}) == "exploring"
    assert progress.level_for({"steps": [1], "watched": [1]}) == "working"
    assert progress.level_for({"steps": [1], "watched": [1], "questions": [1]}) == "working"
    assert progress.level_for({"merged": [1]}) == "contributed"


def test_first_task_weights_are_the_plans():
    assert first_task.WEIGHTS == {"focus": 0.30, "explained": 0.25, "small": 0.20, "tested": 0.15, "quiet": 0.10}
    assert round(sum(first_task.WEIGHTS.values()), 10) == 1.0 and first_task.SHOWN == 3
    assert first_task._in_focus("engine/validate.py", "engine") and not first_task._in_focus("webhooks/server.py", "engine")


def test_a_git_diff_becomes_pull_request_files():
    diff = ("diff --git a/engine/new.py b/engine/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/engine/new.py\n"
            "@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n"
            "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1,1 +1,1 @@\n-old\n+new\n")
    files = files_from_diff(diff)
    assert [(f["filename"], f["status"]) for f in files] == [("engine/new.py", "added"), ("README.md", "modified")]
    assert files[0]["patch"].startswith("@@ -0,0 +1,2 @@")


def test_templates_scale_with_their_length():
    assert T.checkin_days(90) == [30, 60, 90] and T.checkin_days(120) == [40, 80, 120]
    defaults = T.default_templates("o")
    assert len(defaults) == 16 and {t.key for t in defaults} >= {"backend-junior", "data-staff"}
    staff = next(t for t in defaults if t.key == "backend-staff")
    assert {m["key"] for m in staff.milestones} >= {"design_review", "tech_direction", "checkin_90"}
    assert all(1 <= p.due_in_days <= 60 for p in T.plan_tasks(staff, 365))  # proposals stay inside the Task agent's limits
    assert [m.due_day for m in T.milestones_for(staff, "ob", 180) if m.key == "checkin_90"] == [180]


def test_flags(monkeypatch):
    monkeypatch.delenv("FEATURES", raising=False)
    assert all(flags.enabled().values())
    monkeypatch.setenv("FEATURES", "all,-surveys,-reports")
    assert not flags.is_on("surveys") and not flags.is_on("reports") and flags.is_on("routing")
    monkeypatch.setenv("FEATURES", "routing,blockers")
    assert {k for k, v in flags.enabled().items() if v} == {"routing", "blockers"}


def test_the_clock():
    assert Clock(5).today() == date.today() + timedelta(days=5)
    assert program_day(date(2026, 1, 1), date(2026, 1, 31)) == 30
    assert week_start(date(2026, 11, 14)) == date(2026, 11, 9)


def test_below_a_floor_a_figure_is_not_enough_data_never_zero():
    f = reporting._figure("x", "X", "days", "how", {"current": (None, 2), "previous": (0, 7)})
    assert f["current"] == {"value": None, "n": 2, "display": "not enough data"}
    assert f["previous"] == {"value": 0, "n": 7, "display": "0 days"}  # a real zero, from enough data, is shown
    turns = [{"question": "Where is X?", "confidence": 0.9, "status": "answered", "created_at": datetime.now(timezone.utc)}] * 3
    assert reporting._gaps(turns, ["a", "b", "a"], date.today()) == 3  # the same question 3 times by 2 people
    assert reporting._gaps(turns, ["a", "a", "a"], date.today()) == 0


# Calibrated on real Gemini answers about a real repository (2026-11-14): the model cites with "[1, 2]", numbers its
# steps, names commands from two files in one sentence and states negatives. All of that must pass; a wrong number, a
# flipped negative or an invented name must not.
RUN_ALL = Chunk(id="ra", project_id="p", source_type="code", ref={"path": "run_all.sh", "start_line": 1, "end_line": 6},
                content="#!/usr/bin/env bash\nset -e\npython -m method.calibrate data/34855\npython solve.py data/34855\n"
                        "python validate.py data/34855 300\n")
README_RUN = Chunk(id="rm", project_id="p", source_type="code", ref={"path": "README.md", "start_line": 1, "end_line": 5},
                   content="## Run\npip install -r requirements.txt\nThen run `bash run_all.sh` to reproduce every step.\n"
                           "imagery.tif and boundaries.tif are not included in the repository (large files).")
REAL_STYLE = ("To run the pipeline, install the requirements with `pip install -r requirements.txt` [2]. Then run "
              "`bash run_all.sh` [1, 2], which runs these steps in order: 1) `python -m method.calibrate data/34855`, "
              "2) `python solve.py data/34855`, 3) `python validate.py data/34855 300`.\n\n"
              "The raster files imagery.tif and boundaries.tif are not included in the repository [2].")


def test_real_answer_style_passes_the_citation_check():
    check = routing.citation_check(REAL_STYLE, [RUN_ALL, README_RUN])
    assert check["ok"], [r for r in check["sentences"] if r["claim"] and not r["sources"]]


@pytest.mark.parametrize("wrong, right", [
    ("data/34855 300", "data/34855 500"),                       # a number the source does not state
    ("are not included", "are included"),                       # the opposite of what the source says
    ("`bash run_all.sh`", "`bash run_everything.sh`"),           # a script that does not exist
])
def test_falsified_real_style_answers_are_refused(wrong, right):
    assert not routing.citation_check(REAL_STYLE.replace(wrong, right, 1), [RUN_ALL, README_RUN])["ok"]


def _ctx_with(drafts):
    """A minimal Ctx for routing.ai_answer: one retrieval result per search (the research crew searches through the index's
    search() when it has no search_grid, and looks names up in an index that holds no files here), and a model that writes
    the given drafts."""
    from types import SimpleNamespace

    from app.agents.core.llm import FakeLLM
    from app.agents.core.schemas import Hit

    hits = [Hit(chunk=RUN_ALL, score=1.0, similarity=0.9), Hit(chunk=README_RUN, score=0.9, similarity=0.8)]
    queue = list(drafts)
    llm = FakeLLM({"QADraft": lambda prompt, media: queue.pop(0)})
    index = SimpleNamespace(search=lambda *a, **k: hits, symbols_named=lambda *a, **k: [], all_paths=lambda *a, **k: [],
                            file_chunks=lambda *a, **k: [])
    ctx = SimpleNamespace(deps=SimpleNamespace(llm=llm, store=None, index=index), index=index)
    return ctx, llm


def test_an_unsupported_first_draft_is_repaired_once_and_then_answered(monkeypatch):
    monkeypatch.setattr(routing.qa, "kt_clips_for", lambda *a, **k: [])
    wrong = {"answer": "Run `bash run_all.sh` [1]; it validates with 500 samples.", "cited": [1, 2], "confidence": 0.8, "sufficient": True}
    right = {"answer": "Run `bash run_all.sh` [1, 2]. It runs `python validate.py data/34855 300` [1].", "cited": [1, 2],
             "confidence": 0.8, "sufficient": True}
    ctx, llm = _ctx_with([wrong, right])
    out = routing.ai_answer(ctx, "p", "How do I run the pipeline end to end with run_all.sh?")
    assert out["result"] is not None and out["result"].answer == right["answer"]
    assert len(llm.calls) == 2 and "unsupported" in llm.calls[1][1]
    assert any("rewritten once" in w for w in out["why"])


def test_a_repair_that_is_still_unsupported_stays_not_enough_evidence(monkeypatch):
    monkeypatch.setattr(routing.qa, "kt_clips_for", lambda *a, **k: [])
    wrong = {"answer": "Run `bash run_all.sh` [1]; it validates with 500 samples.", "cited": [1, 2], "confidence": 0.8, "sufficient": True}
    ctx, llm = _ctx_with([wrong, wrong])
    out = routing.ai_answer(ctx, "p", "How do I run the pipeline end to end with run_all.sh?")
    assert out["result"] is None and out["why"][0].startswith("Not enough evidence") and len(llm.calls) == 2


# ---------------------------------------------------------------- the Guide's checks (release round: the answer path)
SERVER = Chunk(id="s1", project_id="p", source_type="code", ref={"path": "webhooks/server.py", "start_line": 1, "end_line": 11},
               content='from engine.policy import load_policy\n\n'
                       'POLICY = load_policy(open("policies/default.yaml").read()) if False else None')


def _hit(chunk, sim=0.1):
    from app.agents.core.schemas import Hit

    return Hit(chunk=chunk, score=1.0, similarity=sim)


def test_pythons_none_is_a_value_not_the_word_none():
    """`... else None` says nothing negative: a sentence that says "never" needs a source that says the negative."""
    assert not routing.citation_check("webhooks/server.py never calls load_policy [1].", [SERVER])["ok"]
    said = Chunk(id="s2", project_id="p", source_type="doc", ref={"title": "Notes", "page": 1},
                 content="The webhook server never calls load_policy at import time.")
    assert routing.citation_check("The webhook server never calls load_policy [1].", [said])["ok"]
    assert routing._negated("it is not None") is False and routing._negated("None of them pass") is False
    assert routing._negated("it does not retry") is True


def test_each_claims_marker_points_at_a_source_that_says_it():
    """A sentence whose [n] names a source that does not support it is pointed at the one that does; an unmarked claim gets
    the marker of its source. Numbers are the answer's own (context block numbers), so the citation chips stay right."""
    text = "check_labels returns a missing label message for each required label [3]."
    fixed = routing.align_markers(text, [SERVER, CODE], [3, 5])
    assert fixed == "check_labels returns a missing label message for each required label [5]."
    assert routing.align_markers("check_labels returns a missing label message for each key", [CODE], [2]).endswith("key [2]")
    kept = "check_labels returns a missing label message for each required label [1, 2]."
    assert routing.align_markers(kept, [SERVER, CODE], [1, 2]) == kept  # one of its markers supports it: left as written
    # a code span's [n] is code, not a marker, and the renumbering leaves it alone
    assert routing.renumber("Index ``parts[1]`` and items[0] come from it [2].", [2]) == "Index ``parts[1]`` and items[0] come from it [1]."
    assert routing.renumber("Steps are in run.sh [2-4].", [2, 3, 4]) == "Steps are in run.sh [1, 2, 3]."


def test_abbreviations_and_english_slashes_are_not_names_and_names_match_whole():
    hits = [_hit(CODE)]
    assert routing.given_names("What is the dress code for checks, e.g. on Fridays, and/or weekends?") == []
    assert routing.named_in("What is the dress code for checks, e.g. on Fridays?", hits) == []  # not in resource.get(
    assert routing.named_in("What does check_labels return?", hits) == ["check_labels"]
    assert routing.named_in("What does check_label do?", hits) == []  # a part of a name is not the name
    assert not routing.answer_gate("What is the office dress code for check validation, e.g. on Fridays?", hits)["ok"]


def test_the_terms_rule_needs_every_word_of_a_short_question():
    """"What is the admission webhook's SLA?": the sources hold "admission" and "webhook", and the one word they miss is the
    whole question. A question of at most three subject words passes the terms rule only with all of them held."""
    doc = Chunk(id="d1", project_id="p", source_type="code", ref={"path": "webhooks/server.py", "start_line": 1, "end_line": 3},
                content='"""Admission webhook: receives AdmissionReview requests and answers allow/deny."""')
    assert not routing.answer_gate("What is the admission webhook's SLA?", [_hit(doc)])["ok"]
    assert not routing.answer_gate("What uptime does the admission webhook have?", [_hit(doc)])["ok"]
    assert routing.answer_gate("What does the admission webhook receive?", [_hit(doc)])["rule"] == "terms"
    assert routing.answer_gate("What does the admision webhok recieve?", [_hit(doc)])["rule"] == "terms"  # misspelt: still held


def test_similarity_alone_does_not_pass_the_gate():
    """A real embedding model puts unanswerable questions about the project above 0.55: the cited sources must still hold a
    third of the question's subject words."""
    assert not routing.answer_gate("How is the payroll export scheduled each quarter?", [_hit(CODE, 0.68)])["ok"]
    assert routing.answer_gate("How are missing labels reported?", [_hit(CODE, 0.68)])["rule"] == "similarity"


def test_a_question_whose_names_no_source_has_is_refused():
    """A false premise ("initContainers") or a file that is not in the repository is not answered by sources about something
    else, however many of the other words they hold. Misspelt names still count as given."""
    priv = Chunk(id="v1", project_id="p", source_type="code", symbol="check_privileged",
                 ref={"path": "engine/validate.py", "start_line": 25, "end_line": 33},
                 content="def check_privileged(resource, rule):\n    \"\"\"The engine checks containers for privileged mode.\"\"\"\n"
                         "    containers = resource.get('spec', {}).get('containers', [])")
    assert not routing.answer_gate("How does the engine check initContainers for privileged mode?", [_hit(priv)])["ok"]
    assert routing.answer_gate("How does the engine check containers for privileged mode?", [_hit(priv)])["ok"]
    assert routing.names_missing("wat does chek_privilaged retrun", [_hit(priv)]) == []
    asked = "What does policies/default.yaml contain?"
    assert not routing.answer_gate(asked, [_hit(SERVER)], paths=["webhooks/server.py", "engine/policy.py"])["ok"]
    assert routing.named_in(asked, [_hit(SERVER)]) == []  # a line that opens a file is not the file


def test_hinglish_function_words_and_contractions_are_not_subject_words():
    assert routing.terms("validation kahan hoti hai?") == {"valid"}
    assert routing.terms("default labels kaun add karta hai aur kab?") == {"default", "label", "add"}
    assert routing.terms("Why doesn't it raise?") == {"rais"}


def test_a_short_question_may_word_its_verb_otherwise():
    """"Where are missing labels reported?": the sources hold the subject (missing labels) and word the verb otherwise. A
    passive verb is not the subject, so the short-question rule does not refuse for it; a missing noun still does."""
    assert routing.answer_gate("Where are missing labels reported?", [_hit(CODE)])["rule"] == "terms"
    assert not routing.answer_gate("Where is the missing labels SLA?", [_hit(CODE)])["ok"]
