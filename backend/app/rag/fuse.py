"""Weighted reciprocal-rank fusion (k = 60): many ranked lists into one, without comparing their scores.

    score(id) = sum over the lists that hold id of weight(list) / (k + rank of id in that list)

Ranks start at 1. A vector list's cosines and a BM25 list's scores live on different scales; RRF needs only their order, so
the crew fuses every (query, kind) list and the exact-name list the same way. The same id twice in one list counts once, at
its best rank."""
from __future__ import annotations

from collections.abc import Iterable, Sequence

RRF_K = 60


def rrf(lists: Iterable[tuple[Sequence[str], float]], k: int = RRF_K) -> dict[str, float]:
    """lists: (ids best first, weight). Returns id -> fused score."""
    out: dict[str, float] = {}
    for ids, weight in lists:
        if weight <= 0:
            continue
        seen: set[str] = set()
        rank = 0
        for cid in ids:
            if cid in seen:
                continue
            seen.add(cid)
            rank += 1
            out[cid] = out.get(cid, 0.0) + weight / (k + rank)
    return out


def ranked(scores: dict[str, float]) -> list[str]:
    """Ids best first; equal scores in id order, so the same input always gives the same list."""
    return sorted(scores, key=lambda cid: (-scores[cid], cid))
