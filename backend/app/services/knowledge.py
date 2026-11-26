"""Shared knowledge core: which code exists, which of it was explained on a KT call, who owns it, who spoke, and what
the team needs explained next.

Knowledge risk, the KT planner and the code tour all read a project through these helpers, so the three pages agree
on every number. Everything here is a function of the Store and Index passed in: no other I/O, no LLM, no tables of its
own. The in-process state only saves work: SafeSearch's memo of code searches, and code_view's copy of the project's
code spans, both keyed by what they depend on. Only Store methods and row fields that BOTH adapters return are used
(memory.py and postgres.py); for example chat_history's `routed_to` is never read, because Postgres does not select it.

Vocabulary
- Span: one symbol (function, class, method) of a code file, with its own line range: the chunker's body_end, so the
  next symbol's decorators and module code after the body are not part of it. Split chunks of a long function are
  merged back into one span. Every page reads these spans, so a function has one line range everywhere.
- A KT link "counts" when its confidence is at least MIN_CONFIDENCE. A counted link explains a span when it names
  the span's symbol, overlaps its lines, or covers the whole file.
- Span state: 'recorded' (explained), 'stale' (explained, but every such clip predates a code change: half credit)
  or 'none'.
- Reason: one demand signal saying that a file or span needs explaining, with a weight and a reference back to the
  task, meeting moment or chat message it came from. A new hire's chat question is theirs: a question reason never
  carries who asked or what they wrote, only that a new hire asked and how it went. `expert_text` is what an expert
  reads instead of `text`: no new hire's name, and nothing of a plan that is still awaiting approval.
- Code search: anchoring a path-less code task or an unanswered question costs an index.search, which on Postgres is
  an embedding call. Features pass demand() a SafeSearch, never the bare index: remembered until the code changes,
  batched into one embedding call per request, never waited on past the request's budget, shared between concurrent
  requests, and degrading to "no hits" (with `partial`) when the embedder is down or slow.
- Reads: a Store facade that fetches everything a page reads about a project up front, in one transaction (a handful
  of queries whatever the team size), and answers the Store calls below from it.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import re
import threading
import time
import weakref
from collections import OrderedDict, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable, Literal, NamedTuple

from app import ports
from app.agents.schemas import Chunk, Hit, KTLink, MeetingResult, Utterance
from app.ports import Index, Meeting, Onboarding, Store, TaskRow, User
from app.services.chunking import module_of
from app.services.codeowners import parse_codeowners, rule_for
from app.services.coverage import coverage_map, gaps
from app.services.diff import TEST_PATH
from app.services.kt_linker import match_path

MIN_CONFIDENCE = 0.5  # a KT link below this is a guess: it explains nothing
STALE_CREDIT = 0.5  # a clip recorded before the code changed is worth half a fresh one
DEFAULT_PACE_S_PER_LINE = 12.0  # used until the project has a KT link with a line range
MIN_SIMILARITY = 0.2  # a search hit below this does not anchor a task or question to code
PATH_CONFIDENCE = 0.85  # kt_linker.match_path confidence needed to read a path out of free text
MAX_QUESTION_SEARCHES = 50  # index searches spent on unanswered questions per request, newest first across all hires
CHAT_ROWS = 200  # chat rows demand() reads per new hire (store.chat_history limit)
SEARCH_BUDGET_S = 4.0  # seconds a request waits for live code search in all; then it stops waiting and is partial
SEARCH_MEMO_MAX = 1024  # code searches remembered per index, across projects (least recently used dropped first)
SEARCH_WORKERS = 8  # live search batches running at once in this process, across requests
SEARCH_QUEUE_MAX = 16  # batches started and not finished; past this a new search is skipped rather than queued
BREAKER_FAILURES = 2  # consecutive failed or timed-out searches that stop live searches on this index...
BREAKER_OPEN_S = 30.0  # ...for this long (remembered answers are still given); the next search after it is a probe
TRACEBACK_EVERY_S = 60.0  # a failed search logs its traceback at most this often per process
CODE_VIEW_CHUNKS = 150_000  # chunks of code views kept per index, across projects (least recently used dropped first)
MAX_DEFINING_FILES = 3  # a name defined in more files than this is too common to attribute
IGNORED_MODULES = ("(root)", "docs", "tests")  # the same ignore list as coverage.gaps()
CLOSED_TASK_STATUSES = frozenset({"done", "rejected"})

SpanStateName = Literal["recorded", "stale", "none"]
ReasonKind = Literal["first_pr", "task", "action_item", "question", "named_not_shown", "gap", "stale"]
REASON_WEIGHTS: dict[str, int] = {"first_pr": 3, "task": 2, "action_item": 2, "question": 1, "named_not_shown": 1, "gap": 1, "stale": 1}
UNANSWERED_QUESTION_WEIGHT = 2  # an unanswered question anchored to code weighs more than one answered without a clip
# A question reason's text: never who asked, never the words (a new hire's chat is theirs; GET /chat/history is owner-only).
QUESTION_ANSWERED_TEXT = "A new hire asked about this code; it was answered from code with no recording"
QUESTION_UNANSWERED_TEXT = "A new hire asked about this code and got no answer"
PLANNED_WORK_TEXT = "Planned work for a new hire, awaiting approval"  # an expert's view of a task not approved yet

log = logging.getLogger("knowledge")


# ---------------------------------------------------------------- spans
@dataclass(frozen=True)
class Span:
    """One symbol of a code file. `lines` is filled in from the range when left 0."""

    symbol: str
    start_line: int
    end_line: int
    lines: int = 0
    path: str = ""

    def __post_init__(self):
        if not self.lines:
            object.__setattr__(self, "lines", self.end_line - self.start_line + 1)

    @property
    def key(self) -> str:
        """'engine/validate.py::check_privileged': the id the UI and the planner's session keys use."""
        return f"{self.path}::{self.symbol}"


def is_code_path(path: str) -> bool:
    """Files that count as product code: not in the root, docs or tests modules, and not a test file anywhere."""
    return module_of(path).split("/")[0] not in IGNORED_MODULES and not TEST_PATH.search(path)


def code_files(index: Index, project_id: str, chunks: list | None = None) -> dict[str, list[Span]]:
    """Every code file with its symbol spans, files by path and spans by start_line.

    Built from one `index.code_chunks(project_id, content=False)` read (pass `chunks` to reuse a read you already
    made, e.g. content=True chunks for the tour). Chunks of one (path, symbol) whose ranges touch are merged:
    chunk_code splits functions over 120 lines. Files without symbols (markdown, yaml), test files and the root,
    docs and tests modules are left out.
    """
    chunks = index.code_chunks(project_id, content=False) if chunks is None else chunks
    return merge_spans(c for c in chunks if c.source_type == "code" and not c.stale and is_code_path(c.ref.get("path") or ""))


def merge_spans(chunks: Iterable[Chunk]) -> dict[str, list[Span]]:
    """Symbol chunks as spans, files by path and spans by start_line. Chunks of one (path, symbol) whose ranges touch are
    one span (chunk_code splits a long function), which ends at the chunker's body_end when the chunk carries one."""
    pieces = sorted((c for c in chunks if c.symbol), key=lambda c: (c.ref["path"], c.ref.get("start_line") or 0))
    files: dict[str, list[Span]] = defaultdict(list)
    chunk_end: dict[str, int] = {}  # each file's last chunk end: whether the next piece continues the same symbol
    for c in pieces:
        path = c.ref["path"]
        start = int(c.ref.get("start_line") or 1)
        end = int(c.ref.get("end_line") or start)
        own = int(c.ref.get("body_end") or end)
        spans = files[path]
        prev = spans[-1] if spans else None
        if prev and prev.symbol == c.symbol and start == chunk_end[path] + 1:
            spans[-1] = Span(prev.symbol, prev.start_line, max(prev.start_line, min(end, own)), path=path)
        else:
            spans.append(Span(c.symbol, start, max(start, min(end, own)), path=path))  # type: ignore[arg-type]
        chunk_end[path] = end
    return {p: files[p] for p in sorted(files)}


def all_spans(spans_by_file: dict[str, list[Span]]) -> list[Span]:
    return [s for spans in spans_by_file.values() for s in spans]


def span_at(spans_by_file: dict[str, list[Span]], path: str, line: int | None, symbol: str | None = None) -> Span | None:
    """The span of `path` that contains `line` (and has `symbol`, when given)."""
    if line is None:
        return None
    return next((s for s in spans_by_file.get(path, []) if s.start_line <= line <= s.end_line and (symbol is None or s.symbol == symbol)), None)


def overlapping(spans: Iterable[Span], start: int | None, end: int | None) -> list[Span]:
    """Spans that share at least one line with [start, end] (end defaults to start)."""
    if start is None:
        return []
    end = start if end is None else end
    return [s for s in spans if start <= s.end_line and end >= s.start_line]


# ---------------------------------------------------------------- KT links and span state
class SpanState(NamedTuple):
    state: SpanStateName
    kt_seconds: float
    links: list[KTLink]


def counted(links: Iterable[KTLink]) -> list[KTLink]:
    """The links that count: confidence >= MIN_CONFIDENCE."""
    return [l for l in links if l.confidence >= MIN_CONFIDENCE]


def links_by_file(links: Iterable[KTLink]) -> dict[str, list[KTLink]]:
    """Counted links grouped by file path, in the order given."""
    out: dict[str, list[KTLink]] = defaultdict(list)
    for l in counted(links):
        out[l.file_path].append(l)
    return dict(out)


def link_matches(link: KTLink, span: Span) -> bool:
    """A link explains a span when it names the span's symbol, overlaps its lines, or covers the whole file (no symbol
    and no lines). Confidence is not checked here; span_state and links_by_file do that."""
    if span.path and link.file_path != span.path:
        return False
    if link.symbol is not None and link.symbol == span.symbol:
        return True
    if link.start_line is not None:
        end = link.end_line if link.end_line is not None else link.start_line
        return link.start_line <= span.end_line and end >= span.start_line
    return link.symbol is None


def clip_seconds(link: KTLink) -> float:
    return max(0.0, link.end_s - link.start_s)


def longest_link(links: Iterable[KTLink], path: str, same_module: bool = False) -> KTLink | None:
    """The longest counted clip on `path` (the first of equal length). With same_module=True and no clip on the file,
    the longest clip on another file of the same module (module_of) instead."""
    pool = counted(links)
    on_file = [l for l in pool if l.file_path == path]
    if not on_file and same_module:
        on_file = [l for l in pool if module_of(l.file_path) == module_of(path)]
    return max(on_file, key=clip_seconds) if on_file else None


def span_state(links_for_file: Iterable[KTLink], span: Span) -> SpanState:
    """('recorded' | 'stale' | 'none', kt_seconds, matching links). Only counted links match. 'stale' means every
    matching link has code_changed_since; kt_seconds is the summed length of the matching clips."""
    matching = [l for l in links_for_file if l.confidence >= MIN_CONFIDENCE and link_matches(l, span)]
    if not matching:
        return SpanState("none", 0.0, [])
    state: SpanStateName = "stale" if all(l.code_changed_since for l in matching) else "recorded"
    return SpanState(state, sum(clip_seconds(l) for l in matching), matching)


def span_states(spans_by_file: dict[str, list[Span]], links: Iterable[KTLink]) -> dict[Span, SpanState]:
    by_file = links_by_file(links)
    return {s: span_state(by_file.get(path, []), s) for path, spans in spans_by_file.items() for s in spans}


def recorded_share(recorded: int | float, stale: int | float, total: int | float) -> float:
    """(recorded + 0.5 * stale) / total, unrounded; 0.0 when there are no lines."""
    return (recorded + STALE_CREDIT * stale) / total if total else 0.0


class LineTotals(NamedTuple):
    total: int
    recorded: int
    stale: int

    @property
    def share(self) -> float:
        return recorded_share(self.recorded, self.stale, self.total)


def line_totals(spans_by_file: dict[str, list[Span]], links: Iterable[KTLink], assume_recorded: Iterable[Span] = ()) -> LineTotals:
    """Lines of code, recorded lines and stale lines. Spans in `assume_recorded` count as recorded (the planner's
    'after this session' numbers)."""
    states = span_states(spans_by_file, links)
    extra = set(assume_recorded)
    total = recorded = stale = 0
    for span, st in states.items():
        total += span.lines
        if span in extra or st.state == "recorded":
            recorded += span.lines
        elif st.state == "stale":
            stale += span.lines
    return LineTotals(total, recorded, stale)


def pace_s_per_line(links: Iterable[KTLink]) -> tuple[float, bool]:
    """(seconds of KT per line of code, pace_is_default): sum(end_s - start_s) / sum(lines) over counted links that
    have a line range, rounded to 2 dp. With no such link: (12.0, True)."""
    ranged = [l for l in counted(links) if l.start_line is not None and l.end_line is not None]
    seconds = sum(clip_seconds(l) for l in ranged)
    lines = sum(max(1, l.end_line - l.start_line + 1) for l in ranged)
    if not ranged or seconds <= 0:
        return DEFAULT_PACE_S_PER_LINE, True
    return round(seconds / lines, 2), False


# ---------------------------------------------------------------- owners
@dataclass(frozen=True)
class Owner:
    login: str  # CODEOWNERS handle without '@': 'ravi-dev', 'org/platform', or an email
    user_id: str | None  # the person it resolves to in this organisation, if any
    name: str  # the person's name, else '@login' (teams, unknown logins) or the email
    team: bool  # an '@org/team' handle: shared knowledge, never one person
    rule: str  # the CODEOWNERS line that decided it, e.g. '/engine/ @ravi-dev'


def _handle(login: str) -> str:
    return login if "@" in login else f"@{login}"


def owners_index(store: Store, org_id: str, project_id: str) -> Callable[[str], list[Owner]]:
    """A function path -> [Owner] for this project's CODEOWNERS (last matching rule wins). All logins are resolved
    with ONE store.users_by_github call; '@org/team' handles and emails stay labels. An unowned path gives []."""
    rules = parse_codeowners(store.codeowners(project_id))
    logins = sorted({o for _, owners in rules for o in owners if "/" not in o and "@" not in o})
    users: dict[str, User] = {}
    for u in store.users_by_github(org_id, logins) if logins else []:
        users.setdefault((u.github_login or "").lower(), u)
    cache: dict[str, list[Owner]] = {}

    def owners(path: str) -> list[Owner]:
        if path not in cache:
            found = rule_for(rules, path)
            out: list[Owner] = []
            if found:
                pattern, handles = found
                handles = list(dict.fromkeys(handles))
                rule = " ".join([pattern, *(_handle(h) for h in handles)])
                for h in handles:
                    team = "/" in h and "@" not in h
                    u = None if team or "@" in h else users.get(h.lower())
                    out.append(Owner(login=h, user_id=u.id if u else None, name=u.name if u else _handle(h), team=team, rule=rule))
            cache[path] = out
        return cache[path]

    return owners


# ---------------------------------------------------------------- speakers
def normalize_name(name: str) -> str:
    """'Ravi (Senior)' -> 'ravi': lowercase, '(...)' dropped, whitespace collapsed."""
    return " ".join(re.sub(r"\([^)]*\)", " ", name or "").lower().split())


def first_name(name: str) -> str:
    """'Ravi (Senior)' -> 'Ravi'; 'Priya Sharma' -> 'Priya'."""
    parts = re.sub(r"\([^)]*\)", " ", name or "").split()
    return parts[0] if parts else (name or "").strip()


def dominant_speaker(utterances: Iterable[Utterance], start_s: float, end_s: float) -> str | None:
    """The speaker label with the most seconds of overlap with [start_s, end_s]; the earliest such speaker on a tie."""
    seconds: dict[str, float] = {}
    for u in utterances:
        overlap = min(u.end_s, end_s) - max(u.start_s, start_s)
        if overlap > 0:
            seconds[u.speaker] = seconds.get(u.speaker, 0.0) + overlap
    return max(seconds, key=seconds.get) if seconds else None  # max keeps the first of equal values


def member_for_speaker(members: Iterable[User], label: str | None) -> User | None:
    """The project member a transcript speaker label names: an exact normalized name, else the GitHub login, else a
    unique first token. Labels like 'Speaker 1' (prompts.py asks Gemini for those) stay unmapped: None."""
    norm = normalize_name(label or "")
    if not norm:
        return None
    members = list(members)
    exact = [m for m in members if normalize_name(m.name) == norm]
    if exact:
        return exact[0] if len(exact) == 1 else None
    by_login = [m for m in members if (m.github_login or "").lower() == norm]
    if len(by_login) == 1:
        return by_login[0]
    first = norm.split()[0]
    token = [m for m in members if normalize_name(m.name).split()[:1] == [first]]
    return token[0] if len(token) == 1 else None


class NotesCache:
    """store.meeting_notes(meeting_id), read at most once per request. Call it like the store method."""

    def __init__(self, store: Store):
        self._store = store
        self._notes: dict[str, MeetingResult | None] = {}

    def __call__(self, meeting_id: str) -> MeetingResult | None:
        if meeting_id not in self._notes:
            self._notes[meeting_id] = self._store.meeting_notes(meeting_id)
        return self._notes[meeting_id]

    def prime(self, meeting_ids: Iterable[str]) -> None:
        """Read the notes of all these meetings in one query (meeting_notes_many) instead of one query each later."""
        wanted = [m for m in dict.fromkeys(meeting_ids) if m not in self._notes]
        many = getattr(self._store, "meeting_notes_many", None)
        if wanted and many:
            found = many(wanted)
            self._notes.update({m: found.get(m) for m in wanted})


class Speakers:
    """Who was talking during a clip, mapped to a project member when the label allows it."""

    def __init__(self, store: Store, members: Iterable[User], notes: NotesCache | None = None):
        self.notes = notes or NotesCache(store)
        self.members = list(members)
        self._by_label: dict[str, User | None] = {}

    def label(self, meeting_id: str, start_s: float, end_s: float) -> str | None:
        n = self.notes(meeting_id)
        return dominant_speaker(n.utterances, start_s, end_s) if n else None

    def member(self, label: str | None) -> User | None:
        if label is None:
            return None
        if label not in self._by_label:
            self._by_label[label] = member_for_speaker(self.members, label)
        return self._by_label[label]

    def for_link(self, link: KTLink) -> tuple[str | None, User | None]:
        """(speaker label, member or None) for the clip of a KT link."""
        label = self.label(link.meeting_id, link.start_s, link.end_s)
        return label, self.member(label)

    def seconds_by_user(self, meeting_ids: Iterable[str]) -> dict[str, float]:
        """Seconds each member spoke across these meetings (only labels that map to a member)."""
        out: dict[str, float] = defaultdict(float)
        for mid in meeting_ids:
            n = self.notes(mid)
            for u in n.utterances if n else []:
                m = self.member(u.speaker)
                if m:
                    out[m.id] += max(0.0, u.end_s - u.start_s)
        return dict(out)


# ---------------------------------------------------------------- text helpers
def fmt_ts(seconds: float | None) -> str:
    """232 -> '3:52'; 3725 -> '1:02:05'."""
    s = int(seconds or 0)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def short_title(title: str) -> str:
    """'KT #1: How validation works' -> 'KT #1'. Titles without a short prefix are kept whole."""
    head = (title or "").split(":", 1)[0].strip()
    return head if ":" in (title or "") and 0 < len(head) <= 24 else (title or "").strip()


def fmt_day(d: date) -> str:
    """date(2026, 10, 24) -> '24 Oct'."""
    return f"{d.day} {d:%b}"


def task_query(title: str) -> str:
    """A task title without a leading 'First PR:', for searching the code."""
    return re.sub(r"^\s*first\s+pr\s*:\s*", "", title or "", flags=re.IGNORECASE).strip()


def under_prefixes(path: str, prefixes: Iterable[str]) -> bool:
    """Prefix match for task related_paths: path == rp or path starts with rp + '/'."""
    for rp in prefixes:
        rp = (rp or "").strip()
        rp = (rp[2:] if rp.startswith("./") else rp).strip("/")
        if rp and (path == rp or path.startswith(rp + "/")):
            return True
    return False


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH_TOKEN = re.compile(r"[\w./-]+\.\w+")


def _code_like(name: str) -> bool:
    return "_" in name or any(ch.isdigit() for ch in name) or any(ch.isupper() for ch in name[1:])


def names_in(text: str, symbols: Iterable[str]) -> list[str]:
    """The symbols a free text names, in order of first mention. Case-sensitive. Identifier-shaped names
    (check_privileged, loadPolicy, ValidationError) count anywhere; a plain word such as 'Policy' or 'run' counts only
    when the text marks it as code: `Policy` or Policy(...)."""
    wanted, out = set(symbols), []
    for m in _IDENT.finditer(text or ""):
        name = m.group(0)
        if name not in wanted or name in out:
            continue
        marked = text[m.start() - 1 : m.start()] == "`" or text[m.end() :].lstrip().startswith("(")
        if _code_like(name) or marked:
            out.append(name)
    return out


def symbol_table(spans_by_file: dict[str, list[Span]]) -> dict[str, list[Span]]:
    """Symbol name -> every span that defines it, for `mentions` (build it once per request, not once per text)."""
    defined: dict[str, list[Span]] = defaultdict(list)
    for s in all_spans(spans_by_file):
        defined[s.symbol].append(s)
    return defined


def mentions(text: str, code_paths: list[str], spans_by_file: dict[str, list[Span]],
             defined: dict[str, list[Span]] | None = None) -> list[tuple[str, Span | None]]:
    """What a free text (an action item, a meeting summary) points at: (path, None) for a code file it names, and
    (path, span) for a symbol it names. Paths are read from tokens like 'engine/policy.py' with kt_linker.match_path
    at >= PATH_CONFIDENCE against `code_paths`. A symbol defined in several files is kept only for files the text
    also names, or when one file defines it; never when more than MAX_DEFINING_FILES do. A file that gets a symbol
    target loses its file-level target. Only files in spans_by_file are returned."""
    named_paths: list[str] = []
    for tok in _PATH_TOKEN.findall(text or ""):
        hit = match_path(tok, code_paths)
        if hit and hit[1] >= PATH_CONFIDENCE and hit[0] in spans_by_file and hit[0] not in named_paths:
            named_paths.append(hit[0])
    defined = symbol_table(spans_by_file) if defined is None else defined
    symbol_targets: list[Span] = []
    for name in names_in(text, defined):
        spans = defined[name]
        files = {s.path for s in spans}
        if len(files) > MAX_DEFINING_FILES:
            continue
        in_named = [s for s in spans if s.path in named_paths]
        symbol_targets += in_named or (spans if len(files) == 1 else [])
    with_symbol = {s.path for s in symbol_targets}
    return [(p, None) for p in named_paths if p not in with_symbol] + [(s.path, s) for s in symbol_targets]


def search_span(index: Index, project_id: str, query: str, spans_by_file: dict[str, list[Span]], k: int = 3,
                min_similarity: float = MIN_SIMILARITY) -> tuple[Span, float] | None:
    """(span, similarity) of the best code search hit that has a symbol, similarity >= min_similarity and lies in a
    code file; None otherwise. Used to anchor a first-PR task (k=3) and an unanswered question (k=1)."""
    if not (query or "").strip():
        return None
    for h in index.search(project_id, query, k, ["code"]):
        c = h.chunk
        if c.symbol and h.similarity >= min_similarity:
            span = span_at(spans_by_file, c.ref.get("path") or "", c.ref.get("start_line"), c.symbol)
            if span:
                return span, h.similarity
    return None


# ---------------------------------------------------------------- the project's code, read once per version
class CodeView(NamedTuple):
    """What a page knows about the project's code: the chunks without content, their spans, every indexed code path,
    the fingerprint SafeSearch and the tour key on, and whether the index's chunk cap left files out."""

    chunks: list[Chunk]
    files: dict[str, list[Span]]
    paths: list[str]
    fingerprint: str
    truncated: bool


_views_guard = threading.Lock()
_views: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # index -> OrderedDict[(project, version), CodeView]


def code_view(index: Index, project_id: str) -> CodeView:
    """The project's code as of index.code_version: read in full (code_chunks without content, all_paths) only when the
    version changed, so a warm request costs one cheap aggregate query instead of every chunk. Kept per index, at most
    CODE_VIEW_CHUNKS chunks across projects. Treat the result as read-only: it is shared."""
    version = index.code_version(project_id)
    key = (project_id, version, ports.CODE_CHUNK_CAP)
    with _views_guard:
        views = _views.get(index)
        if views is None:
            views = _views[index] = OrderedDict()
        hit = views.get(key)
        if hit is not None:
            views.move_to_end(key)
            return hit
    chunks = index.code_chunks(project_id, content=False)
    view = CodeView(chunks, code_files(index, project_id, chunks=chunks), index.all_paths(project_id), code_fingerprint(chunks),
                    bool(getattr(chunks, "truncated", False)))
    if index.code_version(project_id) != version:  # re-indexed while we read: answer from what was read, keep nothing
        return view
    with _views_guard:
        for old in [k for k in views if k[0] == project_id]:
            del views[old]  # a project's older code is never asked for again
        views[key] = view
        while len(views) > 1 and sum(len(v.chunks) for v in views.values()) > CODE_VIEW_CHUNKS:
            views.popitem(last=False)
    return view


def clear_code_views() -> None:
    with _views_guard:
        _views.clear()


# ---------------------------------------------------------------- code search that degrades
def code_fingerprint(chunks: Iterable[Chunk]) -> str:
    """What the project's code looks like, read from index.code_chunks(content=False): chunk ids, symbols, line ranges
    and commit labels, never content, so reads that skip the bodies give the same value. A chunk id is sha1(project,
    path, start, end), and a re-index stamps every chunk it writes with the commit it read, so a change to the code
    changes this even when a push re-indexes a file at the commit already recorded. SafeSearch forgets a project's
    remembered searches when it changes."""
    h = hashlib.blake2b(digest_size=16)
    for c in chunks:
        h.update(f"{c.id}\0{c.symbol or ''}\0{c.ref.get('start_line')}\0{c.ref.get('end_line')}\0{c.ref.get('commit') or ''}\n".encode())
    return h.hexdigest()


class _SearchMemo:
    """The code searches every request on one index shares, plus that index's circuit breaker. `code` holds the
    fingerprint each project's searches were made under (least recently used project first), `hits` the remembered
    results (least recently used first, chunk content dropped: callers read symbols, refs and similarities), `inflight`
    the searches running now, so a second request for one waits for it instead of searching again. Only touched under
    _search_guard."""

    def __init__(self) -> None:
        self.code: OrderedDict[str, str] = OrderedDict()
        self.hits: OrderedDict[tuple, list[Hit]] = OrderedDict()  # (project, query, k, types) -> hits
        self.inflight: dict[tuple, Future] = {}
        self.failures = 0  # consecutive failed or timed-out searches
        self.open_until = 0.0  # monotonic time until which live searches are skipped

    def forget(self, project_id: str) -> None:
        for key in [k for k in self.hits if k[0] == project_id]:
            del self.hits[key]

    def failed(self, now: float) -> None:
        self.failures += 1
        if self.failures >= BREAKER_FAILURES:
            self.open_until = now + BREAKER_OPEN_S

    def succeeded(self) -> None:
        self.failures, self.open_until = 0, 0.0


_search_guard = threading.Lock()
# index -> _SearchMemo. Weakly keyed by the index, so a container that goes away takes its searches with it.
_search_memos: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_search_pool: ThreadPoolExecutor | None = None
_search_running = 0  # batches submitted and not finished, across indexes
_last_traceback = 0.0


def clear_search_memo() -> None:
    """Forget every remembered code search and breaker state (tests, or after swapping the embedder)."""
    with _search_guard:
        _search_memos.clear()


def _pool() -> ThreadPoolExecutor:
    global _search_pool
    with _search_guard:
        if _search_pool is None:
            _search_pool = ThreadPoolExecutor(SEARCH_WORKERS, thread_name_prefix="code-search")
        return _search_pool


def _slim(hits: list[Hit]) -> list[Hit]:
    """Hits without their chunk's content: callers read symbols, refs and similarities, and a remembered 120-line chunk
    per answer would make the memo tens of MiB."""
    return [h.model_copy(update={"chunk": h.chunk.model_copy(update={"content": ""})}) if isinstance(h, Hit) and h.chunk.content else h
            for h in hits]


def _run_batch(index: Index, memo: _SearchMemo, fp: str, project_id: str, shared: bool, keys: list[tuple],
               futures: dict[tuple, Future], source_types: list[str] | None) -> None:
    """One batch of live searches, on a search thread: one embedding call (search_many) for all of them. Settles every
    future, files the answers under the code they were searched for, and moves the breaker."""
    global _search_running, _last_traceback
    try:
        queries = [(key[1], key[2]) for key in keys]
        many = getattr(index, "search_many", None)
        results = many(project_id, queries, source_types) if many and len(keys) > 1 else \
            [index.search(project_id, q, k, source_types) for q, k in queries]
        results = [_slim(r) for r in results]
    except Exception as e:  # noqa: BLE001 - an outage is reported by every request it costs (SafeSearch.report)
        now = time.monotonic()
        with _search_guard:
            memo.failed(now)
            for key in keys:
                memo.inflight.pop(key, None)
            _search_running -= 1
            loud = now - _last_traceback >= TRACEBACK_EVERY_S
            if loud:
                _last_traceback = now
        if loud:
            log.warning("code search failed for project %s (traceback at most once a minute)", project_id, exc_info=True)
        for f in futures.values():
            f.set_exception(e)
        return
    with _search_guard:
        memo.succeeded()
        for key, hits in zip(keys, results):
            memo.inflight.pop(key, None)
            if shared and memo.code.get(project_id) == fp:  # another request may have seen newer code since: keep only current answers
                memo.hits[key] = hits
                memo.hits.move_to_end(key)
        while len(memo.hits) > SEARCH_MEMO_MAX:
            memo.hits.popitem(last=False)
        _search_running -= 1
    for key, hits in zip(keys, results):
        futures[key].set_result(hits)


class SafeSearch:
    """The index as a feature's code searches see it. Make one per request, with the fingerprint of the code the
    request read (code_fingerprint), and pass it wherever an Index goes: `search` is remembered and made safe, every
    other method goes straight to the index.

    - Remembered per index and project until the project's code fingerprint changes (a re-index or a push): search
      results depend on nothing else. At most SEARCH_MEMO_MAX answers per index, least recently used dropped first.
    - Batched: `prefetch` starts every search a request will make as one batch, one embedding call (search_many).
    - Shared: a search that another request is already running is waited for, not repeated.
    - Bounded in time: live searches run on search threads, and the request waits for them at most `budget_s`
      (SEARCH_BUDGET_S by default) after this object is made, however long the embedder takes (Vertex's own timeout and
      retries keep the thread, never the request). A remembered answer is still given after that.
    - Degrading: the first search that fails or times out turns live searches off for the rest of the request, and
      BREAKER_FAILURES in a row turn them off for every request on this index for BREAKER_OPEN_S (a circuit breaker),
      so an outage costs a request or two, not every request.

    A skipped or failed search returns no hits, so the caller loses only that signal; `skipped` counts them, `partial`
    says the response is missing some, and `report` logs it. Treat the returned lists as read-only: they are shared."""

    def __init__(self, index: Index, project_id: str, fingerprint: str, budget_s: float | None = None):
        self._index = index
        self._project_id = project_id
        self._fp = fingerprint
        self._started = time.monotonic()
        self._deadline = self._started + (SEARCH_BUDGET_S if budget_s is None else budget_s)
        self._seen: dict[tuple, list[Hit]] = {}  # this request's answers, whatever the shared memo keeps or drops
        self._futures: dict[tuple, Future] = {}  # searches this request started or joined (prefetch)
        self._failed = False
        self.skipped = 0
        self.searched = 0  # live answers this request used (remembered answers not counted)
        self.reasons: set[str] = set()  # why searches were skipped: 'budget', 'failure', 'breaker', 'busy'
        with _search_guard:
            memo = _search_memos.get(index)
            if memo is None:
                memo = _search_memos[index] = _SearchMemo()
            if memo.code.get(project_id) != fingerprint:
                memo.forget(project_id)
                memo.code[project_id] = fingerprint
            memo.code.move_to_end(project_id)
            while len(memo.code) > SEARCH_MEMO_MAX:
                memo.forget(memo.code.popitem(last=False)[0])
        self._memo = memo

    @property
    def partial(self) -> bool:
        return self.skipped > 0

    def __getattr__(self, name: str):
        return getattr(self._index, name)

    def _skip(self, reason: str) -> list[Hit]:
        self.skipped += 1
        self.reasons.add(reason)
        return []

    def _remembered(self, key: tuple) -> list[Hit] | None:
        if key[0] != self._project_id:  # the fingerprint describes this project's code only
            return None
        with _search_guard:
            memo = self._memo
            found = memo.hits.get(key) if memo.code.get(key[0]) == self._fp else None
            if found is not None:
                memo.hits.move_to_end(key)
            return found

    def _start(self, keys: list[tuple], source_types: list[str] | None) -> tuple[dict[tuple, Future], str | None]:
        """Futures for these keys: running searches joined, the rest started as one batch per project. The second value
        says why nothing new could start ('failure', 'budget', 'breaker', 'busy'), else None."""
        global _search_running
        out: dict[tuple, Future] = {}
        missing: list[tuple] = []
        with _search_guard:
            memo = self._memo
            for key in keys:
                running = memo.inflight.get(key) if key[0] == self._project_id and memo.code.get(key[0]) == self._fp else None
                if running is not None:
                    out[key] = running
                elif key not in missing:
                    missing.append(key)
            if not missing:
                return out, None
            now = time.monotonic()
            why = ("failure" if self._failed else "budget" if now >= self._deadline else "breaker" if now < memo.open_until
                   else "busy" if _search_running >= SEARCH_QUEUE_MAX else None)
            if why:
                return out, why
            batches: dict[str, list[tuple]] = defaultdict(list)
            for key in missing:
                batches[key[0]].append(key)
            started = []
            for project_id, batch in batches.items():
                shared = project_id == self._project_id
                futures = {key: Future() for key in batch}
                if shared:
                    memo.inflight.update(futures)
                _search_running += 1
                started.append((project_id, shared, batch, futures))
                out.update(futures)
        pool = _pool()
        for project_id, shared, batch, futures in started:
            ctx = contextvars.copy_context()  # the tenant (Postgres row-level security) travels with the search
            pool.submit(ctx.run, _run_batch, self._index, memo, self._fp, project_id, shared, batch, futures, source_types)
        return out, None

    def prefetch(self, queries: Iterable[tuple[str, int]], source_types: list[str] | None = None) -> None:
        """Start, as one batch, the live searches for these (query, k) pairs that are neither remembered nor running.
        Does not wait: `search` collects each answer."""
        types = tuple(source_types or ())
        keys = [(self._project_id, q, k, types) for q, k in dict.fromkeys(queries) if (q or "").strip()]
        keys = [key for key in keys if key not in self._seen and key not in self._futures and self._remembered(key) is None]
        if keys:
            self._futures.update(self._start(keys, source_types)[0])

    def search(self, project_id: str, query: str, k: int = 8, source_types: list[str] | None = None) -> list[Hit]:
        key = (project_id, query, k, tuple(source_types or ()))
        if key in self._seen:
            return self._seen[key]
        future = self._futures.get(key)
        found = self._remembered(key) if future is None or not future.done() else None
        if found is not None:
            self._seen[key] = found
            return found
        why = None
        if future is None:
            futures, why = self._start([key], source_types)
            future = futures.get(key)
        if future is None:
            return self._skip(why or "budget")
        wait = self._deadline - time.monotonic()
        if wait <= 0 and not future.done():
            return self._skip("budget")
        try:
            hits = future.result(timeout=max(0.0, wait))
        except FutureTimeout:
            self._failed = True  # slow: stop waiting for search in this request; the search thread finishes on its own
            with _search_guard:
                self._memo.failed(time.monotonic())
            return self._skip("budget")
        except Exception:  # noqa: BLE001 - logged by the search thread (rate-limited) and by report()
            self._failed = True
            return self._skip("failure")
        self.searched += 1
        self._seen[key] = hits
        return hits

    def report(self, page: str) -> None:
        """One structured log line when this request's response is partial, so the rate of partial pages can be measured
        and alerted on (the request id is added by the API's log formatter)."""
        if not self.partial:
            return
        log.warning("code search incomplete: %s for project %s is partial", page, self._project_id, extra={"extra_fields": {
            "event": "knowledge.partial", "page": page, "project_id": self._project_id, "searched": self.searched,
            "skipped": self.skipped, "reason": ",".join(sorted(self.reasons)),
            "elapsed_ms": round((time.monotonic() - self._started) * 1000)}})


# ---------------------------------------------------------------- reads shared by the features
def ready_meetings(store: Store, project_id: str) -> list[Meeting]:
    return [m for m in store.list_meetings(project_id) if m.status == "ready"]


def open_tasks(store: Store, project_id: str, onboardings: list[Onboarding] | None = None) -> list[tuple[TaskRow, Onboarding]]:
    """Tasks of the project's ACTIVE onboardings that are not done or rejected (proposed ones included), with their
    onboarding, in the store's order (due date, title). Pass `onboardings` (store.onboardings(pid)) to save a read."""
    active = {o.id: o for o in (store.onboardings(project_id) if onboardings is None else onboardings)}
    return [(t, active[t.onboarding_id]) for t in store.tasks(project_id=project_id)
            if t.onboarding_id in active and t.status not in CLOSED_TASK_STATUSES]


def codeowner_logins(text: str) -> list[str]:
    """The person handles of a CODEOWNERS text (no teams, no emails), sorted: what owners_index looks up."""
    return sorted({o for _, owners in parse_codeowners(text) for o in owners if "/" not in o and "@" not in o})


def _version(text: str | None) -> str:
    return hashlib.blake2b((text or "").encode(), digest_size=8).hexdigest()


class Reads:
    """The Store as one request of the insight views sees it. Everything those pages read about the project is fetched
    up front, inside store.reading() (Postgres: one connection, one read-only transaction, one snapshot): the
    repository, CODEOWNERS text and the users it names, KT links, members, managers, meetings, active onboardings
    (sorted by id: Postgres reads them without ORDER BY), tasks, and with the flags the new hires' chats (one query for
    all of them), the ready meetings' notes (one query) and the project's open expert requests (one query). A page costs
    the same handful of queries whatever the size of the team.

    Pass it wherever a Store goes: those Store calls are answered from what was read, get_user from every user read,
    anything else (or another project) goes straight to the store. `digest` names everything read, so a result built
    only from these reads (and the code) can be cached under it."""

    def __init__(self, store: Store, project_id: str, org_id: str, *, notes: bool = True, chats: bool = True, requests: bool = False):
        self._store, self.project_id, self.org_id = store, project_id, org_id
        reading = getattr(store, "reading", None)
        with reading() if reading else nullcontext():
            self.repos = store.repos_for(project_id)
            self.codeowners_text = store.codeowners(project_id)
            self.links = store.kt_links(project_id)
            self._members = store.members(project_id)  # type: ignore[attr-defined]
            self._managers = store.managers(project_id)
            self.meetings = store.list_meetings(project_id)  # type: ignore[attr-defined]
            self._onboardings = sorted(store.onboardings(project_id), key=lambda o: o.id)
            self._tasks = store.tasks(project_id=project_id)
            self._logins = codeowner_logins(self.codeowners_text)
            self._owner_users = store.users_by_github(org_id, self._logins) if self._logins else []
            hires = list(dict.fromkeys(o.new_hire_id for o in self._onboardings))
            self._chats = (store.chat_histories(project_id, hires, CHAT_ROWS) if hires else {}) if chats else None  # type: ignore[attr-defined]
            ready = [m.id for m in self.meetings if m.status == "ready"]
            self._ready = set(ready)
            self._notes = (store.meeting_notes_many(ready) if ready else {}) if notes else None
            self._requests = store.open_expert_requests(project_id) if requests else None  # type: ignore[attr-defined]
        self._users: dict[str, User | None] = {u.id: u for u in [*self._owner_users, *self._members]}
        self._more_notes: dict[str, MeetingResult | None] = {}

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    def _mine(self, project_id) -> bool:
        return str(project_id) == str(self.project_id)

    # the Store calls answered from the reads
    def repos_for(self, project_id):
        return self.repos if self._mine(project_id) else self._store.repos_for(project_id)

    def codeowners(self, project_id):
        return self.codeowners_text if self._mine(project_id) else self._store.codeowners(project_id)

    def kt_links(self, project_id, path=None, chunk_ids=None):
        if self._mine(project_id) and path is None and chunk_ids is None:
            return self.links
        return self._store.kt_links(project_id, path, chunk_ids)

    def members(self, project_id):
        return self._members if self._mine(project_id) else self._store.members(project_id)  # type: ignore[attr-defined]

    def managers(self, project_id):
        return self._managers if self._mine(project_id) else self._store.managers(project_id)

    def list_meetings(self, project_id):
        return self.meetings if self._mine(project_id) else self._store.list_meetings(project_id)  # type: ignore[attr-defined]

    def onboardings(self, project_id, active_only=True):
        return self._onboardings if self._mine(project_id) and active_only else self._store.onboardings(project_id, active_only)

    def tasks(self, onboarding_id=None, project_id=None):
        if onboarding_id is None and project_id is not None and self._mine(project_id):
            return self._tasks
        return self._store.tasks(onboarding_id=onboarding_id, project_id=project_id)

    def users_by_github(self, org_id, logins):
        if str(org_id) == str(self.org_id) and sorted(set(logins)) == self._logins:
            return self._owner_users
        return self._store.users_by_github(org_id, logins)

    def get_user(self, user_id):
        if user_id not in self._users:
            self._users[user_id] = self._store.get_user(user_id)
        return self._users[user_id]

    def chat_history(self, project_id, user_id, limit=50):
        if self._chats is not None and self._mine(project_id) and user_id in self._chats and limit <= CHAT_ROWS:
            rows = self._chats[user_id]
            return rows[-limit:] if limit else []
        return self._store.chat_history(project_id, user_id, limit)  # type: ignore[attr-defined]

    def meeting_notes(self, meeting_id):
        if self._notes is not None and (meeting_id in self._notes or meeting_id in self._ready):
            return self._notes.get(meeting_id)
        if meeting_id not in self._more_notes:
            self._more_notes[meeting_id] = self._store.meeting_notes(meeting_id)
        return self._more_notes[meeting_id]

    def expert_requests_for(self, expert_id):
        if self._requests is not None:  # this project's open requests (callers keep only this project's anyway)
            return [r for r in self._requests if str(r.get("expert_id")) == str(expert_id)]
        return self._store.expert_requests_for(expert_id)  # type: ignore[attr-defined]

    def digest(self) -> str:
        """A name for everything read that a page's result can depend on. Notes are named by their meeting's version
        (save_meeting_result writes the notes and the summary together), chats by the fields demand reads."""
        h = hashlib.blake2b(digest_size=16)

        def put(*parts) -> None:
            h.update(repr(parts).encode())
            h.update(b"\x00")

        put("links", [(l.meeting_id, l.meeting_title, l.file_path, l.symbol, l.start_line, l.end_line, l.start_s, l.end_s,
                       l.confidence, l.code_changed_since) for l in self.links])
        put("meetings", [(m.id, m.title, m.status, m.kt_key, m.started_at, _version(m.summary)) for m in self.meetings])
        put("onboardings", [(o.id, o.new_hire_id, o.manager_id, o.start_date, o.status) for o in self._onboardings])
        put("tasks", [(t.id, t.onboarding_id, t.title, t.kind, t.status, t.origin, t.due_date, tuple(t.related_paths),
                       t.github_issue_number) for t in self._tasks])
        for group, users in (("members", self._members), ("managers", self._managers), ("owners", self._owner_users)):
            put(group, [(u.id, u.name, u.role, u.github_login) for u in users])
        put("hires", [(o.new_hire_id, getattr(self.get_user(o.new_hire_id), "name", None)) for o in self._onboardings])
        put("codeowners", self.codeowners_text, self.repos and [(r.id, r.full_name, r.status, r.last_indexed_sha) for r in self.repos])
        for uid in sorted(self._chats or {}):
            put("chat", uid, [(_message_id(r), r.get("status"), r.get("question"), bool(r.get("kt_clips")), str(r.get("created_at")),
                               [((c or {}).get("source_type"), repr(((c or {}).get("ref") or {}))) for c in r.get("citations") or []])
                              for r in self._chats[uid]])
        return h.hexdigest()


# ---------------------------------------------------------------- demand
@dataclass
class Reason:
    """Why a file (symbol None) or a span needs explaining. `ref` points back at the source: {task_id},
    {meeting_id, at_s?}, {message_id} or {module}. `start_line` pins a symbol-level reason to one span when a file
    defines the same name twice; it is None for file-level reasons."""

    path: str
    symbol: str | None
    kind: ReasonKind
    text: str
    weight: int
    due: date | None = None
    pending_approval: bool = False
    ref: dict = field(default_factory=dict)
    start_line: int | None = None
    expert_text: str | None = None  # what an expert reads instead of `text`; None when `text` is fit for them as it is

    @property
    def file_level(self) -> bool:
        return self.symbol is None

    def on(self, span: Span) -> bool:
        """A symbol-level reason about exactly this span (file-level reasons are never 'on' a span)."""
        return (self.symbol is not None and self.path == span.path and self.symbol == span.symbol
                and (self.start_line is None or self.start_line == span.start_line))

    def applies_to(self, span: Span) -> bool:
        """This span, or any span of the file for a file-level reason (the planner's attachment rule)."""
        return self.on(span) or (self.symbol is None and self.path == span.path)

    def as_dict(self, expert: bool = False) -> dict:
        """JSON-ready apart from `due` (a date: FastAPI serializes it as 'YYYY-MM-DD'). `expert`: the copy an expert reads,
        with `expert_text` as the text and, for a task awaiting approval, no reference to it."""
        d = asdict(self)
        alt = d.pop("expert_text")
        if expert and alt is not None:
            d["text"] = alt
            if self.pending_approval:
                d["ref"] = {}
        return d


def question_answered(r: Reason) -> bool:
    """A question reason: answered from code without a clip (weight 1), or never answered and anchored by search."""
    return r.weight != UNANSWERED_QUESTION_WEIGHT


def _message_id(row: dict) -> str:
    return str(row.get("message_id") or row.get("id"))  # the id a question reason's ref carries


def _unanswered(row: dict) -> bool:
    """A chat row demand() anchors with a code search."""
    return row.get("status") == "not_enough_information" and bool((row.get("question") or "").strip())


def _asked_at(v) -> float:
    """created_at as a timestamp: a datetime (both adapters), an ISO string, else 0 (sorted oldest)."""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return 0.0
    return v.timestamp() if isinstance(v, datetime) else 0.0


def _newest_first(row: dict) -> tuple[float, str]:
    """Sort key, used with reverse=True: created_at, then message id."""
    return _asked_at(row.get("created_at")), _message_id(row)


def newest_unanswered(histories: Iterable[list[dict]], cap: int) -> set[str]:
    """Message ids of the `cap` newest unanswered questions across every hire's chat rows (created_at, then message
    id, both descending), so one hire with a long backlog cannot crowd out the others' questions."""
    pending = {_message_id(r): r for rows in histories for r in rows if _unanswered(r)}
    newest = sorted(pending.values(), key=_newest_first, reverse=True)
    return {_message_id(r) for r in newest[: max(0, cap)]}


def demand(store: Store, index: Index, project_id: str, spans_by_file: dict[str, list[Span]], links: list[KTLink], *,
           notes: NotesCache | None = None, code_paths: list[str] | None = None,
           max_question_searches: int = MAX_QUESTION_SEARCHES) -> list[Reason]:
    """Every demand signal on the project's code files, in this order:

    a) open tasks of active onboardings (proposed ones with pending_approval=True): related_paths prefix-match files
       (file-level); a 'code' task without paths is anchored by searching its title (symbol-level). first_pr 3, task 2.
    b) action items of ready meetings that name a code file or symbol (see `mentions`). Weight 2.
    c) named_not_shown: symbols named in a ready meeting's summary or decisions that no counted link explains. Weight 1.
    d) questions from new hires with an active onboarding: answered from code without a KT clip (weight 1, once: on the
       cited span whose symbol the question names, else the first cited span, else the first cited file), or unanswered
       and anchored by a code search with similarity >= 0.2 (weight 2). At most `max_question_searches` unanswered
       questions are searched: the newest across ALL active hires (created_at, then message id), whatever order the
       store returns the onboardings in (newest_unanswered). Questions are listed in that same order, newest first
       across hires: neither adapter defines an order for onboardings.
    e) gap: every file of a module coverage.gaps() reports. Weight 1.
    f) stale: spans whose every explaining clip predates a code change. Weight 1.

    Only files in spans_by_file get reasons. Pass `notes` to share the per-request meeting-notes cache and
    `code_paths` (index.all_paths) if you already read it. Pass a SafeSearch as `index` so a search outage costs the
    searched signals (a) and (d), not the page; its searches for (a) and (d) are started together, as one batch.
    Pass a Reads as `store` so the onboardings' users, chats and notes cost no query each.
    """
    notes = notes or NotesCache(store)
    code_paths = index.all_paths(project_id) if code_paths is None else code_paths
    states = span_states(spans_by_file, links)
    defined = symbol_table(spans_by_file)
    onboardings = store.onboardings(project_id)
    hire_names: dict[str, str] = {}
    for ob in onboardings:
        u = store.get_user(ob.new_hire_id)
        hire_names[ob.new_hire_id] = first_name(u.name) if u else "A new hire"
    tasks = open_tasks(store, project_id, onboardings)
    chats = [(ob, store.chat_history(project_id, ob.new_hire_id, CHAT_ROWS)) for ob in onboardings]  # type: ignore[attr-defined]
    searchable = newest_unanswered((rows for _, rows in chats), max_question_searches)
    prefetch = getattr(index, "prefetch", None)
    if prefetch:  # every search (a) and (d) will make, as one batch: one embedding call instead of one each
        prefetch([(task_query(t.title), 3) for t, _ in tasks if not t.related_paths and t.kind == "code"]
                 + [((r.get("question") or "").strip(), 1) for _, rows in chats for r in rows if _unanswered(r) and _message_id(r) in searchable],
                 ["code"])

    out: list[Reason] = []
    seen: set[tuple] = set()

    def add(r: Reason) -> None:
        key = (r.path, r.symbol, r.start_line, r.kind, tuple(sorted(r.ref.items())))
        if r.path in spans_by_file and key not in seen:
            seen.add(key)
            out.append(r)

    def on_span(span: Span, kind: str, text: str, weight: int, ref: dict, due: date | None = None, pending: bool = False,
                expert_text: str | None = None) -> Reason:
        return Reason(span.path, span.symbol, kind, text, weight, due, pending, ref, span.start_line, expert_text)  # type: ignore[arg-type]

    # a) tasks
    for t, ob in tasks:
        who = hire_names.get(ob.new_hire_id, "A new hire")
        pending = t.status == "proposed"
        kind = "first_pr" if t.origin == "first_pr" else "task"
        weight = REASON_WEIGHTS[kind]
        if kind == "first_pr":
            text = f"First PR for {who}: {task_query(t.title)}" + (f" (issue #{t.github_issue_number})" if t.github_issue_number else "")
        else:
            text = (f"{who}'s task {t.title}" + (f", due {fmt_day(t.due_date)}" if t.due_date else "")
                    + (" (awaiting approval)" if pending else ""))
        # An expert reads no name, and nothing of a task awaiting approval (the managers' alone until it is approved).
        if pending:
            anon = PLANNED_WORK_TEXT
        elif kind == "first_pr":
            anon = f"A new hire's first PR: {task_query(t.title)}" + (f" (issue #{t.github_issue_number})" if t.github_issue_number else "")
        else:
            anon = f"A new hire's task {t.title}" + (f", due {fmt_day(t.due_date)}" if t.due_date else "")
        ref = {"task_id": t.id}
        if t.related_paths:
            for path in spans_by_file:
                if under_prefixes(path, t.related_paths):
                    add(Reason(path, None, kind, text, weight, t.due_date, pending, ref, expert_text=anon))  # type: ignore[arg-type]
        elif t.kind == "code":
            hit = search_span(index, project_id, task_query(t.title), spans_by_file, k=3)
            if hit:
                span, sim = hit
                match = f", {round(sim * 100)}% match"
                add(on_span(span, kind, text + match, weight, ref, t.due_date, pending, anon if pending else anon + match))

    # b) action items and c) named but never shown
    for m in ready_meetings(store, project_id):
        n = notes(m.id)
        if not n:
            continue
        label = short_title(m.title)
        for ai in n.action_items:
            when = f" at {fmt_ts(ai.at_s)}" if ai.at_s is not None else ""
            text = f"Action item from {label}{when}: {ai.text}"
            ref = {"meeting_id": m.id, **({"at_s": ai.at_s} if ai.at_s is not None else {})}
            for path, span in mentions(ai.text, code_paths, spans_by_file, defined):
                if span:
                    add(on_span(span, "action_item", text, REASON_WEIGHTS["action_item"], ref))
                else:
                    add(Reason(path, None, "action_item", text, REASON_WEIGHTS["action_item"], ref=ref))
        summary = "\n".join([n.summary or "", *n.decisions])
        for _, span in mentions(summary, code_paths, spans_by_file, defined):
            if span is None or states[span].state != "none":
                continue
            said = next((u for u in n.utterances if re.search(rf"\b{re.escape(span.symbol)}\b", u.text)), None)
            ref = {"meeting_id": m.id, **({"at_s": said.start_s} if said else {})}
            add(on_span(span, "named_not_shown", f"Named in the {label} summary but never shown on screen", REASON_WEIGHTS["named_not_shown"], ref))

    # d) questions from new hires, newest first across all of them: who asked and the words stay out of the reason
    asked = sorted(((ob, row) for ob, rows in chats for row in rows), key=lambda pair: _newest_first(pair[1]), reverse=True)
    for _ob, row in asked:
        question = (row.get("question") or "").strip()
        ref = {"message_id": _message_id(row)}
        if row.get("status") == "answered":
            if row.get("kt_clips"):
                continue
            # One question is one signal: an answer that cites two functions must not count the question twice.
            cited_files: list[str] = []
            cited: list[Span] = []  # in citation order
            for c in row.get("citations") or []:
                cref = (c or {}).get("ref") or {}
                path = cref.get("path")
                if (c or {}).get("source_type") != "code" or path not in spans_by_file:
                    continue
                cited_files.append(path)
                cited += [s for s in overlapping(spans_by_file[path], cref.get("start_line"), cref.get("end_line")) if s not in cited]
            if not cited_files:
                continue
            text = QUESTION_ANSWERED_TEXT
            named = names_in(question, {s.symbol for s in cited})
            target = next((s for s in cited if s.symbol == named[0]), None) if named else (cited[0] if cited else None)
            if target:
                add(on_span(target, "question", text, REASON_WEIGHTS["question"], ref))
            else:
                add(Reason(cited_files[0], None, "question", text, REASON_WEIGHTS["question"], ref=ref))
        elif _unanswered(row) and ref["message_id"] in searchable:
            hits = index.search(project_id, question, 1, ["code"])
            if not hits or hits[0].similarity < MIN_SIMILARITY:
                continue
            c = hits[0].chunk
            path = c.ref.get("path") or ""
            text = QUESTION_UNANSWERED_TEXT
            span = span_at(spans_by_file, path, c.ref.get("start_line"), c.symbol) if c.symbol else None
            if span:
                add(on_span(span, "question", text, UNANSWERED_QUESTION_WEIGHT, ref))
            else:
                add(Reason(path, None, "question", text, UNANSWERED_QUESTION_WEIGHT, ref=ref))

    # e) modules no KT call has explained
    gap_modules = set(gaps(coverage_map(code_paths, links)))
    for path in spans_by_file:
        module = module_of(path)
        if module in gap_modules:
            add(Reason(path, None, "gap", f"Module {module} has never been explained in a KT session", REASON_WEIGHTS["gap"], ref={"module": module}))

    # f) explained, but the code changed since
    for span, st in states.items():
        if st.state == "stale":
            link = max(st.links, key=clip_seconds)
            where = short_title(link.meeting_title) or "a KT call"
            add(on_span(span, "stale", f"Explained in {where} at {fmt_ts(link.start_s)}, but the code changed since", REASON_WEIGHTS["stale"],
                        {"meeting_id": link.meeting_id, "at_s": link.start_s}))
    return out


def reasons_by_file(reasons: Iterable[Reason]) -> dict[str, list[Reason]]:
    out: dict[str, list[Reason]] = defaultdict(list)
    for r in reasons:
        out[r.path].append(r)
    return dict(out)
