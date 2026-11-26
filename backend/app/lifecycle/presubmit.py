"""Pre-submit review: the hire runs the Review agent on their own change before anyone else sees it.

POST /api/tasks/{id}/presubmit-review takes the branch's diff (`git diff main...HEAD`) or a draft pull request's number.
It is the same Review agent as a pull request gets (evidence-checked findings, rule checks for tests and size), but its
result goes to the hire only: it is not stored, not audited with its content, not in Approvals, and never shown to a
manager. With the AI off (or down), the rule checks still run and say that the model's part is missing."""
from __future__ import annotations

import dataclasses
import re

from app.agents.core.llm import LLMError
from app.agents.core.schemas import ReviewResult
from app.agents.specialists.reviewer import review
from app.lifecycle.context import Ctx
from app.ports import AIUnavailable, TaskRow
from app.services.diff import parse_pr_files

MAX_DIFF = 400_000
_FILE = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.M)


def files_from_diff(diff: str) -> list[dict]:
    """`git diff` output as GitHub's pull request files list: filename, status and the per-file patch."""
    out = []
    starts = list(_FILE.finditer(diff[:MAX_DIFF]))
    for i, m in enumerate(starts):
        body = diff[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(diff)]
        hunk = body.find("\n@@")
        head = body[:hunk if hunk > 0 else None]
        status = "added" if "\nnew file mode" in head else "removed" if "\ndeleted file mode" in head else "modified"
        out.append({"filename": m.group(2), "status": status, "patch": body[hunk + 1:] if hunk >= 0 else ""})
    return out


class _Change:
    """A RepoSource stand-in that serves the hire's change as the pull request the Review agent reads."""

    def __init__(self, files: list[dict]):
        self.files = files

    def pr_files(self, full_name, number, installation_id=None):
        return self.files

    def check_runs(self, full_name, sha, installation_id=None):
        return []


def change_files(ctx: Ctx, task: TaskRow, diff: str | None, pr_number: int | None) -> list[dict]:
    """The hire's change as a pull request's files: their pasted diff, or the task's own pull request (never anyone else's)."""
    repo = next(iter(ctx.store.repos_for(ctx.store.get_onboarding(task.onboarding_id).project_id)), None)
    if diff:
        return files_from_diff(diff)
    if pr_number and repo and pr_number == task.pr_number:
        return ctx.deps.repos.pr_files(repo.full_name, pr_number, repo.installation_id)
    return []


def run(ctx: Ctx, task: TaskRow, diff: str | None, pr_number: int | None, files: list[dict] | None = None) -> dict:
    deps = ctx.deps
    repo = next(iter(ctx.store.repos_for(ctx.store.get_onboarding(task.onboarding_id).project_id)), None)
    files = change_files(ctx, task, diff, pr_number) if files is None else files
    private = dataclasses.replace(deps, repos=_Change(files))
    project_id = ctx.store.get_onboarding(task.onboarding_id).project_id
    try:
        result = review.review_pr(private, project_id, task, repo.full_name if repo else "", None, pr_number or 0, "")
        why = ["The Review agent read your change; the rule checks ran too."]
    except (AIUnavailable, LLMError):
        diffs = parse_pr_files(files)
        findings = review.rule_findings(task, diffs, [])
        serious = any(f.severity in {"blocker", "major"} for f in findings)
        result = ReviewResult(recommendation="needs_work" if serious else "not_enough_information",
                              summary="The AI is not available: only the rule checks (tests, size) ran.",
                              findings=findings, questions=review.DEFAULT_QUESTIONS)
        why = ["The AI is not available: rule checks only."]
    return {"task_id": task.id, "private": True, "files": [f["filename"] for f in files], **result.model_dump(),
            "why": why + ["Only you can see this review; it is not stored and your manager never sees it."]}
