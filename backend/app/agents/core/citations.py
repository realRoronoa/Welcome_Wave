"""How a source is named wherever it is cited: an answer's citations, an agent's trace, the research crew's evidence and
the context blocks a writer reads. One label per chunk, so the same source reads the same everywhere."""
from __future__ import annotations

from app.agents.core.schemas import Chunk


def label_for(c: Chunk) -> str:
    r = c.ref
    if c.source_type == "code":
        sym = f" ({c.symbol})" if c.symbol else ""
        return f"{r['path']}:L{r['start_line']}-L{r['end_line']}{sym}"
    if c.source_type == "doc":
        return f"{r['title']}, page {r['page']}"
    if c.source_type == "meeting":
        s = int(r["start_s"])
        return f"{r['title']} at {s // 60:02d}:{s % 60:02d}"
    by = r.get("expert_name")
    return f"Verified by {by}: {r.get('question', '')[:60]}" if by else f"Verified answer: {r.get('question', '')[:60]}"
