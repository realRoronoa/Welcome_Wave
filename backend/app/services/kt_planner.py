"""KT planner: the next KT sessions, written from what the team needs explained and who can explain it.

Supply is every function no counted KT link explains yet (state 'none') or explains only with clips older than the code
('stale'). Demand is knowledge.demand(): open tasks, action items, symbols a summary named but never showed, new-hire
questions answered from code alone, never-explained modules and stale clips. A span with at least one reason becomes an
agenda item; items are grouped by presenter into sessions, timed at the team's own measured pace, and split once a
session would pass 30 minutes.

Deterministic and LLM-free: the same store gives the same plan and keys. A session's key hashes its presenter and its
agenda in (path, line) order, so it does not depend on the display order (due dates, which a search outage can change).
A booking stores that key on the call it creates (meetings.kt_session_key) and the plan marks a session booked by key,
so a call someone else gives the same title books nothing; the database allows one scheduled, live or processing call
per key, so two simultaneous bookings make one call without a lock. The agenda of a booked call is preserved in the
audit detail and the presenter's notification; once the call is recorded, the Meeting agent's KT links explain the
items and they drop out of the next plan on their own.

Cost per request: the project's code comes from knowledge.code_view (re-read only when the index changes) and every
Store read from one knowledge.Reads (a handful of queries in one transaction). The plan is a function of those reads,
the code and the code searches, so it is cached under their digest: a reload or a booking right after a page load
does no planning work at all. Building it is linear in the code: coverage gaps are worked out once, and each session's
gaps_closed comes from the modules of its files. A response carries at most MAX_SESSIONS sessions of the viewer's
scope (sessions_total counts them all; totals always describe the whole team plan), and an expert's response builds
only their own sessions.

The code searches demand spends on unanswered questions and path-less code tasks go through knowledge.SafeSearch,
shared with knowledge risk and the code tour: remembered until the code changes, batched into one embedding call, and
at most QUESTION_SEARCHES questions (the newest across all hires) per plan. When search is down or slow the plan is
still written, without those anchors, says `partial: true`, and is not cached.
"""
from __future__ import annotations

import hashlib
import math
import threading
import weakref
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.agents.schemas import KTLink
from app.ports import User
from app.services import knowledge as K
from app.services.chunking import module_of
from app.services.coverage import coverage_map, gaps

SESSION_CAP_MIN = 30.0  # a session that would run past this is split into parts
MIN_ITEM_MIN = 0.2  # a one-line class still takes a moment to put on screen
TITLE_MAX = 200  # meetings.title is validated to 200 characters (POST /meetings)
NOTIFY_MAX = 480
BACKLOG_NAMES = 10
MAX_SESSIONS = 50  # sessions in one response (the first of the viewer's scope); sessions_total counts the rest
PLAN_CACHE_ITEMS = 100_000  # agenda items kept in cached plans per index, across projects
PLAN_CACHE_PLANS = 8
PRESENTER_ROLES = frozenset({"expert", "manager", "admin"})  # the same filter qa._pick_expert uses
# A booked call stays booked until its recording is ready (its KT links then explain the items), fails or is cancelled.
BOOKED_STATUSES = frozenset({"scheduled", "live", "processing"})
QUESTION_SEARCHES = 20  # unanswered questions anchored per plan (newest first across hires); knowledge.demand allows 50

PresenterSource = Literal["codeowners", "speaker", "manager", "none"]
_SOURCE_RANK: dict[str, int] = {"codeowners": 0, "speaker": 1, "manager": 2, "none": 3}


class PlanChanged(Exception):
    """The session key is not in the recomputed plan: something was recorded, booked or reassigned since the page loaded."""


class AlreadyBooked(Exception):
    """A scheduled, live or processing call already holds this session's key (another request may just have booked it)."""


class NotYourSession(Exception):
    """An expert tried to book a session someone else presents."""


class NoPresenter(Exception):
    """Nobody can present this session: no CODEOWNERS member, no mapped speaker and no manager on the project."""


@dataclass
class _Item:
    span: K.Span
    state: str  # 'none' | 'stale'
    reasons: list[K.Reason]
    presenter: User | None
    source: PresenterSource


@dataclass
class _Session:
    key: str
    title: str
    presenter: User | None
    source: PresenterSource
    est_minutes: float
    deadline: date | None
    part: int
    parts: int
    items: list[_Item]
    impact: dict
    booked: dict | None


@dataclass
class _Plan:
    """The whole team's plan, before any scope filter. Cached and shared: never change it after it is built."""

    indexed: bool
    pace: float
    pace_is_default: bool
    totals: dict
    sessions: list[_Session]
    rerecord: list[dict]
    backlog: dict
    partial: bool = False  # some code searches failed or ran out of time: tasks and questions without a path may be missing
    index_truncated: bool = False  # the index's chunk cap left files out of the plan
    presenters: list[dict] = field(default_factory=list)  # each presenter's share of the team plan
    by_key: dict[str, _Session] = field(default_factory=dict)


# ---------------------------------------------------------------- small pure helpers
def ceil_half(minutes: float) -> float:
    """Up to the next half minute: 4.86 -> 5.0, 6.02 -> 6.5, 5.0 -> 5.0 (float noise never adds half a minute)."""
    return math.ceil(round(minutes * 2, 6)) / 2


def item_minutes(lines: int, pace: float) -> float:
    return max(MIN_ITEM_MIN, round(lines * pace / 60, 1))


def session_key(presenter_id: str | None, spans: list[K.Span]) -> str:
    """12 hex digits that name a session by its presenter and its items, stable across reloads. The items are taken in
    (path, start_line) order, so the key is the same whatever order the page shows them in."""
    ordered = sorted(spans, key=lambda s: (s.path, s.start_line, s.symbol))
    raw = (presenter_id or "") + "|" + ",".join(s.key for s in ordered)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def session_title(presenter_name: str | None, paths: list[str], part: int, split: bool) -> str:
    """'KT with Meera: webhooks/server.py', plus ' (part 2)' when split. Long path lists are cut, never the part suffix,
    so every part keeps a distinct title."""
    who = K.first_name(presenter_name or "") or "the team"
    suffix = f" (part {part})" if split else ""
    base = f"KT with {who}: {', '.join(paths)}"
    room = TITLE_MAX - len(suffix)
    if len(base) > room:
        base = base[: room - 1].rstrip(", ") + "…"
    return base + suffix


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _person(u: User | None) -> dict | None:
    return {"user_id": u.id, "name": u.name, "login": u.github_login} if u else None


def _share_after(base: K.LineTotals, items: list[_Item]) -> float:
    """recorded_share with these items explained: K.line_totals(..., assume_recorded=spans).share without re-walking
    every span and link once per session (items are unrecorded spans by construction)."""
    gained = sum(i.span.lines for i in items)
    unstaled = sum(i.span.lines for i in items if i.state == "stale")
    return K.recorded_share(base.recorded + gained, base.stale - unstaled, base.total)


def _closed(gaps_before: list[str], items: list[_Item]) -> list[str]:
    """The gaps a recording of these items would close: coverage_map counts a module covered once any of its files has
    a counted link, so recording an item covers its file's module. One set lookup per gap instead of a coverage_map per
    session (the same answer: gaps_before lists uncovered product modules, and every item is a file of one)."""
    modules = {module_of(i.span.path) for i in items}
    return [g for g in gaps_before if g in modules]


# ---------------------------------------------------------------- presenters
class _Presenters:
    """Who presents a file, and who re-records a stale clip. Eligible people are project members with role expert,
    manager or admin; a new hire who did most of the talking in a clip is never asked to present."""

    def __init__(self, store, org_id: str, project_id: str, links: list[KTLink], notes: K.NotesCache):
        members = store.members(project_id)
        self._members = {m.id: m for m in members}
        self._owners = K.owners_index(store, org_id, project_id)
        self._speakers = K.Speakers(store, members, notes)
        self._links = links
        managers = store.managers(project_id)
        self._manager = managers[0] if managers else None
        self._by_path: dict[str, tuple[User | None, PresenterSource]] = {}

    def _eligible(self, u: User | None) -> User | None:  # a deactivated member is never asked to present
        return u if u is not None and u.role in PRESENTER_ROLES and u.status == "active" and u.id in self._members else None

    def owner(self, path: str) -> User | None:
        """The first CODEOWNERS owner of `path` who can present (teams and unknown logins never resolve to a person)."""
        for o in self._owners(path):
            u = self._eligible(self._members.get(o.user_id)) if o.user_id else None
            if u:
                return u
        return None

    def speaker(self, link: KTLink | None) -> User | None:
        return self._eligible(self._speakers.for_link(link)[1]) if link else None

    def for_path(self, path: str) -> tuple[User | None, PresenterSource]:
        if path not in self._by_path:
            u = self.owner(path)
            if u:
                found: tuple[User | None, PresenterSource] = (u, "codeowners")
            else:
                # The longest clip on the file, else on its module: policy.py was never shown, but whoever walked
                # through validate.py knows the engine better than a manager does.
                u = self.speaker(K.longest_link(self._links, path, same_module=True))
                found = (u, "speaker") if u else (self._manager, "manager") if self._manager else (None, "none")
            self._by_path[path] = found
        return self._by_path[path]


# ---------------------------------------------------------------- the plan
def _order_items(items: list[_Item]) -> list[_Item]:
    """Files by the earliest due date among their reasons (undated last), then path; spans by start_line."""
    by_file: dict[str, list[_Item]] = defaultdict(list)
    for i in items:
        by_file[i.span.path].append(i)

    def file_key(path: str):
        dues = [r.due for i in by_file[path] for r in i.reasons if r.due]
        return (min(dues) if dues else date.max, path)

    return [i for path in sorted(by_file, key=file_key) for i in sorted(by_file[path], key=lambda i: i.span.start_line)]


def _split(items: list[_Item], pace: float) -> list[list[_Item]]:
    """Consecutive parts of at most SESSION_CAP_MIN minutes (a single longer item gets a part of its own)."""
    cap, parts, cur, secs = SESSION_CAP_MIN * 60, [], [], 0.0
    for i in items:
        s = i.span.lines * pace
        if cur and secs + s > cap:
            parts.append(cur)
            cur, secs = [], 0.0
        cur.append(i)
        secs += s
    if cur:
        parts.append(cur)
    return parts


def _distinct(reasons) -> list[K.Reason]:
    """One copy of each reason object: a file-level reason is attached to every unrecorded span of its file."""
    seen, out = set(), []
    for r in reasons:
        if id(r) not in seen:
            seen.add(id(r))
            out.append(r)
    return out


def asked_text(topic: str, answered: int, unanswered: int) -> str:
    """'Asked 3 times by new hires about check_labels: 2 answered from code with no recording, 1 not answered'."""
    n = answered + unanswered
    times = {1: "once", 2: "twice"}.get(n, f"{n} times")
    how = ("answered from code, no recording" if not unanswered else "not answered" if not answered
           else f"{answered} answered from code with no recording, {unanswered} not answered")
    return f"Asked {times} by {'a new hire' if n == 1 else 'new hires'} about {topic}: {how}"


def _reason_dicts(reasons: list[K.Reason], expert: bool) -> list[dict]:
    """The reasons as the viewer reads them. New hires' questions about one span (or one file, for file-level reasons)
    become one de-identified count, for managers and experts alike: how many times new hires asked about it and how it
    went, never who asked or what they wrote (`asked`, `unanswered`; the weights add up). An expert also reads every
    other reason without a new hire's name, and nothing of a task awaiting approval (Reason.expert_text)."""
    out: list[dict] = []
    asked: dict[tuple, dict] = {}
    for r in reasons:
        if r.kind != "question":
            out.append(r.as_dict(expert))
            continue
        key = (r.path, r.symbol, r.start_line)
        group = asked.get(key)
        if group is None:
            group = asked[key] = {**r.as_dict(), "ref": {}, "weight": 0, "asked": 0, "unanswered": 0}
            out.append(group)  # where the first of them was, in demand order
        group["weight"] += r.weight
        group["asked"] += 1
        group["unanswered"] += not K.question_answered(r)
    for (path, symbol, _), group in asked.items():
        group["text"] = asked_text(symbol or path, group["asked"] - group["unanswered"], group["unanswered"])
    return out


def _item_dict(i: _Item, pace: float, expert: bool = False) -> dict:
    s = i.span
    return {"path": s.path, "symbol": s.symbol, "start_line": s.start_line, "end_line": s.end_line, "lines": s.lines,
            "est_minutes": item_minutes(s.lines, pace), "state": i.state, "presenter_source": i.source,
            "reasons": _reason_dicts(i.reasons, expert)}


def _session_dict(s: _Session, pace: float, expert: bool = False) -> dict:
    return {"key": s.key, "title": s.title, "presenter": _person(s.presenter), "presenter_source": s.source, "est_minutes": s.est_minutes,
            "deadline": s.deadline, "part": s.part, "parts": s.parts, "items": [_item_dict(i, pace, expert) for i in s.items],
            "impact": dict(s.impact), "booked": s.booked}


_plans_guard = threading.Lock()
_plans: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # index -> OrderedDict[(project, digest), _Plan]


def _cached_plan(index, key: tuple) -> _Plan | None:
    with _plans_guard:
        plans = _plans.get(index)
        hit = plans.get(key) if plans is not None else None
        if hit is not None:
            plans.move_to_end(key)
        return hit


def _remember_plan(index, key: tuple, plan: _Plan) -> None:
    with _plans_guard:
        plans = _plans.get(index)
        if plans is None:
            plans = _plans[index] = OrderedDict()
        for old in [k for k in plans if k[0] == key[0]]:
            del plans[old]  # a project's older inputs are not asked for again
        plans[key] = plan
        while len(plans) > 1 and (len(plans) > PLAN_CACHE_PLANS or sum(p.totals["items"] for p in plans.values()) > PLAN_CACHE_ITEMS):
            plans.popitem(last=False)


def clear_cache() -> None:
    with _plans_guard:
        _plans.clear()


def _compute(store, index, project_id: str, org_id: str) -> _Plan:
    code = K.code_view(index, project_id)
    reads = K.Reads(store, project_id, org_id)
    links = reads.links
    pace, pace_default = K.pace_s_per_line(links)
    if not code.files:
        return _Plan(False, pace, pace_default, _totals([], 0.0, 0.0, [], []), [], [], {"unrecorded_symbols": 0, "lines": 0, "symbols": []},
                     index_truncated=code.truncated)
    cache_key = (project_id, code.fingerprint, reads.digest())
    cached = _cached_plan(index, cache_key)
    if cached is not None:
        return cached

    files, code_paths = code.files, code.paths
    notes = K.NotesCache(reads)
    states = K.span_states(files, links)
    searches = K.SafeSearch(index, project_id, code.fingerprint)
    reasons = K.reasons_by_file(K.demand(reads, searches, project_id, files, links, notes=notes, code_paths=code_paths,
                                         max_question_searches=QUESTION_SEARCHES))
    searches.report("kt-plan")
    who = _Presenters(reads, org_id, project_id, links, notes)

    # Reason.applies_to, by lookup: a file's reasons in demand order, file-level ones apart from those naming a symbol
    file_wide: dict[str, list[tuple[int, K.Reason]]] = defaultdict(list)
    named: dict[tuple[str, str], list[tuple[int, K.Reason]]] = defaultdict(list)
    for path, rs in reasons.items():
        for n, r in enumerate(rs):
            (file_wide[path] if r.symbol is None else named[(path, r.symbol)]).append((n, r))

    items: list[_Item] = []
    idle: list[K.Span] = []
    total = recorded = stale = 0
    for span, st in states.items():  # files by path, spans by start_line
        total += span.lines
        recorded += span.lines if st.state == "recorded" else 0
        stale += span.lines if st.state == "stale" else 0
        if st.state == "recorded":
            continue
        mine = [(n, r) for n, r in named.get((span.path, span.symbol), ()) if r.on(span)]
        attached = [r for _, r in sorted(mine + file_wide.get(span.path, []), key=lambda nr: nr[0])] if mine else \
            [r for _, r in file_wide.get(span.path, [])]
        if attached:
            items.append(_Item(span, st.state, attached, *who.for_path(span.path)))
        else:
            idle.append(span)

    base = K.LineTotals(total, recorded, stale)  # what K.line_totals(files, links) gives, without walking the links again
    gaps_before = gaps(coverage_map(code_paths, links))
    explained_before = {l.file_path for l in K.counted(links)}
    booked: dict[str, dict] = {}
    for m in reads.meetings:  # newest first: the newest call booked for a session wins
        if m.kt_key and m.status in BOOKED_STATUSES:
            booked.setdefault(m.kt_key, {"meeting_id": m.id, "status": m.status, "created_by": m.created_by})

    groups: dict[str, list[_Item]] = defaultdict(list)
    for i in items:
        groups[i.presenter.id if i.presenter else ""].append(i)

    drafts = []
    for group in groups.values():
        parts = _split(_order_items(group), pace)
        presenter = group[0].presenter
        for n, part in enumerate(parts, start=1):
            spans = [i.span for i in part]
            paths = list(dict.fromkeys(s.path for s in spans))
            why = _distinct(r for i in part for r in i.reasons)
            dues = [r.due for r in why if r.due]
            deadline = min(dues) if dues else None
            key = session_key(presenter.id if presenter else None, spans)
            session = _Session(
                key=key, title=session_title(presenter.name if presenter else None, paths, n, len(parts) > 1), presenter=presenter,
                source=min((i.source for i in part), key=_SOURCE_RANK.__getitem__),
                est_minutes=ceil_half(sum(s.lines for s in spans) * pace / 60), deadline=deadline, part=n, parts=len(parts), items=part,
                impact={
                    "files_newly_covered": [p for p in paths if p not in explained_before],
                    "gaps_closed": _closed(gaps_before, part),
                    "tasks_unblocked": len({r.ref["task_id"] for r in why if "task_id" in r.ref}),
                    "questions_addressed": len({r.ref.get("message_id") for r in why if r.kind == "question"}),
                    "recorded_share_before": round(base.share, 2),
                    "recorded_share_after": round(_share_after(base, part), 2),
                },
                booked=booked.get(key))
            drafts.append(((deadline or date.max, -sum(r.weight for r in why), presenter.name if presenter else "~", n, session.title), session))
    sessions = [s for _, s in sorted(drafts, key=lambda d: d[0])]

    closed_by_all = set(_closed(gaps_before, items))
    gaps_after = [g for g in gaps_before if g not in closed_by_all]
    session_dicts = [{"est_minutes": s.est_minutes, "items": s.items} for s in sessions]
    totals = _totals(session_dicts, base.share, _share_after(base, items), gaps_before, gaps_after)
    presenters: dict[str, dict] = {}
    for s in sessions:
        if s.presenter:
            row = presenters.setdefault(s.presenter.id, {**_person(s.presenter), "sessions": 0, "est_minutes": 0.0})
            row["sessions"] += 1
            row["est_minutes"] += s.est_minutes
    plan = _Plan(True, pace, pace_default, totals, sessions, _rerecord(links, states, who),
                 {"unrecorded_symbols": len(idle), "lines": sum(s.lines for s in idle), "symbols": [s.symbol for s in idle[:BACKLOG_NAMES]]},
                 searches.partial, code.truncated, list(presenters.values()), {s.key: s for s in sessions})
    if not plan.partial:  # a partial plan is missing what the next request may find: never serve it again
        _remember_plan(index, cache_key, plan)
    return plan


def _totals(sessions: list[dict], before: float, after: float, gaps_before: list[str], gaps_after: list[str]) -> dict:
    return {"sessions": len(sessions), "est_minutes": sum(s["est_minutes"] for s in sessions), "items": sum(len(s["items"]) for s in sessions),
            "recorded_share_before": round(before, 2), "recorded_share_after": round(after, 2),
            "gaps_before": gaps_before, "gaps_after": gaps_after}


def _rerecord(links: list[KTLink], states: dict[K.Span, K.SpanState], who: _Presenters) -> list[dict]:
    """Clips that are the only explanation of code that changed since (a span still 'stale'), with who should
    re-record each: the owner, else whoever explained it the first time. A fresh clip of the same span, or the file
    leaving the index, takes the clip off the list, so the queue agrees with the session items."""
    def lkey(l: KTLink) -> tuple:
        return (l.meeting_id, l.file_path, l.symbol, l.start_s)

    stale = {lkey(l) for st in states.values() if st.state == "stale" for l in st.links}
    out, seen = [], set()
    for l in sorted(K.counted(links), key=lambda l: (l.file_path, l.start_line or 0, l.start_s)):
        key = lkey(l)
        if key not in stale or key in seen:
            continue
        seen.add(key)
        presenter = who.owner(l.file_path) or who.speaker(l)
        out.append({"meeting_id": l.meeting_id, "meeting_title": l.meeting_title, "file_path": l.file_path, "symbol": l.symbol,
                    "start_line": l.start_line, "end_line": l.end_line, "start_s": l.start_s, "end_s": l.end_s,
                    "presenter": _person(presenter)})
    return out


# ---------------------------------------------------------------- public API
def build_plan(store, index, project_id: str, org_id: str, viewer: User, presenter: str | None = None) -> dict:
    """GET /api/kt-plan. An expert (who is not a manager or admin) always sees their own queue (scope 'mine'), with no new
    hire named and no task awaiting approval described; others see the team plan, optionally filtered to one presenter
    (an empty filter is no filter). Everyone reads new hires' questions as de-identified counts (_reason_dicts);
    impact.questions_addressed counts them too. Pace, totals and backlog always describe the whole team plan, so the
    numbers agree whichever view asked.
    At most MAX_SESSIONS sessions are returned, the first of the view in plan order; sessions_total counts the view's
    sessions and `presenters` (team views only) each presenter's share of the team plan."""
    plan = _compute(store, index, project_id, org_id)
    mine = viewer.role == "expert"
    wanted = viewer.id if mine else (presenter or None)

    def keep(person: User | dict | None) -> bool:
        uid = person.get("user_id") if isinstance(person, dict) else person.id if person else None
        return wanted is None or uid == wanted

    view = [s for s in plan.sessions if keep(s.presenter)]
    shown = [_session_dict(s, plan.pace, expert=mine) for s in view[:MAX_SESSIONS]]  # only these are turned into JSON
    return {
        "indexed": plan.indexed,
        "pace_s_per_line": plan.pace,
        "pace_is_default": plan.pace_is_default,
        "partial": plan.partial,
        "index_truncated": plan.index_truncated,
        "scope": "mine" if mine else "team",
        "presenter_filter": wanted,
        "totals": plan.totals,
        "sessions": shown,
        "sessions_total": len(view),
        "presenters": [] if mine else [dict(p) for p in plan.presenters],
        "rerecord": [r for r in plan.rerecord if keep(r["presenter"])],
        "backlog": plan.backlog,
    }


def schedule(store, index, project_id: str, org_id: str, user: User, key: str, create=None) -> dict:
    """POST /api/kt-plan/schedule: book the session with this key as a KT session and send its presenter the agenda.

    The plan is recomputed for the whole team (or taken from the cache, when nothing it depends on changed since the page
    loaded), so a stale page cannot book an agenda that no longer exists (PlanChanged): an unknown key is refused before
    anything is written. No lock is taken and no connection is held while the plan is built: the session is created with
    the plan session's key, and the database keeps one scheduled, live or processing session per key, so of two simultaneous
    bookings one gets AlreadyBooked. Raises PlanChanged, NotYourSession, NoPresenter or AlreadyBooked; the API maps them
    to HTTP.

    `create(title, key, presenter) -> Meeting | None` makes the session: the API passes services/scheduling.book with the
    time, attendees and provider asked for (Google Calendar with a Meet link, or anywhere). Without it the session is
    unscheduled ('manual'): held whenever it suits, its recording uploaded afterwards."""
    plan = _compute(store, index, project_id, org_id)
    found = plan.by_key.get(key)
    if found is None:
        raise PlanChanged(key)
    presenter = found.presenter
    if user.role == "expert" and (presenter is None or presenter.id != user.id):
        raise NotYourSession(key)
    if presenter is None:
        raise NoPresenter(key)
    if found.booked:
        raise AlreadyBooked(found.booked["meeting_id"])
    m = create(found.title, key, presenter) if create else store.create_meeting(project_id, found.title, user.id, kt_key=key)
    if m is None:  # another request booked it between our read and our insert
        raise AlreadyBooked(None)
    session = _session_dict(found, plan.pace, expert=user.role == "expert")
    agenda = [f"{i['path']}::{i['symbol']}" for i in session["items"]]
    store.audit(org_id, user.id, "kt_plan.scheduled", m.id,
                {"key": key, "presenter_id": presenter.id, "items": agenda, "est_minutes": session["est_minutes"],
                 "provider": m.provider})
    notified = None
    if presenter.id != user.id:
        where = "The invitation is in your calendar; join from KT sessions." if m.provider == "google" else "It is in KT sessions."
        text = _clip(f"{user.name} booked a KT session for you to present: '{session['title']}', about {session['est_minutes']} min. "
                     f"Walk through {agenda_text(session['items'])}. {where}", NOTIFY_MAX)
        # the link names the call: cancelling it finds who was told, and retires this notice
        store.notify(presenter.id, "kt_request", text, f"/meetings?call={m.id}&project={project_id}")
        notified = presenter.id
    return {"meeting": m, "notified_user_id": notified,
            "session": {**session, "booked": {"meeting_id": m.id, "status": m.status, "created_by": m.created_by}}}


def planned_title(store, index, project_id: str, org_id: str, key: str) -> str | None:
    """The title of the plan session with this key (a Calendly booking made from the planner names it), else None."""
    try:
        found = _compute(store, index, project_id, org_id).by_key.get(key)
    except Exception:  # noqa: BLE001 - a title is a nicety: the booking stands without it
        return None
    return found.title if found else None


def agenda_text(items: list[dict]) -> str:
    """'handle_admission, Handler and do_POST in webhooks/server.py; check_labels in engine/validate.py': the agenda as a
    person would say it, file by file in agenda order (a method keeps its class: Handler.do_POST becomes do_POST)."""
    by_path: dict[str, list[str]] = {}
    for i in items:
        name = (i.get("symbol") or "").rsplit(".", 1)[-1] or "the top of the file"
        names = by_path.setdefault(i["path"], [])
        if name not in names:
            names.append(name)
    parts = []
    for path, names in by_path.items():
        listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        parts.append(f"{listed} in {path}")
    return "; ".join(parts)
