"""KT-to-Code Linking: connect moments in a KT recording to the exact code that was on screen.

Input:  screen moments the Meeting agent saw (time, description, file paths and symbols visible on screen)
        plus the transcript around each moment.
Output: KTLink rows - "meeting M, 12:40-14:10, explains engine/validate.py lines 10-42 (validate_resource)".

Matching is deliberately layered from most to least certain, and every link carries its method and confidence
so the UI can show weak links differently and a senior can confirm them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.agents.core.schemas import KTLink, ScreenMoment, Utterance

DEFAULT_SPAN_S = 90.0
CONTEXT_S = 30.0
SEMANTIC_MIN = 0.35


@dataclass
class CodeRef:
    path: str
    symbol: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    chunk_id: str | None = None


class CodeIndex(Protocol):
    def all_paths(self, project_id: str) -> list[str]: ...
    def symbols_named(self, project_id: str, name: str) -> list[CodeRef]: ...
    def symbols_in(self, project_id: str, path: str) -> list[CodeRef]: ...
    def semantic_code(self, project_id: str, text: str, k: int = 3) -> list[tuple[CodeRef, float]]: ...


_LINE_SUFFIX = re.compile(r"[:#]L?\d+(?:-L?\d+)?$")


def clean_path(raw: str) -> str:
    p = raw.strip().strip("`'\"").replace("\\", "/")
    p = _LINE_SUFFIX.sub("", p)
    return p.lstrip("./")


def match_path(raw: str, paths: list[str]) -> tuple[str, float] | None:
    """Screen text is partial: an editor tab shows `validate.py`, a breadcrumb shows `engine/validate.py`."""
    p = clean_path(raw)
    if not p:
        return None
    if p in paths:
        return p, 0.97
    suffix = [x for x in paths if x.endswith("/" + p)]
    if len(suffix) == 1:
        return suffix[0], 0.92 if "/" in p else 0.85
    if len(suffix) > 1:  # ambiguous basename: keep the shortest, lower confidence
        return sorted(suffix, key=len)[0], 0.55
    return None


def _nearby_speech(utterances: list[Utterance], at_s: float) -> str:
    return " ".join(u.text for u in utterances if at_s - CONTEXT_S <= u.start_s <= at_s + CONTEXT_S)


def link_screen_moments(
    project_id: str,
    meeting_id: str,
    meeting_title: str,
    moments: list[ScreenMoment],
    utterances: list[Utterance],
    index: CodeIndex,
    meeting_end_s: float | None = None,
) -> list[KTLink]:
    paths = index.all_paths(project_id)
    moments = sorted(moments, key=lambda m: m.at_s)
    raw_links: list[KTLink] = []

    for i, m in enumerate(moments):
        next_at = moments[i + 1].at_s if i + 1 < len(moments) else (meeting_end_s or m.at_s + DEFAULT_SPAN_S)
        end_s = max(m.at_s + 5, min(next_at, m.at_s + 5 * 60))
        found: list[tuple[CodeRef, float, str]] = []

        # 1) a file path is visible on screen
        for raw in m.visible_paths:
            hit = match_path(raw, paths)
            if hit:
                path, conf = hit
                syms = [s for s in index.symbols_in(project_id, path) if s.symbol in set(m.visible_symbols)]
                if syms:  # path and function both visible: link to the function's lines
                    found += [(s, min(0.99, conf + 0.02), "path") for s in syms]
                else:
                    found.append((CodeRef(path=path), conf, "path"))

        # 2) only a function / class name is visible
        if not found:
            for name in m.visible_symbols:
                refs = index.symbols_named(project_id, name)
                if len(refs) == 1:
                    found.append((refs[0], 0.88, "symbol"))
                elif refs:
                    found.append((refs[0], 0.6, "symbol"))

        # 3) nothing readable on screen: match what is shown and said to code by meaning
        if not found:
            query = f"{m.description} {_nearby_speech(utterances, m.at_s)}"
            for ref, sim in index.semantic_code(project_id, query, k=1):
                if sim >= SEMANTIC_MIN:
                    found.append((ref, round(min(0.75, sim), 3), "semantic"))

        for ref, conf, method in found:
            raw_links.append(
                KTLink(
                    meeting_id=meeting_id,
                    meeting_title=meeting_title,
                    file_path=ref.path,
                    symbol=ref.symbol,
                    chunk_id=ref.chunk_id,
                    start_line=ref.start_line,
                    end_line=ref.end_line,
                    start_s=m.at_s,
                    end_s=end_s,
                    confidence=conf,
                    method=method,  # type: ignore[arg-type]
                )
            )
    return merge_adjacent(raw_links)


def merge_adjacent(links: list[KTLink], gap_s: float = 20.0) -> list[KTLink]:
    """The same code shown across consecutive moments becomes one longer clip."""
    merged: list[KTLink] = []
    for link in sorted(links, key=lambda l: (l.file_path, l.symbol or "", l.start_s)):
        prev = merged[-1] if merged else None
        if prev and prev.file_path == link.file_path and prev.symbol == link.symbol and link.start_s - prev.end_s <= gap_s:
            prev.end_s = max(prev.end_s, link.end_s)
            prev.confidence = max(prev.confidence, link.confidence)
        else:
            merged.append(link.model_copy())
    return sorted(merged, key=lambda l: l.start_s)
