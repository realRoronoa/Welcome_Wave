"""Parse GitHub per-file patches so review findings can be checked against real changed lines."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<len>\d+))? @@")
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec|_spec)\.\w+$|(^|/)test_[^/]+$")


@dataclass
class FileDiff:
    path: str
    status: str = "modified"  # added | modified | removed | renamed
    added: dict[int, str] = field(default_factory=dict)  # new line number -> text
    context: dict[int, str] = field(default_factory=dict)
    additions: int = 0
    deletions: int = 0

    @property
    def is_test(self) -> bool:
        return bool(TEST_PATH.search(self.path))

    def has_line(self, line: int) -> bool:
        return line in self.added or line in self.context

    def contains(self, snippet: str) -> bool:
        snippet = " ".join(snippet.split())
        if not snippet:
            return False
        body = " ".join(" ".join(list(self.added.values()) + list(self.context.values())).split())
        return snippet in body


def parse_patch(path: str, patch: str | None, status: str = "modified") -> FileDiff:
    fd = FileDiff(path=path, status=status)
    new_line = 0
    for raw in (patch or "").splitlines():
        m = HUNK.match(raw)
        if m:
            new_line = int(m.group("start"))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            fd.added[new_line] = raw[1:]
            fd.additions += 1
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            fd.deletions += 1
        elif raw.startswith("\\"):
            continue
        else:
            fd.context[new_line] = raw[1:] if raw.startswith(" ") else raw
            new_line += 1
    return fd


def parse_pr_files(files: list[dict]) -> dict[str, FileDiff]:
    """`files` is the GitHub `GET /repos/{o}/{r}/pulls/{n}/files` response."""
    return {f["filename"]: parse_patch(f["filename"], f.get("patch"), f.get("status", "modified")) for f in files}
