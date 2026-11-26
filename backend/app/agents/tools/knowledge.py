"""Knowledge tools: the research crew's search, reading a file, finding a symbol, and verified answers. Read-only; see __init__.

search_knowledge runs the crew's rules (app/rag/crew.search: the query's plan, every kind in one batched pass, the names it
gives looked up exactly, fused and ranked), never a model: the agent calling it is the one thinking. Each result's similarity
is its cosine to the query as given."""
from __future__ import annotations

import posixpath
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.core.runtime import REGISTRY, ToolCtx, ToolResult, untrusted
from app.agents.core.schemas import Chunk
from app.rag import crew
from app.services.chunking import _chunk_id

KINDS = {"code": "code", "doc": "doc", "kt": "meeting", "verified": "verified_answer"}
KIND_WORDS = {"code": "code", "doc": "documents", "kt": "KT sessions", "verified": "verified answers"}
SNIPPET = 1800  # characters of each search hit the model sees
MAX_READ = 200  # lines one read_file call returns


def _kinds_text(kinds: list[str]) -> str:
    return ", ".join(KIND_WORDS[k] for k in kinds) if kinds else "code, documents and KT sessions"


# ---------------------------------------------------------------- search_knowledge
class SearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300,
                       description="What to look for, in the project's own words: names of files, functions, commands or terms")
    k: int = Field(6, ge=1, le=8, description="How many results (at most 8)")
    kinds: list[Literal["code", "doc", "kt", "verified"]] = Field(
        default_factory=list, description="Only these kinds of source (kt: recorded KT sessions). Empty: all of them")


@REGISTRY.tool("search_knowledge", "Search the project's code, documents, recorded KT sessions and verified answers: several "
               "searches in the project's own words (semantic and keyword), the files and functions the query names looked up "
               "exactly, ranked best first. Returns numbered sources [n] you can cite. Search again with other words, or "
               "narrower, when the results miss.", SearchArgs, label="Searched the knowledge base")
def search_knowledge(ctx: ToolCtx, args: SearchArgs) -> ToolResult:
    kinds = list(dict.fromkeys(args.kinds))
    hits = crew.search(ctx.deps, ctx.project_id, args.query, k=args.k, kinds=[KINDS[k] for k in kinds] or None)
    known = {e.chunk.id for e in ctx.evidence}
    rows, sources = [], []
    for h in hits:
        n = ctx.cite(h.chunk, h.similarity)
        ev = ctx.evidence[n - 1]
        sources.append(ev.source)
        rows.append({"n": n, "source": ev.source.label, "kind": h.chunk.source_type, "similarity": round(h.similarity, 2),
                     "text": h.chunk.content[:SNIPPET]})
    what = _kinds_text(kinds)
    payload = {"query": args.query, "results": rows}
    if not rows:
        payload["note"] = "Nothing matched. Try other words, a file or function name, or fewer kinds."
    elif all(h.chunk.id in known for h in hits):  # nothing new: another search with similar words will not find more
        payload = {"query": args.query, "results": [{"n": r["n"], "source": r["source"]} for r in rows],
                   "note": "Only sources you have seen already. Similar searches will not find more: read a file, or conclude."}
    return ToolResult(text=untrusted(payload),
                      sources=sources, summary=f"Searching {what}: {args.query[:80]} ({len(rows)} found)",
                      shared=f"Searched {what} ({len(rows)} found)")


# ---------------------------------------------------------------- read_file
class ReadArgs(BaseModel):
    path: str = Field(min_length=1, max_length=400, description="The file's path in the repository, e.g. src/app/main.py")
    start: int | None = Field(None, ge=1, description="First line to read (default 1)")
    end: int | None = Field(None, ge=1, description=f"Last line to read (at most {MAX_READ} lines from start)")


def body_lines(chunk: Chunk) -> list[str]:
    """A code chunk's own lines, without the 'File:' and 'Symbol:' header lines the chunker puts first."""
    lines = chunk.content.split("\n")
    if lines and lines[0].startswith("File: "):
        lines = lines[1:]
    if chunk.symbol and lines and lines[0].startswith("Symbol: "):
        lines = lines[1:]
    return lines


def _resolve(ctx: ToolCtx, path: str) -> tuple[str | None, list[str]]:
    """The indexed file `path` names: itself, or the one file whose path ends with it. Else (None, close candidates)."""
    path = path.strip().lstrip("./")
    if ctx.deps.index.file_chunks(ctx.project_id, path):
        return path, []
    paths = ctx.deps.index.all_paths(ctx.project_id)
    ends = [p for p in paths if p.endswith("/" + path) or p == path]
    if len(ends) == 1:
        return ends[0], []
    base = posixpath.basename(path)
    near = ends or [p for p in paths if posixpath.basename(p) == base] or [p for p in paths if base and base.split(".")[0] in p]
    return None, near[:8]


@REGISTRY.tool("read_file", f"Read lines of one file of the repository (read-only), at most {MAX_READ} lines per call, numbered. "
               "Use it to follow a function or a config that search found. Returns one numbered source [n].", ReadArgs,
               label="Read a file")
def read_file(ctx: ToolCtx, args: ReadArgs) -> ToolResult:
    path, near = _resolve(ctx, args.path)
    if path is None:
        return ToolResult(text=untrusted({"error": "No such file in the repository as read.", "did_you_mean": near}),
                          summary="That file was not found", shared="A file was not found")
    chunks = ctx.deps.index.file_chunks(ctx.project_id, path)
    lines: dict[int, str] = {}
    for c in chunks:
        first = int(c.ref.get("start_line") or 1)
        for i, text in enumerate(body_lines(c)):
            lines.setdefault(first + i, text)
    last = max(lines, default=0)
    start = max(1, args.start or 1)
    end = min(last, args.end or start + MAX_READ - 1, start + MAX_READ - 1)
    if not lines or start > last:
        return ToolResult(text=untrusted({"path": path, "error": f"The file has {last} lines."}),
                          summary=f"Read {path}: past its end", shared=f"Read {path}: past its end")
    body = [lines.get(i, "") for i in range(start, end + 1)]
    inside = next((c for c in chunks if c.symbol and (c.ref.get("start_line") or 0) <= start and end <= (c.ref.get("end_line") or 0)), None)
    commit = next((c.ref.get("commit") for c in chunks if c.ref.get("commit")), "")
    chunk = Chunk(id=_chunk_id(ctx.project_id, path, start, end), project_id=ctx.project_id, source_type="code",
                  symbol=inside.symbol if inside else None,
                  ref={"path": path, "start_line": start, "end_line": end, "commit": commit, "language": (chunks[0].ref.get("language") if chunks else "")},
                  content=f"File: {path} (lines {start}-{end})\n" + "\n".join(body))
    n = ctx.cite(chunk)
    ctx.notes.setdefault("opened", set()).add(n)  # the Guide opened this source itself (guide._waived_how)
    numbered = "\n".join(f"{i:>5} | {t}" for i, t in zip(range(start, end + 1), body))
    more = {"more": f"The file goes on to line {last}: read from {end + 1}."} if end < last else {}
    where = f"{path} · L{start}–{end}"
    return ToolResult(text=untrusted({"n": n, "source": ctx.evidence[n - 1].source.label, "lines": numbered, **more}),
                      sources=[ctx.evidence[n - 1].source], summary=f"Reading {where}", shared=f"Read {where}")


# ---------------------------------------------------------------- find_symbol
class SymbolArgs(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="A function, class or method name as the code writes it")


@REGISTRY.tool("find_symbol", "Find where a function, class or method is defined (numbered sources [n] with its code) and where "
               "the code mentions it (path and lines).", SymbolArgs, label="Looked up a symbol")
def find_symbol(ctx: ToolCtx, args: SymbolArgs) -> ToolResult:
    name = args.name.strip().split(".")[-1].strip("()")
    index = ctx.deps.index
    refs = index.symbols_named(ctx.project_id, name)
    defs, sources, seen = [], [], set()
    for r in refs[:5]:
        chunk = next((c for c in index.file_chunks(ctx.project_id, r.path) if c.id == r.chunk_id), None)
        if chunk is None or chunk.id in seen:
            continue
        seen.add(chunk.id)
        n = ctx.cite(chunk)
        ctx.notes.setdefault("opened", set()).add(n)  # the Guide opened this source itself (guide._waived_how)
        sources.append(ctx.evidence[n - 1].source)
        defs.append({"n": n, "source": ctx.evidence[n - 1].source.label, "text": chunk.content[:SNIPPET]})
    word = re.compile(rf"\b{re.escape(name)}\b")
    uses = []
    for h in index.search(ctx.project_id, name, k=8, source_types=["code"]):
        if h.chunk.id in seen or not word.search(h.chunk.content):
            continue
        r = h.chunk.ref
        uses.append(f"{r.get('path')}:L{r.get('start_line')}-L{r.get('end_line')}" + (f" (in {h.chunk.symbol})" if h.chunk.symbol else ""))
    if not defs and not uses:
        return ToolResult(text=untrusted({"name": name, "definitions": [], "references": [], "note": "Not found: try search_knowledge."}),
                          summary=f"Looking up {name[:60]}: not found", shared="Looked up a symbol: not found")
    return ToolResult(text=untrusted({"name": name, "definitions": defs, "references": uses[:8]}), sources=sources,
                      summary=f"Looking up {name[:60]}: {len(defs)} definition{'s' if len(defs) != 1 else ''}, {len(uses[:8])} use{'s' if len(uses[:8]) != 1 else ''}",
                      shared=f"Looked up {name[:60]}" if defs else "Looked up a symbol")


# ---------------------------------------------------------------- verified_answers
class VerifiedArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300, description="The question, or its subject in a few words")


MIN_SHOWN = 0.35  # similarity at which a verified answer is shown to the model (tier 2 itself needs routing.RELATED_SIMILARITY)


@REGISTRY.tool("verified_answers", "Answers people on this project verified for earlier questions (de-identified: no names, never "
               "another person's words). Numbered sources [n] you can cite; each has an id and its similarity to your query.",
               VerifiedArgs, label="Checked verified answers")
def verified_answers(ctx: ToolCtx, args: VerifiedArgs) -> ToolResult:
    from app.lifecycle import routing

    store = ctx.deps.lifecycle
    if store is None:
        return ToolResult(text=untrusted({"answers": []}), summary="No verified answers here", shared="Checked verified answers (none)")
    answers = store.verified_answers(ctx.project_id)
    cands = [("verified_answer", a.id, a.question, a.answer, a.question) for a in answers]
    cands += [("expert_answer", str(r["id"]), r["question"], r["answer"], routing.paraphrase(r["question"]))
              for r in store.answered_requests(ctx.project_id) if r.get("answer")]
    rows, sources, seen = [], [], set()
    shown = ctx.notes.setdefault("related", {})

    def sim_of(c) -> float:  # like its question, or like the question with its answer ("how are swaps agreed?")
        return max(routing.similarity(args.query, c[2]), routing.similarity(args.query, f"{c[2]} {c[3]}"))

    for (kind, rid, q, a, question), sim in sorted(((c, sim_of(c)) for c in cands), key=lambda x: -x[1])[:5]:
        if sim < MIN_SHOWN or routing.norm_question(q) in seen:
            continue
        seen.add(routing.norm_question(q))
        # tier 2 (guide._related) reads the similarity of the questions alone: an answer that shares words is a source to cite
        item = {"kind": kind, "id": rid, "question": question, "answer": a, "similarity": round(routing.similarity(args.query, q), 2)}
        cid = routing._chunk_id(item, answers)
        ref = {"question": question, **({"verified_answer_id": rid} if kind == "verified_answer" else {"request_id": rid})}
        n = ctx.cite(Chunk(id=cid, project_id=ctx.project_id, source_type="verified_answer", ref=ref,
                             content=f"Q: {question}\nA (verified): {a}"), sim)
        shown[rid] = {**item, "n": n}
        sources.append(ctx.evidence[n - 1].source)
        rows.append({"n": n, "id": rid, "question": question, "answer": a[:SNIPPET], "similarity": round(sim, 2)})
    return ToolResult(text=untrusted({"query": args.query, "answers": rows}), sources=sources,
                      summary=f"Checking verified answers: {len(rows)} similar", shared=f"Checked verified answers ({len(rows)} similar)")
