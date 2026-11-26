"""The research crew wired into the Guide (docs/specs/rag-crew.md, "Where the crew is used"): the Ask page's tier-1 attempt,
QA's chat answer, routing.ai_answer and the search_knowledge tool all gather their evidence through app/rag/crew.py.

These tests hold the integration's promises on the seeded demo: the starter questions are answered from the right sources
and their trace opens with the crew's plan, search and rank; the agent loop starts from the crew's evidence (with why each
ranked, in our words); rules mode and the offline model call no model in the crew; the `crew` block rides on every tier a
retrieval ran for and `writer` on tier 1; the gate still reads similarity to the question as asked (an exact-name hit passes
only through the name); and shared traces and run records hold counts, never the question, a query or the model's words."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.agents.core import prompts
from app.agents.core import registry as R
from app.agents.core import runtime as rt
from app.agents.core.citations import label_for
from app.agents.core.llm import DATA_END, DATA_START, FakeLLM, OfflineLLM, read_data
from app.agents.core.schemas import Chunk, Hit, QAResult
from app.agents.specialists.guide import qa
from app.agents.tools.knowledge import SearchArgs, search_knowledge
from app.config import get_settings
from app.lifecycle import routing
from app.main import create_app
from app.ports import VerifiedAnswer
from app.rag import crew

PID, ORG = "proj-demo", "org-demo"
ADMIN, MANAGER, HIRE = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay"))
CREW_TOOLS = ["plan_search", "search_knowledge", "rank_evidence"]
SUMMARY_KEYS = {"intent", "queries", "planner", "candidates", "by_kind", "kept", "judged", "degraded", "ms"}
# the Ask page's starter questions (frontend/src/pages/Chat.tsx) and the sources each answer must cite
STARTERS = {
    "Which function reports missing labels?": {"engine/validate.py:L20-L22 (check_labels)"},
    "Why does validation return a list instead of raising?": {"KT #1: How validation works at 01:38", "docs/CONTRIBUTING.md:L1-L6"},
    "Where do default labels get added?": {"engine/mutate.py:L1-L5", "engine/mutate.py:L6-L11 (apply_defaults)"},
}
CHECK = "check_labels returns a missing label message for each key in rule.require_labels that is not in labels"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _c(client):
    return client.app.state.container


def ask(client, question, who=HIRE, **extra) -> dict:
    r = client.post("/api/ask/route", json={"project_id": PID, "question": question, **extra}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


class Recording(FakeLLM):
    """A FakeLLM that also keeps each structured call's system prompt (the crew may think with it: it is a FakeLLM)."""

    def structured(self, prompt, schema, *, smart=False, media=None, system=None):
        self.__dict__.setdefault("systems", []).append((schema.__name__, system))
        return super().structured(prompt, schema, smart=smart, media=media, system=system)


def _answer_check_labels(prompt, media=None):
    """A model's QADraft: the check_labels sentence, citing the block that shows check_labels."""
    ctx = read_data(prompt)["context"]
    n = next(b["n"] for b in ctx if b["label"].endswith("(check_labels)"))
    return {"answer": f"{CHECK} [{n}].", "cited": [n], "confidence": 0.8, "sufficient": True}


def _plan(prompt, media=None):
    return {"intent": "locate", "queries": [{"text": "zebra marimba lookup", "kinds": ["code"]}],
            "hypothetical": "The quokka passage says where labels are checked."}


def _grades(prompt, media=None):
    sources = read_data(prompt)["sources"]
    return {"grades": [{"n": s["n"], "g": 3 if "check_labels" in s["source"] else 1} for s in sources]}


# ---------------------------------------------------------------- the demo's starter questions
@pytest.mark.parametrize("question", list(STARTERS))
def test_the_starter_questions_are_answered_from_the_right_sources_offline(client, question):
    out = ask(client, question)
    assert out["tier"] == 1 and out["status"] == "answered", out["why"]
    labels = [c["label"] for c in out["citations"]]
    assert STARTERS[question] <= set(labels) and len(labels) <= 2
    assert [s["tool"] for s in out["steps"][:3]] == CREW_TOOLS and [s["kind"] for s in out["steps"][:3]] == ["think", "tool", "verify"]
    assert out["steps"][-1]["text"].startswith("✓ Sources checked")
    assert out["check"]["ok"] and out["retrieval"]["ok"]  # the citation guard and the retrieval gate both passed
    # the offline writer quoted the sources: no model wrote it, and the answer says so through `writer`
    assert out["writer"] == "extractive" and out["explain"]["ai"] is False
    # [n] are citation numbers: every marker points at a citation, and the first one is [1]
    marks = [int(x) for m in re.findall(r"\[(\d+(?:,\s*\d+)*)\]", out["answer"]) for x in re.findall(r"\d+", m)]
    assert marks and marks[0] == 1 and set(marks) <= set(range(1, len(labels) + 1))
    assert set(out["crew"]) == SUMMARY_KEYS and out["crew"]["planner"] == "rules" and out["crew"]["judged"] is False
    assert out["crew"]["kept"] == 8 and out["crew"]["candidates"] >= out["crew"]["kept"] and out["crew"]["queries"] >= 2
    assert set(out["crew"]["by_kind"]) == {"code", "doc", "kt", "verified"} and set(out["crew"]["ms"]) == {"plan", "retrieve", "rank", "judge", "total"}


def test_the_starters_pass_the_gate_by_their_subject_words_not_by_similarity(client):
    """The hashing embedder puts the starters at 0.28-0.33 to their sources and they name no identifier: the Guide's third gate
    rule (the cited sources hold most of the question's subject words) is what lets the quoted answer through. A question
    whose cited sources hold only half its subject words is still refused on this path (it has no VERIFY)."""
    out = ask(client, "Where do default labels get added?")
    assert out["retrieval"]["rule"] == "terms" and out["retrieval"]["similarity"] < routing.MIN_SIMILARITY
    assert any("hold the question's subject words" in w for w in out["why"])
    helm = ask(client, "How do I deploy the policy engine with Helm?")  # half its subject words are "policy engine": not enough
    assert helm["status"] == "offered" and helm["writer"] is None


def test_unrelated_questions_are_still_refused_with_the_crew(client):
    for q in ("What is the capital of France?", "How do I deploy to production?", "What port does the webhook server listen on?"):
        out = ask(client, q)
        assert out["status"] == "offered" and out["writer"] is None and out["explain"]["ai"] is False, q
        assert set(out["crew"]) == SUMMARY_KEYS and [s["tool"] for s in out["steps"][:3]] == CREW_TOOLS


# ---------------------------------------------------------------- the crew block on every tier, writer on tier 1
def test_the_crew_block_rides_on_every_tier_a_retrieval_ran_for(client):
    # a verified answer only the lifecycle store holds (saved, not indexed): tier 2 finds it by the question
    _c(client).lifecycle.add_verified_answer(VerifiedAnswer(
        id="", project_id=PID, question="Which Slack channel announces the release train?", answer="#release-train, every Tuesday.",
        verified_by="u-ravi", created_at=datetime.now(timezone.utc)))
    related = ask(client, "Which Slack channel announces the release train?")
    assert related["tier"] == 2 and related["status"] == "related" and set(related["crew"]) == SUMMARY_KEYS
    assert "writer" not in related  # a person wrote a verified answer: writer is tier 1's
    offered = ask(client, "What is the capital of France?")
    assert offered["tier"] == 1 and offered["status"] == "offered" and offered["crew"]["kept"] >= 0 and offered["writer"] is None
    # asking for a person runs no answer attempt (routing's person tiers search as before): no crew block
    routed = ask(client, "What is the capital of France?", ask_person=True, message_id=offered["message_id"])
    assert routed["status"] == "routed" and "crew" not in routed and "writer" not in routed


def test_a_model_writer_and_the_model_stages_show_in_writer_crew_and_explain(client):
    """A scripted model on the deterministic path (no agent loop scripted): the model plans and judges, writes the answer,
    and the result says so; the plan's own words reach nobody but the model, inside its data block."""
    llm = Recording({"QueryPlan": _plan, "EvidenceGrades": _grades, "QADraft": _answer_check_labels})
    _c(client).deps.llm = llm
    out = ask(client, "Where does check_labels report missing labels?")
    assert out["status"] == "answered" and out["writer"] == "model" and out["explain"]["ai"] is True
    assert out["crew"]["planner"] == "model" and out["crew"]["judged"] is True
    assert out["citations"][0]["label"] == "engine/validate.py:L20-L22 (check_labels)"
    assert "the model judged" in out["steps"][2]["text"]
    for name, system in llm.systems:
        assert prompts.UNTRUSTED in (system or ""), name
    for name, prompt in llm.calls:
        assert prompt.count(DATA_START) == 1 and prompt.rstrip().endswith(DATA_END), name
        assert "check_labels" not in prompt.split(DATA_START)[0], name  # the question reaches the model as data only
    dumped = json.dumps({k: v for k, v in out.items() if k != "steps"})
    assert "zebra" not in dumped and "quokka" not in dumped  # the model's queries are not in the answer's blocks


def test_markers_become_citation_numbers_and_code_keeps_its_indexes():
    text = "Run `parts[1]` first [3]. Then `x[0]` [5, 3]. Not cited [7]."
    assert routing.renumber(text, [3, 5]) == "Run `parts[1]` first [1]. Then `x[0]` [2, 1]. Not cited."


def test_the_writer_is_decided_in_one_place():
    assert routing.writer_of(OfflineLLM()) == "extractive" and routing.writer_of(FakeLLM()) == "model"


# ---------------------------------------------------------------- the agent loop starts from the crew's evidence
def test_the_agent_loop_receives_the_crews_evidence_as_its_first_results(client):
    c, seen = _c(client), {}

    def turn(messages):
        data = read_data(messages[0]["text"])
        seen.update(data)
        n = next(r["n"] for r in data["first_results"] if r["source"].endswith("(check_labels)"))
        return {"final": {"answer": f"{CHECK} [{n}].", "cited": [n], "sufficient": True, "confidence": 0.8}}

    llm = FakeLLM({"agent:guide": turn, "agent:guide_verify": lambda m: {"final": {"claims": [{"sentence": 1, "supported": True, "source": 1}]}}})
    c.deps.llm = llm
    question = "Where does check_labels report missing labels?"
    out = ask(client, question)
    assert out["status"] == "answered" and out["writer"] == "model" and out["explain"]["ai"] is True
    # the same evidence the crew gives on its own (rules only: this FakeLLM scripts no crew schema), in the same order
    found = crew.retrieve(R.deps_for(c.deps, ORG, "guide"), PID, question, k=8)
    rows = seen["first_results"]
    assert [r["source"] for r in rows] == [label_for(h.chunk)[:200] for h in found.hits]
    assert [r["similarity"] for r in rows] == [round(h.similarity, 2) for h in found.hits]  # the gate's evidence, as asked
    assert all(r["why"] == found.reasons[h.chunk.id][:4] for r, h in zip(rows, found.hits) if found.reasons.get(h.chunk.id))
    assert any("defines check_labels" in w for r in rows for w in r.get("why", []))
    assert seen["question"] == question and [s["tool"] for s in out["steps"][:3]] == CREW_TOOLS
    assert out["crew"]["planner"] == "rules" and out["crew"]["kept"] == len(rows)


# ---------------------------------------------------------------- rules mode and the offline model call no model
def test_rules_mode_makes_no_model_call_in_the_crew(client):
    c = _c(client)
    assert client.patch("/api/agents/guide", json={"mode": "rules"}, headers=ADMIN).status_code == 200
    llm = FakeLLM({"QueryPlan": _plan, "EvidenceGrades": _grades, "QADraft": _answer_check_labels,
                   "agent:guide": lambda m: {"final": {"answer": "", "cited": [], "sufficient": False, "confidence": 0.0}}})
    c.deps.llm = llm
    out = ask(client, "Where does check_labels report missing labels?")
    assert llm.calls == [] and llm.agent_calls == []
    assert out["status"] == "offered" and out["crew"]["planner"] == "rules" and out["crew"]["judged"] is False
    chat = client.post("/api/chat", json={"project_id": PID, "question": "Where does check_labels report missing labels?"}, headers=HIRE)
    assert chat.status_code == 200 and llm.calls == []  # chat.question (QA) in rules mode: the crew thinks no more than QA writes


def test_the_offline_model_is_never_asked_to_plan_or_judge(client, monkeypatch):
    c, asked = _c(client), []
    real = OfflineLLM.structured

    def spy(self, prompt, schema, **kw):
        asked.append(schema.__name__)
        return real(self, prompt, schema, **kw)

    monkeypatch.setattr(OfflineLLM, "structured", spy)
    assert isinstance(c.deps.llm, OfflineLLM)
    out = ask(client, "Which function reports missing labels?")
    assert out["status"] == "answered" and asked and set(asked) == {"QADraft"}


# ---------------------------------------------------------------- the gate: similarity to the question as asked
def _code(cid: str, body: str, symbol: str | None = None) -> Chunk:
    return Chunk(id=cid, project_id="p", source_type="code", symbol=symbol, ref={"path": "engine/x.py", "start_line": 1, "end_line": 3},
                 content=f"File: engine/x.py (lines 1-3)\n{body}")


def test_an_exact_name_hit_passes_the_gate_only_through_the_name():
    exact = Hit(chunk=_code("c1", "def check_labels(resource, rule):\n    return []", "check_labels"), score=1.0, similarity=0.0)
    named = routing.answer_gate("What does check_labels return?", [exact])
    assert named["ok"] and named["rule"] == "identifier" and named["names"] == ["check_labels"]
    assert not routing.answer_gate("How are invoices archived for auditors?", [exact])["ok"]  # no name, none of its words
    assert routing.retrieval_gate("How are invoices archived for auditors?", [exact])["rule"] is None  # never "similarity"


def test_the_subject_word_rule_needs_more_than_half_without_verify():
    """Half the subject words ("policy engine" of "deploy the policy engine with Helm") pass only where VERIFY checks every
    claim afterwards (the agent loop); the deterministic path, which has no VERIFY, needs more than half."""
    src = Hit(chunk=_code("c2", "class PolicyEngine:\n    \"\"\"The policy engine.\"\"\""), score=1.0, similarity=0.1)
    question = "How do I deploy the policy engine with Helm?"
    assert len(routing.terms(question) & routing.terms(src.chunk.content)) * 2 == len(routing.terms(question))  # exactly half
    assert not routing.answer_gate(question, [src])["ok"]
    assert routing.answer_gate(question, [src], verified=True)["rule"] == "terms"


def test_qa_answers_from_a_zero_similarity_source_by_the_same_gate_as_routing(world, monkeypatch):
    """Similarity 0 means "only an exact-name lookup, or a search in other words, found it": the chat (qa.answer) takes such
    a source by the Guide's own gate (routing.answer_gate), never by similarity: when the question names what it holds, or
    when it holds the question's subject words; a question about something else is refused."""
    src = _code("c-only", "def archive_invoices(batch):\n    \"\"\"Moves paid invoices to cold storage for auditors.\"\"\"", "archive_invoices")
    found = crew.Retrieval(hits=[Hit(chunk=src, score=1.0, similarity=0.0)], analysis=crew.analyze("q"), candidates=1,
                           by_kind={"code": 1}, planner="rules", judged=False)
    monkeypatch.setattr(crew, "retrieve", lambda *a, **kw: found)
    world.llm = FakeLLM({"QADraft": lambda p, m: {"answer": "archive_invoices moves paid invoices to cold storage [1].", "cited": [1],
                                                  "confidence": 0.9, "sufficient": True}})
    other, _ = qa.answer(world, "p1", "u-hire", "How are refunds approved by finance?")
    assert other.status == "not_enough_information"
    plain, _ = qa.answer(world, "p1", "u-hire", "How are invoices archived for the auditors?")
    assert plain.status == "answered" and plain.writer == "model"  # rule "terms": it holds archive, invoices and auditors
    named, _ = qa.answer(world, "p1", "u-hire", "What does archive_invoices do?")
    assert named.status == "answered" and named.answer.endswith("[1].")


# ---------------------------------------------------------------- chat stays compatible
def test_chat_answers_keep_their_shape(client):
    body = client.post("/api/chat", json={"project_id": PID, "question": "Which function reports missing labels?"}, headers=HIRE).json()
    assert set(body) == set(QAResult.model_fields) | {"message_id"}  # no new fields on the persisted answer
    assert body["status"] == "answered" and body["citations"][0]["label"] == "engine/validate.py:L20-L22 (check_labels)"
    assert body["answer"].count("[1]") >= 1 and "[3]" not in body["answer"]  # citation numbers, not context block numbers


# ---------------------------------------------------------------- the search_knowledge tool
def test_search_knowledge_runs_the_crews_rules_with_the_same_payload(client):
    c = _c(client)
    hire = c.store.get_user("u-vijay")
    ctx = rt.ToolCtx(deps=c.deps, org_id=ORG, project_id=PID, user=hire, agent="guide")
    out = search_knowledge(ctx, SearchArgs(query="check_labels missing label", kinds=["code"], k=4))
    data = read_data(out.text)
    assert data["query"] == "check_labels missing label" and 1 <= len(data["results"]) <= 4
    assert all(set(r) == {"n", "source", "kind", "similarity", "text"} and r["kind"] == "code" for r in data["results"])
    assert data["results"][0]["source"] == "engine/validate.py:L20-L22 (check_labels)"
    want = crew.search(c.deps, PID, "check_labels missing label", k=4, kinds=["code"])
    assert [r["source"] for r in data["results"]] == [label_for(h.chunk)[:200] for h in want]
    assert out.shared == f"Searched code ({len(data['results'])} found)" and "check_labels" not in out.shared


# ---------------------------------------------------------------- the roster
def test_the_guide_lists_its_research_crew(client):
    agents = {a["key"]: a for a in client.get(f"/api/agents?project_id={PID}", headers=MANAGER).json()["agents"]}
    members = agents["guide"]["crew"]
    # the search planner is not the Planner agent (a row of its own on the same page): another key and another name
    assert [m["key"] for m in members] == ["search_planner", "retrievers", "fusion", "judge", "writer", "critic"]
    assert [m["name"] for m in members] == ["Search planner", "Retrievers", "Fusion", "Judge", "Writer", "Critic"]
    assert not set(m["key"] for m in members) & set(agents) and "offered a person" in members[-1]["role"]
    assert all(set(m) == {"key", "name", "role"} and 20 <= len(m["role"]) <= 160 for m in members)
    assert "crew" not in agents["planner"] and "index.search_grid" in agents["guide"]["tools"]


# ---------------------------------------------------------------- privacy: counts only
def test_shared_traces_and_run_records_hold_counts_never_the_question_or_the_models_words(client):
    c = _c(client)
    llm = Recording({"QueryPlan": _plan, "EvidenceGrades": _grades, "QADraft": _answer_check_labels})
    c.deps.llm = llm
    rid = str(uuid.uuid4())
    question = "Where does check_labels report missing labels for the xylophone team?"
    out = ask(client, question, run_id=rid)
    assert out["status"] == "answered" and out["crew"]["planner"] == "model"
    # the asker's own trace may say what the plan looks for; the shared one says how many
    run = client.get(f"/api/agents/runs/{rid}", headers=MANAGER).json()
    shared = run["steps"]
    assert shared[0]["text"] == f"Planned {out['crew']['queries']} searches"
    assert shared[1]["text"] == out["steps"][1]["text"] and shared[2]["text"] == out["steps"][2]["text"]  # counts: the same lines
    stored = json.dumps([r["detail"] for r in c.store.runs], default=str) + json.dumps(run)
    for secret in ("xylophone", "zebra", "marimba", "quokka", "missing labels for"):
        assert secret not in stored, secret
    blob = json.dumps(out["crew"])
    assert not re.search(r"xylophone|zebra|quokka|check_labels|labels", blob)  # the crew block: counts, intent, planner, times
    assert set(out["crew"]) == SUMMARY_KEYS


# ---------------------------------------------------------------- one writer and one set of checks for the chat and the Ask page
def _invents(prompt, media=None):
    """A model's QADraft that cites a real block for a claim no block makes."""
    return {"answer": "The policy engine is deployed with Helm to the EU cluster every Friday by the platform team [1].",
            "cited": [1], "confidence": 0.95, "sufficient": True}


def test_the_chat_refuses_what_the_ask_page_refuses(client):
    """POST /api/chat (qa.answer) runs the Ask page's tier 1: the same retrieval gate and citation check. A draft that cites
    a source for a claim no source makes is not published on either."""
    _c(client).deps.llm = FakeLLM({"QADraft": _invents})
    chat = client.post("/api/chat", json={"project_id": PID, "question": "How is the policy engine deployed?"}, headers=HIRE).json()
    assert chat["status"] == "not_enough_information" and "Helm" not in chat["answer"]
    routed = ask(client, "How is the policy engine deployed?")
    assert routed["status"] == "offered" and routed.get("writer") is None


def test_who_wrote_an_answer_survives_a_reload(client):
    """writer and the crew's summary are kept with the chat turn: the Ask page labels a quoted answer the same after a
    reload (GET /api/chat/history)."""
    out = ask(client, "Which function reports missing labels?")
    assert out["status"] == "answered" and out["writer"] == "extractive"
    turn = next(t for t in client.get(f"/api/chat/history?project_id={PID}", headers=HIRE).json() if t["id"] == out["message_id"])
    assert turn["writer"] == "extractive" and turn["crew"]["kept"] == out["crew"]["kept"]
    chat = client.post("/api/chat", json={"project_id": PID, "question": "Which function reports missing labels?"}, headers=HIRE).json()
    assert chat["status"] == "answered" and chat["writer"] == "extractive" and chat["crew"]["planner"] == "rules"


def _incidents(client) -> list:
    store = _c(client).store
    return [n for u in ("u-priya", "u-admin", "u-ravi") for n in store.notifications_for(u) if n["kind"] == "incident"]


def test_with_the_ai_off_the_ask_page_still_answers_from_sources_and_reaches_a_person(client):
    """No model and no embedder on the server (self-hosted before a key is added): the Ask page is honest and useful. The
    crew searches by the names a question gives, the offline writer quotes the sources (writer "extractive", and the why
    says the AI is off), "Ask a person" reaches the mentor, and nobody gets an incident for a question."""
    from app.agents.core.llm import NoLLM
    from app.services.embeddings import NoEmbedder

    c = _c(client)
    c.deps.llm = NoLLM()
    c.deps.index.embedder = NoEmbedder()
    before = len(_incidents(client))
    out = ask(client, "What does check_labels return?")
    assert out["status"] == "answered" and out["writer"] == "extractive" and out["crew"]["degraded"] is True
    assert any("AI is off" in w for w in out["why"]) and out["citations"][0]["label"].endswith("(check_labels)")
    assert any(s["text"].startswith("Quoted the sources") for s in out["steps"])  # the trace says no model wrote it
    offered = ask(client, "How do I deploy to production?")
    assert offered["status"] == "offered"
    routed = ask(client, "How do I deploy to production?", ask_person=True, message_id=offered["message_id"])
    assert routed["status"] == "routed" and routed["routed_to"]["name"]
    assert len(_incidents(client)) == before


def test_a_failing_embedder_degrades_the_search_instead_of_failing_the_question(client):
    """The question's embedding fails (a 429, the daily quota): the question is still answered from what the keyword and
    exact-name searches find, the answer says semantic search was off, and nobody gets an incident."""
    class Down:
        dim = 768

        def embed(self, texts, task="document"):
            raise RuntimeError("429 RESOURCE_EXHAUSTED (simulated)")

    _c(client).deps.index.embedder = Down()
    before = len(_incidents(client))
    out = ask(client, "What does check_labels return?")
    assert out["status"] == "answered" and out["crew"]["degraded"] is True
    assert any("Semantic search is unavailable" in w for w in out["why"])
    assert len(_incidents(client)) == before


def test_the_demo_seed_spends_no_model_calls_even_with_a_key(monkeypatch):
    """With GEMINI_API_KEY set, the demo's setup (plans, the handover, summaries) runs on the offline model: about 60 calls
    of the free tier's quota were spent before anyone asked anything. The real model answers every request after it."""
    from app.agents.core.llm import GeminiLLM
    from app.config import Settings
    from app.container import build_container

    calls = []
    monkeypatch.setattr(GeminiLLM, "structured", lambda self, *a, **k: calls.append("structured") or (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr(GeminiLLM, "agent_turn", lambda self, *a, **k: calls.append("agent_turn") or (_ for _ in ()).throw(RuntimeError("no")))
    c = build_container(Settings(mode="demo", gemini_api_key="test-key-not-used"), serves_http=False)
    assert calls == [] and isinstance(c.deps.llm, GeminiLLM)
    assert c.lifecycle.verified_answers(PID) and c.store.list_meetings(PID)  # the demo is all there
