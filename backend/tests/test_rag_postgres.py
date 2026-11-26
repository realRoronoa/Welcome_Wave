"""The research crew on PostgreSQL + pgvector, as ww_app under row-level security (the setup of tests/test_postgres.py, whose
fixtures this module reuses): PgIndex.search_grid keeps the port's contract (one embedding call, one connection, every
similarity to queries[0], each kind's own size, nothing stale, nothing from another project or organisation) and agrees with
MemoryIndex where both measure the same thing; and the crew runs on it end to end, its graph's parallel nodes included (they
must see the tenant). Skipped without TEST_PG_ADMIN_URL."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.adapters.memory import MemoryIndex
from app.adapters.postgres import PgIndex, current_org
from app.agents.core.llm import OfflineLLM
from app.agents.core.schemas import Chunk, Utterance
from app.config import Settings
from app.rag import crew
from app.services.chunking import chunk_code, chunk_document, chunk_transcript
from app.services.embeddings import HashEmbedder, cosine
from tests.test_postgres import ADMIN, SAMPLE, _org, db, engine, store

FIXTURES = (db, engine, store)  # test_postgres's database fixtures, reused here by name

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")
KINDS = ["code", "doc", "meeting", "verified_answer"]
FILES = ("engine/validate.py", "engine/policy.py", "engine/mutate.py", "webhooks/server.py", "tests/test_validate.py")


class CountingEmbedder(HashEmbedder):
    def __init__(self):
        super().__init__(768)
        self.calls: list[tuple[int, str]] = []

    def embed(self, texts, task="document"):
        self.calls.append((len(texts), task))
        return super().embed(texts, task)


def _corpus(pid: str) -> list[Chunk]:
    chunks = [c for path in FILES for c in chunk_code(pid, path, (SAMPLE / path).read_text(), "sha1")]
    chunks += chunk_document(pid, str(uuid.uuid4()), "Contributing", ["Run pytest before opening a pull request. Validation functions "
                                                                       "return a list of violations and never raise."])
    chunks += chunk_transcript(pid, str(uuid.uuid4()), "KT #1", [
        Utterance(start_s=0, end_s=30, speaker="Ravi", text="check_labels returns a violation for every missing label"),
        Utterance(start_s=30, end_s=70, speaker="Ravi", text="apply_defaults adds the default labels before validation")])
    chunks.append(Chunk(id=f"va-{uuid.uuid4().hex[:8]}", project_id=pid, source_type="verified_answer",
                        ref={"question": "How do I add a default label?", "expert_name": "Ravi"},
                        content="Q: How do I add a default label?\nA (verified by Ravi): add it to DEFAULT_LABELS in engine/mutate.py"))
    return chunks


@pytest.fixture
def world(store, engine):
    admin, project = _org(store, "ragpg")
    other = store.create_project(admin.org_id, "Other", "", admin.id)
    pid, oid = str(project.id), str(other.id)
    embedder = CountingEmbedder()
    pg = PgIndex(engine, embedder)
    chunks = _corpus(pid)
    pg.upsert(chunks)
    pg.upsert([c.model_copy(update={"id": f"x-{c.id}", "project_id": oid}) for c in chunks])  # the same words, another project
    stale = next(c for c in chunks if c.symbol == "check_privileged")
    assert pg.run("UPDATE chunks SET stale = true WHERE id = :i", i=stale.id) == 1
    mem = MemoryIndex(HashEmbedder(768))
    mem.upsert(chunks)
    mem.chunks[stale.id] = stale.model_copy(update={"stale": True})
    yield SimpleNamespace(admin=admin, pid=pid, oid=oid, pg=pg, mem=mem, embedder=embedder, stale=stale, chunks=chunks)
    current_org.set(None)


def test_search_grid_on_postgres_keeps_the_contract(world, engine):
    pg, pid = world.pg, world.pid
    queries = ["Where is check_labels defined?", "check_labels", "add default labels", "privileged containers"]
    k = {"code": 5, "doc": 2, "meeting": 1, "verified_answer": 1}
    checkouts = []
    listener = lambda *a: checkouts.append(1)
    event.listen(engine.pool, "checkout", listener)
    world.embedder.calls.clear()
    try:
        grid = pg.search_grid(pid, queries, KINDS, k)
    finally:
        event.remove(engine.pool, "checkout", listener)
    assert world.embedder.calls == [(len(queries), "query")]  # one embedding call for every query
    assert len(checkouts) == 1  # every (query, kind) on one connection
    assert len(grid) == len(queries) and all(set(row) == set(KINDS) for row in grid)

    anchor = HashEmbedder(768).embed([queries[0]], "query")[0]
    vectors = dict(zip((c.id for c in world.chunks), HashEmbedder(768).embed([c.content for c in world.chunks], "document")))
    for row in grid:
        for kind, hits in row.items():
            assert len(hits) <= k[kind]
            for h in hits:
                assert h.chunk.source_type == kind and h.chunk.project_id == pid and not h.chunk.stale
                assert h.chunk.id != world.stale.id and not h.chunk.id.startswith("x-")
                # the similarity is to the question as asked, whichever query found the hit
                assert h.similarity == pytest.approx(max(0.0, cosine(anchor, vectors[h.chunk.id])), abs=2e-3)
    assert grid[1]["code"][0].chunk.symbol == "check_labels"
    assert grid[0]["meeting"] and grid[0]["verified_answer"]
    assert all(h.chunk.id != world.stale.id for row in grid for hits in row.values() for h in hits)

    # queries[0]'s similarities are search()'s, and its code list holds search()'s best
    plain = {h.chunk.id: h.similarity for h in pg.search(pid, queries[0], 5, ["code"])}
    for h in grid[0]["code"]:
        if h.chunk.id in plain:
            assert h.similarity == pytest.approx(plain[h.chunk.id], abs=1e-6)
    assert grid[0]["code"][0].chunk.id in plain
    assert pg.search_grid(pid, [], KINDS, k) == []


def test_search_grid_agrees_with_memory(world):
    """Both adapters measure the same similarity and find the same thing for a name. Their keyword channels differ (BM25 in
    memory, ts_rank_cd in Postgres), and Postgres's vector channel keeps chunks with no positive similarity as its search()
    does, so whole lists are compared by overlap, not order or length."""
    queries = ["What does validate_resource do?", "validate_resource", "missing label violation", "apply_defaults"]
    k = {"code": 6, "doc": 2, "meeting": 2, "verified_answer": 1}
    pg = world.pg.search_grid(world.pid, queries, KINDS, k)
    mem = world.mem.search_grid(world.pid, queries, KINDS, k)
    for p_row, m_row in zip(pg, mem):
        for kind in KINDS:
            p_ids, m_ids = [h.chunk.id for h in p_row[kind]], [h.chunk.id for h in m_row[kind]]
            assert len(m_ids) <= len(p_ids) == min(k[kind], len(p_ids))
            if m_ids:
                assert len(set(p_ids) & set(m_ids)) / len(m_ids) >= 0.5
            sims = {h.chunk.id: h.similarity for h in m_row[kind]}
            for h in p_row[kind]:
                if h.chunk.id in sims:
                    assert h.similarity == pytest.approx(sims[h.chunk.id], abs=2e-3)
    for qi in (1, 3):
        assert pg[qi]["code"][0].chunk.id == mem[qi]["code"][0].chunk.id


def test_the_crew_runs_on_postgres(world):
    """The crew's graph runs retrieve and model_plan in parallel worker threads: they must read as this organisation (row-level
    security), or every search would come back empty."""
    deps = SimpleNamespace(index=world.pg, llm=OfflineLLM(), settings=Settings(mode="demo"))
    steps = []
    r = crew.retrieve(deps, world.pid, "Where is check_labels defined?", steps=steps)
    assert r.hits and r.hits[0].chunk.symbol == "check_labels" and r.planner == "rules" and not r.judged
    assert [s.tool for s in steps] == ["plan_search", "search_knowledge", "rank_evidence"]
    assert all(h.chunk.project_id == world.pid and h.chunk.id != world.stale.id for h in r.hits)
    assert crew.search(deps, world.pid, "add default labels", kinds=["verified"])[0].chunk.source_type == "verified_answer"
    named = crew.search(deps, world.pid, "what is in engine/policy.py?", kinds=["code"])
    assert named[0].chunk.ref["path"] == "engine/policy.py"

    current_org.set(None)  # no tenant: row-level security shows nothing, and the crew says so instead of failing
    assert crew.retrieve(deps, world.pid, "Where is check_labels defined?").hits == []


def test_postgres_reads_definitions_in_one_order_and_skips_unknown_kinds(world):
    """symbols_named follows (path, start line), not the heap order a re-index leaves; an unknown kind has no hits (as in
    MemoryIndex), never a DataError from the enum cast."""
    pg, pid = world.pg, world.pid
    files = [chunk_code(pid, f"jobs/job{i}.py", f"def run(day):\n    return {i}\n", "sha1") for i in range(8)]
    pg.upsert([c for f in reversed(files) for c in f])
    want = [f"jobs/job{i}.py" for i in range(8)]
    assert [r.path for r in pg.symbols_named(pid, "run")] == want
    pg.upsert(files[0] + files[1] + files[2])  # unchanged files re-indexed: new row versions, same order
    assert [r.path for r in pg.symbols_named(pid, "run")] == want
    mem = MemoryIndex(HashEmbedder(768))
    mem.upsert([c for f in files for c in f])
    assert [r.path for r in mem.symbols_named(pid, "run")] == want
    assert [r.start_line for r in pg.symbols_in(pid, "engine/policy.py")] == sorted(r.start_line for r in pg.symbols_in(pid, "engine/policy.py"))
    grid = pg.search_grid(pid, ["check_labels"], ["kt", "code"], {"kt": 3, "code": 3})
    assert grid[0]["kt"] == [] and grid[0]["code"]


def test_postgres_keyword_only_search_and_a_failed_query_embedding(world):
    pg, pid = world.pg, world.pid
    world.embedder.calls.clear()
    grid = pg.search_grid(pid, ["check_labels", "privileged containers"], KINDS, {kd: 3 for kd in KINDS}, semantic=False)
    assert world.embedder.calls == []  # no embedding call at all
    assert grid[0]["code"][0].chunk.symbol == "check_labels" and all(h.similarity == 0.0 for row in grid for hs in row.values() for h in hs)
    assert all(h.chunk.id != world.stale.id and h.chunk.project_id == pid for row in grid for hs in row.values() for h in hs)

    class Down(CountingEmbedder):
        def embed(self, texts, task="document"):
            if task == "query":
                raise RuntimeError("429 RESOURCE_EXHAUSTED (simulated)")
            return super().embed(texts, task)

    deps = SimpleNamespace(index=PgIndex(pg.engine, Down()), llm=OfflineLLM(), settings=Settings(mode="demo"))
    r = crew.retrieve(deps, pid, "Where is check_labels defined?")
    assert r.degraded and r.hits[0].chunk.symbol == "check_labels" and all(h.similarity == 0.0 for h in r.hits)
