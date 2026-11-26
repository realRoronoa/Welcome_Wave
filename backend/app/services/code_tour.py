"""Code tour: the codebase in the order it runs, each stop linked to the KT moment that explained it, or to who to ask.

Two layers, both deterministic (no LLM, no tables of their own):

- `build_graph(chunks) -> Graph` (pure). Reads symbols, docstrings, imports, containers and call edges out of the
  indexed code chunks, then lays out chapters (one DFS per entry point), a tests appendix and the symbols nothing
  reaches. `graph_for` caches it per (project, indexed commit, index.code_version): the version is one aggregate
  query, so a warm request reads no chunk at all; the push that re-indexes the repository rebuilds the tour and
  nothing else does. Memory stays bounded on a large repository: entry points are ranked with fixed-width bitsets
  (memory linear in the symbols, not quadratic), only the symbols that become stops keep their code, concurrent cold
  requests share one build (single-flight), and the cache is bounded by the symbols it holds, not by a count.
- `build_tour(store, index, ...) -> dict`. The graph for one viewer: every stop enriched through
  app.services.knowledge (KT clips and who spoke, the CODEOWNERS owner, action items, the first-PR target, stale
  clips), plus summary stats. This is the GET /api/tour response. Each Store table is read once per request; the
  results that cost more than a row read are remembered: every meeting's notes for its action items and speakers next
  to the graph, keyed by what they depend on, and the code search per first-PR wording in knowledge.SafeSearch, the
  memo knowledge risk and the KT planner share. A search outage drops the targets it would have found and sets
  `partial`; the rest of the tour is unaffected.

The graph is best-effort static analysis on top of chunking's regex symbol boundaries: a call is a name followed by
'(' that resolves through the symbol table, with import evidence to break ties. Dynamic dispatch, callbacks and
decorators are not seen, and the UI labels the tour accordingly.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
import threading
from collections import OrderedDict, defaultdict
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable, Literal

from app import ports
from app.agents.core.schemas import Chunk, KTLink
from app.ports import AppStore, Index, Meeting, TaskRow, User
from app.services import knowledge as K
from app.services.chunking import language_of, module_of
from app.services.diff import TEST_PATH

Order = Literal["execution", "reading"]

MAX_CHAPTERS = 12
MAX_STOPS = 40  # per chapter, reference stops included
MAX_CODE_LINES = 40  # lines of code sent per stop
MAX_APPENDIX = 200  # rows in each of the tests and unreached appendices
MAX_EDGES = 20  # calls and called_by listed per stop (calls_total and called_by_total count them all)
DOC_CHARS = 160
CACHE_SIZE = 8  # graphs at most (and the pin lists built on them)...
CACHE_SYMBOLS = 120_000  # ...and at most this many symbols across the cached graphs (the newest graph is always kept)
ENTRY_BATCH = 1024  # entry points ranked per pass: memory for ranking is symbols x ENTRY_BATCH bits, whatever the repo size
SIDE_CACHE_SIZE = 2048  # clip speakers: small values, one per clip
HIDDEN_FROM_HIRE = frozenset({"proposed", "rejected"})  # the GET /tasks rule: a new hire never sees an unapproved plan

CALL = re.compile(r"(?<![\w])([A-Za-z_]\w*)\s*\(")
_DEFINES = re.compile(r"\b(?:def|class|function|fn|func|fun)\s*\*?\s*$")  # `def name(` defines, it does not call
_RECEIVER = re.compile(r"([A-Za-z_]\w*)\s*\.\s*$")
_JSX_TAG = re.compile(r"(?<![\w$.])<([A-Z][\w$]*)")  # <Backlog /> renders a component: a call in JS/TS
# Method names of builtin dicts, lists, strings, files and promises: x.get(...) on an unknown receiver is almost always
# one of those, never the project's own get(). Calls on self, a module or Name() still resolve.
_BUILTIN_METHODS = frozenset("""
    get set add update pop append extend insert remove clear copy items keys values setdefault sort index count join split
    strip lstrip rstrip replace lower upper startswith endswith format encode decode read write close open flush readline
    seek then catch finally map filter reduce forEach push slice splice includes find some every toString json text
""".split())
_NEW_RECEIVER = re.compile(r"\b([A-Z]\w*)\s*\([^()]*\)\s*\.\s*$")  # Reader().run(): run of class Reader
_SKIP = frozenset("""
    if elif else for while return yield print len int str float bool bytes dict list set tuple frozenset super isinstance
    issubclass range open type object repr hash min max sum any all abs round sorted reversed enumerate zip map filter
    iter next getattr setattr hasattr delattr callable vars dir format input chr ord divmod pow globals locals
    not and or lambda with assert del raise except try finally import from pass break continue class def async await
    switch case catch function new typeof instanceof void delete throw sizeof func defer select make append cap copy
    panic recover match loop impl struct enum trait use pub let const var self this cls require
""".split())
_CLASSY = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:(?:public|private|protected|internal|abstract|final|sealed|open|data|static|"
                     r"pub(?:\([^)]*\))?)\s+)*(?:class|interface|enum|struct|trait|object|impl)\b|^type\s+\w+\s+(?:struct|interface)\b")
_PY_SCRUB = re.compile(r"(?s)(\"\"\"|''')(?:\\.|(?!\1).)*(?:\1|$)|#[^\n]*|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'")
_C_SCRUB = re.compile(r"(?s)//[^\n]*|/\*.*?(?:\*/|$)|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*(?:`|$)")
_COMMENT = re.compile(r"^\s*(?:/\*\*?|\*/|\*(?!/)|//+!?|#+(?!!))")
_PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]+(\([^)]*\)|[^\n#]+)", re.M)
_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([^\n#]+)", re.M)
_JS_IMPORT = re.compile(r"""^[ \t]*(?:import|export)\s+(?:type\s+)?([^'";]*?)\s*from\s*['"]([^'"]+)['"]""", re.M)
_JS_REQUIRE = re.compile(r"""(?:const|let|var)\s+([\w$]+|\{[^}]*\})\s*=\s*require\(\s*['"]([^'"]+)['"]\s*\)""")
_JS_EXTS = (".ts", ".tsx", ".js", ".jsx")
_GO_IMPORT = re.compile(r'^[ \t]*import\s*(?:\(([^)]*)\)|(?:[\w.]+\s+)?"([^"]+)")', re.M)
_JVM_IMPORT = re.compile(r"^[ \t]*import\s+(?:static\s+)?([\w.]+)", re.M)
_SAME_PACKAGE = frozenset({"go", "java", "kotlin"})  # files of one directory share a namespace without importing it
_JS = frozenset({"javascript", "typescript"})
# `persist(key: string): Promise<void> {` at the start of a line, in a class body or an object literal, defines a method
_JS_MEMBER_PREFIX = re.compile(r"[ \t]*(?:(?:public|private|protected|static|async|readonly|override|abstract|get|set)[ \t]+)*\*?[ \t]*")
_JS_BLOCK_AFTER = re.compile(r"\s*(?::[^{};=]*)?\{")


# ---------------------------------------------------------------- the graph (pure, cached, read-only once built)
@dataclass(frozen=True)
class Sym:
    key: str  # 'path::symbol', qualified when a file defines the name twice
    path: str
    symbol: str
    start_line: int
    end_line: int  # the span's last line, as knowledge.code_files merges it: KT links match against this
    shown_end: int  # the symbol's own last line, nested defs included (Python stops at the first dedent: module code, the next decorator)
    container: str | None  # the class it is defined directly in (Python), e.g. 'Handler' for do_POST; None for a def nested in a function
    kind: str  # 'class' | 'function' | 'method'
    doc: str | None
    test: bool
    code: tuple[str, ...]  # start_line .. shown_end, at most MAX_CODE_LINES + 1 lines (one extra flags the cut); stops only
    span: K.Span

    @property
    def label(self) -> str:
        return f"{self.container}.{self.symbol}" if self.container else self.symbol


@dataclass(frozen=True)
class Chapter:
    id: str
    title: str
    entry: str
    execution: tuple[tuple[str, str | None], ...]  # (key, ref_chapter) in DFS preorder: the order the code runs
    reading: tuple[tuple[str, str | None], ...]  # the same stops in DFS postorder: leaves first
    trails: dict[str, tuple[str, ...]] = field(default_factory=dict)  # key -> the call path from the entry down to its caller


@dataclass(frozen=True)
class Graph:
    """Shared by every request through the cache: treat every field as read-only."""

    symbols: dict[str, Sym]  # by key, in (path, start_line) order
    calls: dict[str, tuple[str, ...]]  # callees in first-occurrence order
    called_by: dict[str, tuple[str, ...]]  # callers from non-test code
    chapters: tuple[Chapter, ...]
    tests: tuple[str, ...]  # at most MAX_APPENDIX; tests_total counts them all
    unreached: tuple[str, ...]
    truncated: bool  # more than MAX_CHAPTERS entry points, or a chapter cut at MAX_STOPS
    spans_by_file: dict[str, list[K.Span]]  # non-test tour symbols, for knowledge.mentions and knowledge.search_span
    key_at: dict[tuple[str, int], str]  # (path, start_line) -> key
    file_docs: dict[str, str | None]  # each toured file's docstring or leading comment
    tests_total: int = 0
    unreached_total: int = 0
    paths: tuple[str, ...] = ()  # every indexed code path, toured or not: what knowledge.mentions reads paths against
    fingerprint: str = ""  # knowledge.code_fingerprint of the chunks it was built from: the key SafeSearch's memo shares
    index_truncated: bool = False  # the index's chunk cap left files out of the chunks it was built from


@dataclass
class _File:
    path: str
    lang: str
    raw: list[str]  # 1-based: raw[0] is ''
    clean: list[str]  # raw with string and comment contents blanked, same columns
    doc: str | None = None


@dataclass
class _Imports:
    files: list[str] = field(default_factory=list)  # files this one imports, in import order
    names: dict[str, tuple[str, str]] = field(default_factory=dict)  # local name -> (file, name it has there)
    modules: dict[str, str] = field(default_factory=dict)  # a module bound to a local name (import x as m) -> its file
    external: set[str] = field(default_factory=set)  # names imported from outside the repository


@dataclass
class _Info:
    span: K.Span
    test: bool
    shown_end: int
    container: str | None
    kind: str
    doc: str | None
    body: str  # cleaned body without the def line and signature: what the call scan reads
    key: str = ""
    outer: K.Span | None = None  # the symbol it is defined in, class or function (Python)
    local: bool = False  # defined inside a function body, at any depth: visible only through that function's scope


def build_graph(chunks: list[Chunk]) -> Graph:
    code = [c for c in chunks if c.source_type == "code" and not c.stale and c.ref.get("path")]
    spans = _tour_spans(code)
    files = _files(code, spans)
    infos: list[_Info] = []
    for path, file_spans in spans.items():
        infos += _analyse(files[path], file_spans)
    _assign_keys(infos)
    by_key = {i.key: i for i in infos}
    imports = _imports(files, {c.ref["path"] for c in code})
    calls = _call_edges(infos, imports, files)

    product = [i.key for i in infos if not i.test]
    called_by: dict[str, list[str]] = defaultdict(list)
    for k in product:
        for callee in calls[k]:
            if not by_key[callee].test:
                called_by[callee].append(k)
    edges = {k: tuple(c for c in calls[k] if not by_key[c].test) for k in product}
    test_callees = {c for i in infos if i.test for c in calls[i.key]}

    entries, reach_all = _entry_points(product, edges, by_key)
    chapters, truncated = _chapters(entries, edges, by_key, files)

    reachable = reach_all | test_callees
    by_span = {i.span: i for i in infos}

    def enclosing_function(i: _Info) -> _Info | None:
        o = by_span.get(i.outer) if i.outer is not None else None
        while o is not None and o.kind == "class":
            o = by_span.get(o.outer) if o.outer is not None else None
        return o

    # shown as part of a stop someone reaches: a class whose method is reached, a def nested in a reached function
    folded = {(i.span.path, i.container) for i in infos if i.container and i.key in reachable}
    nested_shown = {i.key for i in infos if i.local and (f := enclosing_function(i)) is not None and f.key in reachable}
    containers_of_tests = {(i.span.path, i.container) for i in infos if i.container and i.test}
    unreached = [k for k in product if k not in reachable and k not in nested_shown
                 and (by_key[k].span.path, by_key[k].span.symbol) not in folded]
    tests = [i.key for i in infos if i.test and not i.local and (i.span.path, i.span.symbol) not in containers_of_tests]

    stops = {key for ch in chapters for key, ref in ch.execution if ref is None}  # the only symbols whose code is sent
    symbols = {i.key: Sym(key=i.key, path=i.span.path, symbol=i.span.symbol, start_line=i.span.start_line, end_line=i.span.end_line,
                          shown_end=i.shown_end, container=i.container, kind=i.kind, doc=i.doc, test=i.test,
                          code=tuple(files[i.span.path].raw[i.span.start_line: min(i.shown_end, i.span.start_line + MAX_CODE_LINES) + 1])
                          if i.key in stops else (),
                          span=i.span) for i in infos}
    spans_by_file: dict[str, list[K.Span]] = defaultdict(list)
    for i in infos:
        if not i.test:
            spans_by_file[i.span.path].append(i.span)
    return Graph(symbols=symbols, calls={k: tuple(v) for k, v in calls.items()}, called_by={k: tuple(called_by.get(k, ())) for k in symbols},
                 chapters=tuple(chapters), tests=tuple(tests[:MAX_APPENDIX]), unreached=tuple(unreached[:MAX_APPENDIX]), truncated=truncated,
                 spans_by_file=dict(spans_by_file), key_at={(i.span.path, i.span.start_line): i.key for i in infos},
                 file_docs={p: f.doc for p, f in files.items()}, tests_total=len(tests), unreached_total=len(unreached),
                 paths=tuple(sorted({c.ref["path"] for c in code})), fingerprint=K.code_fingerprint(chunks),
                 index_truncated=bool(getattr(chunks, "truncated", False)))


# ---------------------------------------------------------------- symbols
def _in_tour(path: str) -> bool:
    """Product code and root files (a root main.py is often THE entry point) are toured, test files feed the tests
    appendix, and the docs module is left out."""
    return bool(TEST_PATH.search(path)) or module_of(path).split("/")[0] != "docs"


def _tour_spans(chunks: list[Chunk]) -> dict[str, list[K.Span]]:
    """knowledge.code_files for product code (so every feature merges split functions the same way), plus test and
    root files merged by the same rule, which code_files leaves out."""
    product = K.code_files(None, "", chunks=chunks)  # type: ignore[arg-type]  # the index is not read when chunks are given
    extra = K.merge_spans(c for c in chunks if c.ref["path"] not in product and _in_tour(c.ref["path"]))
    merged = {**product, **extra}
    return {p: merged[p] for p in sorted(merged)}


def _files(chunks: list[Chunk], spans: dict[str, list[K.Span]]) -> dict[str, _File]:
    """Each toured file's text, put back together from its chunks (header chunk plus symbol chunks)."""
    lines: dict[str, dict[int, str]] = defaultdict(dict)
    for c in chunks:
        path = c.ref["path"]
        if path not in spans:
            continue
        body = c.content.split("\n", 2 if c.symbol else 1)[-1]  # drop the 'File: …' and optional 'Symbol: …' headers
        start = int(c.ref.get("start_line") or 1)
        for i, text in enumerate(body.split("\n")):
            lines[path][start + i] = text
    out = {}
    for path, by_line in lines.items():
        raw = [""] + [by_line.get(n, "") for n in range(1, max(by_line) + 1)]
        lang = language_of(path) or "text"
        out[path] = _File(path, lang, raw, _scrub(raw, lang))
    return out


def _scrub(raw: list[str], lang: str) -> list[str]:
    """Blank string literals and comments, keeping every column and line, so neither a call scan nor an indentation
    walk is fooled by text (a docstring that mentions validate(), SQL at column 0 inside a function)."""
    pattern = _PY_SCRUB if lang == "python" else _C_SCRUB
    text = pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), "\n".join(raw))
    return text.split("\n")


def _indent(text: str) -> int:
    expanded = text.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def _brackets(text: str) -> int:
    return sum(text.count(ch) for ch in "([{") - sum(text.count(ch) for ch in ")]}")


def _clip(text: str) -> str | None:
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= DOC_CHARS else text[: DOC_CHARS - 1].rstrip() + "…"


def _py_docstring(raw: list[str], start: int, stop: int) -> str | None:
    """The first line of a docstring that opens on the first non-blank line in [start, stop]."""
    for n in range(start, min(stop, len(raw) - 1) + 1):
        line = raw[n].strip()
        if not line:
            continue
        m = re.match(r"[rRuU]?(\"\"\"|''')", line)
        if not m:
            return None
        text = "\n".join(raw[n: stop + 1]).lstrip()
        body = text[m.end():]
        close = body.find(m.group(1))
        inner = body if close < 0 else body[:close]
        return next((c for c in (_clip(x) for x in inner.split("\n")) if c), None)
    return None


def _comment_text(line: str) -> str:
    return _COMMENT.sub("", line).replace("*/", "").strip()


def _first_comment(lines: list[str]) -> str | None:
    """The first meaningful line of a comment block: '/**', '*/' and bare '*' lines are skipped."""
    for line in lines:
        text = _clip(_comment_text(line))
        if text:
            return text
    return None


def _file_doc(f: _File, first_symbol: int) -> str | None:
    """Python: a module docstring before the first symbol. Others: the leading comment block (not a licence header)."""
    lines = f.raw[1:first_symbol]
    if f.lang == "python":
        for n, line in enumerate(lines, start=1):
            if line.strip() and not line.lstrip().startswith("#"):
                return _py_docstring(f.raw, n, first_symbol - 1)
        return None
    block: list[str] = []
    for line in lines + [""]:
        stripped = line.strip()
        if stripped.startswith("#!"):
            continue
        if stripped and _COMMENT.match(line):
            block.append(line)
            continue
        if block:
            if not re.search(r"copyright|licen[cs]e|spdx", " ".join(block), re.I):
                return _first_comment(block)
            block = []
        if stripped:
            return None
    return None


def _analyse(f: _File, spans: list[K.Span]) -> list[_Info]:
    test = bool(TEST_PATH.search(f.path))
    f.doc = _file_doc(f, spans[0].start_line)
    if f.lang == "python":
        return _analyse_python(f, spans, test)
    out = []
    for s in spans:
        shown_end = next((n for n in range(min(s.end_line, len(f.raw) - 1), s.start_line, -1) if f.raw[n].strip()), s.start_line)
        above: list[str] = []
        n = s.start_line - 1
        while n >= 1 and f.raw[n].strip() and _COMMENT.match(f.raw[n]):
            above.insert(0, f.raw[n])
            n -= 1
        first_body = next((f.raw[n] for n in range(s.start_line + 1, shown_end + 1) if f.raw[n].strip()), "")
        doc = _first_comment(above) or (_first_comment([first_body]) if _COMMENT.match(first_body) else None)
        out.append(_Info(span=s, test=test, shown_end=shown_end, container=None,
                         kind="class" if _CLASSY.match(f.clean[s.start_line]) else "function", doc=doc,
                         body="\n".join(f.clean[s.start_line + 1: s.end_line + 1])))
    return out


def _analyse_python(f: _File, spans: list[K.Span], test: bool) -> list[_Info]:
    """One indentation walk over the cleaned file. A line belongs to the innermost open symbol whose def line is less
    indented; a dedent closes symbols, and lines outside every symbol are module code (ignored, like header chunks).
    Continuation lines (open brackets, a trailing backslash) never close anything, so multi-line signatures and calls
    stay with their symbol.

    A symbol's container is the class it is defined directly in: a def nested in a method is a local function of that
    method, not a method. A function's code runs to its last line, nested defs included: chunking starts a span at
    every def, so the span of a function that opens with a helper ends where the helper starts. A class shows its own
    lines up to its first method (each method is its own stop)."""
    starts = {s.start_line: s for s in spans}
    is_class = {s: bool(re.match(r"\s*class\b", f.clean[s.start_line])) for s in spans}
    owned: dict[K.Span, list[int]] = defaultdict(list)
    header: dict[K.Span, set[int]] = defaultdict(set)
    outer: dict[K.Span, K.Span | None] = {}
    local: dict[K.Span, bool] = {}
    stack: list[tuple[K.Span, int]] = []
    depth, cont, signature = 0, False, None
    for n in range(1, len(f.clean)):
        text = f.clean[n]
        if n in starts:
            s, ind = starts[n], _indent(text)
            while stack and stack[-1][1] >= ind:
                stack.pop()
            outer[s] = stack[-1][0] if stack else None
            local[s] = any(not is_class[t] for t, _ in stack)
            stack.append((s, ind))
            owned[s].append(n)
            header[s].add(n)
            depth, cont, signature = 0, False, s
        elif text.strip():
            if depth > 0 or cont:
                if stack:
                    owned[stack[-1][0]].append(n)
                if signature:
                    header[signature].add(n)
            else:
                ind = _indent(text)
                while stack and stack[-1][1] >= ind:
                    stack.pop()
                if stack:
                    owned[stack[-1][0]].append(n)
                signature = None
        else:
            continue
        depth = max(0, depth + _brackets(text))
        cont = text.rstrip().endswith("\\")
        if depth == 0 and not cont:
            signature = None
    extent = {s: max(owned[s]) for s in spans}  # the last line of the symbol or of anything defined inside it
    for s in reversed(spans):  # spans run in start-line order, so a nested def is folded in before what encloses it
        if outer[s] is not None:
            extent[outer[s]] = max(extent[outer[s]], extent[s])
    out = []
    for s in spans:
        mine, parent = owned[s], outer[s]
        container = parent.symbol if parent is not None and is_class[parent] else None
        shown_end = max([n for n in mine if n <= s.end_line] or [s.start_line]) if is_class[s] else extent[s]
        kind = "class" if is_class[s] else ("method" if container else "function")
        out.append(_Info(span=s, test=test, shown_end=shown_end, container=container, kind=kind,
                         doc=_py_docstring(f.raw, max(header[s]) + 1, max(s.end_line, shown_end)),
                         body="\n".join(f.clean[n] for n in mine if n not in header[s]), outer=parent, local=local[s]))
    return out


def _assign_keys(infos: list[_Info]) -> None:
    """'path::symbol', unique: a name defined twice in one file is qualified by its class (or the function it is nested
    in), else by its line."""
    seen = defaultdict(int)
    for i in infos:
        seen[(i.span.path, i.span.symbol)] += 1
    taken: set[str] = set()
    for i in infos:
        key = f"{i.span.path}::{i.span.symbol}"
        if seen[(i.span.path, i.span.symbol)] > 1:
            scope = i.container or (i.outer.symbol if i.outer is not None else None)
            key = f"{i.span.path}::{scope}.{i.span.symbol}" if scope else key
            if key in taken or not scope:
                key = f"{i.span.path}::{i.span.symbol}@{i.span.start_line}"
        taken.add(key)
        i.key = key


# ---------------------------------------------------------------- imports
def _imports(files: dict[str, _File], known: set[str]) -> dict[str, _Imports]:
    suffix: dict[str, list[str]] = defaultdict(list)  # 'b/c.py' -> every known path ending in '/b/c.py'
    for p in known:
        parts = p.split("/")
        for i in range(1, len(parts)):
            suffix["/".join(parts[i:])].append(p)
    dirs: dict[str, list[str]] = defaultdict(list)
    for p in sorted(known):
        dirs[posixpath.dirname(p)].append(p)

    def find(*candidates: str) -> str | None:
        for c in candidates:
            if c in known:
                return c
        for c in candidates:
            if len(suffix.get(c, [])) == 1:  # a src/ layout: the only file that ends with the module path
                return suffix[c][0]
        return None

    out: dict[str, _Imports] = {}
    for path, f in files.items():
        imp = _Imports()
        text = "\n".join(f.raw)
        add = lambda p: p and p != path and p not in imp.files and imp.files.append(p)  # noqa: E731
        if f.lang == "python":
            for dots, module, names in _PY_FROM.findall(text):
                base = posixpath.dirname(path)
                for _ in range(max(0, len(dots) - 1)):
                    base = posixpath.dirname(base)
                rel = "/".join(x for x in ([base] if dots else []) + module.split(".") if x)
                target = find(f"{rel}.py", f"{rel}/__init__.py") if rel else None
                add(target)
                for part in names.strip("()").replace("\n", " ").split(","):
                    bits = part.split()
                    if not bits or bits[0] == "*":
                        continue
                    name, local = bits[0], bits[-1]
                    sub = find(f"{rel}/{name}.py" if rel else f"{name}.py")
                    if sub:  # `from pkg import module`: calls look like module.func(), a dotted call into that file
                        add(sub)
                        imp.modules[local] = sub
                    elif target:
                        imp.names[local] = (target, name)
                    else:
                        imp.external.add(local)
            for spec in _PY_IMPORT.findall(text):
                for part in spec.split(","):
                    bits = part.split()
                    if bits:
                        rel = bits[0].replace(".", "/")
                        target = find(f"{rel}.py", f"{rel}/__init__.py")
                        add(target)
                        if target:  # `import a.b as m` binds m; `import a.b` is called as a.b.f(), whose receiver is b
                            imp.modules[bits[-1] if len(bits) == 3 and bits[1] == "as" else bits[0].rsplit(".", 1)[-1]] = target
        elif f.lang in ("javascript", "typescript"):
            pairs = _JS_IMPORT.findall(text) + _JS_REQUIRE.findall(text)
            for clause, spec in pairs:
                if not spec.startswith("."):
                    continue
                base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
                stem = re.sub(r"\.(?:js|jsx|mjs|cjs)$", "", base)
                target = find(base, *(stem + e for e in _JS_EXTS), *(f"{base}/index{e}" for e in _JS_EXTS))
                add(target)
                named = re.search(r"\{([^}]*)\}", clause)
                for part in (named.group(1).split(",") if named else []):
                    bits = part.replace("type ", "").split()
                    if bits and target:
                        imp.names[bits[-1]] = (target, bits[0])
                default = re.match(r"\s*([\w$]+)\s*(?:,|$)", clause)
                if default and target and default.group(1) not in ("type",):
                    imp.names.setdefault(default.group(1), (target, default.group(1)))
                    imp.modules.setdefault(default.group(1), target)  # a default export used as an object: X.method()
                namespace = re.search(r"\*\s*as\s+([\w$]+)", clause)
                if namespace and target:
                    imp.modules[namespace.group(1)] = target
        elif f.lang == "go":
            for block, single in _GO_IMPORT.findall(text):
                for spec in re.findall(r'"([^"]+)"', block) if block else [single]:
                    for d, members in dirs.items():
                        if d and (spec == d or spec.endswith("/" + d)):
                            for p in members:
                                if p.endswith(".go"):
                                    add(p)
        elif f.lang in ("java", "kotlin"):
            ext = ".java" if f.lang == "java" else ".kt"
            for dotted in _JVM_IMPORT.findall(text):
                target = find(dotted.replace(".", "/") + ext)
                add(target)
                if target:
                    imp.names[dotted.rsplit(".", 1)[-1]] = (target, dotted.rsplit(".", 1)[-1])
        if f.lang in _SAME_PACKAGE:
            for p in dirs.get(posixpath.dirname(path), []):
                if language_of(p) == f.lang:
                    add(p)
        out[path] = imp
    return out


# ---------------------------------------------------------------- calls
def _js_method_def(body: str, start: int, name: str) -> bool:
    """True when `name(` at `start` opens a method definition, not a call: the name starts its line (after modifiers
    such as async or static) and the closing bracket is followed by a block, maybe after a return type:
    `persist(key: string) {`, `async load(): Promise<void> {`. No call is followed by a block in JS or TS."""
    line_start = body.rfind("\n", 0, start) + 1
    if not _JS_MEMBER_PREFIX.fullmatch(body, line_start, start):
        return False
    depth = 0
    for n in range(body.find("(", start + len(name)), min(len(body), start + 2000)):
        if body[n] == "(":
            depth += 1
        elif body[n] == ")":
            depth -= 1
            if depth == 0:
                return bool(_JS_BLOCK_AFTER.match(body, n + 1))
    return False


def _call_edges(infos: list[_Info], imports: dict[str, _Imports], files: dict[str, _File]) -> dict[str, list[str]]:
    table: dict[str, list[_Info]] = defaultdict(list)
    members: dict[tuple[str, str], list[_Info]] = defaultdict(list)
    nested: dict[K.Span, list[_Info]] = defaultdict(list)  # outer span -> the defs directly inside it
    by_span = {i.span: i for i in infos}
    for i in infos:
        table[i.span.symbol].append(i)
        if i.container:
            members[(i.span.path, i.container)].append(i)
        if i.outer is not None:
            nested[i.outer].append(i)

    def in_scope(name: str, caller: _Info) -> _Info | None:
        """Python scoping for a bare name: a def nested in the caller, else in a function that encloses it (a class body
        is not an enclosing scope). Locals shadow imports and module names."""
        scope: _Info | None = caller
        while scope is not None:
            if scope.kind != "class":
                hit = next((c for c in nested.get(scope.span, ()) if c.span.symbol == name), None)
                if hit:
                    return hit
            scope = by_span.get(scope.outer) if scope.outer is not None else None
        return None

    def resolve(name: str, receiver: str | None, caller: _Info, of_class: str | None = None) -> _Info | None:
        """`receiver` is the name before the dot for obj.name(...), '' for a dot after an expression, None for name(...).
        `of_class` is the class a method call's receiver is known to be: self's own, or Name() for Name().method()."""
        imp = imports.get(caller.span.path) or _Imports()
        dotted = receiver is not None
        if receiver in ("self", "cls", "this"):
            of_class = caller.container
        lang = files[caller.span.path].lang
        python = lang == "python"
        if python and not dotted:
            hit = in_scope(name, caller)
            if hit:
                return hit
        if not dotted and name in imp.names:  # imported by name, maybe under an alias: that file's symbol wins
            target, original = imp.names[name]
            hit = next((c for c in table.get(original, []) if c.span.path == target and c.container is None and not c.local), None)
            if hit or original != name:
                return hit
            # not defined there (a package __init__ that re-exports it): fall through to the general rules
        # a function nested in another one is reached only through that function's scope (above), never from outside
        pool = [c for c in table.get(name, []) if (caller.test or not c.test) and not (c.local and c.container is None)]
        if receiver and receiver in imp.modules:  # module.func(): that module's top-level symbol, or nothing
            return next((c for c in pool if c.span.path == imp.modules[receiver] and c.container is None), None)
        if dotted and not of_class and name in _BUILTIN_METHODS:
            return None
        if not pool or len({c.span.path for c in pool}) > K.MAX_DEFINING_FILES:
            return None
        if python and not dotted:
            pool = [c for c in pool if c.container is None]  # a bare name never reaches a method
        if of_class and dotted:
            typed = [c for c in pool if c.container == of_class]
            pool = typed or pool
        same = [c for c in pool if c.span.path == caller.span.path]
        if same:  # two same-file candidates and no hint which (x.run() with run in two classes): ambiguous, no edge
            return same[0] if len(same) == 1 else None
        if not dotted and name in imp.external:
            return None
        if lang in _JS and not dotted and name not in imp.names:  # ES modules have no implicit globals: import it or define it here
            return None
        for path in imp.files:
            hit = next((c for c in pool if c.span.path == path), None)
            if hit:
                return hit
        if not dotted and len({c.span.path for c in pool}) == 1:
            return pool[0]
        return None

    calls: dict[str, list[str]] = {}
    for i in infos:
        found: list[str] = []
        js = files[i.span.path].lang in _JS
        sites = [(m.start(), m.group(1), False) for m in CALL.finditer(i.body)]
        if js:
            sites = sorted(sites + [(m.start(), m.group(1), True) for m in _JSX_TAG.finditer(i.body)])
        for start, name, jsx in sites:
            if len(name) < 3 or name in _SKIP or name == i.span.symbol:
                continue
            before = i.body[max(0, start - 24): start]
            if not jsx and (_DEFINES.search(before) or (js and _js_method_def(i.body, start, name))):
                continue
            receiver, of_class = None, None
            if not jsx and before.rstrip().endswith("."):
                r = _RECEIVER.search(before)
                receiver = r.group(1) if r else ""
                made = _NEW_RECEIVER.search(before)
                of_class = made.group(1) if made else None
            target = resolve(name, receiver, i, of_class)
            if target and target.key != i.key and target.key not in found:
                found.append(target.key)
        if i.kind == "class":  # Foo(...) runs Foo.__init__ (Python): keep that path walkable
            for member in members.get((i.span.path, i.span.symbol), []):
                if member.span.symbol in ("__new__", "__init__", "__post_init__") and member.key not in found:
                    found.append(member.key)
        calls[i.key] = found
    return calls


# ---------------------------------------------------------------- chapters
def _sccs(nodes: list[str], edges: dict[str, tuple[str, ...]]) -> list[list[str]]:
    """Tarjan's strongly connected components, iteratively. Emitted in reverse topological order: a component comes
    after every component it reaches."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, i = work[-1]
            if i == 0 and v not in index:
                index[v] = low[v] = len(index)
                stack.append(v)
                on_stack.add(v)
            succ = edges.get(v, ())
            if i < len(succ):
                work[-1] = (v, i + 1)
                w = succ[i]
                if w not in index:
                    work.append((w, 0))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def _add_column(counters: list[int], mask: int, at: int) -> None:
    """Add 2**at to the count of every column set in `mask`. counters[j] holds bit j of each column's count (a
    bit-sliced binary counter), so a pass counts ENTRY_BATCH columns at once with a few big-int operations per node."""
    j = at
    while mask:
        while j >= len(counters):
            counters.append(0)
        carry = counters[j] & mask
        counters[j] ^= mask
        mask, j = carry, j + 1


def _entry_points(nodes: list[str], edges: dict[str, tuple[str, ...]], by_key: dict[str, _Info]) -> tuple[list[str], set[str]]:
    """Symbols nothing in product code calls that call something, ranked by how many symbols they reach (then path). A
    cycle nothing outside calls (a() <-> b()) is one entry, its first symbol by path and line. Returns (entries, every
    symbol some entry reaches).

    Reach is counted over the component DAG in passes of ENTRY_BATCH entries: each pass walks the components once in
    topological order, carrying an ENTRY_BATCH-bit mask of which entries reach each one, and adds each component's size
    to the counts of the entries in its mask. Memory is components x ENTRY_BATCH bits, linear in the symbols; a reach
    set per component sized by the symbol count (n x n bits, a third of a GiB at 50,000 symbols) is never built."""
    comps = _sccs(nodes, edges)  # reverse topological: a component comes after every component it reaches
    comp_of = {v: ci for ci, comp in enumerate(comps) for v in comp}
    succ: list[set[int]] = [set() for _ in comps]
    has_caller = [False] * len(comps)
    for ci, comp in enumerate(comps):
        for v in comp:
            for w in edges.get(v, ()):
                cw = comp_of[w]
                if cw != ci:
                    succ[ci].add(cw)
                    has_caller[cw] = True
    roots: list[tuple[str, int]] = []
    for ci, comp in enumerate(comps):
        if has_caller[ci]:
            continue
        first = min(comp, key=lambda v: (by_key[v].span.path, by_key[v].span.start_line))
        if edges.get(first) or len(comp) > 1:
            roots.append((first, ci))
    reach: dict[str, int] = {}
    reached = bytearray(len(comps))
    for at in range(0, len(roots), ENTRY_BATCH):
        batch = roots[at:at + ENTRY_BATCH]
        mask = [0] * len(comps)
        for bit, (_, ci) in enumerate(batch):
            mask[ci] |= 1 << bit
        counters: list[int] = []
        for ci in range(len(comps) - 1, -1, -1):  # topological: every caller before what it calls
            m = mask[ci]
            if not m:
                continue
            mask[ci] = 0
            reached[ci] = 1
            size, j = len(comps[ci]), 0
            while size:
                if size & 1:
                    _add_column(counters, m, j)
                size, j = size >> 1, j + 1
            for cw in succ[ci]:
                mask[cw] |= m
        for bit, (first, _) in enumerate(batch):
            reach[first] = sum(((c >> bit) & 1) << j for j, c in enumerate(counters))
    entries = sorted((first for first, _ in roots), key=lambda v: (-reach[v], by_key[v].span.path, by_key[v].span.start_line))
    return entries, {v for v in nodes if reached[comp_of[v]]}


def _chapters(entries: list[str], edges: dict[str, tuple[str, ...]], by_key: dict[str, _Info],
              files: dict[str, _File]) -> tuple[list[Chapter], bool]:
    shown: dict[str, str] = {}  # key -> the chapter that expanded it
    used_titles: set[str] = set()
    chapters: list[Chapter] = []
    truncated = len(entries) > MAX_CHAPTERS
    for entry in entries[:MAX_CHAPTERS]:
        cid = f"ch-{len(chapters) + 1}"
        pre: list[tuple[str, str | None]] = []
        post: list[tuple[str, str | None]] = []
        local: set[str] = set()
        trails: dict[str, tuple[str, ...]] = {}
        cut = False

        def visit(v: str, trail: tuple[str, ...]) -> None:
            nonlocal cut
            if v in local:
                return
            if len(pre) >= MAX_STOPS:
                cut = True
                return
            local.add(v)
            trails[v] = trail
            if v in shown:  # explained in an earlier chapter: a pointer, not a second walk
                pre.append((v, shown[v]))
                post.append((v, shown[v]))
                return
            shown[v] = cid
            pre.append((v, None))
            for w in edges.get(v, ()):
                visit(w, (*trail, v))
            post.append((v, None))

        visit(entry, ())
        truncated = truncated or cut
        info = by_key[entry]
        title = files[info.span.path].doc
        if not title or title in used_titles:
            title = info.doc or f"Starting at {info.container + '.' if info.container else ''}{info.span.symbol}"
        used_titles.add(title)
        chapters.append(Chapter(id=cid, title=title, entry=entry, execution=tuple(pre), reading=tuple(post), trails=trails))
    return chapters, truncated


# ---------------------------------------------------------------- cache
_CACHE: OrderedDict[tuple, Graph] = OrderedDict()
_PINS: OrderedDict[tuple, tuple[tuple[str, dict], ...]] = OrderedDict()  # (graph key, ready meetings) -> action item pins
_LABELS: OrderedDict[tuple, str | None] = OrderedDict()  # (meeting, its notes' version, clip) -> who was speaking
_BUILDING: dict[tuple, Future] = {}  # graph key -> the build in progress: concurrent cold requests wait for it
_LOCK = threading.Lock()
_MISS = object()


def _cached(cache: OrderedDict, key: tuple):
    with _LOCK:
        hit = cache.get(key, _MISS)
        if hit is not _MISS:
            cache.move_to_end(key)
        return hit


def _remember(cache: OrderedDict, key: tuple, value, size: int):
    with _LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > size:
            cache.popitem(last=False)
    return value


def _remember_graph(key: tuple, graph: Graph) -> None:
    """At most CACHE_SIZE graphs and CACHE_SYMBOLS symbols across them, least recently used dropped first; a project's
    older graphs go at once (its code moved on)."""
    with _LOCK:
        for old in [k for k in _CACHE if k[0] == key[0] and k != key]:
            del _CACHE[old]
        _CACHE[key] = graph
        _CACHE.move_to_end(key)
        while len(_CACHE) > 1 and (len(_CACHE) > CACHE_SIZE or sum(len(g.symbols) for g in _CACHE.values()) > CACHE_SYMBOLS):
            _CACHE.popitem(last=False)


def _graph(project_id: str, sha: str | None, version: str, load: Callable[[], list[Chunk]],
           still: Callable[[], bool] = lambda: True) -> tuple[tuple, Graph]:
    """(cache key, graph) for the project's code at `version` (index.code_version). On a miss `load` reads the chunks
    with their bodies, once however many requests ask at the same time (single-flight); the graph is cached only when
    `still()` says the index has not moved on since `version` was read."""
    key = (project_id, sha, version, ports.CODE_CHUNK_CAP)
    hit = _cached(_CACHE, key)
    if hit is not _MISS:
        return key, hit
    with _LOCK:
        hit = _CACHE.get(key, _MISS)
        if hit is not _MISS:
            return key, hit
        building = _BUILDING.get(key)
        mine = building is None
        if mine:
            building = _BUILDING[key] = Future()
    if not mine:
        return key, building.result()  # another request is building this very graph: wait for it (its error is ours too)
    try:
        graph = build_graph(load())  # outside the lock: a slow build never blocks other projects' reads
        if still():
            _remember_graph(key, graph)
        building.set_result(graph)
        return key, graph
    except BaseException as e:
        building.set_exception(e)
        raise
    finally:
        with _LOCK:
            _BUILDING.pop(key, None)


def graph_for(index: Index, project_id: str, sha: str | None) -> Graph:
    """The project's graph, built once per (project, indexed commit, code version). ingest_repo and ingest_push update
    last_indexed_sha after their upserts, so a push rebuilds it; the version also catches a re-index that kept the
    commit."""
    version = index.code_version(project_id)
    return _graph(project_id, sha, version, lambda: index.code_chunks(project_id, content=True),
                  lambda: index.code_version(project_id) == version)[1]


def clear_cache() -> None:
    with _LOCK:
        for cache in (_CACHE, _PINS, _LABELS):
            cache.clear()


def _digest(text: str | None) -> str:
    return hashlib.blake2b((text or "").encode(), digest_size=8).hexdigest()


def _notes_version(m: Meeting) -> tuple:
    """What changes when a meeting's notes are (re)written: save_meeting_result sets the summary with them."""
    return (m.id, m.title, m.status, _digest(m.summary))


# ---------------------------------------------------------------- the tour for one viewer
def _stats(stops: int = 0, explained: int = 0, kt_seconds: float = 0.0, by_owner: list | None = None) -> dict:
    return {"stops": stops, "explained": explained, "kt_seconds": kt_seconds, "unexplained_by_owner": by_owner or []}


def _union_seconds(clips: set[tuple[str, float, float]]) -> float:
    """Seconds of recording covered by these clips, overlaps counted once per meeting."""
    total = 0.0
    by_meeting: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for mid, s, e in clips:
        by_meeting[mid].append((s, e))
    for spans in by_meeting.values():
        end = float("-inf")
        for s, e in sorted(spans):
            if e > end:
                total += e - max(s, end)
                end = e
    return round(total, 1)


def _first_pr_anchor(searches: K.SafeSearch, project_id: str, g: Graph, t: TaskRow) -> tuple[str, float | None] | None:
    """(stop key, search similarity) where a first-PR task lands: the best search hit (within related_paths when the
    task has them, else anywhere), else the first symbol under related_paths. In production a search is an embedding
    call plus a hybrid pgvector query; SafeSearch remembers it per task wording until the code changes, and gives no
    hits when search is down (the task then has no target unless related_paths names one)."""
    pool = g.spans_by_file
    if t.related_paths:
        pool = {p: s for p, s in pool.items() if K.under_prefixes(p, t.related_paths)}
    found = K.search_span(searches, project_id, K.task_query(t.title), pool, k=8 if t.related_paths else 3)
    span = found[0] if found else next((s for spans in pool.values() for s in spans), None) if t.related_paths else None
    key = g.key_at.get((span.path, span.start_line)) if span else None
    return (key, round(found[1], 2) if found else None) if key else None


def _first_pr_targets(store: AppStore, searches: K.SafeSearch, project_id: str, viewer: User, g: Graph,
                      members: list[User]) -> dict[str, list[dict]]:
    """Every visible first-PR task, by the stop it lands on: the viewer's own first, then by onboarding (start date,
    the new hire's name, id) and the store's task order, so two hires whose first PRs land on one stop are both named,
    the same way on every request and on either adapter (ids are random, so a cohort that starts on one day is ordered
    by name). A new hire sees only their own, once approved (the GET /tasks rule); managers, experts and admins see
    every active onboarding's, proposed ones included, so they can check a plan before approving it. One onboardings
    read and one tasks read, whatever the number of hires."""
    onboardings = store.onboardings(project_id)
    if viewer.role == "new_hire":
        onboardings, hidden = [o for o in onboardings if o.new_hire_id == viewer.id], HIDDEN_FROM_HIRE
    else:
        hidden = frozenset({"rejected"})
    if not onboardings:
        return {}
    people = {u.id: u for u in members}
    hires = {o.new_hire_id: people.get(o.new_hire_id) or store.get_user(o.new_hire_id) for o in onboardings}  # someone who left still has a name
    onboardings = sorted(onboardings, key=lambda o: (o.start_date, hires[o.new_hire_id].name if hires[o.new_hire_id] else "", o.id))
    rank = {o.id: n for n, o in enumerate(onboardings)}
    hire_of = {o.id: o.new_hire_id for o in onboardings}
    tasks = [t for t in store.tasks(project_id=project_id) if t.onboarding_id in rank and t.origin == "first_pr" and t.status not in hidden]
    out: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(tasks, key=lambda t: rank[t.onboarding_id]):  # stable: the store's (due date, title) order within a hire
        anchor = _first_pr_anchor(searches, project_id, g, t)
        if not anchor:
            continue
        key, match = anchor
        hire_id = hire_of[t.onboarding_id]
        hire = hires[hire_id]
        out[key].append({"task_id": t.id, "title": t.title, "issue": t.github_issue_number,
                         "new_hire": K.first_name(hire.name) if hire else "A new hire", "new_hire_id": hire_id,
                         "status": t.status, "match": match})
    for rows in out.values():
        rows.sort(key=lambda r: r["new_hire_id"] != viewer.id)
    return dict(out)


def _edges(keys: tuple[str, ...]) -> list[str]:
    return list(keys[:MAX_EDGES])


def build_tour(store: AppStore, index: Index, project_id: str, org_id: str, viewer: User, order: Order = "execution") -> dict:
    version = index.code_version(project_id)  # one aggregate query: the cache key; chunks are read only on a miss
    store = K.Reads(store, project_id, org_id, notes=False, chats=False)  # notes only on a cache miss (speakers, pins)
    repo = (store.repos or [None])[0]
    sha = repo.last_indexed_sha if repo else None
    indexed = not version.startswith("0:")  # 'count:digest': no code chunk at all
    out: dict = {"indexed": indexed, "sha": sha, "order": order, "truncated": False, "index_truncated": False, "partial": False,
                 "files": 0, "symbols": 0, "tests_total": 0, "unreached_total": 0, "stats": _stats(), "chapters": [], "tests": [],
                 "unreached": [], "hint": None}
    if not indexed:
        return out
    gkey, g = _graph(project_id, sha, version, lambda: index.code_chunks(project_id, content=True),
                     lambda: index.code_version(project_id) == version)
    if not g.paths:  # the code went between the two reads
        return {**out, "indexed": False}
    product = [s for s in g.symbols.values() if not s.test]
    out.update(truncated=g.truncated, index_truncated=g.index_truncated, files=len({s.path for s in product}), symbols=len(product),
               tests_total=g.tests_total, unreached_total=g.unreached_total)

    links = K.counted(store.links)
    links_on: dict[str, list[KTLink]] = defaultdict(list)
    for link in links:
        links_on[link.file_path].append(link)
    members = store.members(project_id)
    speakers = K.Speakers(store, members)
    meetings = store.list_meetings(project_id)
    durations = {m.id: m.duration_s for m in meetings}
    versions = {m.id: _notes_version(m) for m in meetings}
    owners = K.owners_index(store, org_id, project_id)
    searches = K.SafeSearch(index, project_id, g.fingerprint)  # the code fingerprint every feature's search memo shares
    first_prs = _first_pr_targets(store, searches, project_id, viewer, g, members)
    out["partial"] = searches.partial  # a first-PR search failed or ran out of time: that target may be missing
    searches.report("tour")

    def speaker_of(link: KTLink) -> tuple[str | None, User | None]:
        """speakers.for_link, with the label cached per clip and notes version: finding it reads the meeting's notes."""
        ck = (versions.get(link.meeting_id), link.meeting_id, link.start_s, link.end_s)
        label = _cached(_LABELS, ck)
        if label is _MISS:
            label = _remember(_LABELS, ck, speakers.label(link.meeting_id, link.start_s, link.end_s), SIDE_CACHE_SIZE)
        return label, speakers.member(label)

    kt_cache: dict[str, list[dict]] = {}

    def kt_for(s: Sym) -> list[dict]:
        if s.key not in kt_cache:
            rows, seen = [], set()
            for link in sorted((l for l in links_on.get(s.path, []) if K.link_matches(l, s.span)), key=lambda l: (l.start_s, l.meeting_id)):
                clip = (link.meeting_id, link.start_s, link.end_s)
                if clip in seen:
                    continue
                seen.add(clip)
                label, member = speaker_of(link)
                rows.append({"meeting_id": link.meeting_id, "meeting_title": link.meeting_title, "start_s": link.start_s, "end_s": link.end_s,
                             "confidence": link.confidence, "speaker": label, "speaker_user_id": member.id if member else None,
                             "code_changed_since": link.code_changed_since, "meeting_duration_s": durations.get(link.meeting_id)})
            kt_cache[s.key] = rows
        return kt_cache[s.key]

    def owner_for(path: str) -> dict | None:
        found = owners(path)
        o = next((x for x in found if x.user_id), found[0] if found else None)  # a person to ask beats a team handle
        return {"user_id": o.user_id, "name": o.name, "login": o.login, "rule": o.rule, "team": o.team} if o else None

    chapters = []
    for ch in g.chapters:
        stops = []
        for n, (key, ref) in enumerate(ch.execution if order == "execution" else ch.reading, start=1):
            s = g.symbols[key]
            kt = kt_for(s)
            prs = [] if ref else first_prs.get(key, [])
            tags = []
            if prs:
                tags.append("first_pr_target")
            if any(k["code_changed_since"] for k in kt):
                tags.append("stale_clip")
            code = [] if ref else list(s.code[:MAX_CODE_LINES])
            trail = ch.trails.get(key, ())
            calls, callers = g.calls.get(key, ()), g.called_by.get(key, ())
            stops.append({"key": key, "n": n, "depth": len(trail), "trail": list(trail), "path": s.path, "symbol": s.symbol, "container": s.container, "kind": s.kind,
                          "start_line": s.start_line, "end_line": s.shown_end, "summary": s.doc, "file_summary": g.file_docs.get(s.path),
                          "code": code, "code_truncated": not ref and len(s.code) > MAX_CODE_LINES,
                          # a reference stop points at the stop that carries them; a hub called from everywhere lists MAX_EDGES
                          "calls": [] if ref else _edges(calls), "called_by": [] if ref else _edges(callers),
                          "calls_total": len(calls), "called_by_total": len(callers), "ref_chapter": ref,
                          "kt": kt, "owner": owner_for(s.path), "tags": tags,
                          # first_pr: the viewer's own if they have one here, else the first; first_prs: every one the viewer may see
                          "first_pr": prs[0] if prs else None, "first_prs": prs, "action_item": None})
        chapters.append({"id": ch.id, "title": ch.title, "entry": ch.entry, "pinned": [], "stops": stops})

    _attach_action_items(g, gkey, chapters, [m for m in meetings if m.status == "ready"], speakers.notes)

    real = [s for ch in chapters for s in ch["stops"] if not s["ref_chapter"]]
    clips = {(k["meeting_id"], k["start_s"], k["end_s"]) for s in real for k in s["kt"]}
    unexplained: dict[str, dict] = {}
    for s in real:
        if not s["kt"] and s["owner"]:
            o = s["owner"]  # GitHub logins are case-insensitive: @Ravi-Dev and @ravi-dev are one person
            row = unexplained.setdefault(o["user_id"] or o["login"].lower(), {"user_id": o["user_id"], "name": o["name"], "login": o["login"], "count": 0})
            row["count"] += 1
    out["stats"] = _stats(len(real), sum(1 for s in real if s["kt"]), _union_seconds(clips),
                          sorted(unexplained.values(), key=lambda r: (-r["count"], r["name"].lower())))
    out["chapters"] = chapters
    out["tests"] = [{"key": k, "path": g.symbols[k].path, "symbol": g.symbols[k].symbol, "container": g.symbols[k].container,
                     "start_line": g.symbols[k].start_line, "end_line": g.symbols[k].shown_end, "calls": _edges(g.calls.get(k, ())),
                     "calls_total": len(g.calls.get(k, ()))} for k in g.tests]
    out["unreached"] = [{"key": k, "path": g.symbols[k].path, "symbol": g.symbols[k].symbol, "container": g.symbols[k].container,
                         "kind": g.symbols[k].kind, "start_line": g.symbols[k].start_line, "end_line": g.symbols[k].shown_end,
                         "summary": g.symbols[k].doc} for k in g.unreached]
    if not chapters:
        out["hint"] = ("Nothing here calls into the rest of the code: it looks like a library of leaf helpers, or code that only "
                       "module-level statements run." if product else "No functions or classes were found in the indexed code.")
    return out


def _pins(g: Graph, gkey: tuple, ready: list[Meeting], notes: K.NotesCache) -> tuple[tuple[str, dict], ...]:
    """(stop key, pin) for every ready meeting's action item that names a toured file or symbol (knowledge.mentions,
    the rule knowledge risk and the planner use). A symbol mention lands on that stop; a file mention on the file's
    first stop in execution order. Cached per graph and ready meetings (with their notes' versions): reading every
    meeting's notes is the slow part, and the answer changes only with the code or the meetings. Treat as read-only."""
    ck = (gkey, tuple(_notes_version(m) for m in ready))
    hit = _cached(_PINS, ck)
    if hit is not _MISS:
        return hit
    notes.prime(m.id for m in ready)  # one read for every meeting's notes, not one each
    first_of_file: dict[str, str] = {}
    for ch in g.chapters:
        for key, ref in ch.execution:
            if not ref:
                first_of_file.setdefault(g.symbols[key].path, key)
    stops = {key for ch in g.chapters for key, ref in ch.execution if not ref}
    code_paths = list(g.paths)
    pins: list[tuple[str, dict]] = []
    for m in ready:
        n = notes(m.id)
        for ai in n.action_items if n else []:
            for path, span in K.mentions(ai.text, code_paths, g.spans_by_file):
                key = g.key_at.get((span.path, span.start_line)) if span else first_of_file.get(path)
                if key in stops:
                    pins.append((key, {"text": ai.text, "meeting_id": m.id, "meeting_title": m.title, "at_s": ai.at_s, "key": key}))
    return _remember(_PINS, ck, tuple(pins), CACHE_SIZE * 8)


def _attach_action_items(g: Graph, gkey: tuple, chapters: list[dict], ready: list[Meeting], notes: K.NotesCache) -> None:
    """Put the pins on this response's stops: the first on its stop (so the pin stays put when the reader switches to
    reading order), and each on its chapter."""
    if not ready or not chapters:
        return
    where = {s["key"]: (ch, s) for ch in chapters for s in ch["stops"] if not s["ref_chapter"]}
    for key, item in _pins(g, gkey, ready, notes):
        if key not in where:
            continue
        ch, stop = where[key]
        if stop["action_item"] is None:
            stop["action_item"] = {k: v for k, v in item.items() if k != "key"}
            stop["tags"].insert(1 if "first_pr_target" in stop["tags"] else 0, "action_item")
        if not any(p["meeting_id"] == item["meeting_id"] and p["text"] == item["text"] for p in ch["pinned"]):
            ch["pinned"].append(dict(item))
