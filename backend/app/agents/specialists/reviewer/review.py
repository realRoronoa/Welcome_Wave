"""Review agent: checks a PR against its task. LLM findings must carry evidence that exists in the diff;
rule-based checks (tests present, CI green, size) run regardless of the model."""
from __future__ import annotations

from app.agents.core import prompts
from app.agents.core.deps import Deps
from app.agents.core.llm import embed_data
from app.agents.core.schemas import Finding, ReviewDraft, ReviewResult
from app.ports import TaskRow
from app.services.diff import FileDiff, parse_pr_files

NO_DIFF = ("The review agent could not read any changed files in this change, so it could not judge the change. "
           "Check the pull request on GitHub before deciding.")

DEFAULT_QUESTIONS = [
    "In one sentence, what problem does your change solve?",
    "How did you check that it works, and what did you not test?",
    "Which other part of the code could be affected by this change?",
]


def validate_findings(findings: list[Finding], diffs: dict[str, FileDiff]) -> tuple[list[Finding], int]:
    kept, dropped = [], 0
    for f in findings:
        d = diffs.get(f.file)
        if d is None:
            dropped += 1
            continue
        line_ok = f.line is not None and d.has_line(f.line)
        evidence_ok = bool(f.evidence) and d.contains(f.evidence)
        if line_ok and not f.evidence:
            f = f.model_copy(update={"evidence": d.added.get(f.line) or d.context.get(f.line, "")})
            evidence_ok = True
        if (f.line is None or line_ok) and evidence_ok:
            kept.append(f)
        else:
            dropped += 1
    return kept, dropped


def rule_findings(task: TaskRow, diffs: dict[str, FileDiff], checks: list[dict]) -> list[Finding]:
    out = []
    code = [d for d in diffs.values() if not d.is_test and d.status != "removed" and not d.path.endswith(".md")]
    if task.kind == "code" and code and not any(d.is_test for d in diffs.values()):
        first = code[0]
        line = min(first.added) if first.added else None
        out.append(Finding(file=first.path, line=line, severity="major", source="rule",
                           message="No test files were added or changed.",
                           evidence=first.added.get(line, "") if line else ""))
    for c in checks:
        if c.get("conclusion") in {"failure", "timed_out", "cancelled"}:
            path = code[0].path if code else next(iter(diffs), "")
            out.append(Finding(file=path, severity="blocker", source="rule", message=f"CI check '{c.get('name')}' {c.get('conclusion')}."))
    total = sum(d.additions + d.deletions for d in diffs.values())
    if total > 800:
        out.append(Finding(file=next(iter(diffs)), severity="info", source="rule", message=f"Large change ({total} lines); consider splitting it."))
    return out


def review_pr(deps: Deps, project_id: str, task: TaskRow, full_name: str, installation_id: str | None, pr_number: int, head_sha: str,
              files: list[dict] | None = None, checks: list[dict] | None = None) -> ReviewResult:
    """The one-call review (structured output): the fallback of the Reviewer's agent loop
    (app/agents/specialists/reviewer/agent.py). `files` and `checks`: the pull request's, when the caller read them already."""
    files = deps.repos.pr_files(full_name, pr_number, installation_id) if files is None else files
    diffs = parse_pr_files(files)
    checks = deps.repos.check_runs(full_name, head_sha, installation_id) if checks is None else checks
    guide = deps.index.search(project_id, "contributing guidelines coding style tests review", k=3, source_types=["doc", "code"])
    data = {
        "task": {"title": task.title, "description": task.description, "acceptance_criteria": task.acceptance_criteria},
        "files": [{"path": f["filename"], "status": f.get("status"), "patch": (f.get("patch") or "")[:6000]} for f in files[:30]],
        "ci_checks": [{"name": c.get("name"), "conclusion": c.get("conclusion")} for c in checks],
        "project_guidelines": [h.chunk.content[:1500] for h in guide],
    }
    draft = deps.llm.structured(embed_data(data), ReviewDraft, smart=True, system=prompts.REVIEW)
    return conclude(task, diffs, checks, draft, [f.model_copy(update={"source": "llm"}) for f in draft.findings])


def conclude(task: TaskRow, diffs: dict[str, FileDiff], checks: list[dict], draft: ReviewDraft, findings: list[Finding]) -> ReviewResult:
    """The review from a model's draft, checked in code: its findings only if they quote the diff (validate_findings), the rule
    findings always, and a recommendation no finding contradicts (serious findings: needs_work; needs_work with no evidence:
    not_enough_information; nothing read: not_enough_information)."""
    llm_findings, dropped = validate_findings(findings, diffs)
    rules = rule_findings(task, diffs, checks)
    findings = rules + llm_findings

    rec = draft.recommendation
    serious = any(f.severity in {"blocker", "major"} for f in findings)
    if serious:
        rec = "needs_work"
    elif rec == "needs_work":  # the model wanted changes but could not back them with evidence
        rec = "not_enough_information"
    summary = draft.summary
    if not diffs:  # nothing was read, so nothing can be judged: say so rather than imply the change is clean
        rec = "not_enough_information"
        summary = NO_DIFF

    questions = (draft.questions + DEFAULT_QUESTIONS)[:3]
    return ReviewResult(recommendation=rec, summary=summary, findings=findings, questions=questions, dropped_findings=dropped)
