"""Task agent: proposes a 30-day plan and a real first PR, and keeps task status in sync with GitHub."""
from __future__ import annotations

import re

from app.agents.core import prompts
from app.agents.core.deps import Deps
from app.agents.core.llm import embed_data
from app.agents.core.schemas import ActionItem, ProposedTask, TaskPlan
from app.ports import Onboarding, TaskRow
from app.services.coverage import coverage_map, gaps


def plan_for(deps: Deps, onboarding: Onboarding, action_items: list[ActionItem] | None = None) -> list[ProposedTask]:
    s, store = deps.settings, deps.store
    project = store.get_project(onboarding.project_id)
    paths = deps.index.all_paths(project.id)
    cov = coverage_map(paths, store.kt_links(project.id))
    issues = []
    for repo in store.repos_for(project.id):
        issues += deps.repos.good_first_issues(repo.full_name, repo.installation_id)
    existing = {t.title.lower() for t in store.tasks(onboarding_id=onboarding.id) if t.status != "rejected"}

    data = {
        "project": {"name": project.name, "description": project.description},
        "new_hire_role": onboarding.role_title,
        "modules": [{"module": r.module, "files": r.files, "kt_minutes": r.kt_minutes} for r in cov],
        "coverage_gaps": gaps(cov),
        "action_items": [a.model_dump() for a in (action_items or [])],
        "good_first_issues": [{"number": i["number"], "title": i["title"], "labels": i.get("labels", [])} for i in issues[:10]],
        "existing_tasks": sorted(existing),
    }
    system = prompts.TASK_PLAN.format(horizon=s.plan_horizon_days, max_tasks=s.max_plan_tasks)
    plan = deps.llm.structured(embed_data(data), TaskPlan, smart=True, system=system)
    return validate_plan(plan, paths, issues, existing, s.max_plan_tasks, s.plan_horizon_days)


def validate_plan(plan: TaskPlan, paths: list[str], issues: list[dict], existing: set[str], max_tasks: int, horizon: int) -> list[ProposedTask]:
    known = set(paths) | {p.rsplit("/", i)[0] for p in paths for i in range(1, p.count("/") + 1)}
    out: list[ProposedTask] = []
    seen = set(existing)
    for t in plan.tasks:
        if t.title.lower() in seen:
            continue
        seen.add(t.title.lower())
        out.append(t.model_copy(update={"related_paths": [p for p in t.related_paths if p.rstrip("/") in known],
                                        "due_in_days": min(t.due_in_days, horizon)}))
    if plan.first_pr:
        valid_issue = plan.first_pr.issue_number in {i["number"] for i in issues}
        files = [f for f in plan.first_pr.files if f in known]
        if valid_issue or files:
            out.append(ProposedTask(
                title=f"First PR: {plan.first_pr.title}",
                description=f"{plan.first_pr.why}" + (f" (issue #{plan.first_pr.issue_number})" if valid_issue else ""),
                kind="code", due_in_days=min(14, horizon), related_paths=files, source="first_pr",
                issue_number=plan.first_pr.issue_number if valid_issue else None,
                acceptance_criteria=["PR opened and linked to this task", "Tests added or updated", "Review agent and manager approve"],
            ))
    return out[:max_tasks]


TASK_REF = re.compile(r"task[-_/ ]?#?([0-9a-f][0-9a-f-]{7,35})", re.I)
CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)


def short_ref(task_id: str) -> str:
    """What a new hire puts in a branch name: task-<first 8 hex digits of the id>."""
    return task_id.replace("-", "")[:8].lower()


def find_task_for_pr(tasks: list[TaskRow], branch: str, body: str, by_issue: bool = True) -> TaskRow | None:
    """A PR belongs to a task if its branch or description names the task id (full, or its first 8+ hex digits),
    or (when `by_issue`) it closes the task's issue. An ambiguous prefix matches nothing rather than the wrong task."""
    ids = {t.id.replace("-", "").lower(): t for t in tasks}
    for m in TASK_REF.finditer(f"{branch} {body}"):
        ref = m.group(1).replace("-", "").lower()
        # "task-1a2b3c4d-fix-docs" captures "1a2b3c4d-f": try the longest prefix first, down to the 8-digit short ref
        for n in range(len(ref), 7, -1):
            hits = [t for key, t in ids.items() if key.startswith(ref[:n])]
            if len(hits) == 1:
                return hits[0]
    if not by_issue:
        return None
    issues = {int(n) for n in CLOSES.findall(body or "")}
    return next((t for t in tasks if t.github_issue_number in issues), None)
