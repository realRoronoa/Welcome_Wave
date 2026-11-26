"""The first task: a shortlist of real work with its reasons, and the brief the manager's selection makes.

Candidates: open issues labelled for newcomers (RepoSource.good_first_issues) and the Task agent's proposed code tasks
for this hire. A task's files are its paths; an issue's (or a task's without paths) are the code a search for its title
finds (similarity >= 0.2).
An issue the hire already has a task for is that task, not a second candidate.

Score (0 to 1), each factor listed as a reason when it scores:
  0.30 in focus area   every code file (test files aside) is in the program's focus area
  0.25 explained       the share of its code files a KT call explained (KT links)
  0.20 small           three files or fewer
  0.15 tested          a test file exists for one of its files (tests/test_<name>.py and the like)
  0.10 quiet           nobody else's open task or pull request touches its files (the RepoSource port has no commit
                       history, so open work by others stands in for "no commits by others in 14 days")
Files of a single-owner critical area (knowledge risk: band critical, one holder) exclude a candidate: a newcomer's
first change should not land where only one person can review it. The top 3 are shown; ties go to the issue number,
then the title."""
from __future__ import annotations

import re

from app.lifecycle.context import HireCtx
from app.services.chunking import module_of
from app.services.codeowners import owners_for, parse_codeowners
from app.services.diff import TEST_PATH
from app.services.knowledge import MIN_SIMILARITY, task_query
from app.services.knowledge_risk import build_knowledge_risk

WEIGHTS = {"focus": 0.30, "explained": 0.25, "small": 0.20, "tested": 0.15, "quiet": 0.10}
SMALL_FILES = 3
SHOWN = 3
OPEN_WORK = frozenset({"approved", "in_progress", "submitted", "changes_requested"})


def _stem(path: str) -> str:
    return re.sub(r"\.\w+$", "", path.rsplit("/", 1)[-1])


def _in_focus(path: str, focus: str | None) -> bool:
    return bool(focus) and (module_of(path) == focus or path.startswith(focus.rstrip("/") + "/"))


def candidates(hc: HireCtx) -> list[dict]:
    ctx, ob = hc.ctx, hc.ob
    repo = next(iter(ctx.store.repos_for(ob.project_id)), None)
    issues = ctx.deps.repos.good_first_issues(repo.full_name, repo.installation_id) if repo else []
    tasks = [t for t in hc.tasks if t.kind == "code" and t.status != "rejected" and t.status != "done"]
    out = [{"id": f"task:{t.id}", "kind": "task", "title": t.title, "task_id": t.id, "issue": t.github_issue_number, "status": t.status,
            "files": list(dict.fromkeys(t.related_paths))} for t in tasks]
    taken = {t.github_issue_number for t in tasks if t.github_issue_number}
    for issue in issues:
        if issue["number"] not in taken:
            out.append({"id": f"issue:{issue['number']}", "kind": "issue", "title": issue["title"], "task_id": None,
                        "issue": issue["number"],
                        "status": "open", "files": []})
    for cand in out:  # no paths of its own (an issue, a task the agent gave none): the code a search for its title finds
        if not cand["files"]:
            hits = ctx.index.search(ob.project_id, task_query(cand["title"]), k=3, source_types=["code"])
            cand["files"] = list(dict.fromkeys(h.chunk.ref["path"] for h in hits if h.similarity >= MIN_SIMILARITY))
    for cand in out:  # a task with folder paths: the indexed files under them
        if any(f.endswith("/") or "." not in f.rsplit("/", 1)[-1] for f in cand["files"]):
            paths = ctx.index.all_paths(ob.project_id)
            cand["files"] = list(dict.fromkeys(p for f in cand["files"] for p in paths if p == f or p.startswith(f.rstrip("/") + "/")))
    return out


def shortlist(hc: HireCtx, limit: int | None = SHOWN) -> dict:
    ctx, ob = hc.ctx, hc.ob
    project = ctx.store.get_project(ob.project_id)
    risk = build_knowledge_risk(ctx.store, ctx.index, project.id, project.org_id)
    critical = {f["path"] for f in risk["files"] if f["risk"] == "critical" and f["single_holder"]}
    paths = ctx.index.all_paths(ob.project_id)
    tests = [p for p in paths if TEST_PATH.search(p)]
    explained = {l.file_path for l in hc.kt_links if l.confidence >= 0.5}
    others = [t for t in ctx.store.tasks(project_id=ob.project_id) if t.onboarding_id != ob.id and t.status in OPEN_WORK]
    busy = {p for t in others for p in t.related_paths}
    focus = hc.program.focus_area
    scored, excluded = [], []
    for cand in candidates(hc):
        files = cand["files"]
        blocked = sorted(set(files) & critical)
        if blocked:
            excluded.append({**cand, "why": [f"{', '.join(blocked)} is a single-owner critical area: only one person could review it."]})
            continue
        factors = []
        code = [f for f in files if not TEST_PATH.search(f)]  # tests are where a change starts, not what it is about
        if code and all(_in_focus(f, focus) for f in code):
            factors.append(("focus", 1.0, f"In the focus area ({focus}).", {"focus_area": focus}))
        share = len([f for f in code if f in explained]) / len(code) if code else 0.0
        if share:
            factors.append(("explained", share, f"{round(share * 100)}% of its code was explained in a KT session.",
                            {"explained": [f for f in code if f in explained]}))
        if files and len(files) <= SMALL_FILES:
            factors.append(("small", 1.0, f"Small: {len(files)} file{'s' if len(files) != 1 else ''}.", {"files": files}))
        found_tests = sorted({t for t in tests for f in code if _stem(f) in t.rsplit("/", 1)[-1]} | {f for f in files if f in tests})
        if found_tests:
            factors.append(("tested", 1.0, f"Tests exist: {', '.join(found_tests)}.", {"tests": found_tests}))
        if files and not any(f in busy or any(f.startswith(b.rstrip("/") + "/") for b in busy) for f in files):
            factors.append(("quiet", 1.0, "Nobody else has open work on these files.", {}))
        score = round(sum(WEIGHTS[k] * v for k, v, _, _ in factors), 2)
        scored.append({**cand, "score": score, "factors": {k: round(WEIGHTS[k] * v, 3) for k, v, _, _ in factors},
                       "why": [text for _, _, text, _ in factors] or ["No factor scored."],
                       "evidence": {k: ev for k, _, _, ev in factors if ev}})
    scored.sort(key=lambda c: (-c["score"], c["issue"] or 10**9, c["title"]))
    return {"focus_area": focus, "candidates": scored[:limit] if limit else scored, "excluded": excluded,
            "weights": WEIGHTS, "selected": hc.program.first_task}


def brief(hc: HireCtx, cand: dict) -> dict:
    """What the hire gets: the task, why it was chosen, who explained the code and who owns it, and the tests to start from."""
    ctx, ob = hc.ctx, hc.ob
    rules = parse_codeowners(ctx.store.codeowners(ob.project_id))
    owners = sorted({o for f in cand["files"] for o in owners_for(rules, f)})
    clips = sorted((l for l in hc.kt_links if l.file_path in cand["files"] and l.confidence >= 0.5), key=lambda l: -l.confidence)[:3]
    return {"candidate_id": cand["id"], "title": cand["title"], "issue": cand["issue"], "files": cand["files"], "score": cand["score"],
            "why": cand["why"], "owners": owners, "tests": cand.get("evidence", {}).get("tested", {}).get("tests", []),
            "kt_clips": [c.model_dump(mode="json") for c in clips],
            "acceptance_criteria": ["Pull request opened and linked to this task", "Tests added or updated",
                                    "Review agent and manager approve"],
            "next": "Run a private review of your branch before opening the pull request."}
