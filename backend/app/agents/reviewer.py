"""The Reviewer: reviews pull requests (through the Supervisor's review approval) and, privately, the hire's own branch before
they open one (event `presubmit.requested`).

It thinks (app/agents/thinking.py): it reads the change (`pr_diff`: the changed files, then each file's numbered lines),
explores around it (read_file, find_symbol, tests_for, search_knowledge for the guidelines, kt_moments for decisions the team
explained on recorded calls, such as "we never raise in validation", verified_answers) and writes findings, each citing a
changed line and the code, test or KT moment that backs it.

Guardrails, in code (app/agents/review.conclude, the same as the one-call review): a finding is kept only if its file is in the
change, its line is a changed or context line, and its evidence is text of the diff; the rule checks (tests, CI, size) always
run; a serious finding makes it needs_work, and needs_work with nothing backed becomes not_enough_information. `pr_diff` reads
only the change under review (our code puts it in the run; RepoSource is read-only). The manager decides the review.

The private review's result goes back to the hire only: their stream and their response carry its steps; its run record says
that a private review ran and for whom, nothing else (no file, no finding, no step; docs/AGENTS.md, privacy of run records).

Fallback: with no loop (the AI off, the offline demo, "rules" mode, the daily budget) or no answer, the one-call review
(app/agents/review.py); in "rules" mode, the rule checks without the model, as the pre-submit review is with the AI off."""
from __future__ import annotations

import re

from app.agents import prompts
from app.agents import registry as R
from app.agents import review
from app.agents.registry import RulesMode
from app.agents.schemas import AgentReview, Finding, ReviewDraft, ReviewResult
from app.services.diff import parse_pr_files

KEY = "reviewer"
PRIVATE_SUMMARY = "A private review ran."
RULES_SUMMARY = ("The Reviewer is set to rules: only the rule checks (tests, CI, size) ran. A manager reads the change before "
                 "deciding.")
TOOLS = ("pr_diff", "read_file", "find_symbol", "tests_for", "kt_moments", "search_knowledge", "verified_answers")


def think_review(deps, project_id: str, task, files: list[dict], checks: list[dict], user=None, run_id: str | None = None):
    """The Reviewer's loop over a change: (the checked ReviewResult or None, the Thought), or None when no loop could run or the
    change has no file to read (the one-call review says so)."""
    from app.agents import thinking
    from app.agents.llm import embed_data
    from app.agents.tools import REGISTRY

    diffs = parse_pr_files(files)
    if not diffs:
        return None
    org = R.org_of(deps, project_id)
    data = {"task": {"title": task.title, "description": task.description, "acceptance_criteria": task.acceptance_criteria},
            "changed_files": sorted(diffs)[:40], "ci_checks": [{"name": c.get("name"), "conclusion": c.get("conclusion")} for c in checks]}
    t = thinking.think(deps, KEY, org_id=org, project_id=project_id, user=user, system=prompts.REVIEWER_AGENT,
                       task="Review this pull request against its task. Start with pr_diff.\n\n" + embed_data(data),
                       tools=REGISTRY.select(*TOOLS), output=AgentReview, run_id=run_id, max_steps=8, timeout_s=45,
                       notes={"pr_files": files})
    if t is None:
        return None
    if t.output is None:
        return None, t
    out: AgentReview = t.output
    findings = []
    for f in out.findings[:20]:
        backed = [s["label"] for s in t.sources(f.sources)][:2]
        message = " ".join(f.message.split())[:600] + (f" (See {'; '.join(backed)}.)" if backed else "")
        path, evidence = anchor(f.file, f.line, f.evidence, diffs)
        findings.append(Finding(file=path, line=f.line, severity=f.severity, message=message, evidence=evidence, source="llm"))
    draft = ReviewDraft(recommendation=out.recommendation, summary=" ".join(out.summary.split())[:1200],
                        questions=[q[:300] for q in out.questions[:3]])
    result = review.conclude(task, diffs, checks, draft, findings)
    if result.dropped_findings:
        t.note("verify", f"Dropped {result.dropped_findings} finding{'s' if result.dropped_findings != 1 else ''} that did not cite a "
                         "changed line", shared="Dropped findings that did not cite a changed line")
    t.note("final", f"Recommended {result.recommendation.replace('_', ' ')}, {len(result.findings)} finding"
                    f"{'s' if len(result.findings) != 1 else ''}", shared="Finished the review")
    return result, t


PREFIX = re.compile(r"^[+\- ]?\s*\d*\s*\|\s?")  # pr_diff's "+   31 | " in front of a line


def anchor(file: str, line: int | None, evidence: str, diffs: dict) -> tuple[str, str]:
    """A finding's file and evidence as the diff has them: the path without a diff's a/ or b/ (or the one changed file it ends
    with), and, when its line is a real changed or context line, that line's exact text. The line is the citation; validate_findings
    then keeps only findings on real lines of the change."""
    path = (file or "").strip().lstrip("./")
    if path not in diffs and path[:2] in {"a/", "b/"} and path[2:] in diffs:
        path = path[2:]
    if path not in diffs:
        ends = [p for p in diffs if p.endswith("/" + path)]
        path = ends[0] if len(ends) == 1 else path
    text = PREFIX.sub("", (evidence or "").strip())
    d = diffs.get(path)
    if d is not None and line is not None and d.has_line(line) and not d.contains(text):
        text = d.added.get(line) or d.context.get(line, "")
    return path, text


def review_pr(deps, project_id: str, task, full_name: str, installation_id, pr_number: int, head_sha: str) -> ReviewResult:
    """The review of a pull request (see review_pr_traced)."""
    return review_pr_traced(deps, project_id, task, full_name, installation_id, pr_number, head_sha)[0]


def review_pr_traced(deps, project_id: str, task, full_name: str, installation_id, pr_number: int, head_sha: str):
    """(the review, the Reviewer's Thought or None): the agent loop's review, else the one-call review with the Reviewer's mode
    (in "rules" mode the model is not asked, and the rule checks are the review)."""
    files = deps.repos.pr_files(full_name, pr_number, installation_id)
    checks = deps.repos.check_runs(full_name, head_sha, installation_id)
    thought = think_review(deps, project_id, task, files, checks)
    if thought is not None and thought[0] is not None:
        return thought
    t = thought[1] if thought is not None else None
    mdeps = R.deps_for(deps, R.org_of(deps, project_id), KEY)
    try:
        return review.review_pr(mdeps, project_id, task, full_name, installation_id, pr_number, head_sha, files, checks), t
    except RulesMode:
        findings = review.rule_findings(task, parse_pr_files(files), checks)
        serious = any(f.severity in {"blocker", "major"} for f in findings)
        return ReviewResult(recommendation="needs_work" if serious else "not_enough_information", summary=RULES_SUMMARY,
                            findings=findings, questions=review.DEFAULT_QUESTIONS), t


def presubmit(deps, ev: dict) -> dict:
    """presubmit.requested {project_id, user_id, task_id, diff?, pr_number?, run_id?}: the hire's private review. The result may
    carry `trace`, the run record's part, which keeps only who it was for (guarded() takes it off)."""
    from app.lifecycle import presubmit as P

    user = deps.store.get_user(ev["user_id"])
    ctx = R.lifecycle_ctx(R.deps_for(deps, user.org_id, KEY), user.org_id, user)
    task = deps.store.get_task(ev["task_id"])
    files = P.change_files(ctx, task, ev.get("diff"), ev.get("pr_number"))
    thought = think_review(deps, ev["project_id"], task, files, [], user=user, run_id=ev.get("run_id"))
    if thought is not None and thought[0] is not None:
        result, t = thought
        why = ["The Reviewer read your change and the code around it; the rule checks ran too.",
               "Only you can see this review; it is not stored and your manager never sees it."]
        out = {"task_id": task.id, "private": True, "files": [f["filename"] for f in files], **result.model_dump(), "why": why}
        out["explain"] = R.explain(KEY, why, [R.source(f, "code", path=f) for f in out["files"][:10]] + t.found()[:4], None, True)
        return {**out, "run_id": t.run_id, "steps": t.public(), "trace": t.trace(private=True)}
    out = P.run(ctx, task, ev.get("diff"), ev.get("pr_number"), files)
    out["explain"] = R.explain(KEY, out.get("why", []), [R.source(f, "code", path=f) for f in out.get("files", [])[:10]], None,
                               "rule checks" not in out.get("summary", "") and R.mode(deps, user.org_id, KEY) == "ai")
    if thought is not None:  # the loop ran but gave no answer: the hire still sees what it did
        t = thought[1]
        return {**out, "run_id": t.run_id, "steps": t.public(), "trace": t.trace(private=True)}
    return out
