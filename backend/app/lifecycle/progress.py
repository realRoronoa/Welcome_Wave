"""Progress by area, and the milestones the product checks itself.

Areas are the project's code modules (the coverage map's modules, without root, docs and tests). Four kinds of evidence:
  steps      reading-path steps the hire recorded (PATCH .../areas/{area}) and done learn or meet tasks in the area
  watched    KT clips of the area the hire recorded watching, or KT calls about the area the hire spoke in
  questions  the hire's questions answered from the area's code (not marked wrong, not out of date)
  merged     done code tasks with a pull request in the area
Level: contributed with a merged change; working with two or more kinds of evidence; exploring with one; else not
started. There are no scores and no ranking between people, by design: the levels describe evidence, not performance.
The hire's own assessment (self_level) is shown next to it, never instead of it.

Auto milestones (templates.AUTO_MILESTONES) are marked done the first time their evidence is seen, with that evidence."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from app.lifecycle import templates as T
from app.lifecycle.context import HireCtx
from app.ports import AreaProgress
from app.services import knowledge as K
from app.services.chunking import module_of
from app.services.coverage import coverage_map

LEVELS = ("not_started", "exploring", "working", "contributed")
KINDS = ("steps", "watched", "questions", "merged")


def merged_tasks(hc: HireCtx) -> set[str]:
    """Tasks whose pull request was merged (pr.merged, or the demo's merge): an approved review alone is not a merge."""
    if "_merged" not in hc.__dict__:
        hc.__dict__["_merged"] = {m.subject_id for m in hc.ctx.lc.metrics(hc.program.org_id, ["pr.merged"])}
    return hc.__dict__["_merged"]


def project_areas(hc: HireCtx) -> list[str]:
    cov = coverage_map(hc.ctx.index.all_paths(hc.ob.project_id), hc.kt_links)
    return sorted(r.module for r in cov if r.module.split("/")[0] not in K.IGNORED_MODULES)


def _in(area: str, paths) -> bool:
    return any(module_of(p) == area or p.rstrip("/") == area or p.startswith(area + "/") for p in paths)


def level_for(evidence: dict) -> str:
    if evidence.get("merged"):
        return "contributed"
    kinds = sum(bool(evidence.get(k)) for k in ("steps", "watched", "questions"))
    return LEVELS[min(kinds, 2)]


def areas(hc: HireCtx) -> list[dict]:
    ctx = hc.ctx
    stored = {a.area: a for a in ctx.lc.area_progress(hc.ob.id)}
    links_by_area: dict[str, list] = defaultdict(list)
    for l in hc.kt_links:
        if l.confidence >= K.MIN_CONFIDENCE:
            links_by_area[module_of(l.file_path)].append(l)
    spoke = _meetings_spoken_in(hc)
    out = []
    for area in project_areas(hc):
        row = stored.get(area)
        ev = {k: [] for k in KINDS}
        ev["steps"] = list((row.evidence if row else {}).get("steps", []))
        ev["steps"] += [{"task_id": t.id, "title": t.title} for t in hc.tasks
                        if t.status == "done" and t.kind in {"learn", "meet"} and _in(area, t.related_paths)]
        ev["watched"] = list((row.evidence if row else {}).get("watched", []))
        ev["watched"] += [{"meeting_id": m,
                           "attended": True} for m in dict.fromkeys(l.meeting_id for l in links_by_area[area]) if m in spoke]
        for turn in hc.chats:
            paths = [c["ref"].get("path", "") for c in turn.get("citations", []) if c.get("source_type") == "code"]
            if turn.get("status") == "answered" and not turn.get("stale") and turn.get("feedback") != -1 and _in(area, paths):
                ev["questions"].append({"message_id": turn.get("message_id") or turn.get("id"),
                                        "question": (turn.get("question") or "")[:120]})
        merged = merged_tasks(hc)
        ev["merged"] = [{"task_id": t.id, "title": t.title, "pr_url": t.pr_url} for t in hc.tasks
                        if t.kind == "code" and t.status == "done" and t.pr_number and t.id in merged and _in(area, t.related_paths)]
        level = level_for(ev)
        kinds = [k for k in KINDS if ev[k]]
        if level == "contributed":
            why = [f"A merged change in {area}."]
        elif kinds:
            why = [f"{len(kinds)} kind{'s' if len(kinds) != 1 else ''} of evidence: {', '.join(kinds)}."]
        else:
            why = [f"No evidence in {area} yet."]
        out.append({"area": area, "level": level, "self_level": row.self_level if row else None, "evidence": ev, "why": why})
    return out


def _meetings_spoken_in(hc: HireCtx) -> set[str]:
    """KT calls the hire spoke in (their name matched to a speaker label of the transcript)."""
    hire = hc.hire
    if not hire:
        return set()
    speakers = K.Speakers(hc.ctx.store, [hire])
    meetings = {l.meeting_id for l in hc.kt_links}
    return {m for m in meetings if speakers.seconds_by_user([m]).get(hire.id)}


def record(hc: HireCtx, area: str, self_level: str | None, step: str | None, watched: dict | None) -> AreaProgress:
    lc = hc.ctx.lc
    old = next((a for a in lc.area_progress(hc.ob.id) if a.area == area), None)
    evidence = dict(old.evidence) if old else {}
    now = hc.ctx.clock.now()
    if step:
        evidence["steps"] = [*evidence.get("steps", []), {"title": step[:200], "at": now.isoformat()}][-50:]
    if watched:
        evidence["watched"] = [*evidence.get("watched", []), {**watched, "at": now.isoformat()}][-50:]
    if self_level is None and old:
        self_level = old.self_level
    return lc.save_area_progress(AreaProgress(onboarding_id=hc.ob.id, area=area, evidence=evidence, self_level=self_level, updated_at=now))


# ---------------------------------------------------------------- milestones the product checks itself
def _auto_evidence(hc: HireCtx, key: str, area_levels: list[str] | None) -> dict | None:
    """The evidence that completes an auto milestone, or None while it is missing."""
    if key == "preboarding_complete":
        acks = hc.acknowledgements
        return {"acknowledged": [a.doc_key for a in acks]} if acks and all(a.acknowledged_at for a in acks) else None
    if key == "environment_ready":
        last = hc.environment_checks[-1] if hc.environment_checks else None
        return {"check_ran_at": last.ran_at.isoformat()} if last and last.ok else None
    if key == "access_ready":
        items = hc.access
        return {"systems": [a.system for a in items]} if items and all(a.status != "requested" for a in items) else None
    if key == "first_pr_opened":
        t = next((t for t in hc.tasks if t.kind == "code" and t.pr_number), None)
        return {"task_id": t.id, "pr_url": t.pr_url} if t else None
    if key == "first_pr_merged":
        merged = merged_tasks(hc)
        t = next((t for t in hc.tasks if t.kind == "code" and t.pr_number and t.status == "done" and t.id in merged), None)
        return {"task_id": t.id, "pr_url": t.pr_url} if t else None
    if key.startswith("checkin_"):
        day = T.scale(int(key.split("_")[1]), hc.program.duration_days)
        roles = {c.author_role for c in hc.checkins if c.day == day}
        return {"day": day} if roles >= {"hire", "manager"} else None
    if key in {"area_working", "area_contributed"} and area_levels is not None:
        wanted = {"working", "contributed"} if key == "area_working" else {"contributed"}
        return {"levels": area_levels} if wanted & set(area_levels) else None
    return None


def evaluate_milestones(hc: HireCtx) -> list[str]:
    """Mark auto milestones done whose evidence now exists. Returns the keys it marked."""
    pending = [m for m in hc.milestones if m.status == "pending" and m.key in T.AUTO_MILESTONES]
    if not pending:
        return []
    needs_areas = any(m.key.startswith("area_") for m in pending)
    levels = [a["level"] for a in areas(hc)] if needs_areas else None
    done = []
    for m in pending:
        ev = _auto_evidence(hc, m.key, levels)
        if ev is not None:
            hc.ctx.lc.update_milestone(hc.ob.id, m.key, status="done", done_at=hc.ctx.clock.now(), evidence={"auto": True, **ev})
            done.append(m.key)
    if done:
        hc.forget("milestones")
    return done


def milestone_view(hc: HireCtx) -> list[dict]:
    """The timeline. An auto milestone whose evidence no longer holds is shown pending again, with what happened (the
    environment passed its check once, but its latest check fails: the open blocker and the milestone agree)."""
    today = hc.ctx.clock.today()
    last_env = hc.environment_checks[-1] if hc.environment_checks else None
    out = []
    for m in hc.milestones:
        due = hc.ob.start_date + timedelta(days=m.due_day)
        status, done_at, evidence = m.status, m.done_at, m.evidence
        if m.key == "environment_ready" and m.status == "done" and evidence.get("auto") and last_env and not last_env.ok:
            status, done_at = "pending", None
            evidence = {**evidence, "regressed": True, "passed_at": m.done_at.isoformat() if m.done_at else None,
                        "failing_since": last_env.ran_at.isoformat()}
        state = (status if status != "pending" else "overdue" if due < today else "due_soon" if (due - today).days <= 7 else "upcoming")
        out.append({"key": m.key, "title": m.title, "due_day": m.due_day, "due_date": due, "status": status, "state": state,
                    "done_at": done_at, "evidence": evidence, "auto": m.key in T.AUTO_MILESTONES})
    return out
