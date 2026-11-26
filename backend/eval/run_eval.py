"""Evaluation harness. Run: `python -m eval.run_eval` (demo data) - CI fails if a metric drops below target.

Retrieval is measured always. Answer quality (honesty, citations) is measured only with a real model
(GEMINI_API_KEY or MODE=gcp), because the offline stand-in LLM is not meant to be smart.
Set LANGSMITH_TRACING=true and LANGSMITH_API_KEY to trace every evaluated run in LangSmith.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import get_settings
from app.container import build_container
from app.seed import PROJECT

TARGETS = {"recall_at_5": 0.80, "honesty": 0.90, "citation_validity": 0.95}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> int:
    settings = get_settings()
    c = build_container(settings)
    cases = load(Path(__file__).with_name("golden_set.jsonl"))
    real_model = bool(settings.gemini_api_key) or settings.mode == "gcp"

    hits_ok, answerable = 0, [x for x in cases if x["answerable"]]
    for case in answerable:
        hits = c.index.search(PROJECT, case["q"], k=5)
        paths = {h.chunk.ref.get("path") for h in hits if h.chunk.source_type == "code"}
        if paths & set(case["expect"]):
            hits_ok += 1
        else:
            print(f"  miss: {case['q']}  got {sorted(p for p in paths if p)}")
    metrics = {"recall_at_5": hits_ok / len(answerable)}

    if real_model:
        honest = valid = cited = 0
        unanswerable = [x for x in cases if not x["answerable"]]
        for case in unanswerable:
            out = c.handle({"type": "chat.question", "project_id": PROJECT, "user_id": "u-vijay", "question": case["q"]})
            honest += out["result"]["status"] == "not_enough_information"
        for case in answerable:
            out = c.handle({"type": "chat.question", "project_id": PROJECT, "user_id": "u-vijay", "question": case["q"]})
            for cit in out["result"].get("citations", []):
                cited += 1
                valid += bool(cit["chunk_id"]) and (cit["ref"].get("path") in case["expect"] or cit["source_type"] != "code")
        metrics["honesty"] = honest / len(unanswerable)
        metrics["citation_validity"] = valid / cited if cited else 0.0

    failed = False
    for k, v in metrics.items():
        ok = v >= TARGETS[k]
        failed |= not ok
        print(f"{k:20s} {v:6.2f}  target {TARGETS[k]:.2f}  {'PASS' if ok else 'FAIL'}")
    if not real_model:
        print("(answer metrics skipped: no real model configured)")
    # With LANGSMITH_TRACING=true every chat run above is traced in LangSmith, so a failing case can be opened step by step.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
