"""Live evaluation of the agentic Guide with a REAL Gemini key (never in CI): the Guide's tier-1 attempt on questions about
the connected repository, plus off-topic ones it must refuse. Reports the answered rate, the citation guard's pass rate,
average steps and latency, and the steps of one run.

    cd backend && .venv/bin/python -m eval.agentic_eval <state.json> [out.json] [--only N] [--gap S]

<state.json> names the test organisation's project ({"project_id": ...}); the repository must be indexed already. The
settings come from backend/selfhost.env, read into this process's environment (the key is never printed or logged).
The eval only reads: it runs the Guide's attempt (search, the agent loop, VERIFY, the guard) and saves no chat turn,
request or metric. Questions are spaced (--gap, default 6 s) so the free tier's per-minute limits hold."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]

ON_TOPIC = [
    "How does solve.py compute the boundary for a village?",
    "What does validate.py check?",
    "How do I run the whole pipeline end to end?",
    "What does baseline_translation.py do?",
    "Which steps does run_all.sh run, in order?",
    "What is in the qa folder and what is it for?",
    "How is the result for Malatavadi produced?",
    "What Python packages does the project need?",
    "What does the submission checklist require?",
    "Where are transcripts stored and what are they used for?",
]
OFF_TOPIC = [
    "What is the capital of France?",
    "Write me a haiku about autumn leaves.",
]


def load_env(path: Path) -> None:
    """KEY=VALUE lines into os.environ (comments and blanks skipped; quotes stripped). Nothing is printed."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in {'"', "'"} and v[0] in v[1:]:  # quoted, as the shell reads it
            v = v[1:v.index(v[0], 1)]
        else:  # unquoted: an inline comment starts at whitespace and '#'
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        os.environ[k.strip().removeprefix("export ").strip()] = v


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    opts = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("--") and not argv[i + 1].startswith("--")}
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and not (i and argv[i - 1] in {"--only", "--gap", "--ask"})]
    if not args:
        print(__doc__)
        return 2
    state = json.loads(Path(args[0]).read_text())
    out_path = Path(args[1]) if len(args) > 1 else None
    load_env(HERE / "selfhost.env")
    os.environ.setdefault("REQUIRE_VERIFIED_EMAIL", "false")
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    os.environ.setdefault("TICK_EVERY_S", "0")
    os.environ.setdefault("REPO_POLL_S", "0")
    os.environ.setdefault("RECORDING_POLL_S", "0")
    logging.basicConfig(level=logging.WARNING)
    for noisy in ("httpx", "httpcore", "google", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    for ours in ("llm", "agents.runtime"):  # turn failures (model, error class; never a key or a prompt) when --verbose
        logging.getLogger(ours).setLevel(logging.WARNING if "--verbose" in argv else logging.ERROR)
    sys.path.insert(0, str(HERE))

    from app.adapters.postgres import current_org
    from app.agents.core import registry as R
    from app.agents.core.runtime import turns_today
    from app.agents.specialists.guide import agent as guide
    from app.config import get_settings
    from app.container import build_container

    settings = get_settings()
    if not settings.gemini_api_key:
        print("No Gemini key in selfhost.env: this eval needs the real model.")
        return 2
    c = build_container(settings, serves_http=False)
    pid = state["project_id"]
    org = c.store.org_of_project(pid)
    current_org.set(org)
    user = c.store.user_by_email(org, state["email"]) if state.get("email") else None
    user = user or next(u for u in c.store.members(pid) if u.role in {"admin", "manager"})
    ctx = R.lifecycle_ctx(R.deps_for(c.deps, org, "guide"), org, user)
    print(f"models: loop={settings.model_smart if guide.GUIDE_SMART else settings.model_fast} verify={settings.model_fast} "
          f"fallback={settings.model_fallback}")
    questions = [(q, True) for q in ON_TOPIC] + [(q, False) for q in OFF_TOPIC]
    if "--only" in opts:
        questions = questions[: int(opts["--only"])]
    if "--ask" in opts:  # one question of the lists by its number (1-based), to look at it again
        questions = [questions[int(opts["--ask"]) - 1]]
    gap = float(opts.get("--gap", 6))
    rows = []
    for i, (q, on_topic) in enumerate(questions):
        if i:
            time.sleep(gap)
        steps: list = []
        t0 = time.time()
        try:
            ai = guide.attempt(ctx, pid, q, False, None, steps, None)
            err = None
        except Exception as e:  # noqa: BLE001 - one failed question is reported, the eval goes on
            ai, err = {}, repr(e)[:200]
        secs = round(time.time() - t0, 1)
        out = ai.get("outcome")
        answered = bool(ai.get("result"))
        check = ai.get("check")
        row = {"q": q, "on_topic": on_topic, "answered": answered, "agentic": bool(ai.get("agentic")), "secs": secs,
               "tool_calls": out.tool_calls if out else None, "turns": out.turns if out else None, "tokens": out.tokens if out else None,
               "stopped": out.stopped if out else None, "run_error": out.error if out else None, "guard_ran": check is not None, "guard_ok": bool(check and check["ok"]),
               "repaired": any("rewritten once" in w for w in ai.get("why", [])), "why": ai.get("why"), "error": err,
               "answer": ai["result"].answer if answered else None,
               "citations": [x.label for x in ai["result"].citations] if answered else [],
               "route": ai.get("route"), "steps": [s.public() for s in steps]}
        rows.append(row)
        mark = "answered" if answered else "refused " if not on_topic else "no      "
        print(f"{secs:>5}s  {mark}  tools={row['tool_calls']} turns={row['turns']} {row['stopped'] or ''}  {q}", flush=True)
    on = [r for r in rows if r["on_topic"]]
    off = [r for r in rows if not r["on_topic"]]
    drafted = [r for r in rows if r["guard_ran"]]
    agentic = [r for r in rows if r["agentic"]]
    summary = {
        "on_topic_answered": f"{sum(r['answered'] for r in on)}/{len(on)}",
        "off_topic_refused": f"{sum(not r['answered'] for r in off)}/{len(off)}",
        "citation_guard_pass": f"{sum(r['guard_ok'] for r in drafted)}/{len(drafted)}",
        "repaired": sum(r["repaired"] for r in rows),
        "agentic_runs": f"{len(agentic)}/{len(rows)}",
        "avg_tool_calls": round(sum(r["tool_calls"] or 0 for r in agentic) / max(1, len(agentic)), 1),
        "avg_turns": round(sum(r["turns"] or 0 for r in agentic) / max(1, len(agentic)), 1),
        "avg_secs": round(sum(r["secs"] for r in rows) / max(1, len(rows)), 1),
        "max_secs": max((r["secs"] for r in rows), default=0),
        "model_turns_today": turns_today(org),
    }
    print(json.dumps(summary, indent=1))
    example = next((r for r in rows if r["answered"] and r["tool_calls"]), next(iter(rows), None))
    if example:
        print(f"\nOne run: {example['q']}")
        for s in example["steps"]:
            print(f"  {s['n']:>2}. [{s['kind']}] {s['text']}" + (f"  ({s['ms']} ms)" if s["ms"] else ""))
    if out_path:
        out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
