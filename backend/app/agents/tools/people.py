"""People and KT tools: the KT moments that explained code, CODEOWNERS, who knows a path (with their availability), and KT
coverage. Read-only; see __init__. The people these tools name are the only ones an agent may suggest a question goes to
(ToolCtx.people), and the capped delivery (app/lifecycle/routing.deliver) still decides when they are told."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.agents.core.runtime import REGISTRY, Source, ToolCtx, ToolResult, untrusted
from app.agents.core.schemas import Chunk

PERSON_ROLES = frozenset({"expert", "manager", "admin"})
MAX_MOMENTS = 5


def _ts(seconds: float) -> str:
    s = int(seconds or 0)
    return f"{s // 60:02d}:{s % 60:02d}"


def _eligible(ctx: ToolCtx, u) -> bool:
    """Someone a question may go to: an active expert, manager or admin on this project, not the asker."""
    store = ctx.deps.store
    return (u is not None and (ctx.user is None or u.id != ctx.user.id) and u.role in PERSON_ROLES and getattr(u, "status", "active") == "active"
            and store.is_member(ctx.project_id, u.id))


# ---------------------------------------------------------------- kt_moments
class MomentsArgs(BaseModel):
    path: str | None = Field(None, max_length=400, description="A file path: the KT moments that explained this file")
    symbol: str | None = Field(None, max_length=120, description="A function or class name: the KT moments that explained it")


@REGISTRY.tool("kt_moments", "Recorded KT sessions that explained a file or a function: who explained it, the session's title and time, "
               "whether the code changed since, and a redacted transcript snippet. Numbered sources [n] you can cite.", MomentsArgs,
               label="Checked the KT sessions")
def kt_moments(ctx: ToolCtx, args: MomentsArgs) -> ToolResult:
    from app.services.knowledge import Speakers

    store = ctx.deps.store
    if not args.path and not args.symbol:
        return ToolResult(text=untrusted({"error": "Give a path or a symbol."}), summary="Checked the KT sessions: nothing to look for", shared="Checked the KT sessions")
    links = store.kt_links(ctx.project_id, path=args.path.strip().lstrip("./") if args.path else None)
    if args.symbol:
        links = [lk for lk in links if lk.symbol == args.symbol.split(".")[-1].strip("()")]
    links = sorted((lk for lk in links if lk.confidence >= 0.5), key=lambda lk: (-lk.confidence, lk.start_s))[:MAX_MOMENTS]
    speakers = Speakers(store, store.members(ctx.project_id))
    rows, sources = [], []
    clips = ctx.notes.setdefault("kt_links", {})
    for lk in links:
        label, who = speakers.for_link(lk)
        notes = speakers.notes(lk.meeting_id)
        said = [u for u in (notes.utterances if notes else []) if u.end_s >= lk.start_s - 5 and u.start_s <= lk.end_s + 5][:12]
        lines = []
        for u in said:
            text = u.text
            if ctx.deps.redactor is not None:
                text, _ = ctx.deps.redactor.redact(text)
            lines.append(f"[{_ts(u.start_s)}] {u.speaker}: {text}")
        title = lk.meeting_title or "KT session"
        chunk = Chunk(id=f"kt-{lk.meeting_id}-{int(lk.start_s)}", project_id=ctx.project_id, source_type="meeting",
                      ref={"meeting_id": lk.meeting_id, "title": title, "start_s": lk.start_s, "end_s": lk.end_s},
                      content=f"Meeting: {title} ({_ts(lk.start_s)}-{_ts(lk.end_s)})\nExplains: {lk.file_path}"
                              + (f" ({lk.symbol})" if lk.symbol else "") + "\n" + "\n".join(lines))
        n = ctx.cite(chunk, lk.confidence)
        clips[n] = lk
        if who and _eligible(ctx, who):
            ctx.people.setdefault(who.id, f"explained {lk.file_path} in {title}")
        sources.append(ctx.evidence[n - 1].source)
        rows.append({"n": n, "call": title, "at": _ts(lk.start_s), "explains": lk.file_path, "symbol": lk.symbol,
                     "explained_by": {"user_id": who.id, "name": who.name} if who else (label or "unknown"),
                     "code_changed_since": lk.code_changed_since, "transcript": "\n".join(lines)})
    first = f"{rows[0]['call']} · {rows[0]['at']}" if rows else ""
    return ToolResult(text=untrusted({"moments": rows} if rows else {"moments": [], "note": "No recorded KT session explained this."}),
                      sources=sources, summary=f"Checking {first}" + (f" and {len(rows) - 1} more" if len(rows) > 1 else "") if rows
                      else "Checking the KT sessions: none explained it",
                      shared=(f"Checked {first}" + (f" and {len(rows) - 1} more" if len(rows) > 1 else "")) if rows else "Checked the KT sessions (none explained it)")


# ---------------------------------------------------------------- code_owners
class PathArgs(BaseModel):
    path: str = Field(min_length=1, max_length=400, description="A file or folder path in the repository")


def owners_of(ctx: ToolCtx, path: str) -> tuple[str | None, bool, list]:
    """(the CODEOWNERS pattern that owns `path`, whether it is the catch-all, the active members it names)."""
    from app.services.codeowners import CATCH_ALL, parse_codeowners, rule_for

    store = ctx.deps.store
    rule = rule_for(parse_codeowners(store.codeowners(ctx.project_id)), path)
    if not rule or not rule[1]:
        return None, False, []
    people = [u for u in store.users_by_github(ctx.org_id, rule[1]) if getattr(u, "status", "active") == "active"
              and store.is_member(ctx.project_id, u.id)]
    return rule[0], rule[0] in CATCH_ALL, people


@REGISTRY.tool("code_owners", "Who owns a path according to the repository's CODEOWNERS file, as project members (user ids).",
               PathArgs, label="Checked CODEOWNERS")
def code_owners(ctx: ToolCtx, args: PathArgs) -> ToolResult:
    path = args.path.strip().lstrip("./")
    pattern, catch_all, people = owners_of(ctx, path)
    for u in people:
        if _eligible(ctx, u):
            ctx.people.setdefault(u.id, f"CODEOWNERS gives {pattern} to them" + (" (the catch-all rule)" if catch_all else ""))
    rows = [{"user_id": u.id, "name": u.name, "role": u.role} for u in people]
    src = [Source(label=f"CODEOWNERS {pattern}", kind="code", ref={"path": "CODEOWNERS", "pattern": pattern})] if pattern else []
    names = ", ".join(u.name for u in people) or "nobody"
    return ToolResult(text=untrusted({"path": path, "rule": pattern, "catch_all": catch_all, "owners": rows}), sources=src,
                      summary=f"CODEOWNERS for {path}: {names}", shared=f"Checked CODEOWNERS ({names})" if pattern else "Checked CODEOWNERS (no rule)")


# ---------------------------------------------------------------- who_knows
@REGISTRY.tool("who_knows", "Who knows a path best, ranked: CODEOWNERS ownership and the KT sessions in which they explained it, with "
               "whether a question would reach them now (office hours, today's interruptions against their daily cap) or wait for "
               "their daily digest. Use it to decide who should answer when the sources are not enough.", PathArgs,
               label="Ranked who knows this code")
def who_knows(ctx: ToolCtx, args: PathArgs) -> ToolResult:
    from app.lifecycle import routing
    from app.services.knowledge import Speakers

    store = ctx.deps.store
    path = args.path.strip().lstrip("./")
    score: dict[str, float] = defaultdict(float)
    why: dict[str, list[str]] = defaultdict(list)
    users: dict[str, object] = {}
    pattern, catch_all, owners = owners_of(ctx, path)
    for u in owners:
        users[u.id] = u
        score[u.id] += 1.0 if catch_all else 3.0
        why[u.id].append(f"CODEOWNERS {pattern}" + (" (catch-all)" if catch_all else ""))
    links = [lk for lk in store.kt_links(ctx.project_id) if lk.confidence >= 0.5
             and (lk.file_path == path or lk.file_path.startswith(path.rstrip("/") + "/"))]
    speakers = Speakers(store, store.members(ctx.project_id))
    for lk in links:
        _, who = speakers.for_link(lk)
        if who is None:
            continue
        users[who.id] = who
        score[who.id] += 2.0 if not lk.code_changed_since else 1.0
        line = f"explained {lk.file_path} in {lk.meeting_title or 'a KT session'}"
        if line not in why[who.id]:
            why[who.id].append(line)
    rows, sources = [], []
    lctx = ctx.lc() if ctx.deps.lifecycle is not None else None
    now = lctx.clock.now() if lctx else datetime.now(timezone.utc)
    for uid in sorted(score, key=lambda i: (-score[i], users[i].name)):
        u = users[uid]
        if not _eligible(ctx, u):
            continue
        row = {"user_id": u.id, "name": u.name, "role": u.role, "score": score[uid], "why": why[uid][:4]}
        if lctx is not None:
            prefs = lctx.lc.expert_preferences(u.id)
            used = routing.interruptions_today(lctx, ctx.org_id, u.id)
            hours = routing.in_office_hours(prefs.office_hours, now)
            row.update(reaches_them_now=hours and used < prefs.max_interruptions_per_day, in_office_hours=hours,
                       interruptions_today=f"{used} of {prefs.max_interruptions_per_day}", digest=prefs.digest)
        ctx.people.setdefault(u.id, "; ".join(why[uid][:2]))
        rows.append(row)
        sources.append(Source(label=f"{u.name}: {'; '.join(why[uid][:2])}"[:200], kind="event", ref={"user_id": u.id}))
    top = ", ".join(r["name"] for r in rows[:3]) or "nobody found"
    return ToolResult(text=untrusted({"path": path, "people": rows[:6]} if rows else {"path": path, "people": [],
                                     "note": "Nobody owns or explained this path. The domain owner rules still apply when the asker asks a person."}),
                      sources=sources[:6], summary=f"Who knows {path}: {top}", shared=f"Ranked who knows the code ({top})")


# ---------------------------------------------------------------- kt_coverage
class AreaArgs(BaseModel):
    area: str | None = Field(None, max_length=200, description="A folder (module) to look at; empty: the whole project")


@REGISTRY.tool("kt_coverage", "How much of each module recorded KT sessions explained: files explained, minutes, and the modules no session "
               "explained (gaps).", AreaArgs, label="Read what KT sessions explained")
def kt_coverage(ctx: ToolCtx, args: AreaArgs) -> ToolResult:
    from app.services.coverage import coverage_map, gaps

    rows = coverage_map(ctx.deps.index.all_paths(ctx.project_id), ctx.deps.store.kt_links(ctx.project_id))
    if args.area:
        area = args.area.strip().strip("/")
        rows = [r for r in rows if r.module == area or r.module.startswith(area + "/") or area.startswith(r.module + "/")]
    out = [{"module": r.module, "files": r.files, "files_explained": r.files_explained, "kt_minutes": r.kt_minutes} for r in rows[:30]]
    missing = gaps(rows)
    return ToolResult(text=untrusted({"modules": out, "gaps": missing[:20]}),
                      sources=[Source(label=f"Explained in KT sessions: {r['module']} ({r['files_explained']} of {r['files']} files)", kind="metric",
                                      ref={"module": r["module"]}) for r in out[:6]],
                      summary=f"Read what KT sessions explained: {len(missing)} of {len(out)} modules not explained",
                      shared=f"Read what KT sessions explained ({len(missing)} modules not explained)")
