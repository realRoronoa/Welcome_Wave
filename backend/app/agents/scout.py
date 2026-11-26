"""The Scout: chooses a safe first task and writes its brief.

It thinks (app/agents/thinking.py). Asked for a shortlist, it reads the candidates (`first_task_candidates`: open newcomer
issues and the hire's proposed code tasks, with the factors the rules scored and the ones excluded), explores the promising
ones (read_file, tests_for, kt_moments, kt_coverage, recent_changes, code_owners, program, issues, search_knowledge) and picks
at most 3, each with its reason, the files it touches and the sources it read. Asked for the brief of the candidate the manager
selected, it reads that task's files, tests and KT moments and writes the brief and the first steps.

Guardrails, in code: a pick must be a real candidate (an open issue's number, the hire's own task id), never one the rules
excluded (a single-owner critical area: only one person could review it); its files must be indexed and outside those areas;
at most 3; the brief's text passes the checked writer over what the model read. The manager still selects: nothing here
creates a task or tells anyone (the selection route does, as the manager), and the files of the selected brief are what the
route copies into the task's related_paths.

The picks are kept (metric `scout.picks`, with a fingerprint of the candidates) so the hire's visibility page and the
manager's shortlist show the same thing, and a page view does not ask a model again until the candidates change.

Fallback: with no loop (the AI off, the offline demo, "rules" mode, the daily budget) or no answer, the rules' shortlist
(app/lifecycle/first_task.py: focus area, KT explanation, size, tests, quiet files) and the checked writer's brief.
`shortlist_view` is also the visibility registry's builder (app/lifecycle/visibility.py): it never asks a model."""
from __future__ import annotations

import hashlib
import json

from app.agents import prompts
from app.agents import registry as R
from app.agents.schemas import ScoutBrief, ScoutShortlist

KEY = "scout"
MAX_PICKS = 3
TOOLS = ("first_task_candidates", "issues", "read_file", "tests_for", "kt_moments", "kt_coverage", "recent_changes", "code_owners",
         "program", "search_knowledge")
BRIEF_TOOLS = ("read_file", "tests_for", "kt_moments", "code_owners", "search_knowledge")
WEIGHTS_LINE = ("Score = 0.30 in the focus area + 0.25 explained on a KT call + 0.20 small (3 files or fewer) + 0.15 tested + "
                "0.10 quiet (nobody else's open work on its files).")


def _cand_sources(hc, cand: dict) -> list[dict]:
    explained = {l.file_path: l for l in hc.kt_links if l.confidence >= 0.5}
    out = [R.source(f, "code", path=f) for f in cand.get("files", [])[:6]]
    out += [R.source(f"{l.meeting_title or 'KT call'}: {f}", "kt", meeting_id=l.meeting_id, path=f, start_s=l.start_s)
            for f, l in explained.items() if f in cand.get("files", [])][:3]
    if cand.get("issue"):
        out.append(R.source(f"Issue #{cand['issue']}", "event", issue=cand["issue"]))
    return out


# ---------------------------------------------------------------- the shortlist
def fingerprint(s: dict) -> str:
    """What the picks were made for: the candidates (ids and files), the excluded ones and the focus area."""
    key = json.dumps([sorted([c["id"], *c["files"]] for c in s["candidates"]), sorted(c["id"] for c in s["excluded"]), s["focus_area"]])
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def stored_picks(hc, fp: str) -> dict | None:
    """The Scout's last picks for this hire, if they were made for these candidates."""
    lc = hc.ctx.lc
    rows = [m for m in lc.metrics(hc.program.org_id, ["scout.picks"]) if m.subject_id == hc.ob.id]
    last = max(rows, key=lambda m: m.at, default=None)
    return last.props if last and last.props.get("fingerprint") == fp else None


def shortlist_view(hc) -> dict:
    """The shortlist with an explain block per candidate (and per excluded one): the Scout's stored picks when they were made
    for these candidates, else the rules' top 3. Never asks a model and writes nothing."""
    from app.lifecycle import first_task

    full = first_task.shortlist(hc, limit=None)
    s = {**full, "candidates": full["candidates"][:first_task.SHOWN]}
    picks = stored_picks(hc, fingerprint(full))
    by_id = {c["id"]: c for c in full["candidates"]}
    chosen = [p for p in (picks or {}).get("picks", []) if p["candidate_id"] in by_id]
    if chosen:
        s["candidates"] = []
        for p in chosen:
            cand = {**by_id[p["candidate_id"]], "agent_reason": p["reason"]}
            if p.get("related_paths"):
                cand["files"] = list(p["related_paths"])
            cand["explain"] = R.explain(KEY, [p["reason"]] + cand["why"], p.get("sources", []) + _cand_sources(hc, cand), cand["score"], True)
            s["candidates"].append(cand)
        s["agent"] = {k: picks.get(k) for k in ("run_id", "rationale", "steps", "at")}
        logic = [picks.get("rationale") or "The Scout read the candidates and chose these.",
                 "Only open newcomer issues and the hire's own tasks can be picked, never a single-owner critical area.",
                 WEIGHTS_LINE]
    else:
        for cand in s["candidates"]:
            cand["explain"] = R.explain(KEY, cand["why"], _cand_sources(hc, cand), cand["score"])
        logic = [WEIGHTS_LINE, "Files of a single-owner critical area exclude a candidate.",
                 f"The top {first_task.SHOWN} are shown; ties go to the issue number, then the title."]
    for cand in s["excluded"]:
        cand["explain"] = R.explain(KEY, cand["why"], _cand_sources(hc, cand))
    s["explain"] = R.explain(KEY, logic, [], None, bool(chosen))
    return s


def check_picks(deps, hc, shortlist: ScoutShortlist, cited) -> tuple[list[dict], list[str]]:
    """The model's picks that pass the guardrails, and a line for each one dropped (for the trace)."""
    from app.agents.thinking import known_paths, real_paths
    from app.lifecycle import first_task
    from app.services.knowledge_risk import build_knowledge_risk

    full = first_task.shortlist(hc, limit=None)
    by_id = {c["id"]: c for c in full["candidates"]}
    excluded = {c["id"] for c in full["excluded"]}
    project = hc.ctx.store.get_project(hc.ob.project_id)
    risk = build_knowledge_risk(hc.ctx.store, hc.ctx.index, project.id, project.org_id)
    critical = {f["path"] for f in risk["files"] if f["risk"] == "critical" and f["single_holder"]}
    files, _ = known_paths(deps, hc.ob.project_id)
    kept, dropped = [], []
    for p in shortlist.picks:
        cid = p.candidate_id.strip()
        if cid in excluded:
            dropped.append(f"Dropped {cid}: a single-owner critical area")
            continue
        if cid not in by_id:
            dropped.append("Dropped a pick that is not a candidate")
            continue
        if any(k["candidate_id"] == cid for k in kept):
            continue
        paths = [x for x in real_paths(p.related_paths, files) if x not in critical]
        kept.append({"candidate_id": cid, "reason": " ".join(p.reason.split())[:400] or "Chosen by the Scout.",
                     "related_paths": paths or by_id[cid]["files"], "sources": cited(p.sources)})
        if len(kept) == MAX_PICKS:
            break
    return kept, dropped


def shortlist(deps, hc, user, run_id: str | None = None) -> dict:
    """first_task.requested with no candidate: the Scout's picks (asking the model only when the candidates changed), else the
    rules' shortlist. Returns the view (the same value the hire's visibility page shows: the stored picks carry the run's id
    and shared steps) plus, when the model ran, the run record's `trace`. The asker watched the full steps stream."""
    from app.agents import thinking
    from app.agents.llm import embed_data
    from app.agents.tools import REGISTRY
    from app.lifecycle import first_task

    full = first_task.shortlist(hc, limit=None)
    fp = fingerprint(full)
    if stored_picks(hc, fp) or not full["candidates"]:
        return shortlist_view(hc)
    task = ("Shortlist at most 3 first tasks for this new hire. Start with first_task_candidates; the hire's onboarding id and "
            "focus area are in the data.\n\n" + embed_data({"onboarding_id": hc.ob.id, "focus_area": hc.program.focus_area,
                                                             "role": hc.ob.role_title, "day": hc.day}))
    t = thinking.think(deps, KEY, org_id=hc.program.org_id, project_id=hc.ob.project_id, user=user, system=prompts.SCOUT_AGENT,
                       task=task, tools=REGISTRY.select(*TOOLS), output=ScoutShortlist, run_id=run_id, max_steps=8,
                       clock=hc.ctx.clock, notes={"onboarding_id": hc.ob.id})
    if t is None:
        return shortlist_view(hc)
    if t.output is None:
        return {**shortlist_view(hc), "trace": t.trace()}
    kept, dropped = check_picks(deps, hc, t.output, t.sources)
    for line in dropped:
        t.note("verify", line, shared="Dropped a pick that failed a check")
    if not kept:
        t.note("final", "No pick passed the checks: the rules' shortlist stands.")
        return {**shortlist_view(hc), "trace": t.trace()}
    rationale, _ = t.text(t.output.rationale, 400)
    t.note("final", f"Picked {len(kept)} first task{'s' if len(kept) != 1 else ''}", shared=f"Picked {len(kept)} first tasks")
    from app.agents.runtime import shared_steps

    hc.ctx.lc.record_metric(hc.program.org_id, hc.ob.project_id, "scout.picks", hc.ob.id,
                            {"fingerprint": fp, "picks": kept, "rationale": rationale or "", "run_id": t.run_id,
                             "steps": shared_steps(t.steps), "at": hc.ctx.clock.now().isoformat()})
    return {**shortlist_view(hc), "trace": t.trace()}


# ---------------------------------------------------------------- the brief
def _draft(b: dict) -> str:
    parts = [f"Your first task: {b['title']}."]
    if b.get("why"):
        parts.append("Why this one: " + " ".join(b["why"]))
    if b.get("files"):
        parts.append(f"It touches {', '.join(b['files'][:4])}.")
    clips = b.get("kt_clips") or []
    if clips:
        parts.append(f"It was explained in {clips[0].get('meeting_title') or 'a KT call'}: watch that clip first.")
    if b.get("owners"):
        parts.append(f"Reviewed by {', '.join('@' + o for o in b['owners'][:3])}.")
    if b.get("tests"):
        parts.append(f"Start from the tests in {', '.join(b['tests'][:3])}.")
    parts.append("Run a private review of your branch before you open the pull request.")
    return " ".join(parts)


def _data(b: dict) -> dict:
    return {"task": {"title": b["title"], "issue": b["issue"], "files": b["files"], "why": b["why"], "owners": b["owners"],
                     "tests": b["tests"], "acceptance_criteria": b["acceptance_criteria"]},
            "kt_clips": [{"meeting_title": c.get("meeting_title"), "path": c.get("file_path")} for c in b["kt_clips"]]}


def brief(deps, hc, cand: dict, trace: str | None = None, user=None, run_id: str | None = None) -> dict:
    """The brief of the selected candidate (first_task.brief) with the Scout's text and its explain block. With a model that runs
    a loop, the Scout reads the task's code first and writes the brief and first steps (checked); else the checked writer."""
    from app.agents import thinking
    from app.agents.llm import embed_data
    from app.agents.tools import REGISTRY
    from app.lifecycle import first_task

    picks = stored_picks(hc, fingerprint(first_task.shortlist(hc, limit=None))) or {}
    pick = next((p for p in picks.get("picks", []) if p["candidate_id"] == cand["id"]), None)
    if pick and pick.get("related_paths"):
        cand = {**cand, "files": list(pick["related_paths"])}  # the Scout's files: the route copies them into related_paths
    b = first_task.brief(hc, cand)
    draft = _draft(b)
    t = None
    if user is not None:
        task = ("Write the brief of this first task, which the manager selected. Read its files and tests first.\n\n"
                + embed_data({**_data(b), "scout_reason": pick["reason"] if pick else None}))
        t = thinking.think(deps, KEY, org_id=hc.program.org_id, project_id=hc.ob.project_id, user=user, system=prompts.SCOUT_BRIEF_AGENT,
                           task=task, tools=REGISTRY.select(*BRIEF_TOOLS), output=ScoutBrief, run_id=run_id, clock=hc.ctx.clock,
                           notes={"onboarding_id": hc.ob.id})
    if t is not None and t.output is not None:
        text, why_not = t.text(t.output.summary, 900, draft)
        steps = [s for s in (t.text(x, 300, draft)[0] for x in t.output.first_steps[:4]) if s]
        logic = ([pick["reason"]] if pick else []) + list(cand.get("why") or [])
        if text is None:
            t.note("verify", f"The brief's wording was not used: {why_not}", shared="The brief's wording failed a check")
            logic.append(f"The AI's wording was not used: {why_not}.")
        b["summary"] = text or draft
        b["first_steps"] = steps
        b["explain"] = R.explain(KEY, logic, (t.sources(t.output.sources) or t.found()) + _cand_sources(hc, cand), cand.get("score"),
                                 text is not None, trace)
        from app.agents.runtime import shared_steps

        b["agent"] = {"run_id": t.run_id, "steps": shared_steps(t.steps)}
        return {**b, "run_id": t.run_id, "steps": t.public(), "trace": t.trace()}
    text, ai, dropped = R.write(deps, KEY, hc.program.org_id, system=prompts.SCOUT_BRIEF, data=_data(b), draft=draft, max_len=900)
    logic = list(cand.get("why") or []) + ([f"The AI's wording was not used: {dropped}."] if dropped else [])
    b["summary"] = text
    b["explain"] = R.explain(KEY, logic, _cand_sources(hc, cand), cand.get("score"), ai, trace)
    return {**b, **({"run_id": t.run_id, "steps": t.public(), "trace": t.trace()} if t is not None else {})}


# ---------------------------------------------------------------- ask.free: "which first task should I pick?"
def suggest(deps, ev: dict, steps: list, on_step=None) -> dict | None:
    """The Scout's answer to a free-form request in Ask, for a hire with a program: its picks (the stored ones when they are
    current), as a proposal. The manager selects. None: no program, or no loop could run."""
    from app.agents import thinking
    from app.agents.llm import embed_data
    from app.agents.tools import REGISTRY
    from app.lifecycle import first_task, hires

    user = deps.store.get_user(ev["user_id"])
    ob = next((o for o in deps.store.onboardings(ev["project_id"]) if o.new_hire_id == user.id), None) if user else None
    hc = hires.load(R.lifecycle_ctx(deps, user.org_id, user), ob.id) if ob and deps.lifecycle else None
    if hc is None:
        return None
    full = first_task.shortlist(hc, limit=None)
    by_id = {c["id"]: c for c in full["candidates"]}
    stored = stored_picks(hc, fingerprint(full))
    picks = stored["picks"] if stored else None
    if picks is None:
        task = ("A new hire asked which first task to pick (their words are in the data). Shortlist at most 3 with first_task_candidates "
                "and the other tools.\n\n" + embed_data({"request": ev.get("question", ""), "onboarding_id": hc.ob.id,
                                                          "focus_area": hc.program.focus_area}))
        t = thinking.think(deps, KEY, org_id=user.org_id, project_id=hc.ob.project_id, user=user, system=prompts.SCOUT_AGENT, task=task,
                           tools=REGISTRY.select(*TOOLS), output=ScoutShortlist, max_steps=6, clock=hc.ctx.clock,
                           notes={"onboarding_id": hc.ob.id}, steps=steps, on_step=on_step)
        if t is None or t.output is None:
            return None
        picks, _ = check_picks(deps, hc, t.output, t.sources)
    if not picks:
        return None
    names = "; ".join(by_id[p["candidate_id"]]["title"] for p in picks if p["candidate_id"] in by_id)
    proposal = f"The Scout suggests: {names}. Your manager selects your first task from the shortlist."
    return {"proposal": proposal[:600], "picks": [{**p, "title": by_id[p["candidate_id"]]["title"]} for p in picks if p["candidate_id"] in by_id],
            "actions": [{"label": "Open first-task shortlist", "href": f"#/program/{hc.ob.id}?tab=first-task"}]}


# ---------------------------------------------------------------- the node
def run(deps, ev: dict, trace: str | None = None) -> dict:
    """first_task.requested {project_id, onboarding_id, user_id, candidate_id?, run_id?}: the shortlist, or the brief of a
    candidate. The result may carry `trace` (the run record's part: guarded() takes it off)."""
    from app.lifecycle import first_task, hires

    user = deps.store.get_user(ev["user_id"])
    hc = hires.load(R.lifecycle_ctx(deps, user.org_id, user), ev["onboarding_id"])
    if not ev.get("candidate_id"):
        return shortlist(deps, hc, user, ev.get("run_id"))
    found = next((x for x in first_task.shortlist(hc, limit=None)["candidates"] if x["id"] == ev["candidate_id"]), None)
    if not found:
        return {"error": "unknown_candidate"}
    return brief(deps, hc, found, trace, user=user, run_id=ev.get("run_id"))


def summary(out: dict) -> str:
    if "candidates" in out:
        n = len(out["candidates"])
        who = "The Scout picked" if out.get("agent") else "Shortlisted"
        return f"{who} {n} first task{'s' if n != 1 else ''} ({len(out['excluded'])} excluded)"
    if out.get("error"):
        return "Asked for a candidate that is not on the shortlist"
    return f"Wrote the brief of a first task{' (AI)' if out.get('explain', {}).get('ai') else ''}"
