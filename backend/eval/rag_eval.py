"""Evaluation of the Guide's research crew (docs/specs/rag-crew.md, "Evaluation"): retrieval quality per method, and the
Guide's tier-1 answers end to end.

    cd backend && .venv/bin/python -m eval.rag_eval [--corpus demo|self|unseen|all] [--split dev|holdout|unseen|all]
        [--method baseline|crew|both] [--e2e] [--json PATH] [--worst N] [--live] [--gap S] [--unseen-dir DIR]

Three corpora, each with a golden set (eval/README.md says how a case is written and graded):
- demo    the seeded demo project (MODE=demo: the sample repo, the KT call, the verified answer): eval/golden_demo.jsonl
- self    Welcome Wave's own backend/app/**/*.py and docs/*.md, chunked the way ingestion chunks a connected repository
          (chunk_code for every file, markdown included) into a MemoryIndex(HashEmbedder()) under project "proj-self":
          eval/golden_self.jsonl
- unseen  a clone of encode/starlette at UNSEEN_COMMIT (--unseen-dir; the commit is checked), indexed the same way under
          project "proj-unseen": eval/golden_unseen.jsonl, a held-out set nobody tuned on (every case has split "unseen").
          `all` is demo and self: unseen needs the clone, so it runs only when asked for.

Methods: `baseline` = Index.search(question, k=10), the Guide's retrieval before the crew; `crew` =
app.rag.crew.retrieve(deps, project, question, k=10, purpose="answer"), imported lazily: when it is missing or fails to
import, the baseline is measured alone and the report says why. --e2e also runs the demo and unseen corpora (when chosen)
end to end, read-only (no chat turn is saved): each method's top 8 through the tier-1 writer (routing.ai_answer), and the
Guide's tier-1 attempt (guide.attempt) as it is wired today.

Offline by default: the demo's offline model, no network (LangSmith tracing is forced off), and nothing is written unless
--json names a file. --live uses the real model for the crew's model stages and the agentic Guide, and only when
GEMINI_API_KEY is already in this command's environment: no env file is read."""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

EVAL = Path(__file__).resolve().parent
BACKEND = EVAL.parent
ROOT = BACKEND.parent
SELF_PROJECT, SELF_ORG, SELF_USER = "proj-self", "org-self", "u-self"
UNSEEN_PROJECT, UNSEEN_ORG, UNSEEN_USER = "proj-unseen", "org-unseen", "u-unseen"
UNSEEN_COMMIT = "271c277da0ff64e2048a4d46ecc812b9d61db5f6"  # encode/starlette, 2026-11-06 (Starlette 1.10.0)
UNSEEN_DIR = "/private/tmp/claude-501/-Users-vijayabaskar-Downloads-welcome-wave/5d7d2a42-9a86-4bd7-909e-2badd0f9b712/scratchpad/unseen/starlette"
SPLITS = {"demo": ("dev", "holdout"), "self": ("dev", "holdout"), "unseen": ("unseen",)}  # the splits each golden set may use
GUIDE_CORPORA = ("demo", "unseen")  # the corpora --e2e runs end to end
K = 10  # retrieval depth for every metric
E2E_K = 8  # what the Guide hands its writer
RELEVANT = 2  # a hit counts for hit@1, recall and MRR when it matches an expected source of this grade or higher
CATEGORIES = ("locate", "explain", "howto", "why", "people", "kt", "doc", "verified", "multi", "paraphrase", "typo",
              "offtopic", "unanswerable")
KINDS = {"code", "doc", "meeting", "verified_answer"}
METHODS = ("baseline", "crew")


# ---------------------------------------------------------------- golden sets
def load_cases(path: Path, splits: tuple[str, ...] = ("dev", "holdout")) -> list[dict]:
    """The golden set, checked for shape: a malformed case stops the run instead of skewing a metric. `splits`: the splits
    this set may use (the unseen set has only "unseen")."""
    cases, ids = [], set()
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        where = f"{path.name}:{n}"
        problems = [p for p, bad in (
            ("an id is missing or repeated", not case.get("id") or case["id"] in ids),
            ("unknown category", case.get("category") not in CATEGORIES),
            (f"split is not {' or '.join(splits)}", case.get("split") not in splits),
            ("style, when given, is a non-empty string", "style" in case and not (isinstance(case["style"], str) and case["style"])),
            ("an answerable case needs an expect entry of grade 2 or 3",
             case.get("answerable") and not any(e.get("grade", 0) >= RELEVANT for e in case.get("expect", []))),
            ("an unanswerable case expects nothing", not case.get("answerable") and case.get("expect")),
            ("an expect entry has an unknown kind or a grade outside 1-3",
             any(e.get("kind") not in KINDS or e.get("grade") not in (1, 2, 3) for e in case.get("expect", []))),
            ("a code entry needs a path", any(e["kind"] == "code" and not e.get("path") for e in case.get("expect", []))),
        ) if bad]
        if problems:
            raise SystemExit(f"{where}: {'; '.join(problems)}")
        ids.add(case["id"])
        cases.append(case)
    return cases


# ---------------------------------------------------------------- relevance
_NORM: dict[str, str] = {}


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _content(chunk) -> str:
    """A chunk's text, whitespace-folded once (the golden check compares every text anchor with every chunk)."""
    key = chunk.id or ""
    if key not in _NORM:
        _NORM[key] = _norm(chunk.content)
    return _NORM[key]


def _lines_overlap(ref: dict, entry: dict) -> bool:
    start, end = ref.get("start_line"), ref.get("end_line")
    if start is None or end is None:
        return False
    return start <= entry.get("end_line", entry["start_line"]) and entry["start_line"] <= end


def match(entry: dict, chunk) -> str | None:
    """"full" when the chunk is the source the entry names, "partial" when it is the same file (or KT call) but another part
    of it, else None. eval/README.md states the rule."""
    from app.ports import norm_question

    kind, ref = entry["kind"], chunk.ref or {}
    if kind == "code":
        if chunk.source_type != "code" or ref.get("path") != entry["path"]:
            return None
        if not any(k in entry for k in ("symbol", "start_line", "text")):
            return "full"  # the whole file is the answer
        if ("symbol" in entry and chunk.symbol == entry["symbol"]) or ("start_line" in entry and _lines_overlap(ref, entry)) \
                or ("text" in entry and _norm(entry["text"]) in _content(chunk)):
            return "full"
        return "partial"
    if kind == "doc":
        if chunk.source_type != "doc" or _norm(ref.get("title")) != _norm(entry.get("title")):
            return None
        return "partial" if "page" in entry and ref.get("page") != entry["page"] else "full"
    if kind == "meeting":
        if chunk.source_type != "meeting":
            return None
        if entry.get("meeting"):
            if str(ref.get("meeting_id")) != str(entry["meeting"]):
                return None
        elif _norm(ref.get("title")) != _norm(entry.get("title")):
            return None
        if "at_s" in entry:
            start = float(ref.get("start_s") or 0)
            end = float(ref.get("end_s") or start)
            return "full" if start <= float(entry["at_s"]) <= end else "partial"
        return "full"
    if kind == "verified_answer" and chunk.source_type == "verified_answer":
        vid = entry.get("id")
        if vid and (str(ref.get("verified_answer_id")) == str(vid) or chunk.id == f"va-{vid}"):
            return "full"
        if entry.get("question") and norm_question(ref.get("question", "")) == norm_question(entry["question"]):
            return "full"
    return None


def grade_list(chunks: list, expect: list[dict]) -> list[tuple[int, bool]]:
    """Per ranked chunk: (its gain grade, whether it counts as relevant). Each expected source is credited once, to the first
    chunk that matches it in full (so a function split over two chunks cannot score twice); a chunk of the right file but
    the wrong part earns grade 1 once per expected source, and never counts as relevant."""
    taken: set[int] = set()
    near: set[int] = set()
    out = []
    for c in chunks:
        full = [(e["grade"], i) for i, e in enumerate(expect) if i not in taken and match(e, c) == "full"]
        if full:
            g, i = max(full)
            taken.add(i)
            out.append((g, g >= RELEVANT))
            continue
        part = [i for i, e in enumerate(expect) if i not in taken and i not in near and match(e, c) == "partial"]
        if part:
            near.add(part[0])
            out.append((1, False))
        else:
            out.append((0, False))
    return out


def case_metrics(graded: list[tuple[int, bool]], expect: list[dict]) -> dict[str, float]:
    rel = [r for _, r in graded[:K]]
    gains = [g for g, _ in graded[:K]]
    first = next((i for i, r in enumerate(rel) if r), None)
    ideal = sorted((e["grade"] for e in expect), reverse=True)[:K]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    dcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(gains))
    return {"hit1": float(bool(rel[:1] and rel[0])), "r5": float(any(rel[:5])), "r10": float(any(rel)),
            "mrr": 1.0 / (first + 1) if first is not None else 0.0, "ndcg": min(1.0, dcg / idcg) if idcg else 0.0,
            "near5": float(any(g > 0 for g in gains[:5]))}


# ---------------------------------------------------------------- corpora
@dataclass
class Corpus:
    name: str
    deps: Any
    project: str
    org: str
    user: Any
    chunks: list
    verified: list = field(default_factory=list)  # the lifecycle store's verified answers (indexed or not)
    info: str = ""

    def by_id(self) -> dict:
        return {c.id: c for c in self.chunks}


def _llm(settings, live: bool):
    from app.agents.core.llm import GeminiLLM, OfflineLLM

    return GeminiLLM(settings) if live else OfflineLLM()


def build_demo(live: bool) -> Corpus:
    """The demo container, always seeded offline: with a key in the environment the seed's agents (the plan, the handover)
    would spend the model's quota before the eval asks anything. --live then gives the eval's own calls the real model."""
    from app.config import get_settings
    from app.container import build_container
    from app.seed import ORG, PROJECT

    key = os.environ.pop("GEMINI_API_KEY", None)
    get_settings.cache_clear()
    t0 = time.perf_counter()
    try:
        c = build_container(get_settings(), serves_http=False)
    finally:
        if key:
            os.environ["GEMINI_API_KEY"] = key
        get_settings.cache_clear()
    secs = time.perf_counter() - t0
    deps = c.deps
    if live:
        settings = get_settings()
        deps = dataclasses.replace(deps, settings=settings, llm=_llm(settings, True))
    chunks = [ch for ch in getattr(deps.index, "chunks", {}).values() if ch.project_id == PROJECT and not ch.stale]
    kinds = Counter(ch.source_type for ch in chunks)
    verified = deps.lifecycle.verified_answers(PROJECT) if deps.lifecycle else []
    info = (f"demo: {len(chunks)} chunks ({', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))}), "
            f"{len(verified)} verified answer(s) in the lifecycle store; container built in {secs:.1f} s")
    return Corpus("demo", deps, PROJECT, ORG, deps.store.get_user("u-vijay"), chunks, verified, info)


# Working-status docs: rewritten every round (results, plans, a dated snapshot, one machine's walkthrough). Indexing them
# made the self numbers move with every status update, not with retrieval. eval/README.md ("Corpora") states the rule: a doc
# goes here only if it records the work rather than explaining the product, and no golden_self case points into it (the
# golden check would list such a case as missing).
WORKING_DOCS = frozenset({"STATUS.md", "PLAN.md", "CURRENT_STATE.md", "LOCAL_FIRST_USE.md"})


def self_files() -> list[Path]:
    """What the self corpus indexes: the backend's application code and the top-level docs (not docs/specs), less the
    working-status docs (WORKING_DOCS)."""
    docs = [p for p in sorted((ROOT / "docs").glob("*.md")) if p.name not in WORKING_DOCS]
    return sorted((BACKEND / "app").rglob("*.py")) + docs


@dataclass
class _Repo:
    """A folder indexed as ingestion indexes a connected repository, with the dependencies an agent gets."""
    deps: Any
    user: Any
    chunks: list
    files: int
    chunk_s: float
    index_s: float


def _index_repo(live: bool, scratch: str, root: Path, files: list[Path], project: str, org: str, user_id: str, name: str,
                description: str) -> _Repo:
    """`files` as a connected repository would be indexed (app/agents/specialists/librarian/agent.py: should_index, then
    chunk_code for every file, markdown included; paths relative to `root`) into a MemoryIndex(HashEmbedder()), with the project, a new hire on
    it, the lifecycle store and a read-only LocalRepoSource on `root`: what the Guide needs to run end to end."""
    from app.adapters.lifecycle_memory import MemoryLifecycleStore
    from app.adapters.memory import InlineBus, LocalBlobStorage, LocalRepoSource, MemoryIndex, MemoryStore
    from app.agents.core.deps import Deps
    from app.config import get_settings
    from app.ports import Project, User
    from app.services.chunking import chunk_code, should_index
    from app.services.embeddings import HashEmbedder
    from app.services.redaction import RegexRedactor

    settings = get_settings()
    t0 = time.perf_counter()
    chunks, n = [], 0
    for f in files:
        rel, text = f.relative_to(root).as_posix(), f.read_text(encoding="utf-8")
        if should_index(rel, len(text.encode())):
            n += 1
            chunks += chunk_code(project, rel, text)
    t1 = time.perf_counter()
    index = MemoryIndex(HashEmbedder())
    index.upsert(chunks)
    t2 = time.perf_counter()
    store = MemoryStore()
    store.add_project(Project(id=project, org_id=org, name=name, description=description))
    user = User(id=user_id, org_id=org, email=f"new.engineer@{org.removeprefix('org-')}.invalid", name="New engineer", role="new_hire")
    store.add_user(user, project)
    deps = Deps(settings=settings, llm=_llm(settings, live), index=index, store=store, repos=LocalRepoSource(str(root)),
                blobs=LocalBlobStorage(scratch), redactor=RegexRedactor(), bus=InlineBus(), lifecycle=MemoryLifecycleStore(store))
    return _Repo(deps, user, chunks, n, t1 - t0, t2 - t1)


def build_self(live: bool, scratch: str) -> Corpus:
    """Welcome Wave's own code and docs as a connected repository would be indexed (app/agents/specialists/librarian/agent.py: should_index,
    then chunk_code for every file, markdown included), with the minimal dependencies an agent gets."""
    r = _index_repo(live, scratch, ROOT, self_files(), SELF_PROJECT, SELF_ORG, SELF_USER, "Welcome Wave",
                    "Welcome Wave's own backend and docs")
    info = (f"self: {r.files} files, {len(r.chunks)} chunks; chunked in {r.chunk_s:.2f} s, embedded and indexed in {r.index_s:.2f} s "
            f"(MemoryIndex(HashEmbedder()), markdown via chunk_code as ingestion does)")
    return Corpus("self", r.deps, SELF_PROJECT, SELF_ORG, r.user, r.chunks, [], info)


def unseen_files(root: Path) -> list[Path]:
    """What the unseen corpus indexes (eval/README.md, "Unseen corpus"): Starlette's package code, its docs and its README.
    tests/, benchmarks/ and scripts/ are left out, as self leaves out backend/tests/."""
    return sorted((root / "starlette").rglob("*.py")) + sorted((root / "docs").rglob("*.md")) + [root / "README.md"]


def unseen_commit(root: Path) -> str:
    """The clone's commit, checked: the golden set's anchors are lines of UNSEEN_COMMIT, so another commit, or a local edit
    to an indexed file, would move or remove them. Stops the run with what to do."""
    import subprocess

    how = f"clone it as eval/README.md says ('Unseen corpus') and check out {UNSEEN_COMMIT}, or pass --unseen-dir"
    if not (root / ".git").exists() or not (root / "starlette").is_dir():
        raise SystemExit(f"--unseen-dir {root} is not a clone of Starlette: {how}")

    def git(*args: str) -> str:
        try:
            done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60, check=True)
        except (OSError, subprocess.SubprocessError) as e:
            raise SystemExit(f"cannot read the Starlette clone's commit with git ({type(e).__name__}): {how}") from None
        return done.stdout.strip()

    head = git("rev-parse", "HEAD")
    if head != UNSEEN_COMMIT:
        raise SystemExit(f"the Starlette clone at {root} is at {head[:12]}, not {UNSEEN_COMMIT[:12]}: "
                         f"git -C {root} checkout {UNSEEN_COMMIT}")
    dirty = git("status", "--porcelain", "--untracked-files=all", "--", "starlette", "docs", "README.md")
    if dirty:
        raise SystemExit(f"the Starlette clone at {root} has local changes in the indexed files:\n{dirty[:600]}")
    return head


def build_unseen(live: bool, scratch: str, root: Path) -> Corpus:
    """Starlette at UNSEEN_COMMIT, indexed as self is (should_index, then chunk_code for every file), under its own
    project, organisation and new hire, so the Guide runs end to end on it."""
    root = root.expanduser().resolve()
    commit = unseen_commit(root)
    r = _index_repo(live, scratch, root, unseen_files(root), UNSEEN_PROJECT, UNSEEN_ORG, UNSEEN_USER, "Starlette",
                    "encode/starlette, a small ASGI web framework: its package code, docs and README")
    kinds = Counter("markdown" if c.ref.get("language") == "markdown" else "code" for c in r.chunks)
    info = (f"unseen: starlette@{commit[:12]}, {r.files} files, {len(r.chunks)} chunks ({kinds['code']} code, "
            f"{kinds['markdown']} markdown); chunked in {r.chunk_s:.2f} s, embedded and indexed in {r.index_s:.2f} s "
            f"(MemoryIndex(HashEmbedder()), markdown via chunk_code as ingestion does)")
    return Corpus("unseen", r.deps, UNSEEN_PROJECT, UNSEEN_ORG, r.user, r.chunks, [], info)


def check_golden(corpus: Corpus, cases: list[dict]) -> list[str]:
    """Every expected source must exist in the corpus, or a metric would be capped without anyone noticing (a renamed function,
    an edited doc). A verified answer the lifecycle store holds but the index does not is reported as such."""
    from app.ports import norm_question

    by_path: dict[str, list] = defaultdict(list)
    for c in corpus.chunks:
        by_path[c.ref.get("path") if c.source_type == "code" else c.source_type].append(c)
    problems = []
    for case in cases:
        for e in case["expect"]:
            pool = by_path.get(e["path"], []) if e["kind"] == "code" else by_path.get(e["kind"], [])
            if any(match(e, c) == "full" for c in pool):
                continue
            if e["kind"] == "verified_answer" and any(
                    a.id == e.get("id") or norm_question(a.question) == norm_question(e.get("question", "")) for a in corpus.verified):
                problems.append(f"{case['id']}: {describe(e)} is in the lifecycle store, not in the index")
            else:
                problems.append(f"{case['id']}: {describe(e)} matches no chunk")
    return problems


def describe(e: dict) -> str:
    if e["kind"] == "code":
        where = e["path"] + (f"#{e['symbol']}" if "symbol" in e else "") + \
            (f":L{e['start_line']}-{e.get('end_line', e['start_line'])}" if "start_line" in e else "") + \
            (f" ~'{e['text'][:40]}'" if "text" in e else "")
    elif e["kind"] == "meeting":
        where = f"KT {e.get('meeting') or e.get('title')}" + (f"@{e['at_s']}s" if "at_s" in e else "")
    elif e["kind"] == "doc":
        where = f"doc '{e.get('title')}'" + (f" p{e['page']}" if "page" in e else "")
    else:
        where = f"verified '{(e.get('question') or e.get('id') or '')[:50]}'"
    return f"{where} ({e['grade']})"


def label(chunk) -> str:
    from app.agents.core.citations import label_for

    try:
        return label_for(chunk)
    except Exception:  # noqa: BLE001 - a malformed ref still gets a label
        return f"{chunk.source_type}:{chunk.id}"


# ---------------------------------------------------------------- methods
def load_crew():
    """app.rag.crew, or None and why (another track builds it; the harness measures whatever is there)."""
    try:
        from app.rag import crew
    except Exception as e:  # noqa: BLE001 - missing or broken: measure the baseline only
        return None, f"{type(e).__name__}: {e}"
    if not callable(getattr(crew, "retrieve", None)):
        return None, "app.rag.crew has no retrieve()"
    return crew, None


def retriever(method: str, corpus: Corpus, crew) -> Callable[[str, int], list]:
    if method == "baseline":
        return lambda q, k: corpus.deps.index.search(corpus.project, q, k=k)
    return lambda q, k: crew.retrieve(corpus.deps, corpus.project, q, k=k, purpose="answer").hits


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile."""
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


METRICS = ("hit1", "r5", "r10", "mrr", "ndcg", "near5")


def run_retrieval(corpus: Corpus, method: str, fn, cases: list[dict], gap: float) -> dict:
    try:  # warm-up (a compiled graph, a first embedding): not timed, not scored
        fn("warm up", K)
    except Exception:  # noqa: BLE001 - a real failure shows on the cases themselves
        pass
    rows = []
    for i, case in enumerate(cases):
        if i and gap:
            time.sleep(gap)
        t0 = time.perf_counter()
        error = None
        try:
            hits = list(fn(case["q"], K))[:K]
        except Exception as e:  # noqa: BLE001 - one failing question is reported, the run goes on
            hits, error = [], f"{type(e).__name__}: {str(e)[:160]}"
        ms = (time.perf_counter() - t0) * 1000
        chunks = [h.chunk for h in hits]
        graded = grade_list(chunks, case["expect"])
        row = {"id": case["id"], "q": case["q"], "category": case["category"], "split": case["split"],
               "answerable": case["answerable"], "named": case.get("named"), "style": case.get("style"), "ms": round(ms, 1),
               "error": error,
               "top_similarity": round(hits[0].similarity, 3) if hits else 0.0,
               "got": [{"label": label(c), "grade": g, "relevant": r, "similarity": round(h.similarity, 3)}
                       for c, h, (g, r) in zip(chunks, hits, graded)]}
        if case["answerable"]:
            row.update(case_metrics(graded, case["expect"]))
        rows.append(row)
    return {"rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["answerable"]]
    out = {"cases": len(scored), **{m: round(mean(r[m] for r in scored), 3) for m in METRICS},
           "p50_ms": round(pct([r["ms"] for r in rows], 0.5), 1), "p95_ms": round(pct([r["ms"] for r in rows], 0.95), 1),
           "errors": sum(bool(r["error"]) for r in rows),
           "top_sim_answerable": round(mean(r["top_similarity"] for r in scored), 3),
           "top_sim_unanswerable": round(mean(r["top_similarity"] for r in rows if not r["answerable"]), 3)}
    return out


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    """Answerable rows by category, then by split, then (when the set marks it) by whether the question names an identifier,
    then (when the set marks any) by style: "style:short" for one-word queries, "style:sentence" for the rest."""
    out: dict[str, list[dict]] = {}
    for cat in CATEGORIES:
        sel = [r for r in rows if r["answerable"] and r["category"] == cat]
        if sel:
            out[cat] = sel
    for split in ("dev", "holdout", "unseen"):
        out[f"split:{split}"] = [r for r in rows if r["answerable"] and r["split"] == split]
    if any(r["named"] is not None for r in rows):
        out["names an identifier"] = [r for r in rows if r["answerable"] and r["named"]]
        out["no identifier"] = [r for r in rows if r["answerable"] and r["named"] is False]
    styles = sorted({r.get("style") for r in rows if r.get("style")})
    if styles:
        for style in styles:
            out[f"style:{style}"] = [r for r in rows if r["answerable"] and r.get("style") == style]
        out["style:sentence"] = [r for r in rows if r["answerable"] and not r.get("style")]
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------- end to end (demo)
def _cited_chunks(corpus: Corpus, result) -> list:
    """A citation as the chunk it names: the index's own chunk when it has it, else one rebuilt from the citation."""
    import re

    from app.agents.core.schemas import Chunk

    known = corpus.by_id()
    out = []
    for cit in result.citations:
        c = known.get(cit.chunk_id or "")
        if c is None:
            sym = re.search(r"\(([^)]+)\)$", cit.label or "")
            c = Chunk.model_construct(id=cit.chunk_id or "", project_id=corpus.project, source_type=cit.source_type,
                                      ref=dict(cit.ref or {}), content="", symbol=sym.group(1) if sym else None, stale=False)
        out.append(c)
    return out


def run_e2e(corpus: Corpus, crew, methods: list[str], cases: list[dict], live: bool, gap: float) -> dict:
    """The tier-1 writer on each method's evidence (routing.ai_answer with the method's top E2E_K), and the Guide's attempt
    as it is wired now (guide.attempt: tier 1 only; it routes nobody and saves no chat turn), on one corpus whose Deps the
    Guide can use (demo, unseen). Live runs the Guide alone, to spend the model's quota on what users get. Each row also
    keeps the retrieval gate's rule and similarity when the gate ran ("gate": similarity, identifier, terms, or None when
    it refused)."""
    from app.agents.core import registry as R
    from app.agents.specialists.guide import agent as guide
    from app.lifecycle import routing

    deps = R.deps_for(corpus.deps, corpus.org, "guide")
    ctx = R.lifecycle_ctx(deps, corpus.org, corpus.user)
    pid = corpus.project
    variants: list[tuple[str, Callable[[str], dict]]] = []
    if not live:
        if "baseline" in methods:
            variants.append(("baseline", lambda q: routing.ai_answer(ctx, pid, q, deps.index.search(pid, q, k=E2E_K))))
        if "crew" in methods and crew is not None:
            variants.append(("crew", lambda q: routing.ai_answer(ctx, pid, q, crew.retrieve(deps, pid, q, k=E2E_K, purpose="answer").hits)))
    variants.append(("guide", lambda q: guide.attempt(ctx, pid, q, False, None, [], None)))
    out = {}
    for name, fn in variants:
        rows, wired = [], set()
        for i, case in enumerate(cases):
            if i and gap:
                time.sleep(gap)
            t0 = time.perf_counter()
            try:
                ai, error = fn(case["q"]), None
            except Exception as e:  # noqa: BLE001 - reported per case
                ai, error = {}, f"{type(e).__name__}: {str(e)[:160]}"
            ms = (time.perf_counter() - t0) * 1000
            if name == "guide" and not error:
                wired.add("crew" if ai.get("crew") is not None else "search")
            result, check, gate = ai.get("result"), ai.get("check"), ai.get("retrieval") or {}
            cited = _cited_chunks(corpus, result) if result else []
            good = [any(match(e, c) == "full" for e in case["expect"]) for c in cited]
            rows.append({"id": case["id"], "q": case["q"], "category": case["category"], "split": case["split"],
                         "answerable": case["answerable"], "answered": bool(result), "ms": round(ms, 1), "error": error,
                         "guard_ran": check is not None, "guard_ok": bool(check and check.get("ok")),
                         "citations": [label(c) for c in cited], "citations_ok": good,
                         "gate": gate.get("rule"), "gate_similarity": gate.get("similarity"),
                         "writer": ai.get("writer"), "why": (ai.get("why") or [""])[0][:200]})
        key = f"guide [{'+'.join(sorted(wired)) or '?'}]" if name == "guide" else name
        out[key] = {"rows": rows, "summary": e2e_summary(rows)}
    return out


def e2e_summary(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    answered = [r for r in ans if r["answered"]]
    cites = [ok for r in answered for ok in r["citations_ok"]]
    guarded = [r for r in rows if r["guard_ran"]]
    return {"answerable": len(ans), "unanswerable": len(una),
            "answered_rate": round(mean(r["answered"] for r in ans), 3),
            "refusal_rate": round(mean(not r["answered"] for r in una), 3),
            "citation_precision": round(mean(cites), 3) if cites else None,
            "cites_expected": round(mean(any(r["citations_ok"]) for r in answered), 3) if answered else None,
            "guard_pass_rate": round(mean(r["guard_ok"] for r in guarded), 3) if guarded else None,
            "guard_runs": len(guarded),
            "p50_ms": round(pct([r["ms"] for r in rows], 0.5), 1), "p95_ms": round(pct([r["ms"] for r in rows], 0.95), 1),
            "errors": sum(bool(r["error"]) for r in rows)}


# ---------------------------------------------------------------- report
def fmt(v, width=6, digits=2) -> str:
    if v is None:
        return "-".rjust(width)
    return f"{v:.{digits}f}".rjust(width) if isinstance(v, float) else str(v).rjust(width)


def print_retrieval(results: dict, split: str) -> None:
    print(f"\nRetrieval: k={K}, split={split}; answerable cases only; relevant = an expected source of grade {RELEVANT}-3; "
          "near@5 = any expected source or its file in the top 5 (eval/README.md)")
    head = f"{'corpus':7}{'method':9}{'cases':>6}{'hit@1':>7}{'R@5':>6}{'R@10':>6}{'MRR':>6}{'nDCG':>6}{'near@5':>7}" \
           f"{'p50ms':>8}{'p95ms':>8}{'err':>5}"
    print(head)
    print("-" * len(head))
    for corpus, by_method in results.items():
        for method, res in by_method.items():
            s = res["summary"]
            print(f"{corpus:7}{method:9}{s['cases']:>6}{fmt(s['hit1'], 7)}{fmt(s['r5'])}{fmt(s['r10'])}{fmt(s['mrr'])}"
                  f"{fmt(s['ndcg'])}{fmt(s['near5'], 7)}{fmt(s['p50_ms'], 8, 1)}{fmt(s['p95_ms'], 8, 1)}{s['errors']:>5}")
    for corpus, by_method in results.items():
        sims = "; ".join(f"{m} {r['summary']['top_sim_answerable']:.2f} vs {r['summary']['top_sim_unanswerable']:.2f}"
                         for m, r in by_method.items())
        print(f"  {corpus}: top-1 similarity, answerable vs unanswerable (the retrieval gate's evidence; gate 0.55): {sims}")


def print_categories(results: dict) -> None:
    for corpus, by_method in results.items():
        methods = list(by_method)
        grouped = {m: groups(by_method[m]["rows"]) for m in methods}
        print(f"\n{f'{corpus} by category':24}{'':>5}" + "".join(f" | {m:^29}" for m in methods))
        print(f"{'':24}{'n':>5}" + "".join(f" | {'hit@1':>5}{'R@5':>6}{'R@10':>6}{'MRR':>6}{'nDCG':>6}" for _ in methods))
        for name in grouped[methods[0]]:
            cells = ""
            for m in methods:
                rows = grouped[m].get(name, [])
                cells += f" | {fmt(mean(r['hit1'] for r in rows), 5)}{fmt(mean(r['r5'] for r in rows))}" \
                         f"{fmt(mean(r['r10'] for r in rows))}{fmt(mean(r['mrr'] for r in rows))}{fmt(mean(r['ndcg'] for r in rows))}"
            print(f"  {name:22}{len(grouped[methods[0]][name]):>5}{cells}")


def print_worst(results: dict, cases_by_corpus: dict, n: int) -> None:
    if n <= 0:
        return
    for corpus, by_method in results.items():
        expect = {c["id"]: c["expect"] for c in cases_by_corpus[corpus]}
        for method, res in by_method.items():
            rows = sorted((r for r in res["rows"] if r["answerable"]), key=lambda r: (r["ndcg"], r["mrr"], r["id"]))[:n]
            print(f"\nWorst {len(rows)}: {corpus} / {method}")
            for r in rows:
                print(f"  {r['id']:12} nDCG {r['ndcg']:.2f}  MRR {r['mrr']:.2f}  {r['q']}")
                print(f"      expected: {'; '.join(describe(e) for e in expect[r['id']])}")
                got = "  ".join(f"{i}. {g['label'][:60]} [{g['grade'] if g['grade'] else '.'}{'*' if g['relevant'] else ''}]"
                                for i, g in enumerate(r["got"][:3], 1)) or (r["error"] or "nothing")
                print(f"      got:      {got}")


def print_e2e(e2e: dict, corpus: str = "demo") -> None:
    print(f"\nEnd to end on {corpus} (tier 1; answered = an answer that passed the retrieval gate and the citation guard)")
    head = f"{'variant':20}{'answered':>9}{'refused':>8}{'cite-prec':>10}{'cites-exp':>10}{'guard':>7}{'p50ms':>8}{'p95ms':>8}{'err':>5}"
    print(head)
    print("-" * len(head))
    for name, res in e2e.items():
        s = res["summary"]
        print(f"{name:20}{fmt(s['answered_rate'], 9)}{fmt(s['refusal_rate'], 8)}{fmt(s['citation_precision'], 10)}"
              f"{fmt(s['cites_expected'], 10)}{fmt(s['guard_pass_rate'], 7)}{fmt(s['p50_ms'], 8, 1)}{fmt(s['p95_ms'], 8, 1)}"
              f"{s['errors']:>5}")
    print("  answered: share of answerable cases; refused: share of unanswerable ones; cite-prec: cited sources the golden set "
          "expects / all cited; cites-exp: answered cases citing at least one expected source; guard: citation-guard pass rate "
          "where it ran")
    for name, res in e2e.items():
        wrong = [r for r in res["rows"] if not r["answerable"] and r["answered"]]
        for r in wrong:
            print(f"  {name}: answered unanswerable {r['id']} (gate: {r.get('gate')}, {r.get('gate_similarity')}): "
                  f"{r['q'][:80]} -> {'; '.join(r['citations'][:3])}")


# ---------------------------------------------------------------- main
def _prepare_env(live: bool, scratch: str) -> None:
    """Before any app import: demo mode (no env file is read), runtime files in a scratch folder, and offline unless --live."""
    sys.dont_write_bytecode = True
    os.environ["MODE"] = "demo"
    os.environ["DATA_DIR"] = scratch
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    if not live:
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ["LANGSMITH_TRACING"] = "false"
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m eval.rag_eval", description="Retrieval and answer evaluation of the Guide's research crew.")
    p.add_argument("--corpus", choices=("demo", "self", "unseen", "all"), default="all",
                   help="all = demo and self; unseen (a Starlette clone) runs only when named")
    p.add_argument("--split", choices=("dev", "holdout", "unseen", "all"), default="all",
                   help="the unseen set has only split unseen")
    p.add_argument("--method", choices=("baseline", "crew", "both"), default="both")
    p.add_argument("--e2e", action="store_true", help="also run the demo and unseen corpora end to end, when chosen (tier-1 "
                                                      "writer and the Guide's attempt)")
    p.add_argument("--unseen-dir", type=Path, default=Path(UNSEEN_DIR), metavar="DIR",
                   help=f"the Starlette clone at {UNSEEN_COMMIT[:12]} (eval/README.md, 'Unseen corpus'; default {UNSEEN_DIR})")
    p.add_argument("--json", metavar="PATH", help="write every case's results here")
    p.add_argument("--worst", type=int, default=5, metavar="N", help="print the N worst cases per corpus and method (default 5)")
    p.add_argument("--live", action="store_true", help="the real model for the crew's model stages and the agentic Guide "
                                                       "(needs GEMINI_API_KEY in this command's environment)")
    p.add_argument("--gap", type=float, default=None, metavar="S", help="seconds between questions (default 0; 6 with --live, "
                                                                         "for the free tier's per-minute limits)")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse(argv)
    if args.live and not os.environ.get("GEMINI_API_KEY"):
        print("--live needs GEMINI_API_KEY in this command's environment (no env file is read), e.g.\n"
              "  GEMINI_API_KEY=... .venv/bin/python -m eval.rag_eval --live --corpus demo")
        return 2
    gap = args.gap if args.gap is not None else (6.0 if args.live else 0.0)
    with tempfile.TemporaryDirectory(prefix="ww-rag-eval-") as scratch:
        _prepare_env(args.live, scratch)
        import logging

        logging.basicConfig(level=logging.WARNING)
        for noisy in ("httpx", "httpcore", "google", "urllib3", "llm", "agents.runtime"):
            logging.getLogger(noisy).setLevel(logging.ERROR)
        return run(args, gap, scratch)


def run(args: argparse.Namespace, gap: float, scratch: str) -> int:
    t_start = time.perf_counter()
    names = ["demo", "self"] if args.corpus == "all" else [args.corpus]
    methods = list(METHODS) if args.method == "both" else [args.method]
    crew, crew_error = (None, "not requested") if "crew" not in methods else load_crew()
    if "crew" in methods and crew is None:
        print(f"crew unavailable ({crew_error}): measuring the baseline only")
        methods = [m for m in methods if m != "crew"] or ["baseline"]
    print(f"mode: {'LIVE (real model)' if args.live else 'offline (OfflineLLM, no network)'}; methods: {', '.join(methods)}")

    for name in names:  # before anything is built
        if args.split != "all" and args.split not in SPLITS[name]:
            raise SystemExit(f"the {name} golden set has no split {args.split} (it has {', '.join(SPLITS[name])}): "
                             "use --split all or one of those")
    corpora: dict[str, Corpus] = {}
    cases_by_corpus: dict[str, list[dict]] = {}
    for name in names:
        if name == "demo":
            corpora[name] = build_demo(args.live)
        elif name == "self":
            corpora[name] = build_self(args.live, scratch)
        else:
            corpora[name] = build_unseen(args.live, scratch, args.unseen_dir)
        print(corpora[name].info)
        cases = load_cases(EVAL / f"golden_{name}.jsonl", SPLITS[name])
        problems = check_golden(corpora[name], cases)
        if problems:
            print(f"  golden check: {len(problems)} expected source(s) not found in the {name} index:")
            for line in problems[:8]:
                print(f"    {line}")
            if len(problems) > 8:
                print(f"    ... and {len(problems) - 8} more")
        if args.split != "all":
            cases = [c for c in cases if c["split"] == args.split]
        cases_by_corpus[name] = cases
        print(f"  {name}: {len(cases)} cases ({sum(c['answerable'] for c in cases)} answerable), split={args.split}")

    results: dict[str, dict] = {}
    for name, corpus in corpora.items():
        results[name] = {}
        for method in methods:
            results[name][method] = run_retrieval(corpus, method, retriever(method, corpus, crew), cases_by_corpus[name], gap)

    print_retrieval(results, args.split)
    print_categories(results)
    print_worst(results, cases_by_corpus, args.worst)

    e2e, e2e_by_corpus = None, {}
    if args.e2e:
        ready = [n for n in corpora if n in GUIDE_CORPORA]
        if not ready:
            print("\n--e2e runs on the demo and unseen corpora: add --corpus demo, unseen or all")
        for name in ready:
            e2e_by_corpus[name] = run_e2e(corpora[name], crew, methods, cases_by_corpus[name], args.live, gap)
            print_e2e(e2e_by_corpus[name], name)
        e2e = e2e_by_corpus.get("demo")
    total = time.perf_counter() - t_start
    print(f"\ntotal {total:.1f} s")

    if args.json:
        payload = {"meta": {"mode": "live" if args.live else "offline", "split": args.split, "methods": methods, "k": K,
                            "relevant_grade": RELEVANT, "crew_error": crew_error if crew is None else None,
                            "corpora": {n: c.info for n, c in corpora.items()}, "seconds": round(total, 1)},
                   "retrieval": results, "e2e": e2e, "e2e_by_corpus": e2e_by_corpus}
        Path(args.json).write_text(json.dumps(payload, indent=1, default=str))
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
