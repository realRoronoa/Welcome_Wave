"""BM25 (Okapi; k1 1.2, b 0.75) over token lists, scored through postings so a query touches only the documents that hold
one of its words.

The offline keyword channel used to count shared words: "the" in a question counted as much as "apply_defaults". BM25 weighs
a word by how rare it is in the corpus (idf) and dampens repeats and long chunks, which is what makes a keyword list rank.

    idf(t)   = ln(1 + (N - df + 0.5) / (df + 0.5))        (never negative, unlike the original form)
    score(d) = sum over the query's distinct words t of idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / avg_len))
"""
from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Mapping, Sequence

K1, B = 1.2, 0.75


class BM25:
    """Built once over a corpus (a list of token lists, or of {token: count} maps); documents are numbered from 0."""

    def __init__(self, docs: Sequence[Sequence[str] | Mapping[str, int]], k1: float = K1, b: float = B):
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.postings: dict[str, list[tuple[int, int]]] = {}
        lengths: list[int] = []
        for i, doc in enumerate(docs):
            counts = doc if isinstance(doc, Mapping) else _count(doc)
            lengths.append(sum(counts.values()))
            for t, tf in counts.items():
                self.postings.setdefault(t, []).append((i, tf))
        avg = (sum(lengths) / self.n) if self.n else 0.0
        # the length part of the denominator, per document, computed once
        self.norm = [k1 * (1 - b + b * (ln / avg if avg else 0.0)) for ln in lengths]

    def idf(self, term: str) -> float:
        df = len(self.postings.get(term, ()))
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5)) if self.n else 0.0

    def scores(self, query: Iterable[str]) -> dict[int, float]:
        """Document number -> score, for the documents that hold at least one of the query's words."""
        out: dict[int, float] = {}
        k1 = self.k1
        for t in dict.fromkeys(query):
            post = self.postings.get(t)
            if not post:
                continue
            idf = self.idf(t)
            for i, tf in post:
                out[i] = out.get(i, 0.0) + idf * tf * (k1 + 1) / (tf + self.norm[i])
        return out

    def top(self, query: Iterable[str], n: int) -> list[tuple[int, float]]:
        """The n best (document, score), best first; ties by document number."""
        return heapq.nsmallest(n, ((i, s) for i, s in self.scores(query).items()), key=lambda x: (-x[1], x[0]))


def _count(tokens: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tokens:
        out[t] = out.get(t, 0) + 1
    return out
