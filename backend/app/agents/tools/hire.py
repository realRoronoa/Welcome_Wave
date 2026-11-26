"""The tools of the thinking specialists (Track K): a hire's signals and program, the first-task candidates, the cohort's
outcomes for a template, a pull request's diff, the KT explanations a push changed, a flagged target, and what a departing
person holds. Read-only, like every tool (see __init__).

    hire_signals          Coach   de-identified: question topics counted (never a question's words), task activity, CI runs,
                                  the environment check, review findings, the open blocker alerts, milestones. Sensitive.
    program               all     the program as the hire sees it: plan status, milestones, visible tasks, areas, phases, and
                                  the template's plan (for a plan still being drafted)
    first_task_candidates Scout   the rules' candidates with their scored factors, and the excluded ones with the reason
    cohort_evidence       Planner the completed programs of a template's track, and survey questions answered by 5 or more people
    pr_diff               Reviewer the changed files of the pull request under review, and one file's numbered lines
    kt_changes            Keeper  the KT-linked code a push changed: the diff since the session, and what the session said
    feedback_target       Keeper  a flagged target's current text and the readers' notes
    person_footprint      Keeper  what a departing person owns alone, explained, and is planned to present

Each re-checks what the model asked for: an onboarding must be in this project, the run's own one when the run is about one
hire (ctx.notes["onboarding_id"]), and one the acting user may see (the hire, their mentor, a manager of the project, an admin,
or the system). The rest read only what our code put in ctx.notes for this run (the pull request it reviews, the push's
links, the flagged target, the person leaving): a model can name nothing else."""
from __future__ import annotations

import difflib
from collections import Counter, defaultdict
from datetime import timedelta

from pydantic import BaseModel, Field

from app.agents.core.runtime import REGISTRY, Source, ToolCtx, ToolDenied, ToolResult, untrusted

TOPIC_DAYS = 14  # the window of question topics the Coach sees
DIFF_LINES = 80  # lines of one link's diff the Keeper sees
PATCH_LINES = 220  # lines of one file's patch the Reviewer sees per call


class OnboardingArgs(BaseModel):
    onboarding_id: str = Field("", max_length=64, description="The onboarding's id; empty: this run's hire")


def hire_of(ctx: ToolCtx, onboarding_id: str = ""):
    """The onboarding (a lifecycle HireCtx) the model asked for, re-checked (see the module docstring). ToolDenied otherwise."""
    from app.lifecycle.context import HireCtx

    mine = ctx.notes.get("onboarding_id")
    wanted = (onboarding_id or "").strip() or mine
    if not wanted or (mine and wanted != mine):
        raise ToolDenied("not this run's onboarding")
    store, lc = ctx.deps.store, ctx.deps.lifecycle
    ob = store.get_onboarding(wanted)
    program = lc.get_program(wanted) if (ob and lc is not None) else None
    if ob is None or program is None or ob.project_id != ctx.project_id or str(program.org_id) != str(ctx.org_id):
        raise ToolDenied("no such onboarding in this project")
    u = ctx.user
    if u is not None and not (ob.new_hire_id == u.id or program.mentor_id == u.id or u.role == "admin"
                              or (u.role == "manager" and store.is_member(ob.project_id, u.id))):
        raise ToolDenied("the acting user may not see this onboarding")
    return HireCtx(ctx.lc(), ob, program)


def _first(name: str | None) -> str:
    return (name or "").split(" (")[0].split(" ")[0]


# ---------------------------------------------------------------- hire_signals (the Coach's; sensitive)
def signals(hc) -> dict:
    """The de-identified signals of one hire (the payload hire_signals shows). Every item carries a `signal` id the Coach cites."""
    from app.lifecycle import blockers as B
    from app.lifecycle import progress
    from app.lifecycle.clock import business_days
    from app.services.chunking import module_of

    ctx, lc = hc.ctx, hc.ctx.lc
    now = ctx.clock.now()
    since = now - timedelta(days=TOPIC_DAYS)
    topics: dict[str, Counter] = defaultdict(Counter)
    seen = set()
    for turn in hc.chats:
        at = B._at(turn.get("created_at"))
        if not at or at < since:
            continue
        code = [c for c in turn.get("citations", []) if c.get("source_type") == "code" and (c.get("ref") or {}).get("path")]
        mod = module_of(code[0]["ref"]["path"]) if code else "(no code source)"
        topics[mod]["asked"] += 1
        topics[mod]["answered_from_sources"] += turn.get("status") == "answered"
        seen.add(turn.get("message_id") or turn.get("id"))
    for m in lc.metrics(hc.program.org_id, ["route.asked"], since):
        if m.subject_id == hc.ob.new_hire_id and m.props.get("module") and m.props.get("message_id") not in seen:
            topics[m.props["module"]]["asked"] += 1
    by_topic = [{"signal": f"topic:{mod}", "module": mod, **dict(c)} for mod, c in sorted(topics.items(), key=lambda kv: -kv[1]["asked"])]

    visible = [t for t in hc.tasks if t.status not in {"proposed", "rejected"}]
    activity = lc.task_activity([t.id for t in visible])
    tasks = []
    for t in visible:
        last = activity.get(t.id)
        tasks.append({"signal": f"task:{t.id}", "title": t.title, "kind": t.kind, "status": t.status,
                      "business_days_since_activity": business_days(last, now) if last else None,
                      "due_in_days": (t.due_date - ctx.clock.today()).days if t.due_date else None, "pr": t.pr_number})

    reviews = {}
    for r in lc.reviews_for_tasks([t.id for t in visible]):
        reviews[r["task_id"]] = r  # oldest first: the last is the newest
    titles = {t.id: t.title for t in visible}
    review_rows, ci_rows = [], []
    repo = next(iter(ctx.store.repos_for(hc.ob.project_id)), None)
    for tid, r in reviews.items():
        at = B._at(r.get("created_at"))
        serious = [f for f in r.get("findings") or [] if f.get("severity") in {"blocker", "major"}]
        review_rows.append({"signal": f"review:{r['id']}", "task": titles.get(tid), "recommendation": r.get("recommendation"),
                            "serious_findings": len(serious), "decision": r.get("decision"), "days_open": (now - at).days if at else None})
        task = next((t for t in visible if t.id == tid), None)
        if repo and task and task.pr_number and task.status not in {"done", "rejected"}:
            try:
                runs = ctx.deps.repos.check_runs(repo.full_name, r["head_sha"], repo.installation_id)
            except Exception:  # noqa: BLE001 - CI unreachable: the other signals still count
                runs = []
            window = now - timedelta(hours=B.CI_WINDOW_H)
            failed = [x for x in runs if x.get("conclusion") in B.FAILED and (B._at(x.get("completed_at")) or now) >= window]
            ci_rows.append({"signal": f"ci:{tid}", "pr": task.pr_number, "runs": len(runs), "failed_last_48h": len(failed),
                            "failed_checks": sorted({str(x.get("name")) for x in failed})[:6]})

    env = None
    checks = hc.environment_checks
    if checks:
        last = checks[-1]
        streak = 0
        for c in reversed(checks):
            if c.ok:
                break
            streak += 1
        first_fail = checks[-streak].ran_at if streak else None
        env = {"signal": "env", "ok": last.ok, "failing": sorted({r["name"] for r in last.results if not r.get("ok")}),
               "failing_for_hours": int((now - first_fail).total_seconds() // 3600) if first_fail else 0}

    nudged = {m.props.get("blocker_id") for m in lc.metrics(hc.program.org_id, ["coach.nudge"])}
    alerts = []
    for a in lc.blockers(hc.ob.id, open_only=True):
        alerts.append({"alert_id": a.id, "signal": f"alert:{a.id}", "rule": a.rule, "text": B.describe(a, now), "stage": a.stage,
                       "told_so_far": a.notified_stage, "business_days_open": business_days(a.opened_at, now),
                       "latest_stage_allowed_now": B.stage_for(a.opened_at, now), "nudged": a.id in nudged})
    ms = progress.milestone_view(hc)
    overdue = [{"signal": f"milestone:{m['key']}", "title": m["title"], "due_day": m["due_day"]} for m in ms if m["state"] == "overdue"]
    soon = [{"signal": f"milestone:{m['key']}", "title": m["title"], "due_day": m["due_day"]} for m in ms if m["state"] == "due_soon"]
    mentor = ctx.person(hc.program.mentor_id or hc.ob.manager_id) or {}
    return {"hire": {"first_name": _first(hc.hire.name if hc.hire else None), "day": hc.day, "duration_days": hc.program.duration_days,
                     "focus_area": hc.program.focus_area, "who_can_help": _first(mentor.get("name")) or "your mentor"},
            "questions": {"window_days": TOPIC_DAYS, "by_topic": by_topic[:10], "note": "Counted, never quoted."},
            "tasks": tasks[:20], "ci": ci_rows[:10], "environment": env, "reviews": review_rows[:10], "blocker_alerts": alerts,
            "milestones": {"overdue": overdue, "due_within_7_days": soon}}


def signal_ids(payload: dict) -> set[str]:
    ids = set()

    def walk(v):
        if isinstance(v, dict):
            if isinstance(v.get("signal"), str):
                ids.add(v["signal"])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(payload)
    return ids


@REGISTRY.tool("hire_signals", "Coach only. What is happening in one new hire's onboarding, de-identified: question topics (counted, "
               "never quoted), tasks and how long since each moved, CI runs on their pull requests, the environment check, review "
               "findings, the blocker alerts the rules opened (with the stage each may reach now) and milestones overdue or due soon. "
               "Every item has a signal id.", OnboardingArgs, sensitive=True, label="Read the hire's signals")
def hire_signals(ctx: ToolCtx, args: OnboardingArgs) -> ToolResult:
    if ctx.agent != "coach":
        raise ToolDenied("only the Coach reads a hire's signals")
    hc = hire_of(ctx, args.onboarding_id)
    payload = signals(hc)
    ctx.notes.setdefault("signals", set()).update(signal_ids(payload))
    ctx.notes.setdefault("alerts", {}).update({a["alert_id"]: a for a in payload["blocker_alerts"]})
    n_alerts, n_tasks = len(payload["blocker_alerts"]), len(payload["tasks"])
    return ToolResult(text=untrusted(payload), sources=[Source(label="The hire's signals", kind="metric", ref={"onboarding_id": hc.ob.id})],
                      summary=f"Read the hire's signals: {n_tasks} task{'s' if n_tasks != 1 else ''}, {n_alerts} open alert"
                              f"{'s' if n_alerts != 1 else ''}, {len(payload['questions']['by_topic'])} question topics",
                      shared="Read the hire's signals")


# ---------------------------------------------------------------- program (hire-visible)
def program_view(hc) -> dict:
    from app.lifecycle import enrolment, progress
    from app.lifecycle import templates as T

    ctx, p = hc.ctx, hc.program
    t = ctx.lc.get_template(p.template_id)
    ms = progress.milestone_view(hc)
    out = {"onboarding_id": hc.ob.id, "role_title": hc.ob.role_title, "day": hc.day, "duration_days": p.duration_days,
           "focus_area": p.focus_area, "mentor": (ctx.person(p.mentor_id) or {}).get("name"),
           "template": {"key": t.key, "title": t.title, "track": t.role_track, "level": t.level} if t else None,
           "phases": enrolment.phases(p.duration_days),
           "plan_status": enrolment.plan_status(ctx, hc.ob, p)["status"],
           "milestones": [{"key": m["key"], "title": m["title"], "due_day": m["due_day"], "status": m["status"], "state": m["state"]} for m in ms],
           "tasks": [{"id": x.id, "title": x.title, "kind": x.kind, "status": x.status, "due_date": x.due_date.isoformat() if x.due_date else None,
                      "files": x.related_paths[:6], "issue": x.github_issue_number} for x in hc.tasks if x.status not in {"proposed", "rejected"}],
           "areas": [{"area": a["area"], "level": a["level"], "why": a["why"][0]} for a in progress.areas(hc)]}
    if t and not ms:  # a plan still being drafted: the template's milestones and plan, scaled to the program
        out["template_milestones"] = [{"key": m.key, "title": m.title, "due_day": m.due_day} for m in T.milestones_for(t, hc.ob.id, p.duration_days)]
        out["template_plan"] = [{"title": x.title, "kind": x.kind, "due_day": x.due_in_days} for x in T.plan_tasks(t, p.duration_days)]
    return out


@REGISTRY.tool("program", "One hire's onboarding program as the hire sees it: day and length, phases, milestones with their status, "
               "the approved tasks, progress by area, the focus area, and (for a plan still being drafted) the template's milestones "
               "and plan.", OnboardingArgs, label="Read the program")
def program(ctx: ToolCtx, args: OnboardingArgs) -> ToolResult:
    hc = hire_of(ctx, args.onboarding_id)
    view = program_view(hc)
    ctx.notes.setdefault("milestone_keys", set()).update(m["key"] for m in view["milestones"] + view.get("template_milestones", []))
    return ToolResult(text=untrusted(view), sources=[Source(label="The onboarding program", kind="metric", ref={"onboarding_id": hc.ob.id})],
                      summary=f"Read the program: day {view['day']} of {view['duration_days']}, {len(view['milestones'] or view.get('template_milestones', []))} milestones",
                      shared="Read the program")


# ---------------------------------------------------------------- first_task_candidates (the Scout's)
@REGISTRY.tool("first_task_candidates", "The first-task candidates the Scout may pick from: open issues labelled for newcomers and "
               "the hire's proposed code tasks, each with its id, the files it touches and the factors the rules scored (focus "
               "area, explained in a KT session, small, tested, quiet); and the excluded ones (a single-owner critical area) with why.",
               OnboardingArgs, label="Read the first-task candidates")
def first_task_candidates(ctx: ToolCtx, args: OnboardingArgs) -> ToolResult:
    from app.lifecycle import first_task

    hc = hire_of(ctx, args.onboarding_id)
    s = first_task.shortlist(hc, limit=None)
    ctx.notes["candidates"] = {c["id"]: c for c in s["candidates"]}
    ctx.notes["excluded"] = {c["id"]: c for c in s["excluded"]}
    rows = [{"id": c["id"], "kind": c["kind"], "title": c["title"], "issue": c["issue"], "files": c["files"][:8], "score": c["score"],
             "factors": c["why"], "tests": (c.get("evidence", {}).get("tested") or {}).get("tests", [])} for c in s["candidates"][:15]]
    excluded = [{"id": c["id"], "title": c["title"], "why": c["why"]} for c in s["excluded"][:10]]
    sources = [Source(label=f"Issue #{c['issue']}", kind="issue", ref={"number": c["issue"]}) for c in s["candidates"][:6] if c.get("issue")]
    return ToolResult(text=untrusted({"focus_area": s["focus_area"], "candidates": rows, "excluded": excluded}), sources=sources,
                      summary=f"Read the first-task candidates: {len(rows)} open, {len(excluded)} excluded",
                      shared=f"Read the first-task candidates ({len(rows)})")


# ---------------------------------------------------------------- cohort_evidence (the Planner's)
class NoArgs(BaseModel):
    pass


def cohort(deps, org_id: str, template) -> tuple[list[dict], list[dict], int]:
    """(evidence rows with ids, the cohort's completed programs as (onboarding, program), n): what the completed programs of the
    template's track show, and the survey questions that groups of SURVEY_MIN_GROUP or more people answered (never fewer)."""
    from statistics import median

    from app.lifecycle import checkins
    from app.lifecycle import templates as T

    lc, store = deps.lifecycle, deps.store
    tracks = {t.id: t.role_track for t in lc.templates(org_id, include_inactive=True)}
    done = []
    for p in lc.programs(org_id):
        ob = store.get_onboarding(p.onboarding_id)
        if p.completed_at and ob and tracks.get(p.template_id) == template.role_track:
            done.append((ob, p))
    n, rows = len(done), []

    def day(ob, at) -> int:
        return (at.date() - ob.start_date).days

    for rule in ("environment", "repeated_topic", "stalled_task", "failing_ci", "unresolved_review"):
        hit = sum(any(a.rule == rule and day(ob, a.opened_at) <= 14 for a in lc.blockers(ob.id)) for ob, _ in done)
        rows.append({"id": f"blocker:{rule}", "what": f"programs that opened a {rule.replace('_', ' ')} blocker in their first 14 days",
                     "programs": hit, "of": n})
    for m in template.milestones:
        days = []
        for ob, p in done:
            row = next((x for x in lc.milestones(ob.id) if x.key == m["key"] and x.done_at and x.status == "done"), None)
            if row:
                days.append(round(day(ob, row.done_at) * T.BASE_DAYS / p.duration_days))
        if days:
            rows.append({"id": f"milestone:{m['key']}", "what": f"'{m['title']}': when it was reached (day of a 90-day program)",
                         "planned_day": int(m["due_day"]), "median_day": int(round(median(days))), "reached_by": len(days), "of": n})
    if done:
        lengths = [day(ob, p.completed_at) for ob, p in done]
        rows.append({"id": "duration", "what": "days to completion", "template_days": template.duration_days,
                     "median_days": int(round(median(lengths))), "of": n})
    scores = checkins.floored_scores(lc.survey_responses(org_id))
    for q in checkins.SURVEY:
        if q["id"] in scores:
            rows.append({"id": f"survey:{q['id']}", "what": f"Survey: '{q['text']}' (1-5)", "average": scores[q["id"]][0],
                         "answers": scores[q["id"]][1], "floor": f"only groups of {checkins.SURVEY_MIN_GROUP} or more people"})
    return rows, done, n


@REGISTRY.tool("cohort_evidence", "What the completed programs of this template's role track show (blockers hit in the first two "
               "weeks, when each milestone was reached, how long they ran), survey questions answered by groups of 5 or more people, "
               "and the template itself. Every row has an id to cite.", NoArgs, label="Read the cohort's outcomes")
def cohort_evidence(ctx: ToolCtx, args: NoArgs) -> ToolResult:
    lc = ctx.deps.lifecycle
    template = lc.get_template(ctx.notes.get("template_id", "")) if lc is not None else None
    if template is None or str(template.org_id) != str(ctx.org_id):
        raise ToolDenied("no template for this run")
    rows, _, n = cohort(ctx.deps, ctx.org_id, template)
    ctx.notes["evidence"] = {r["id"]: r for r in rows}
    body = {"template": {"title": template.title, "track": template.role_track, "duration_days": template.duration_days,
                         "milestones": template.milestones, "plan_items": [{"title": i.get("title"), "kind": i.get("kind"),
                                                                            "due_day": i.get("due_day")} for i in template.plan_items]},
            "completed_programs": n, "evidence": rows}
    return ToolResult(text=untrusted(body), sources=[Source(label=f"{n} completed {template.role_track} programs", kind="metric",
                                                           ref={"template_key": template.key})],
                      summary=f"Read the cohort's outcomes: {n} completed programs, {len(rows)} evidence rows",
                      shared=f"Read the cohort's outcomes ({n} programs)")


# ---------------------------------------------------------------- pr_diff (the Reviewer's)
class DiffArgs(BaseModel):
    path: str = Field("", max_length=400, description="A changed file's path; empty: the list of changed files")
    start: int = Field(1, ge=1, description="For a file: the first patch line to show (for a long patch)")


def numbered_patch(patch: str) -> list[str]:
    """A unified diff's lines with the new line numbers a finding must cite ('+  31 | ...', '   30 | ...', '-     | ...')."""
    from app.services.diff import HUNK

    out, new = [], 0
    for raw in (patch or "").splitlines():
        m = HUNK.match(raw)
        if m:
            new = int(m.group("start"))
            out.append(raw)
        elif raw.startswith("+") and not raw.startswith("+++"):
            out.append(f"+{new:>5} | {raw[1:]}")
            new += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            out.append(f"-{'':>5} | {raw[1:]}")
        elif raw.startswith("\\"):
            continue
        else:
            out.append(f" {new:>5} | {raw[1:] if raw.startswith(' ') else raw}")
            new += 1
    return out


@REGISTRY.tool("pr_diff", "The pull request under review (read-only): with no path, its changed files (status, lines added and "
               "removed); with a path, that file's patch with the new line numbers a finding must cite ('+' added, '-' removed).",
               DiffArgs, label="Read the change")
def pr_diff(ctx: ToolCtx, args: DiffArgs) -> ToolResult:
    files = ctx.notes.get("pr_files")
    if files is None:
        raise ToolDenied("no change under review in this run")
    from app.services.diff import parse_pr_files

    diffs = parse_pr_files(files)
    path = args.path.strip().lstrip("./")
    if not path:
        rows = [{"path": d.path, "status": d.status, "added": d.additions, "removed": d.deletions, "test": d.is_test} for d in diffs.values()]
        return ToolResult(text=untrusted({"files": rows[:60]}), summary=f"Read the change: {len(rows)} files", shared=f"Read the change ({len(rows)} files)")
    f = next((x for x in files if x["filename"] == path), None) or next((x for x in files if x["filename"].endswith("/" + path)), None)
    if f is None:
        return ToolResult(text=untrusted({"error": "That file is not in this change.", "files": [x["filename"] for x in files][:40]}),
                          summary="That file is not in the change", shared="A file was not in the change")
    lines = numbered_patch(f.get("patch") or "")
    part = lines[args.start - 1: args.start - 1 + PATCH_LINES]
    more = {"more": f"The patch goes on: call again with start={args.start + PATCH_LINES}."} if len(lines) > args.start - 1 + PATCH_LINES else {}
    src = Source(label=f"{f['filename']} (changed)", kind="code", ref={"path": f["filename"]})
    return ToolResult(text=untrusted({"path": f["filename"], "status": f.get("status"), "patch": "\n".join(part) or "(no patch)", **more}),
                      sources=[src], summary=f"Reading the change to {f['filename']}", shared="Read a changed file")


# ---------------------------------------------------------------- kt_changes (the Keeper's, after a push)
class LinkArgs(BaseModel):
    link: str = Field("", max_length=400, description="A link id from the list; empty: the list of changed links")


@REGISTRY.tool("kt_changes", "The KT explanations a push touched: with no link, the list (the KT session, the file and function it explained, "
               "the share of its lines that changed, whether the code is gone); with a link id, the diff from the code as the session "
               "explained it to the code now, and what the session said (redacted).", LinkArgs, label="Read the changed KT links")
def kt_changes(ctx: ToolCtx, args: LinkArgs) -> ToolResult:
    changes = ctx.notes.get("kt_changes")
    if changes is None:
        raise ToolDenied("no push in this run")
    if not args.link:
        rows = [{"link": c["id"], "session": c["meeting_title"], "file": c["path"], "symbol": c.get("symbol"), "changed_share": c["changed_share"],
                 "gone": c["gone"]} for c in changes]
        return ToolResult(text=untrusted({"links": rows}), summary=f"Read the changed KT links: {len(rows)}", shared=f"Read the changed KT links ({len(rows)})")
    c = next((x for x in changes if x["id"] == args.link.strip()), None)
    if c is None:
        return ToolResult(text=untrusted({"error": "No such link in this push.", "links": [x["id"] for x in changes]}),
                          summary="No such link", shared="Asked for a link that is not in the push")
    diff = list(difflib.unified_diff(c["old"].splitlines(), c["new"].splitlines(), "explained", "now", lineterm="", n=2))[:DIFF_LINES]
    src = [Source(label=f"{c['meeting_title']}: {c['path']}", kind="kt", ref={"meeting_id": c["meeting_id"], "path": c["path"]})]
    return ToolResult(text=untrusted({"link": c["id"], "session": c["meeting_title"], "file": c["path"], "symbol": c.get("symbol"),
                                      "gone": c["gone"], "diff": "\n".join(diff) or "(no line changed)", "what_the_session_said": c["said"]}),
                      sources=src, summary=f"Reading the change to {c['path']} since {c['meeting_title']}", shared="Read a changed KT link")


# ---------------------------------------------------------------- feedback_target (the Keeper's)
@REGISTRY.tool("feedback_target", "The flagged document, file or verified answer: its title, its current text (an excerpt), and the "
               "readers' notes on it (unclear or outdated).", NoArgs, label="Read the flagged target")
def feedback_target(ctx: ToolCtx, args: NoArgs) -> ToolResult:
    t = ctx.notes.get("target")
    if t is None:
        raise ToolDenied("no flagged target in this run")
    kind = {"code": "code", "doc": "doc", "answer": "doc"}.get(t["kind"], "doc")
    return ToolResult(text=untrusted(t), sources=[Source(label=t["title"][:200], kind=kind, ref={"target_kind": t["kind"], "target_ref": t["ref"]})],
                      summary=f"Reading {t['title'][:80]} and {len(t['reader_notes'])} reader notes", shared="Read the flagged target")


# ---------------------------------------------------------------- person_footprint (the Keeper's, for a handover)
class PersonArgs(BaseModel):
    user_id: str = Field("", max_length=64, description="The person's user id; empty: the person leaving")


def footprint(deps, project_id: str, org_id: str, viewer, person) -> dict:
    """What one person holds in a project: files they own (alone or not), the KT moments they explained, the sessions the KT
    planner has them present, and counts of what waits on them (no question's words)."""
    from app.services.codeowners import owners_for, parse_codeowners
    from app.services.knowledge import Speakers
    from app.services.knowledge_risk import build_knowledge_risk
    from app.services.kt_planner import build_plan

    store, index, lc = deps.store, deps.index, deps.lifecycle
    risk = build_knowledge_risk(store, index, project_id, org_id)
    me = next((p for p in risk["people"] if p["user_id"] == person.id), None)
    orphaned = list((me or {}).get("exposure", {}).get("orphaned_files", []))
    rules = parse_codeowners(store.codeowners(project_id))
    login = (person.github_login or "").lower()
    owned, alone = [], []
    if login:
        for p in index.all_paths(project_id):
            logins = [o.lower().lstrip("@") for o in owners_for(rules, p)]
            if login in logins:
                owned.append(p)
                if logins == [login]:
                    alone.append(p)
    speakers = Speakers(store, store.members(project_id))
    explained = []
    for lk in store.kt_links(project_id):
        _, who = speakers.for_link(lk)
        if who is not None and who.id == person.id and lk.confidence >= 0.5:
            explained.append({"session": lk.meeting_title or "KT session", "file": lk.file_path, "symbol": lk.symbol,
                              "code_changed_since": lk.code_changed_since})
    try:
        sessions = build_plan(store, index, project_id, org_id, viewer, person.id)["sessions"]
    except Exception:  # noqa: BLE001 - the planner's view is context, not the plan
        sessions = []
    waiting = {"open_questions": sum(str(r.get("project_id")) == project_id for r in store.expert_requests_for(person.id)),
               "mentees": sum(p.mentor_id == person.id and not p.completed_at for p in lc.programs(org_id, project_id)) if lc else 0,
               "feedback": sum(f.owner_id == person.id and f.status == "open" for f in lc.feedback(project_id)) if lc else 0}
    return {"person": {"user_id": person.id, "name": person.name}, "owns_alone": alone[:40], "owns_with_others": [p for p in owned if p not in alone][:40],
            "orphaned_if_they_leave": orphaned[:40], "explained_in_kt_sessions": explained[:30],
            "planned_sessions": [{"title": s["title"], "minutes": s.get("est_minutes"), "files": [i.get("path") for i in (s.get("items") or []) if i.get("path")][:8]}
                                 for s in sessions[:8]], "waiting_on_them": waiting}


@REGISTRY.tool("person_footprint", "What the person leaving holds in this project: files they own alone (CODEOWNERS) and with others, "
               "files nobody else explained that would be orphaned, the KT moments they explained, the KT sessions planned for them "
               "to present, and how much waits on them (counted).", PersonArgs, label="Read what the person holds")
def person_footprint(ctx: ToolCtx, args: PersonArgs) -> ToolResult:
    pid = ctx.notes.get("person_id")
    if not pid or (args.user_id and args.user_id != pid):
        raise ToolDenied("not the person this handover is for")
    person = ctx.deps.store.get_user(pid)
    if person is None or str(person.org_id) != str(ctx.org_id) or not ctx.deps.store.is_member(ctx.project_id, person.id):
        raise ToolDenied("not a member of this project")
    fp = footprint(ctx.deps, ctx.project_id, ctx.org_id, ctx.user, person)
    paths = set(fp["owns_alone"]) | set(fp["owns_with_others"]) | set(fp["orphaned_if_they_leave"]) | {e["file"] for e in fp["explained_in_kt_sessions"]}
    paths |= {f for s in fp["planned_sessions"] for f in s["files"]}
    ctx.notes.setdefault("footprint_paths", set()).update(paths)
    return ToolResult(text=untrusted(fp), sources=[Source(label=f"Owned or explained by {fp['person']['name']}", kind="metric", ref={"user_id": pid})],
                      summary=f"Read what {fp['person']['name']} holds: {len(fp['owns_alone'])} files alone, "
                              f"{len(fp['explained_in_kt_sessions'])} KT moments", shared="Read what the person holds")
