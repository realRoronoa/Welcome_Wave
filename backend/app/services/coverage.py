"""KT coverage map: how many minutes of KT explained each module, and which modules got none."""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from app.agents.core.schemas import KTLink
from app.services.chunking import module_of


class ModuleCoverage(BaseModel):
    module: str
    files: int
    files_explained: int
    kt_minutes: float
    meetings: list[str]

    @property
    def covered(self) -> bool:
        return self.files_explained > 0


def coverage_map(paths: list[str], links: list[KTLink], min_confidence: float = 0.5, depth: int = 2) -> list[ModuleCoverage]:
    files_by_module: dict[str, set[str]] = defaultdict(set)
    for p in paths:
        files_by_module[module_of(p, depth)].add(p)

    explained: dict[str, set[str]] = defaultdict(set)
    seconds: dict[str, float] = defaultdict(float)
    meetings: dict[str, set[str]] = defaultdict(set)
    for link in links:
        if link.confidence < min_confidence:
            continue
        mod = module_of(link.file_path, depth)
        explained[mod].add(link.file_path)
        seconds[mod] += max(0.0, link.end_s - link.start_s)
        meetings[mod].add(link.meeting_id)

    rows = [
        ModuleCoverage(
            module=m,
            files=len(files),
            files_explained=len(explained[m] & files),
            kt_minutes=round(seconds[m] / 60, 1),
            meetings=sorted(meetings[m]),
        )
        for m, files in files_by_module.items()
    ]
    return sorted(rows, key=lambda r: (r.kt_minutes, -r.files))


def gaps(rows: list[ModuleCoverage], ignore: tuple[str, ...] = ("(root)", "docs", "tests")) -> list[str]:
    return [r.module for r in rows if not r.covered and r.module.split("/")[0] not in ignore]
