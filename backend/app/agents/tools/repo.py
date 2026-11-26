"""Repository tools: open issues, what changed recently, and the tests of a file. Read-only (RepoSource has no write
methods, and the index is only read); see __init__."""
from __future__ import annotations

import posixpath
import re
from collections import defaultdict

from pydantic import BaseModel, Field

from app.agents.runtime import REGISTRY, Source, ToolCtx, ToolResult, untrusted
from app.services.diff import TEST_PATH

PATHISH = re.compile(r"[\w./-]+\.\w{1,5}|[\w-]+/[\w./-]+")


# ---------------------------------------------------------------- issues
class IssuesArgs(BaseModel):
    labels: list[str] = Field(default_factory=list, max_length=5, description="Only issues with all of these labels")
    state: str = Field("open", pattern="^open$", description="Only open issues are listed")


@REGISTRY.tool("issues", "Open issues of the project's repositories that are labelled for newcomers (title, labels, the files they "
               "mention).", IssuesArgs, label="Read the open issues")
def issues(ctx: ToolCtx, args: IssuesArgs) -> ToolResult:
    deps = ctx.deps
    paths = set(deps.index.all_paths(ctx.project_id))
    want = {lbl.lower() for lbl in args.labels}
    rows, sources = [], []
    for repo in deps.store.repos_for(ctx.project_id):
        try:
            found = deps.repos.good_first_issues(repo.full_name, repo.installation_id)
        except Exception:  # noqa: BLE001 - one repository unreachable: the others still count
            continue
        for i in found:
            labels = [str(x) for x in i.get("labels", [])]
            if want and not want <= {x.lower() for x in labels}:
                continue
            text = f"{i.get('title', '')} {i.get('body') or ''}"
            files = sorted({m for m in PATHISH.findall(text) if m in paths or any(p.endswith("/" + m) for p in paths)})[:5]
            rows.append({"repo": repo.full_name, "number": i.get("number"), "title": str(i.get("title", ""))[:200], "labels": labels, "files": files})
            sources.append(Source(label=f"{repo.full_name}#{i.get('number')}", kind="issue", ref={"repo": repo.full_name, "number": i.get("number")}))
    return ToolResult(text=untrusted({"issues": rows[:20]}), sources=sources[:8], summary=f"Reading open issues: {len(rows)} found",
                      shared=f"Read the open issues ({len(rows)})")


# ---------------------------------------------------------------- recent_changes
class ChangesArgs(BaseModel):
    path: str | None = Field(None, max_length=400, description="A file or folder; empty: the whole project")
    days: int = Field(14, ge=1, le=90, description="How far back (the index keeps commits, not dates: used as a hint only)")


@REGISTRY.tool("recent_changes", "What changed in the code since it was last read: the files re-indexed at the newest commit, and the "
               "files whose KT explanation may be outdated because the code changed after the call. Authors are not recorded "
               "('unknown').", ChangesArgs, label="Read the recent changes")
def recent_changes(ctx: ToolCtx, args: ChangesArgs) -> ToolResult:
    deps = ctx.deps
    prefix = (args.path or "").strip().strip("/")

    def under(p: str) -> bool:
        return not prefix or p == prefix or p.startswith(prefix + "/")

    commits: dict[str, set[str]] = defaultdict(set)
    for c in deps.index.code_chunks(ctx.project_id, content=False):
        if under(c.ref.get("path", "")):
            commits[c.ref.get("commit") or ""].add(c.ref["path"])
    repos = [{"repo": r.full_name, "last_indexed_commit": (r.last_indexed_sha or "")[:12] or None} for r in deps.store.repos_for(ctx.project_id)]
    heads = {r.last_indexed_sha for r in deps.store.repos_for(ctx.project_id) if r.last_indexed_sha}
    latest = sorted({p for sha in heads for p in commits.get(sha, set())}) if len(commits) > 1 else []
    outdated = sorted({lk.file_path for lk in deps.store.kt_links(ctx.project_id) if lk.code_changed_since and under(lk.file_path)})
    return ToolResult(text=untrusted({"repositories": repos, "changed_at_latest_commit": latest[:40], "kt_explanations_outdated": outdated[:40],
                                      "authors": "unknown"}),
                      sources=[Source(label=f"Changed after its KT session: {p}", kind="code", ref={"path": p}) for p in outdated[:6]],
                      summary=f"Recent changes: {len(latest)} files at the newest commit, {len(outdated)} KT explanations possibly outdated",
                      shared=f"Read the recent changes ({len(latest)} files)")


# ---------------------------------------------------------------- tests_for
class TestsArgs(BaseModel):
    path: str = Field(min_length=1, max_length=400, description="A source file path")


@REGISTRY.tool("tests_for", "The test files related to a source file: tests named after it, and tests that mention its module.",
               TestsArgs, label="Found the related tests")
def tests_for(ctx: ToolCtx, args: TestsArgs) -> ToolResult:
    deps = ctx.deps
    path = args.path.strip().lstrip("./")
    stem = posixpath.splitext(posixpath.basename(path))[0]
    module = posixpath.splitext(path)[0].replace("/", ".")
    tests = [p for p in deps.index.all_paths(ctx.project_id) if TEST_PATH.search(p)]
    named = [p for p in tests if stem and re.search(rf"(^|[/_.-]){re.escape(stem)}([/_.-]|$)", posixpath.basename(p))]
    mentions = []
    for h in deps.index.search(ctx.project_id, f"{stem} {module}", k=8, source_types=["code"]):
        p = h.chunk.ref.get("path", "")
        if p in tests and p not in named and (stem in h.chunk.content or module in h.chunk.content):
            mentions.append(p)
    found = list(dict.fromkeys(named + mentions))[:10]
    return ToolResult(text=untrusted({"path": path, "tests": found, "named_after_it": named[:10]}),
                      sources=[Source(label=p, kind="code", ref={"path": p}) for p in found[:6]],
                      summary=f"Tests for {path}: {len(found)} found", shared=f"Found the related tests ({len(found)})")
