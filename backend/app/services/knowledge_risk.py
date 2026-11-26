"""Knowledge risk: which code only one person understands, who that person is, and what leaving would orphan.

A file is risky when little of it was explained on a recorded KT call, when few people hold it (CODEOWNERS), and when
new hires are about to work on it (open tasks, first PRs, action items, their questions). Each person who owns code
gets an exposure: the files that would be orphaned if they left today, and the minutes of KT that would hand them over
at the team's measured pace.

Read-only and deterministic: a pure function of the Store and Index, built on the shared core in
app/services/knowledge.py so the KT planner and the code tour report the same numbers.

Scoring, per file
    recorded_share = (recorded lines + 0.5 * stale lines) / lines            (stale: explained, but the code changed since)
    holder_factor  = 1.0 for no owner or one person, 0.6 for two handles or one team, 0.4 for three or more
    demand_factor  = 0.5 + 0.5 * min(1, demand_weight / 4)
    risk_score     = round(100 * (1 - recorded_share) * holder_factor * demand_factor)       (half up)
    bands          = critical >= 70, high >= 40, medium >= 15, low

Demand is what new hires are about to touch. Stale clips are NOT demand: staleness already costs the file half its
recorded credit, and counting it again in the demand factor would charge the same signal twice.

Cost and failure, per request
    The code comes from K.code_view (re-read only when the index changes) and every Store read from one K.Reads: a
    handful of queries in one transaction whatever the size of the team (the new hires' chats, the ready meetings'
    notes and the project's open expert requests are one query each).
    The response is bounded: at most MAX_FILES file rows (riskiest first; `files_total` and the summary count every
    file), MAX_SYMBOLS symbols per row (`symbols_total`, `unrecorded_total`) and MAX_ORPHANS orphaned paths per person
    (`orphaned_count`). Every number is computed over all of the code first.
    `index_truncated` (also `truncated`) says the index's chunk cap (ports.CODE_CHUNK_CAP) left whole files out.
    Code searches anchor path-less code tasks and unanswered questions; on Postgres each one is an embedding call. They
    go through K.SafeSearch, shared with the KT planner and the code tour: an answer is remembered across requests until
    the code changes, the first failed search turns the rest off, and live searches stop after K.SEARCH_BUDGET_S.
    Either way the response says `partial: true` and every other demand signal still counts, so a Vertex outage costs
    those two signals, not the page.
    K.demand spends its question cap (K.MAX_QUESTION_SEARCHES) across all active new hires, newest first, so one hire
    with many unanswered questions cannot crowd out the others.
    Question reasons carry no words and no name (knowledge.demand never writes them): a new hire's chat is theirs alone,
    and this page only counts them. Their message ids stay opaque: no route shows a chat message to anyone but its author.
"""
from __future__ import annotations

import math
from collections import defaultdict

from app.ports import User
from app.services import knowledge as K
from app.services.chunking import module_of
from app.services.codeowners import has_owners, parse_codeowners

HOLDER_FACTOR_SHARED = 0.6  # two owners, or one team handle
HOLDER_FACTOR_MANY = 0.4  # three or more owners
DEMAND_SATURATION = 4  # demand weight at which the demand factor reaches 1.0
ORPHAN_SHARE = 0.5  # a sole-owned file explained less than this is orphaned when its owner leaves
BANDS = ((70, "critical"), (40, "high"), (15, "medium"))
NOT_DEMAND = frozenset({"stale"})
MAX_FILES = 200  # file rows in one response, riskiest first
MAX_SYMBOLS = 40  # symbols (and unrecorded symbols) listed per file row
MAX_ORPHANS = 200  # orphaned paths listed per person
QUESTION_ANSWERED_TEXT = K.QUESTION_ANSWERED_TEXT  # a question reason's whole text: never the words or who asked
QUESTION_UNANSWERED_TEXT = K.QUESTION_UNANSWERED_TEXT


def holders(owners: list[K.Owner]) -> list[K.Owner]:
    """Distinct holders of a file: two handles that resolve to one person (@Ravi-Dev, @ravi-dev) are one holder."""
    seen: dict[str, K.Owner] = {}
    for o in owners:
        seen.setdefault(o.user_id or o.login.lower(), o)
    return list(seen.values())


def holder_factor(owners: list[K.Owner]) -> float:
    """How concentrated the knowledge is. A file no CODEOWNERS rule covers counts as one holder: nobody is accountable."""
    held = holders(owners)
    if len(held) >= 3:
        return HOLDER_FACTOR_MANY
    if len(held) == 2 or (len(held) == 1 and held[0].team):
        return HOLDER_FACTOR_SHARED
    return 1.0


def single_holder(owners: list[K.Owner]) -> bool:
    """One person (resolved or not) holds the file alone. A team handle is shared knowledge."""
    held = holders(owners)
    return len(held) == 1 and not held[0].team


def demand_factor(weight: int) -> float:
    return 0.5 + 0.5 * min(1.0, weight / DEMAND_SATURATION)


def _half_up(x: float) -> int:
    return int(math.floor(x + 0.5 + 1e-9))  # round(87.5) is 88 but round(0.5) is 0 in Python: scores round half up


def band(score: int) -> str:
    return next((name for floor, name in BANDS if score >= floor), "low")


def _empty(indexed: bool, pace: float, pace_is_default: bool, repository: dict | None, has_codeowners: bool, truncated: bool) -> dict:
    return {
        "indexed": indexed, "repository": repository, "has_codeowners": has_codeowners,
        "pace_s_per_line": pace, "pace_is_default": pace_is_default, "partial": False, "truncated": truncated,
        "index_truncated": truncated, "files_total": 0,
        "summary": {"code_files": 0, "code_lines": 0, "owned_lines": 0, "recorded_lines": 0, "stale_lines": 0, "recorded_line_share": 0.0,
                    "single_owner_files": 0, "single_owner_critical_areas": 0, "unowned_files": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "highest_exposure": None},
        "files": [], "people": [],
    }


def build_knowledge_risk(store, index, project_id: str, org_id: str) -> dict:
    """GET /api/knowledge-risk. See the module docstring for the scoring; the response shape is documented in the API.
    Pass the project's canonical id (Project.id from the store), never the raw query string."""
    code = K.code_view(index, project_id)
    store = K.Reads(store, project_id, org_id, requests=True)
    repos = store.repos
    repository = {"full_name": repos[0].full_name, "status": repos[0].status} if repos else None
    has_codeowners = has_owners(parse_codeowners(store.codeowners_text))  # owner-less patterns alone assign nobody
    links = store.links
    pace, pace_is_default = K.pace_s_per_line(links)

    truncated = code.truncated
    files = code.files
    if not files:
        return _empty(bool(code.chunks), pace, pace_is_default, repository, has_codeowners, truncated)

    owners_of = K.owners_index(store, org_id, project_id)  # an owner-less CODEOWNERS pattern leaves its paths unowned
    members = {u.id: u for u in store.members(project_id)}
    owner_users: dict[str, User] = {}
    for path in files:
        for o in owners_of(path):
            if o.user_id and o.user_id not in owner_users:
                u = store.get_user(o.user_id)  # CODEOWNERS resolves org-wide; users_by_github already returned this user
                if u:
                    owner_users[u.id] = u

    notes = K.NotesCache(store)
    speakers = K.Speakers(store, [*members.values(), *(u for uid, u in owner_users.items() if uid not in members)], notes)
    states = K.span_states(files, links)
    by_file_links = K.links_by_file(links)
    searches = K.SafeSearch(index, project_id, code.fingerprint)
    reasons = K.reasons_by_file(r for r in K.demand(store, searches, project_id, files, links, notes=notes, code_paths=code.paths)
                                if r.kind not in NOT_DEMAND)
    searches.report("knowledge-risk")
    meeting_label = {m.id: K.short_title(m.title) for m in store.list_meetings(project_id)}

    rows = []
    for path, spans in files.items():
        owners = owners_of(path)
        total = recorded = stale = 0
        symbols, unrecorded = [], []
        for s in spans:
            st = states[s]
            total += s.lines
            recorded += s.lines if st.state == "recorded" else 0
            stale += s.lines if st.state == "stale" else 0
            clip = max(st.links, key=K.clip_seconds) if st.links else None
            symbols.append({"symbol": s.symbol, "start_line": s.start_line, "end_line": s.end_line, "lines": s.lines, "state": st.state,
                            "kt_seconds": round(st.kt_seconds, 1),
                            "clip": {"meeting_id": clip.meeting_id, "meeting": meeting_label.get(clip.meeting_id) or K.short_title(clip.meeting_title),
                                     "at_s": clip.start_s} if clip else None})
            if st.state == "none":
                unrecorded.append({"symbol": s.symbol, "start_line": s.start_line, "end_line": s.end_line})
        share = K.recorded_share(recorded, stale, total)
        demand = reasons.get(path, [])
        weight = sum(r.weight for r in demand)
        held, wants = holder_factor(owners), demand_factor(weight)
        score = _half_up(100 * (1 - share) * held * wants)
        rows.append({
            "path": path, "module": module_of(path),
            "owners": [{"login": o.login, "user_id": o.user_id, "name": o.name, "team": o.team, "rule": o.rule} for o in owners],
            "voices": _voices(by_file_links.get(path, []), speakers),
            "lines_total": total, "lines_recorded": recorded, "lines_stale": stale, "recorded_share": round(share, 2),
            "symbols": symbols, "unrecorded_symbols": unrecorded,
            "demand": [_reason(r, meeting_label) for r in demand], "demand_weight": weight,
            "risk": band(score), "risk_score": score,
            "score_parts": {"unrecorded": round(1 - share, 4), "holder_factor": held, "demand_factor": round(wants, 4)},
            "single_holder": single_holder(owners),
        })
    rows.sort(key=lambda f: (-f["risk_score"], f["path"]))

    people = _people(store, project_id, rows, owner_users, speakers, pace)
    top = people[0] if people and (people[0]["exposure"]["risk_sum"] or people[0]["exposure"]["unrecorded_lines"]) else None
    totals = K.LineTotals(sum(f["lines_total"] for f in rows), sum(f["lines_recorded"] for f in rows), sum(f["lines_stale"] for f in rows))
    summary = {
        "code_files": len(rows), "code_lines": totals.total,
        "owned_lines": sum(f["lines_total"] for f in rows if f["owners"]),
        "recorded_lines": totals.recorded, "stale_lines": totals.stale, "recorded_line_share": round(totals.share, 2),
        "single_owner_files": sum(f["single_holder"] for f in rows),
        # code areas (folders) with a critical-risk file that one person alone holds: the count leadership reports show, no names
        "single_owner_critical_areas": len({f["module"] for f in rows if f["single_holder"] and f["risk"] == "critical"}),
        "unowned_files": sum(not f["owners"] for f in rows),
        **{name: sum(f["risk"] == name for f in rows) for name in ("critical", "high", "medium", "low")},
        "highest_exposure": {"user_id": top["user_id"], "name": top["name"], "recorded_share": top["recorded_share"],
                             "handover_minutes": top["exposure"]["handover_minutes"]} if top else None,
    }
    for p in people:  # every number above is over all of the code; only the lists sent back are cut
        p["exposure"]["orphaned_count"] = len(p["exposure"]["orphaned_files"])
        p["exposure"]["orphaned_files"] = p["exposure"]["orphaned_files"][:MAX_ORPHANS]
    for f in rows[:MAX_FILES]:
        f["symbols_total"], f["unrecorded_total"] = len(f["symbols"]), len(f["unrecorded_symbols"])
        f["symbols"], f["unrecorded_symbols"] = f["symbols"][:MAX_SYMBOLS], f["unrecorded_symbols"][:MAX_SYMBOLS]
    return {
        "indexed": True, "repository": repository, "has_codeowners": has_codeowners,
        "pace_s_per_line": pace, "pace_is_default": pace_is_default,
        "partial": searches.partial,  # some code searches failed or ran out of time: those demand signals are missing
        "truncated": truncated, "index_truncated": truncated,
        "summary": summary,
        "files": rows[:MAX_FILES],
        "files_total": len(rows),
        "people": people,
    }


def _voices(links: list, speakers: K.Speakers) -> list[dict]:
    """Who was talking while the file was on screen: the dominant speaker of each clip, by seconds. Two links that
    share one clip (two symbols shown at once) count its seconds once."""
    seconds: dict[tuple[str, str | None], float] = defaultdict(float)
    seen: set[tuple] = set()
    for link in links:
        window = (link.meeting_id, link.start_s, link.end_s)
        if window in seen:
            continue
        seen.add(window)
        label, user = speakers.for_link(link)
        if label is None:
            continue
        key = (user.name, user.id) if user else (label, None)
        seconds[key] += K.clip_seconds(link)
    return [{"speaker": name, "user_id": uid, "seconds": round(s, 1)}
            for (name, uid), s in sorted(seconds.items(), key=lambda kv: (-kv[1], kv[0][0]))]


def _reason(r: K.Reason, meeting_label: dict[str, str]) -> dict:
    d = r.as_dict()
    mid = r.ref.get("meeting_id")
    d["meeting"] = meeting_label.get(mid) if mid else None  # 'KT #1', so the UI can label the moment without parsing text
    if r.kind == "question":  # answered from code without a clip, or never answered (anchored by search, weighs more)
        d["answered"] = K.question_answered(r)  # the text says so too, and never the words or who asked (knowledge.demand)
    return d


def _people(store, project_id: str, rows: list[dict], owner_users: dict[str, User], speakers: K.Speakers, pace: float) -> list[dict]:
    """Every person who owns at least one code file, with what their departure would orphan. Teams and logins that
    resolve to nobody in the organisation stay on the file rows only."""
    owned: dict[str, list[dict]] = defaultdict(list)
    for f in rows:
        for uid in dict.fromkeys(o["user_id"] for o in f["owners"] if o["user_id"] in owner_users and not o["team"]):
            owned[uid].append(f)  # two handles of one person (@Ravi-Dev @ravi-dev) count the file once
    if not owned:
        return []
    spoken = speakers.seconds_by_user(m.id for m in K.ready_meetings(store, project_id))
    onboardings = store.onboardings(project_id)

    people = []
    for uid, mine in owned.items():
        u = owner_users[uid]
        sole = [f for f in mine if all(o["user_id"] == uid for o in f["owners"])]  # no other handle, not even a team
        orphaned = [f for f in sole if K.recorded_share(f["lines_recorded"], f["lines_stale"], f["lines_total"]) < ORPHAN_SHARE]  # unrounded
        lines = sum(f["lines_total"] for f in mine)
        recorded = sum(f["lines_recorded"] for f in mine)
        stale = sum(f["lines_stale"] for f in mine)
        unrecorded_lines = sum(s["end_line"] - s["start_line"] + 1 for f in sole for s in f["unrecorded_symbols"])
        open_requests = [r for r in store.expert_requests_for(uid) if str(r.get("project_id")) == str(project_id)]  # this project's only
        people.append({
            "user_id": uid, "name": u.name, "login": u.github_login, "role": u.role,
            "files_owned": len(mine), "sole_owner_files": len(sole), "lines_owned": lines, "lines_recorded": recorded,
            "recorded_share": round(K.recorded_share(recorded, stale, lines), 2),
            "kt_minutes_spoken": round(spoken.get(uid, 0.0) / 60, 1),
            "open_expert_requests": len(open_requests),
            "onboardings_managed": sum(o.manager_id == uid for o in onboardings),
            "exposure": {
                "orphaned_files": [f["path"] for f in orphaned],
                "unrecorded_lines": unrecorded_lines,
                "unrecorded_symbols": sum(len(f["unrecorded_symbols"]) for f in sole),
                "stale_lines": sum(f["lines_stale"] for f in sole),
                "handover_minutes": round(unrecorded_lines * pace / 60, 1),
                "risk_sum": sum(f["risk_score"] for f in orphaned),
            },
        })
    people.sort(key=lambda p: (-p["exposure"]["risk_sum"], -p["exposure"]["unrecorded_lines"], p["name"]))
    return people
