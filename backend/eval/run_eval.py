"""The CI gate: `MODE=demo .venv/bin/python -m eval.run_eval`. Prints a PASS/FAIL table and exits 1 when a metric misses
its target.

Always measured, offline and deterministic (the demo's offline model: no key, no network, about 15 s):
- golden_set.jsonl: recall@5 of Index.search, the right file in the top 5 (the original gate).
- The Guide's research crew (app.rag.crew.retrieve: the rules' plan, the four retrievers, fusion and the lexical judge) on
  golden_demo.jsonl and golden_self.jsonl, every split: recall@5, MRR@10 and nDCG@10, as eval/rag_eval.py and
  eval/README.md define them.
- The Guide's tier-1 answers on the demo, end to end (guide.attempt with the offline writer): the refusal rate on the
  unanswerable questions, and the answered rate on the answerable ones, so that refusing everything cannot pass.
- The golden check: every expected source must still match a chunk of its corpus (a renamed function, an edited doc). Fix
  the case, not the metric. The demo's verified answer is reported, not counted: the seed keeps it in the lifecycle store
  only, so no index-based retriever can find it.

With a real model (GEMINI_API_KEY in the environment, or MODE=gcp) answer quality is measured as well: honesty on the
unanswerable questions of golden_set.jsonl and citation validity on the answerable ones. The crew's metrics need MODE=demo.

Targets are a regression gate, not a goal: each is the value measured when it was set, floored to two decimals, minus 0.03.
`--measure` prints the values and the targets they would give. The self corpus is Welcome Wave's own backend and its
product docs (not the working-status docs: rag_eval.WORKING_DOCS), so its numbers move a little with every change to
backend/app: refresh its targets when a large change lands, never to let a regression through. Set LANGSMITH_TRACING=true and LANGSMITH_API_KEY to trace every evaluated run in LangSmith.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import time
from pathlib import Path

EVAL = Path(__file__).resolve().parent

# The crew's and the end-to-end targets: measured on 2026-11-17 (offline, every split), floored to two decimals, minus 0.03;
# the ones marked 2026-11-18 were raised when the release verification measured them higher.
# The golden_set target is the original gate's (Index.search is no longer what the Guide's tier 1 uses).
TARGETS: dict[str, float] = {
    "golden_set index R@5": 0.80,  # measured 1.00
    "demo crew R@5": 0.90,  # measured 0.939 (2026-11-18)
    "demo crew MRR@10": 0.86,  # measured 0.899 (2026-11-18)
    "demo crew nDCG@10": 0.84,  # measured 0.876 (2026-11-18)
    "self crew R@5": 0.75,  # measured 0.788; 0.776 once the working-status docs left the corpus (2026-11-18)
    "self crew MRR@10": 0.60,  # measured 0.631 (2026-11-18, working-status docs left out)
    "self crew nDCG@10": 0.56,  # measured 0.599 (2026-11-18, working-status docs left out)
    "demo e2e refusal (unanswerable)": 0.97,  # measured 1.00
    "demo e2e answered (answerable)": 0.62,  # measured 0.653 (2026-11-18)
}
MAXIMA: dict[str, float] = {"golden sources missing": 0}  # at most this many
REAL_MODEL_TARGETS: dict[str, float] = {"honesty": 0.90, "citation_validity": 0.95}
MARGIN = 0.03


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def index_recall(container, project: str, cases: list[dict]) -> float:
    """The original gate: share of answerable golden_set questions whose top 5 Index.search hits include an expected file."""
    hits_ok, answerable = 0, [x for x in cases if x["answerable"]]
    for case in answerable:
        hits = container.index.search(project, case["q"], k=5)
        paths = {h.chunk.ref.get("path") for h in hits if h.chunk.source_type == "code"}
        if paths & set(case["expect"]):
            hits_ok += 1
        else:
            print(f"  miss: {case['q']}  got {sorted(p for p in paths if p)}")
    return hits_ok / len(answerable)


def answer_quality(container, project: str, cases: list[dict]) -> dict[str, float]:
    """With a real model: honesty (unanswerable questions get 'not enough information') and citation validity."""
    honest = valid = cited = 0
    unanswerable = [x for x in cases if not x["answerable"]]
    for case in unanswerable:
        out = container.handle({"type": "chat.question", "project_id": project, "user_id": "u-vijay", "question": case["q"]})
        honest += out["result"]["status"] == "not_enough_information"
    for case in (x for x in cases if x["answerable"]):
        out = container.handle({"type": "chat.question", "project_id": project, "user_id": "u-vijay", "question": case["q"]})
        for cit in out["result"].get("citations", []):
            cited += 1
            valid += bool(cit["chunk_id"]) and (cit["ref"].get("path") in case["expect"] or cit["source_type"] != "code")
    return {"honesty": honest / len(unanswerable), "citation_validity": valid / cited if cited else 0.0}


def crew_metrics(scratch: str) -> tuple[dict[str, float], list[str]]:
    """The crew's retrieval metrics on both golden sets (every split), the Guide's end-to-end rates on the demo, and the
    golden check. Offline: both corpora answer with the offline model, so the crew plans and judges by its rules alone."""
    from eval import rag_eval as R

    crew, why = R.load_crew()
    if crew is None:
        raise SystemExit(f"the research crew does not load ({why}): the gate cannot measure it")
    out: dict[str, float] = {}
    notes: list[str] = []
    missing = 0
    for name in ("demo", "self"):
        t0 = time.perf_counter()
        corpus = R.build_demo(False) if name == "demo" else R.build_self(False, scratch)
        cases = R.load_cases(EVAL / f"golden_{name}.jsonl")
        for problem in R.check_golden(corpus, cases):
            if "is in the lifecycle store, not in the index" in problem:
                notes.append(f"{name}: {problem} (reported, not counted)")
            else:
                missing += 1
                notes.append(f"{name}: {problem}")
        s = R.run_retrieval(corpus, "crew", R.retriever("crew", corpus, crew), cases, 0.0)["summary"]
        out[f"{name} crew R@5"], out[f"{name} crew MRR@10"], out[f"{name} crew nDCG@10"] = s["r5"], s["mrr"], s["ndcg"]
        if name == "demo":
            e2e = R.run_e2e(corpus, crew, [], cases, False, 0.0)
            key = next(k for k in e2e if k.startswith("guide"))
            if key != "guide [crew]":
                notes.append(f"demo: the Guide's tier 1 ran as {key}, not on the crew")
            out["demo e2e refusal (unanswerable)"] = e2e[key]["summary"]["refusal_rate"]
            out["demo e2e answered (answerable)"] = e2e[key]["summary"]["answered_rate"]
        notes.append(f"{name}: {s['cases']} answerable of {len(cases)} cases; crew p50 {s['p50_ms']:.0f} ms, p95 {s['p95_ms']:.0f} ms; "
                     f"{time.perf_counter() - t0:.1f} s")
    out["golden sources missing"] = missing
    return out, notes


def target_for(value: float) -> float:
    """The target a measured value sets: floored to two decimals, minus MARGIN."""
    return round(max(0.0, math.floor(value * 100 + 1e-9) / 100 - MARGIN), 2)


def report(metrics: dict[str, float], targets: dict[str, float]) -> bool:
    """The PASS/FAIL table; True when every metric meets its target."""
    width = max(len(k) for k in metrics) + 2
    print(f"\n{'metric':{width}}{'value':>8}{'target':>9}  result")
    print("-" * (width + 25))
    ok_all = True
    for name, value in metrics.items():
        if name in MAXIMA:
            ok, got, want = value <= MAXIMA[name], f"{value:g}", f"<= {MAXIMA[name]:g}"
        else:
            ok, got, want = value >= targets[name], f"{value:.2f}", f"{targets[name]:.2f}"
        ok_all &= ok
        print(f"{name:{width}}{got:>8}{want:>9}  {'PASS' if ok else 'FAIL'}")
    return ok_all


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.run_eval", description="The CI gate for retrieval and answers.")
    parser.add_argument("--measure", action="store_true", help="print each metric and the target it would set (floor - 0.03)")
    args = parser.parse_args(argv)
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ww-run-eval-") as scratch:
        os.environ.setdefault("DATA_DIR", scratch)  # runtime files go to a scratch folder, never into backend/
        os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
        logging.basicConfig(level=logging.WARNING)
        for noisy in ("httpx", "httpcore", "google", "urllib3", "llm", "agents.runtime", "rag.crew"):
            logging.getLogger(noisy).setLevel(logging.ERROR)

        from app.config import get_settings
        from app.container import build_container
        from app.seed import PROJECT

        settings = get_settings()
        real_model = bool(settings.gemini_api_key) or settings.mode == "gcp"
        container = build_container(settings)
        golden_set = load(EVAL / "golden_set.jsonl")
        metrics: dict[str, float] = {"golden_set index R@5": index_recall(container, PROJECT, golden_set)}
        notes: list[str] = []
        if settings.mode == "demo":
            crew, notes = crew_metrics(scratch)
            metrics.update(crew)
        else:
            notes.append(f"crew metrics skipped: they run on the demo corpus (MODE=demo), and MODE is {settings.mode}")
        targets = dict(TARGETS)
        if real_model:
            metrics.update(answer_quality(container, PROJECT, golden_set))
            targets.update(REAL_MODEL_TARGETS)

    for line in notes:
        print(f"  {line}")
    if args.measure:
        print("\nmeasured -> target (floored to two decimals, minus 0.03):")
        for name, value in metrics.items():
            if name not in MAXIMA:
                print(f'    "{name}": {target_for(value):.2f},   # measured {value:.3f}')
    ok = report(metrics, targets)
    if not real_model:
        print("(answer quality skipped: no real model configured)")
    print(f"total {time.perf_counter() - t0:.1f} s")
    # With LANGSMITH_TRACING=true every chat run above is traced in LangSmith, so a failing case can be opened step by step.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
