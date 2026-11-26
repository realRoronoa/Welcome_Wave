"""The Guide's research crew (docs/specs/rag-crew.md): Planner, four Retrievers, Fusion and a Judge in front of every writer.

    retrieve(deps, project_id, question, k=8, purpose="answer", kinds=None, steps=None, on_step=None) -> Retrieval
    search(deps, project_id, query, k=8, kinds=None) -> list[Hit]          # the search_knowledge tool: rules only

retrieve() is a LangGraph subgraph, compiled once per process:

    START -> analyze -+-> retrieve ---+-> more -> rank -(a model can judge)-> judge -> END
                      +-> model_plan -+                \\-(otherwise)------------------> END

    analyze     the rules' plan (app/rag/analyze.py): intent, names, subject, parts, up to 5 queries
    retrieve    every query x every kind in one batched pass (Index.search_grid: one embedding call), plus the code
                retriever's exact-name lookups: symbols_named for a function or class the question names, the file a path names
    model_plan  concurrently, when a model can think: up to 4 rewrites and a hypothetical passage (QueryPlan), time-boxed
    more        the model's queries through the same grid (the asked question stays the similarity anchor)
    rank        weighted RRF over every list, then the lexical cross-scorer (app/rag/rerank.py; its weights follow the
                embedder: rerank.SEMANTIC_WEIGHTS when it reads meaning), then the expansion (the top files' other chunks
                that hold half the subject words; bare class lines read with their first member); spans that mostly repeat
                each other collapse to one
    judge       when a model can think: it grades the top candidates 0-3 (EvidenceGrades), time-boxed; the grade ranks first

search() calls the same functions directly, without the model stages, the graph or steps.

The model stages run only for purpose="answer", when the rag_model_* setting allows, and when the model can think
(can_think: never OfflineLLM, NoLLM or an agent in rules mode; FakeLLM only for a schema the test scripted). A model stage
that errs, returns something invalid or runs past its time leaves the rules' result in place, logged without the question.
Each stage's call runs under llm.call_budget(its time box): it starts no retry or fallback past it, so no work goes on
behind a stage nobody waits for; and each counts against the organisation's daily model turns (runtime.spend_model_call):
past that budget the stage is skipped.

When the query embedding fails (a 429 or 503, the daily quota), the keyword channel and the exact lookups run alone, every
similarity 0.0, and Retrieval.degraded says so (the trace too). A qualified name ('ReportJob.run') is looked up through its
owner: the run inside ReportJob, then ReportJob; only those chunks 'define' it.
Model output is validated in code: queries are plain strings that only feed a read-only search of this project; grades count
only for the numbers shown. The question and every source reach the model inside embed_data, under prompts.UNTRUSTED.

Hit.similarity keeps its meaning for the retrieval gate: the cosine to the question as asked (0.0 when only an exact-name
lookup found the source). Hit.score is the crew's final score. Steps and summary() carry counts, never the question.
"""
from __future__ import annotations

import contextvars
import dataclasses
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.core import prompts
from app.agents.core.citations import label_for
from app.agents.core.llm import FakeLLM, GeminiLLM, call_budget, embed_data
from app.agents.core.schemas import Chunk, EvidenceGrades, Hit, QueryPlan
from app.ports import AIUnavailable
from app.rag import rerank
from app.rag import text as T
from app.rag.analyze import INTENTS, KINDS, PRIORS, Analysis, Query, analyze
from app.rag.fuse import ranked, rrf
from app.services.chunking import module_of

log = logging.getLogger("rag.crew")

SHORT = {"code": "code", "doc": "doc", "meeting": "kt", "verified_answer": "verified"}  # summary and model vocabulary
FROM_SHORT = {"code": "code", "doc": "doc", "kt": "meeting", "verified": "verified_answer", "meeting": "meeting",
              "verified_answer": "verified_answer"}
KIND_WORDS = {"code": "code", "doc": "documents", "meeting": "KT calls", "verified_answer": "verified answers"}
COUNT_WORDS = {"code": "code", "doc": "docs", "meeting": "KT", "verified_answer": "verified"}
K_SETTINGS = {"code": ("rag_k_code", 12), "doc": ("rag_k_doc", 6), "meeting": ("rag_k_meeting", 6), "verified_answer": ("rag_k_verified", 4)}

MAX_MODEL_QUERIES = 4
MAX_MODEL_QUERY_CHARS = 200
MAX_HYPOTHETICAL_CHARS = 400
MODEL_WEIGHT = 0.8  # a model rewrite's lists in the fusion (the question as asked and its names weigh 1.0)
HYPOTHETICAL_WEIGHT = 0.6
EXACT_WEIGHT = 2.0  # the exact-name list: a name the question gives is the strongest evidence there is
MAX_NAMES = 6  # names the code retriever looks up exactly
MAX_REFS = 5  # definitions of one name it reads
FILE_CHUNKS = 6  # chunks of a named file it reads, in line order
MAX_PER_FILE = 3  # hits from one file (or one document) in the top k
EXPAND_FILES = 3  # the expansion reads the other chunks of this many files...
EXPAND_FROM = 5  # ...the first files among this many top candidates...
EXPAND_MAX = 40  # ...at most this many chunks of each...
EXPAND_COVER = 0.5  # ...and keeps those that hold at least this share of the question's subject words
BARE_LINES = 3  # a class chunk of at most this many lines is read with its first member (_bare)
BARE_FILES = 2  # files read only for a bare class chunk's context
SNIPPET = 600  # characters of each source the judge sees
UNGRADED = 1  # a candidate the judge did not grade ranks with the "related" ones, by its lexical score
MAX_MODULES = 60  # module names the model planner sees (the project's vocabulary)
DEGRADED_NOTE = " · keyword search only: semantic search is unavailable right now"


# ---------------------------------------------------------------- the result
@dataclass
class Retrieval:
    hits: list[Hit]  # best first, at most k. similarity: cosine to the question as asked; score: the crew's final score
    analysis: Analysis  # the plan that ran (the model's queries included)
    candidates: int  # distinct chunks considered
    by_kind: dict[str, int]  # candidates per source type: code, doc, meeting, verified_answer
    planner: Literal["rules", "model"]
    judged: bool  # the model graded the candidates
    reasons: dict[str, list[str]] = field(default_factory=dict)  # chunk id -> why it ranked where it did (our words only)
    grades: dict[str, int] = field(default_factory=dict)  # chunk id -> the model's grade, when judged
    ms: dict[str, int] = field(default_factory=dict)  # plan, retrieve, rank, judge, total
    degraded: bool = False  # the query embedding failed: keyword and exact-name search only, every similarity 0.0
    model_calls: int = 0  # the crew's own model calls (planner, judge), counted against the organisation's daily turns

    def summary(self) -> dict:
        """The `crew` block of an answer: counts, intent, planner, judged, whether search ran degraded, and times. Never
        the question, a query or the model's words."""
        return {"intent": self.analysis.intent, "queries": len(self.analysis.queries), "planner": self.planner,
                "candidates": self.candidates, "by_kind": {SHORT[kd]: int(self.by_kind.get(kd, 0)) for kd in KINDS},
                "kept": len(self.hits), "judged": self.judged, "degraded": self.degraded,
                "ms": {key: int(self.ms.get(key, 0)) for key in ("plan", "retrieve", "rank", "judge", "total")}}


# ---------------------------------------------------------------- can a model think here?
def can_think(llm, schema: type | None = None) -> bool:
    """A model that may plan or judge: Gemini, or a FakeLLM whose test scripted that schema (either crew schema when None).
    Never the offline demo model, the AI off (NoLLM) or an agent in rules mode (registry.RulesLLM): those are not on the list."""
    if isinstance(llm, FakeLLM):
        names = [schema.__name__] if schema is not None else [QueryPlan.__name__, EvidenceGrades.__name__]
        return any(n in llm.responders for n in names)
    return isinstance(llm, GeminiLLM)


def _setting(deps, name: str, default):
    return getattr(getattr(deps, "settings", None), name, default)


def _semantic(deps) -> bool:
    """Whether this index's embedder reads meaning (rerank.semantic): the Judge then lets the question's own similarity
    carry more (rerank.SEMANTIC_WEIGHTS)."""
    return rerank.semantic(getattr(getattr(deps, "index", None), "embedder", None))


def _base_k(deps) -> dict[str, int]:
    return {kd: max(1, int(_setting(deps, name, dflt))) for kd, (name, dflt) in K_SETTINGS.items()}


def _kinds(kinds) -> list[str]:
    """Source types, from either vocabulary ('kt' or 'meeting', 'verified' or 'verified_answer'); None or [] -> all four.
    Kinds given but none known -> [] (no source type, so no hits): an unknown filter never widens to every kind."""
    if not kinds:
        return list(KINDS)
    return list(dict.fromkeys(FROM_SHORT[x] for x in kinds if x in FROM_SHORT))


class _Late(TimeoutError):
    """A model stage ran past its time box."""


def _within(fn: Callable[[], Any], seconds: float):
    """fn() in a worker thread, waited for at most `seconds`: past that its result is not waited for and its answer is
    dropped. Every model call inside runs under call_budget(seconds), so it starts nothing past that time (no retry, no
    fallback model) and its request times out with it: the thread ends on its own, soon after."""
    box: dict = {}
    done = threading.Event()
    ctx = contextvars.copy_context()

    def bounded():
        with call_budget(seconds):
            return fn()

    def run():
        try:
            box["out"] = ctx.run(bounded)
        except BaseException as e:  # noqa: BLE001 - handed to the caller below
            box["err"] = e
        finally:
            done.set()

    threading.Thread(target=run, name="rag-model", daemon=True).start()
    if not done.wait(max(0.0, seconds)):
        raise _Late(f"no answer within {seconds:.1f}s")
    if "err" in box:
        raise box["err"]
    return box["out"]


# ---------------------------------------------------------------- retrievers
def _grid(index, project_id: str, queries: list[str], kinds: list[str], k: dict[str, int], semantic: bool = True) -> list[dict[str, list[Hit]]]:
    """Index.search_grid, or, for an index without it, search() per (query, kind) with each hit's similarity taken from the
    queries[0] searches (0 when they did not find it), so similarity keeps meaning 'to the question as asked'.
    semantic=False: the keyword channel alone (no embedding call), every similarity 0.0."""
    fn = getattr(index, "search_grid", None)
    if callable(fn):
        return fn(project_id, queries, kinds, k) if semantic else fn(project_id, queries, kinds, k, semantic=False)
    if not semantic:
        raise TypeError("this index has no keyword-only search")
    rows = [{kd: index.search(project_id, q, k.get(kd, 8), [kd]) for kd in kinds} for q in queries]
    anchor = {h.chunk.id: h.similarity for hits in (rows[0].values() if rows else ()) for h in hits}
    return [{kd: [h.model_copy(update={"similarity": anchor.get(h.chunk.id, 0.0)}) for h in hits] for kd, hits in row.items()}
            for row in rows]


def _module_paths(name: str) -> str | None:
    """'app.config' may be the module app/config.py: the path it would have, without its extension."""
    if T.is_path(name) or "." not in name or name.endswith("()"):
        return None
    return name.replace(".", "/")


def match_paths(name: str, paths: list[str]) -> list[str]:
    """The indexed files a path the question gives names: itself, or those that end with it; a dotted module name
    ('app.config') names app/config.<ext>. Shortest first."""
    out = []
    module = _module_paths(name)
    for p in paths:
        if module is not None:
            stem = p.rsplit(".", 1)[0] if "." in p.rsplit("/", 1)[-1] else p
            if stem == module or stem.endswith("/" + module):
                out.append(p)
        elif not name.endswith("/") and rerank.path_matches(p, name):
            out.append(p)
    return sorted(out, key=lambda p: (len(p), p))


CONTAINERS = frozenset({"class", "struct", "interface", "enum", "trait", "impl", "type", "object", "record", "module", "namespace"})


def _head(c: Chunk) -> tuple[int, str] | None:
    """The indentation and first word of a chunk's first line of code (header, decorators and blank lines skipped)."""
    for line in c.content.split("\n"):
        if line.startswith(("File: ", "Symbol: ", "Document: ", "Meeting: ")):
            continue
        s = line.strip()
        if s and not s.startswith("@"):
            wide = line.expandtabs(4)
            return len(wide) - len(wide.lstrip()), s.split(None, 1)[0].rstrip(":")
    return None


def _refs(index, project_id: str, name: str) -> list:
    """symbols_named in one order whatever the adapter: by path (code point), then start line."""
    return sorted(index.symbols_named(project_id, name), key=lambda r: (r.path or "", r.start_line or 0))


def _members(index, project_id: str, owner: str, sym: str, chunks_of) -> tuple[list[Chunk], list[Chunk]]:
    """For a qualified name ('ReportJob.run'): the chunks of `sym` that sit inside a class (struct, interface ...) named
    `owner`, and that class's own chunks. Inside means: in the owner's file, after its line, before the next line of code
    indented no deeper than the owner's. When no chunk of `sym` is inside, the owner chunks whose body defines `sym` (a class
    attribute or field: 'Rule.kind') are the members."""
    inside: list[Chunk] = []
    owners: list[Chunk] = []
    for ref in _refs(index, project_id, owner)[:MAX_REFS]:
        chunks = chunks_of(ref.path)
        own = next((c for c in chunks if c.id == ref.chunk_id), None)
        head = _head(own) if own is not None else None
        if own is None or head is None or head[1] not in CONTAINERS:
            continue
        mine = [own]
        start = int(own.ref.get("start_line") or 0)
        for c in chunks:
            if int(c.ref.get("start_line") or 0) <= start or c.id == own.id:
                continue
            h = _head(c)
            if h is not None and h[0] <= head[0]:
                break  # the next line of code at the owner's depth: the owner ended
            if c.symbol == owner and owner != sym:  # the owner's own long body, split in parts
                mine.append(c)
            elif c.symbol == sym:
                inside.append(c)
        owners += mine
    if not inside:
        defines = rerank.definition_pattern(sym)
        inside = [c for c in owners if defines.search(_body(c))]
    return inside, owners


def _exact(index, project_id: str, analysis: Analysis, kinds: list[str]) -> tuple[list[Chunk], dict[str, frozenset[str]]]:
    """The code retriever's exact lookups: the definition of every function or class the question names (symbols_named),
    and the first chunks of every file a path in it names. Line order within a file; nothing stale. A qualified name
    ('ReportJob.run') is looked up through its owner: the `run` inside ReportJob first, then ReportJob itself; other classes'
    `run` only when the owner holds none. Second value: each qualified name that resolved -> the ids of the chunks that
    define it (the Judge credits 'defines ReportJob.run' to those alone)."""
    if "code" not in kinds or not analysis.identifiers:
        return [], {}
    names = analysis.identifiers[:MAX_NAMES]
    files: dict[str, list[Chunk]] = {}
    out: list[Chunk] = []
    seen: set[str] = set()
    resolved: dict[str, frozenset[str]] = {}

    def chunks_of(path: str) -> list[Chunk]:
        if path not in files:
            files[path] = list(index.file_chunks(project_id, path))
        return files[path]

    def add(c: Chunk) -> None:
        if c.id not in seen and not c.stale:
            seen.add(c.id)
            out.append(c)

    for n in names:
        if T.is_path(n):
            continue
        sym = T.symbol_of(n)
        if len(sym) < 3:
            continue
        owner = T.qualifier_of(n)
        if owner and len(owner) >= 2:
            inside, owners = _members(index, project_id, owner, sym, chunks_of)
            for c in inside + owners:
                add(c)
            if inside:
                resolved[n] = frozenset(c.id for c in inside if not c.stale)
                continue
        for ref in _refs(index, project_id, sym)[:MAX_REFS]:
            for c in chunks_of(ref.path):
                if c.id == ref.chunk_id:
                    add(c)
    wanted = [n for n in names if (T.is_path(n) and not n.endswith("/")) or _module_paths(n)]
    if wanted:
        paths = list(index.all_paths(project_id))
        for n in wanted:
            for p in match_paths(n, paths)[:2]:
                for c in chunks_of(p)[:FILE_CHUNKS]:
                    add(c)
    return out, resolved


@dataclass
class _Found:
    rows: list  # (Query, {kind: [Hit]}) per query
    exact: list[Chunk]  # the exact-name lookups' chunks
    resolved: dict[str, frozenset[str]]  # qualified name -> the ids of the chunks that define it (see _exact)
    degraded: bool  # the query embedding failed: the keyword channel alone ran (every similarity 0.0)


def _search(index, project_id: str, queries: list[str], kinds: list[str], sizes: dict[str, int], degraded: bool) -> tuple[list, bool]:
    """The grid, and whether it ran degraded. When the query embedding fails (a 429 or 503, the daily quota, a busy
    embedder), the keyword channel runs alone: an exact-name or keyword question still finds its sources, every similarity
    is 0.0 (no meaning was measured, so the gate's similarity rule cannot pass), and the caller says that semantic search
    was off. The AI being off (AIUnavailable: no embedder at all) still propagates, as does a failure of the keyword search."""
    if not degraded:
        try:
            return _grid(index, project_id, queries, kinds, sizes), False
        except AIUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 - re-raised below when the index cannot search by keyword alone
            log.warning("crew: the query embedding failed (%s); keyword search only", type(e).__name__)
            try:
                return _grid(index, project_id, queries, kinds, sizes, semantic=False), True
            except TypeError:
                raise e from None
    return _grid(index, project_id, queries, kinds, sizes, semantic=False), True


def _gather(deps, project_id: str, analysis: Analysis, kinds: list[str], sizes: dict[str, int]) -> _Found:
    grid, degraded = _search(deps.index, project_id, [q.text for q in analysis.queries], kinds, sizes, False)
    exact, resolved = _exact(deps.index, project_id, analysis, kinds)
    return _Found(list(zip(analysis.queries, grid)), exact, resolved, degraded)


# ---------------------------------------------------------------- fusion and the lexical judge
def _fuse(rows: list[tuple[Query, dict[str, list[Hit]]]], exact: list[Chunk]) -> tuple[list[Hit], frozenset[str]]:
    """Weighted RRF over every (query, kind) list a query is meant for, and the exact-name list. Each candidate keeps the
    similarity its grid hit carries (the anchor's); an exact-only candidate has 0.0, and its id is in the second value."""
    pool: dict[str, Hit] = {}
    lists: list[tuple[list[str], float]] = []
    for q, row in rows:
        for kd, hits in row.items():
            if q.kinds and kd not in q.kinds:
                continue
            lists.append(([h.chunk.id for h in hits], q.weight))
            for h in hits:
                if h.chunk.id not in pool or h.similarity > pool[h.chunk.id].similarity:
                    pool[h.chunk.id] = h
    measured = set(pool)
    for c in exact:
        pool.setdefault(c.id, Hit(chunk=c, score=0.0, similarity=0.0))
    lists.append(([c.id for c in exact], EXACT_WEIGHT))
    scores = rrf(lists)
    fused = [pool[cid].model_copy(update={"score": scores[cid]}) for cid in ranked(scores) if cid in pool]
    return fused, frozenset(cid for cid in pool if cid not in measured)


@dataclass
class _Scored:
    hit: Hit
    lexical: float
    features: dict


def _overlaps(a: Chunk, b: Chunk) -> bool:
    """Two code spans of one file that are mostly the same lines: one holds the other, or they share more than half of the
    smaller one. Neighbouring windows (markdown, YAML, scripts: chunk_code's 80-line windows share 10 lines) are not: each
    holds lines the other does not, and only one of them may hold the answer."""
    ra, rb = a.ref or {}, b.ref or {}
    if a.source_type != "code" or b.source_type != "code" or ra.get("path") != rb.get("path"):
        return False
    try:
        a0, a1, b0, b1 = int(ra["start_line"]), int(ra["end_line"]), int(rb["start_line"]), int(rb["end_line"])
    except (KeyError, TypeError, ValueError):
        return False
    shared = min(a1, b1) - max(a0, b0) + 1
    return shared > 0 and shared * 2 > min(a1 - a0 + 1, b1 - b0 + 1)


def _score(analysis: Analysis, fused: list[Hit], unmeasured=frozenset(), resolved: dict[str, frozenset[str]] | None = None,
           semantic: bool = False, contexts: dict[str, str] | None = None,
           alternates: dict[str, frozenset[str]] | None = None) -> list[_Scored]:
    """The lexical cross-scorer over the fused list, best first (ties by fused rank). `semantic`: the index's embedder reads
    meaning (rerank.semantic): its weights and its reading of similarity."""
    weights = rerank.SEMANTIC_WEIGHTS if semantic else rerank.WEIGHTS
    stats = rerank.pool_stats(analysis, fused, unmeasured, resolved, semantic=semantic, texts=contexts, alternates=alternates)
    scored = []
    for h in fused:
        f = rerank.features(analysis, h, stats)
        scored.append(_Scored(h, rerank.score(f, weights), f))
    scored.sort(key=lambda s: (-s.lexical, stats.rank[s.hit.chunk.id]))
    return scored


def _collapse(scored: list[_Scored]) -> list[_Scored]:
    """A code span that mostly repeats a better one (_overlaps) collapses into it."""
    kept: list[_Scored] = []
    for s in scored:
        if not any(_overlaps(s.hit.chunk, o.hit.chunk) for o in kept):
            kept.append(s)
    return kept


def _rank(analysis: Analysis, fused: list[Hit], unmeasured=frozenset(), resolved: dict[str, frozenset[str]] | None = None,
          semantic: bool = False, contexts: dict[str, str] | None = None,
          alternates: dict[str, frozenset[str]] | None = None) -> list[_Scored]:
    """_score, then _collapse: the Judge's order of one pool."""
    return _collapse(_score(analysis, fused, unmeasured, resolved, semantic, contexts, alternates))


def _bare(c: Chunk) -> bool:
    """A class (struct, interface ...) chunk that holds little more than its own line: `class X:` and a short docstring,
    when its body starts with a method. Alone it has only its name to match."""
    if c.source_type != "code" or not c.symbol:
        return False
    head = _head(c)
    lines = [x for x in _body(c).split("\n") if x.strip()]
    return head is not None and head[1] in CONTAINERS and len(lines) <= BARE_LINES


def _contexts(chunks: list[Chunk]) -> dict[str, str]:
    """For each bare class chunk of one file (line order): its text and its first member's, the words it is read by."""
    out = {}
    for c, nxt in zip(chunks, chunks[1:]):
        if _bare(c):
            out[c.id] = c.content + "\n" + _body(nxt)
    return out


def _expand(index, project_id: str, order: list[_Scored], have: set[str], contexts: dict[str, str]) -> list[Hit]:
    """Pool recall: the other chunks of the files the first ranking put on top. A question in other words than the answer
    often still finds the answer's file through one chunk of it (a method of the class, the neighbouring window of a
    document); its sibling chunks then compete on the question's words. Code files only (a repository's documentation
    included), at most EXPAND_FILES files among the first EXPAND_FROM, EXPAND_MAX chunks each. The added chunks carry
    similarity 0.0: no similarity to the question was measured for them (they rank as unmeasured, as exact finds do).
    Each file read also gives its bare class chunks their context (into `contexts`).
    Measured on the dev split, then checked on the holdout and the unseen corpus (eval/rag_eval.py)."""
    files: list[str] = []
    for s in order[:EXPAND_FROM]:
        c = s.hit.chunk
        path = (c.ref or {}).get("path") if c.source_type == "code" else None
        if path and path not in files:
            files.append(path)
        if len(files) == EXPAND_FILES:
            break
    for s in order:  # a bare class chunk among the candidates: its file is read for its context
        c = s.hit.chunk
        path = (c.ref or {}).get("path")
        if _bare(c) and path not in files and len(files) < EXPAND_FILES + BARE_FILES:
            files.append(path)
    out: list[Hit] = []
    for i, path in enumerate(files):
        try:
            chunks = [c for c in index.file_chunks(project_id, path) if not c.stale]
        except Exception:  # noqa: BLE001 - the expansion is a bonus: the first ranking stands without it
            log.warning("crew: reading a file's chunks failed; no expansion")
            return out
        contexts.update(_contexts(chunks))
        if i >= EXPAND_FILES:
            continue  # read for a bare class chunk's context only
        for c in chunks[:EXPAND_MAX]:
            if c.id not in have:
                have.add(c.id)
                out.append(Hit(chunk=c, score=0.0, similarity=0.0))
    return out


def _owned(fused: list[Hit], resolved: dict[str, frozenset[str]] | None) -> dict[str, str]:
    """A member a qualified name resolved to ('ToolCtx.cite' -> the cite inside ToolCtx) is read with its owner's name: the
    method's own text rarely says which class it is in, and the question's words include it."""
    out: dict[str, str] = {}
    by_id = {h.chunk.id: h.chunk for h in fused}
    for name, ids in (resolved or {}).items():
        owner = T.qualifier_of(name)
        for cid in ids:
            if owner and cid in by_id:
                out[cid] = f"{owner}\n{by_id[cid].content}"
    return out


def _covers(analysis: Analysis, c: Chunk, text: str | None = None) -> float:
    """The share of the question's subject words a chunk holds (in any of their forms)."""
    if not analysis.subject:
        return 0.0
    keys = T.terms((text or c.content) + "\n" + rerank.label_text(c)).counts.keys()
    alts = analysis.alternates
    return sum(1 for s in analysis.subject if s in keys or alts.get(s, frozenset()) & keys) / len(analysis.subject)


def _ranked(deps, project_id: str, analysis: Analysis, kinds: list[str], fused: list[Hit], unmeasured: frozenset[str],
            resolved: dict[str, frozenset[str]] | None) -> tuple[list[_Scored], list[Hit], frozenset[str]]:
    """The Judge's order (_rank), after the expansion (_expand) when code is searched: (order, the pool, its unmeasured ids)."""
    semantic = _semantic(deps)
    contexts = _owned(fused, resolved)
    alternates = rerank.read_alternates(analysis, fused, contexts)  # fixed here: the expanded pool reads the question alike
    first = _score(analysis, fused, unmeasured, resolved, semantic, contexts, alternates)
    order = _collapse(first)
    if "code" not in kinds or not (EXPAND_FILES or BARE_FILES):
        return order, fused, unmeasured
    before = dict(contexts)
    extra = [h for h in _expand(deps.index, project_id, order, {h.chunk.id for h in fused}, contexts)
             if _covers(analysis, h.chunk, contexts.get(h.chunk.id)) >= EXPAND_COVER]
    changed = {cid for cid, text in contexts.items() if before.get(cid) != text}
    if not extra and not changed:
        return order, fused, unmeasured
    pool = fused + extra
    unmeasured = frozenset(unmeasured) | {h.chunk.id for h in extra}
    # The added chunks (and a bare class read anew with its context) are scored against the grown pool; every other
    # candidate keeps its first score, so the expansion only inserts sources, never reshuffles the ones already found.
    second = {s.hit.chunk.id: s for s in _score(analysis, pool, unmeasured, resolved, semantic, contexts, alternates)}
    fresh = {h.chunk.id for h in extra} | changed
    place = {h.chunk.id: i for i, h in enumerate(pool)}
    merged = [second[s.hit.chunk.id] if s.hit.chunk.id in fresh else s for s in first] + [second[h.chunk.id] for h in extra]
    merged.sort(key=lambda s: (-s.lexical, place[s.hit.chunk.id]))
    return _collapse(merged), pool, unmeasured


def _top(order: list[_Scored], k: int) -> list[_Scored]:
    """The first k, at most MAX_PER_FILE from one file or document; none when k < 1."""
    out, per = [], {}
    if k < 1:
        return out
    for s in order:
        key = rerank.file_key(s.hit.chunk)
        if key is not None:
            if per.get(key, 0) >= MAX_PER_FILE:
                continue
            per[key] = per.get(key, 0) + 1
        out.append(s)
        if len(out) == k:
            break
    return out


def _finish(analysis: Analysis, order: list[_Scored], grades: dict[str, int], k: int) -> tuple[list[Hit], dict[str, list[str]]]:
    """The final order: the model's grade first when it judged (an ungraded candidate ranks as 'related', but never above the
    best grade the judge gave: when it found nothing related among the sources it was shown, the ones it was not shown, which
    ranked lower, do not jump ahead of them), the lexical score second. Grade-0 sources go to the bottom, and out of the top k
    once at least 3 sources are graded 1 or more."""
    if grades:
        top = max((s.lexical for s in order), default=0.0)
        span = (top - min((s.lexical for s in order), default=0.0)) or 1.0
        ungraded = min(UNGRADED, max(grades.values()))

        def eff(s: _Scored) -> int:
            return grades.get(s.hit.chunk.id, ungraded)

        order = sorted(order, key=lambda s: (-eff(s), -s.lexical))
        if sum(1 for g in grades.values() if g >= 1) >= 3:
            order = [s for s in order if eff(s) != 0]
        final = {s.hit.chunk.id: round(eff(s) + 0.99 * (s.lexical - (top - span)) / span, 4) for s in order}
    else:
        final = {s.hit.chunk.id: round(s.lexical, 4) for s in order}
    chosen = _top(order, k)
    hits = [s.hit.model_copy(update={"score": final[s.hit.chunk.id]}) for s in chosen]
    why = {}
    for s in chosen:
        lines = rerank.reasons(s.features)
        if s.hit.chunk.id in grades:
            lines.insert(0, f"the model graded it {grades[s.hit.chunk.id]} of 3 for this question")
        why[s.hit.chunk.id] = lines
    return hits, why


# ---------------------------------------------------------------- the model stages
def _body(c: Chunk) -> str:
    """A source's text without the chunker's header lines (its label says where it is from)."""
    lines = c.content.split("\n")
    while lines and lines[0].startswith(("File: ", "Symbol: ", "Document: ", "Meeting: ")):
        lines.pop(0)
    return "\n".join(lines).strip()


def excerpt(text: str, subject, limit: int) -> str:
    """At most `limit` characters of a source for a reader that sees no more: its head when the whole fits, else the run of
    whole lines that holds the most of the question's subject words (stems; the earliest such run). A long document window
    (80 lines, often over 2,500 characters) keeps its answer even when the answer sits past the cut."""
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    want = frozenset(subject or ())
    if not want:
        return text[:limit]
    hits = [len(want & set(T.tokens(x))) for x in lines]
    best, best_start, best_score = 0, 0, -1
    for start in range(len(lines)):
        size, score, end = 0, 0, start
        while end < len(lines) and size + len(lines[end]) + 1 <= limit:
            size += len(lines[end]) + 1
            score += hits[end]
            end += 1
        if end == start:
            continue
        if score > best_score:
            best_start, best, best_score = start, end, score
    if best_score <= 0:
        return text[:limit]
    return "\n".join(lines[best_start:best])[:limit]


def _label(c: Chunk) -> str:
    try:
        return label_for(c)[:200]
    except Exception:  # noqa: BLE001 - a chunk with an unusual ref still gets a label
        return rerank.label_text(c)[:200]


def _modules(index, project_id: str) -> list[str]:
    """The project's modules (first two folders of its code paths): the vocabulary the model planner may use."""
    try:
        paths = list(index.all_paths(project_id))
    except Exception:  # noqa: BLE001 - the planner works without them
        return []
    return sorted({module_of(p) for p in paths})[:MAX_MODULES]


def _key(text: str) -> str:
    return " ".join(T.words(text))


def validated_plan(plan: QueryPlan, analysis: Analysis) -> tuple[list[Query], str | None]:
    """The model's plan as queries the grid may run: plain strings of at most MAX_MODEL_QUERY_CHARS, at most
    MAX_MODEL_QUERIES, none repeating a rules' query or another, plus the hypothetical passage (at most
    MAX_HYPOTHETICAL_CHARS) as one more query. And its intent, when it is one of ours."""
    seen = {_key(q.text) for q in analysis.queries}
    out: list[Query] = []
    for pq in list(plan.queries or [])[:20]:
        text = " ".join(str(pq.text or "").split())[:MAX_MODEL_QUERY_CHARS].strip()
        key = _key(text)
        if len(text) < 2 or not key or key in seen:
            continue
        seen.add(key)
        kinds = tuple(dict.fromkeys(FROM_SHORT[x] for x in (pq.kinds or []) if x in FROM_SHORT)) or None
        out.append(Query(text, kinds, MODEL_WEIGHT, "model"))
        if len(out) == MAX_MODEL_QUERIES:
            break
    hyp = " ".join(str(plan.hypothetical or "").split())[:MAX_HYPOTHETICAL_CHARS].strip()
    if len(hyp) >= 2 and _key(hyp) and _key(hyp) not in seen:
        out.append(Query(hyp, None, HYPOTHETICAL_WEIGHT, "hypothetical"))
    intent = plan.intent if plan.intent in INTENTS else None
    return out, intent


def _model_plan(deps, project_id: str, analysis: Analysis) -> tuple[list[Query], str | None]:
    payload = {"question": analysis.question, "names": analysis.identifiers, "modules": _modules(deps.index, project_id)}
    prompt = "Plan the searches that find the sources for the question in the data.\n" + embed_data(payload)
    plan = _within(lambda: deps.llm.structured(prompt, QueryPlan, system=prompts.RAG_PLAN),
                   float(_setting(deps, "rag_plan_timeout_s", 6.0)))
    if not isinstance(plan, QueryPlan):
        plan = QueryPlan.model_validate(plan)
    return validated_plan(plan, analysis)


def _model_grades(deps, analysis: Analysis, order: list[_Scored]) -> dict[str, int]:
    shown = order[: max(0, int(_setting(deps, "rag_judge_top", 12)))]
    if not shown:
        return {}
    sources = [{"n": i + 1, "source": _label(s.hit.chunk), "kind": KIND_WORDS.get(s.hit.chunk.source_type, "documents"),
                "text": excerpt(_body(s.hit.chunk), analysis.subject, SNIPPET)} for i, s in enumerate(shown)]
    prompt = "Grade every numbered source for the question in the data.\n" + embed_data({"question": analysis.question, "sources": sources})
    out = _within(lambda: deps.llm.structured(prompt, EvidenceGrades, system=prompts.RAG_JUDGE),
                  float(_setting(deps, "rag_judge_timeout_s", 8.0)))
    if not isinstance(out, EvidenceGrades):
        out = EvidenceGrades.model_validate(out)
    grades: dict[str, int] = {}
    for g in list(out.grades or [])[: 4 * len(shown)]:
        try:
            n, value = int(g.n), int(g.g)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(shown):  # grades count only for the numbers shown, the first grade of each
            grades.setdefault(shown[n - 1].hit.chunk.id, min(3, max(0, value)))
    return grades


# ---------------------------------------------------------------- the trace
def _sources(hits: list[Hit]):
    from app.agents.core import runtime as rt

    out = []
    for h in hits[:8]:
        ref = {key: h.chunk.ref[key] for key in rt.REF_KEYS if key in (h.chunk.ref or {})}
        out.append(rt.Source(label=_label(h.chunk), kind=rt.SOURCE_KIND.get(h.chunk.source_type, "doc"), ref={"chunk_id": h.chunk.id, **ref}))
    return out


PEOPLE_VERBS = frozenset({"own", "owns", "owned", "know", "knows", "knew", "wrote", "written", "maintain", "maintains", "explained",
                          "built", "ask", "contact", "responsible"})


def _focus(a: Analysis) -> str:
    """What the plan looks for, for the asker's own trace line (others see counts only). A name the question gives goes into
    a short phrase for its intent ("where check_labels is defined"); bare subject words are never stitched into a sentence,
    they are listed as the words searched for ("for: reports, missing, labels")."""
    names = " and ".join(a.identifiers[:2])
    if a.intent == "overview" and not names:
        return ": an overview of the project"
    if names:
        line = {"locate": f"where {names} is defined", "explain": f"what {names} does", "howto": f"how to use {names}",
                "why": f"why {names} is the way it is", "people": f"who knows {names}"}.get(a.intent, names)
        return (": " + line)[:100]
    words = [w for w in a.words if a.intent != "people" or w not in PEOPLE_VERBS][:5]
    return (" for: " + ", ".join(words))[:100] if words else ""


def _step(steps, on_step, kind: str, text: str, shared: str, tool: str, sources=(), ms: int = 0) -> None:
    if steps is None:
        return
    from app.agents.core import runtime as rt

    rt.step(steps, kind, text, shared=shared, tool=tool, sources=sources, ms=ms, on_step=on_step)


def _counts_line(by_kind: dict[str, int], kinds: list[str]) -> str:
    what = [KIND_WORDS[kd] for kd in kinds]
    searched = what[0] if len(what) == 1 else ", ".join(what[:-1]) + " and " + what[-1]
    total = sum(by_kind.values())
    parts = " · ".join(f"{COUNT_WORDS[kd]} {by_kind[kd]}" for kd in KINDS if by_kind.get(kd))
    return f"Searched {searched}: {total} found" + (f" ({parts})" if parts else "")


def _by_kind(hits: list[Hit]) -> dict[str, int]:
    out = {kd: 0 for kd in KINDS}
    for h in hits:
        out[h.chunk.source_type] = out.get(h.chunk.source_type, 0) + 1
    return out


def _ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)


# ---------------------------------------------------------------- the graph
class CrewState(TypedDict, total=False):
    deps: Any
    project_id: str
    question: str
    k: int
    kinds: list[str]
    steps: list | None
    on_step: Any
    think_plan: bool
    think_judge: bool
    t0: float
    analysis: Analysis
    sizes: dict[str, int]
    rows: list
    exact: list
    resolved: dict  # qualified name -> ids of the chunks that define it (_exact)
    degraded: bool  # the query embedding failed: keyword search only
    model: tuple | None  # (queries, intent) from the model planner
    planner: str
    fused: list
    unmeasured: frozenset
    order: list
    grades: dict
    ms_analyze: int
    ms_plan: int
    ms_retrieve: int
    ms_more: int
    ms_rank: int
    ms_judge: int
    calls_plan: int  # model calls made by the planner (0 or 1)
    calls_judge: int  # ...and by the judge


def _analyze_node(state: CrewState) -> dict:
    t = time.monotonic()
    a = analyze(state["question"])
    return {"analysis": a, "sizes": a.k_for(_base_k(state["deps"]), state["kinds"]), "ms_analyze": _ms(t)}


def _retrieve_node(state: CrewState) -> dict:
    t = time.monotonic()
    found = _gather(state["deps"], state["project_id"], state["analysis"], state["kinds"], state["sizes"])
    return {"rows": found.rows, "exact": found.exact, "resolved": found.resolved, "degraded": found.degraded, "ms_retrieve": _ms(t)}


def _org(deps, project_id: str) -> str | None:
    try:
        return deps.store.get_project(project_id).org_id
    except Exception:  # noqa: BLE001 - no store (a test's deps) or an unknown project: counted without an organisation
        return None


def _spend(state: CrewState, stage: str) -> bool:
    """One model call of the crew against the organisation's daily model budget (runtime.spend_model_call); when it is used
    up the stage is skipped and the rules' result stands."""
    from app.agents.core import runtime as rt

    if rt.spend_model_call(state["deps"], _org(state["deps"], state["project_id"])):
        return True
    log.warning("crew: the organisation's daily model budget is used up; the model %s is skipped", stage)
    return False


def _model_plan_node(state: CrewState) -> dict:
    if not state.get("think_plan") or not _spend(state, "planner"):
        return {"model": None, "ms_plan": 0}
    t = time.monotonic()
    try:
        return {"model": _model_plan(state["deps"], state["project_id"], state["analysis"]), "ms_plan": _ms(t), "calls_plan": 1}
    except Exception as e:  # noqa: BLE001 - the rules' plan stands
        log.warning("crew: the model planner gave nothing usable (%s); the rules' plan stands", type(e).__name__)
        return {"model": None, "ms_plan": _ms(t), "calls_plan": 1}


def _more_node(state: CrewState) -> dict:
    """The model's queries through the grid (the asked question first, as the similarity anchor), then the plan and retrieve
    steps: by now the plan is known whole."""
    deps, a = state["deps"], state["analysis"]
    extra, intent = state.get("model") or ([], None)
    t = time.monotonic()
    rows = list(state["rows"])
    if extra:
        wanted = [kd for kd in state["kinds"] if any(q.kinds is None or kd in q.kinds for q in extra)]  # the kinds they ask for
        grid, _ = _search(deps.index, state["project_id"], [a.queries[0].text] + [q.text for q in extra], wanted, state["sizes"],
                          bool(state.get("degraded")))
        rows += list(zip(extra, grid[1:]))
    planner = "model" if extra else "rules"  # the model planned only if it added a search
    if extra:
        a = dataclasses.replace(a, queries=a.queries + list(extra))
        if a.intent == "other" and intent and intent != "other":  # the rules could not tell; the model's intent sets the priors
            a = dataclasses.replace(a, intent=intent, priors=dict(PRIORS[intent]))
    more_ms = _ms(t)
    fused, unmeasured = _fuse(rows, state["exact"])
    n, m = len(a.queries), len(extra)
    plan_ms = state.get("ms_analyze", 0) + state.get("ms_plan", 0)
    planned = f"Planned {n} search{'es' if n != 1 else ''}"
    _step(state.get("steps"), state.get("on_step"), "think", planned + (f" ({m} from the model)" if m else "") + _focus(a),
          planned, "plan_search", ms=plan_ms)
    by_kind = _by_kind(fused)
    line = _counts_line(by_kind, state["kinds"]) + (DEGRADED_NOTE if state.get("degraded") else "")
    _step(state.get("steps"), state.get("on_step"), "tool", line, line, "search_knowledge", _sources(fused),
          ms=state.get("ms_retrieve", 0) + more_ms)
    return {"analysis": a, "rows": rows, "fused": fused, "unmeasured": unmeasured, "planner": planner, "ms_more": more_ms}


def _rank_node(state: CrewState) -> dict:
    t = time.monotonic()
    order, fused, unmeasured = _ranked(state["deps"], state["project_id"], state["analysis"], state["kinds"], state["fused"],
                                       state.get("unmeasured", frozenset()), state.get("resolved"))
    return {"order": order, "fused": fused, "unmeasured": unmeasured, "ms_rank": _ms(t)}


def _judge_node(state: CrewState) -> dict:
    if not _spend(state, "judge"):
        return {"grades": {}, "ms_judge": 0}
    t = time.monotonic()
    try:
        return {"grades": _model_grades(state["deps"], state["analysis"], state["order"]), "ms_judge": _ms(t), "calls_judge": 1}
    except Exception as e:  # noqa: BLE001 - the lexical ranking stands
        log.warning("crew: the model judge gave nothing usable (%s); the lexical ranking stands", type(e).__name__)
        return {"grades": {}, "ms_judge": _ms(t), "calls_judge": 1}


def build_graph():
    g = StateGraph(CrewState)
    for name, fn in (("analyze", _analyze_node), ("retrieve", _retrieve_node), ("model_plan", _model_plan_node),
                     ("more", _more_node), ("rank", _rank_node), ("judge", _judge_node)):
        g.add_node(name, fn)
    g.add_edge(START, "analyze")
    g.add_edge("analyze", "retrieve")
    g.add_edge("analyze", "model_plan")
    g.add_edge(["retrieve", "model_plan"], "more")  # waits for both: the model planner runs while the rules' searches do
    g.add_edge("more", "rank")
    g.add_conditional_edges("rank", lambda s: "judge" if s.get("think_judge") and s.get("order") else "done",
                            {"judge": "judge", "done": END})
    g.add_edge("judge", END)
    return g.compile(checkpointer=False)


_GRAPH = None
_GRAPH_LOCK = threading.Lock()


def graph():
    """The crew's subgraph, compiled once per process."""
    global _GRAPH
    if _GRAPH is None:
        with _GRAPH_LOCK:
            if _GRAPH is None:
                _GRAPH = build_graph()
    return _GRAPH


# ---------------------------------------------------------------- entry points
def _empty(question: str, kinds: list[str]) -> Retrieval:
    return Retrieval(hits=[], analysis=analyze(question), candidates=0, by_kind={kd: 0 for kd in KINDS}, planner="rules",
                     judged=False, ms={"plan": 0, "retrieve": 0, "rank": 0, "judge": 0, "total": 0})


def retrieve(deps, project_id: str, question: str, *, k: int = 8, purpose: Literal["answer", "tool"] = "answer", kinds=None,
             steps: list | None = None, on_step=None) -> Retrieval:
    """The crew's evidence for one question: at most k hits, best first, with the plan, the counts and the reasons. With
    `steps`, appends the plan, retrieve and rank steps (streamed through on_step). A failed query embedding degrades to keyword
    search (Retrieval.degraded); other index errors propagate, as index.search's did (AIUnavailable included: no embedder
    at all); a model stage's error never does. k < 1 or kinds that are all unknown: no hits."""
    t0 = time.monotonic()
    kinds = _kinds(kinds)
    if not (question or "").strip() or not kinds or k < 1:
        return _empty(question or "", kinds)
    answer = purpose == "answer"
    think_plan = answer and bool(_setting(deps, "rag_model_plan", True)) and can_think(deps.llm, QueryPlan)
    think_judge = answer and bool(_setting(deps, "rag_model_judge", True)) and can_think(deps.llm, EvidenceGrades)
    state = graph().invoke({"deps": deps, "project_id": project_id, "question": question, "k": k, "kinds": kinds, "steps": steps,
                            "on_step": on_step, "think_plan": think_plan, "think_judge": think_judge, "t0": t0})
    grades = state.get("grades") or {}
    hits, why = _finish(state["analysis"], state["order"], grades, k)
    by_kind = _by_kind(state["fused"])
    n3 = sum(1 for g in grades.values() if g == 3)
    line = f"Ranked the evidence: kept {len(hits)} of {len(state['fused'])}" + (
        f" (the model judged {n3} directly relevant)" if grades else "")
    _step(steps, on_step, "verify", line, line, "rank_evidence", _sources(hits), ms=state.get("ms_rank", 0) + state.get("ms_judge", 0))
    ms = {"plan": state.get("ms_analyze", 0) + state.get("ms_plan", 0), "retrieve": state.get("ms_retrieve", 0) + state.get("ms_more", 0),
          "rank": state.get("ms_rank", 0), "judge": state.get("ms_judge", 0), "total": _ms(t0)}
    return Retrieval(hits=hits, analysis=state["analysis"], candidates=len(state["fused"]), by_kind=by_kind,
                     planner=state.get("planner", "rules"), judged=bool(grades), reasons=why, grades=dict(grades), ms=ms,
                     degraded=bool(state.get("degraded")), model_calls=int(state.get("calls_plan", 0)) + int(state.get("calls_judge", 0)))


def search(deps, project_id: str, query: str, *, k: int = 8, kinds=None) -> list[Hit]:
    """The search_knowledge tool's search: the rules' plan, the grid, the exact names, fusion and the lexical judge, called
    directly (no graph, no model, no steps). Hit.similarity is the cosine to `query` as given."""
    kinds = _kinds(kinds)
    if not (query or "").strip() or not kinds or k < 1:
        return []
    a = analyze(query)
    found = _gather(deps, project_id, a, kinds, a.k_for(_base_k(deps), kinds))
    fused, unmeasured = _fuse(found.rows, found.exact)
    order, _, _ = _ranked(deps, project_id, a, kinds, fused, unmeasured, found.resolved)
    hits, _ = _finish(a, order, {}, k)
    return hits
