"""Live check of the thinking specialists (Scout, Planner, Coach, Keeper; docs/AGENTS.md) with the real model. Never in CI.

    cd backend && .venv/bin/python -m eval.agents_live_check <state.json> [--inventory]

It loads backend/selfhost.env into this process's environment (never printing a value), builds the self-hosted container,
and runs each agent's loop once on the project named in state.json ({"project_id": ...}): the Scout's shortlist, the Keeper on
a recent file (its doc-edit draft for a flagged file: read-only), the Planner's KT-session proposal (ask.free), and, when the project has an onboarding, the Planner's
30/60/90 plan and the Coach's assessment. Each run goes through the same code checks as in production, but nothing is stored,
proposed or sent: no approval, notification, metric or run record. It prints, per run: the steps (kind, tool, one line), the
latency, the turns and tokens, what the checks kept or dropped, and the output. About 5 agent runs, a few model turns each.

    cd backend && .venv/bin/python -m eval.agents_live_check --demo

The demo world (in memory: the sample repository and its seeded hires) with the real model, for what the test project lacks
(an onboarding with signals, a template, a branch to review): the Scout over real candidates, the Planner's 30/60/90 plan for
a new enrolment, the Coach's assessment of a hire, and the Reviewer's private review of a planted diff. The world is seeded
with the offline model (the seed asks no real model), then the real one takes over; nothing leaves the process."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> None:
    """KEY=VALUE lines into os.environ (values never printed); existing variables win."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in {'"', "'"} and value[0] in value[1:]:
            value = value[1:value.index(value[0], 1)]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # an inline comment, as the shell drops it
        os.environ.setdefault(key.strip(), value)


def show(step) -> None:
    tool = f" [{step.tool}]" if step.tool else ""
    print(f"    {step.n:>2} {step.kind:<6}{tool} {step.text[:150]}", flush=True)


def report(name: str, t, began: float, kept) -> dict:
    ms = round((time.monotonic() - began) * 1000)
    if t is None:
        print(f"  {name}: no loop ran (NoToolUse)\n")
        return {"agent": name, "ran": False}
    o = t.out
    print(f"  {name}: {o.stopped}, {len(o.steps)} steps, {o.tool_calls} tool calls, {o.turns} turns, {o.tokens} tokens, {ms} ms")
    print("  kept: " + json.dumps(kept, default=str, ensure_ascii=False)[:1500] + "\n", flush=True)
    return {"agent": name, "ran": True, "stopped": o.stopped, "steps": len(o.steps), "tool_calls": o.tool_calls, "turns": o.turns,
            "tokens": o.tokens, "ms": ms, "kept": kept}


def main(state_path: str, inventory_only: bool = False) -> None:
    load_env(ROOT / "selfhost.env")
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from app.adapters.postgres import current_org
    from app.agents.core import prompts, thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import CoachAssessment, DocEditDraft, ScoutShortlist
    from app.agents.specialists.planner import agent as planner
    from app.agents.specialists.scout import agent as scout
    from app.agents.tools import REGISTRY
    from app.config import get_settings
    from app.container import build_container
    from app.lifecycle import hires
    from app.lifecycle.context import Ctx

    get_settings.cache_clear()
    c = build_container(get_settings())
    pid = json.load(open(state_path))["project_id"]
    org = c.store.org_of_project(pid)
    current_org.set(org)
    deps = c.deps
    repos = c.store.repos_for(pid)
    obs = c.store.onboardings(pid)
    paths = c.index.all_paths(pid)
    links = c.store.kt_links(pid)
    issues = [i for r in repos for i in deps.repos.good_first_issues(r.full_name, r.installation_id)]
    print(f"model: {type(deps.llm).__name__}; repos: {[r.full_name for r in repos]}; indexed files: {len(paths)}; KT links: {len(links)}; "
          f"open newcomer issues: {len(issues)}; onboardings: {len(obs)}; programs: {sum(bool(c.lifecycle.get_program(o.id)) for o in obs)}\n")
    if inventory_only:
        return
    managers = c.store.managers(pid)
    me = managers[0] if managers else None
    out = []

    # ---- the Scout: a shortlist (with an onboarding, its candidates; without one, the open issues and the code)
    hc = None
    ob = next((o for o in obs if c.lifecycle.get_program(o.id)), None)
    if ob is not None:
        hc = hires.load(Ctx.of(c, me, org), ob.id)
    began = time.monotonic()
    if hc is not None:
        task = ("Shortlist at most 3 first tasks for this new hire. Start with first_task_candidates.\n\n"
                + embed_data({"onboarding_id": hc.ob.id, "focus_area": hc.program.focus_area, "role": hc.ob.role_title}))
        t = thinking.think(deps, "scout", org_id=org, project_id=pid, user=me, system=prompts.SCOUT_AGENT, task=task,
                           tools=REGISTRY.select(*scout.TOOLS), output=ScoutShortlist, max_steps=8, on_step=show,
                           notes={"onboarding_id": hc.ob.id})
        kept = scout.check_picks(deps, hc, t.output, t.sources) if t is not None and t.output is not None else None
    else:
        tools = [x for x in scout.TOOLS if x not in {"first_task_candidates", "program"}]
        task = ("There is no hire yet. Shortlist at most 3 first tasks a newcomer could take in this repository: open issues if "
                "there are any (candidate_id issue:<number>), else small, tested changes you find in the code (candidate_id "
                "code:<path>). Read the code first.\n\n" + embed_data({"open_issues": len(issues)}))
        t = thinking.think(deps, "scout", org_id=org, project_id=pid, user=me, system=prompts.SCOUT_AGENT, task=task,
                           tools=REGISTRY.select(*tools), output=ScoutShortlist, max_steps=6, on_step=show)
        files, _ = thinking.known_paths(deps, pid)
        numbers = {f"issue:{i['number']}" for i in issues}
        kept = None
        if t is not None and t.output is not None:
            kept = [{"candidate_id": p.candidate_id, "real": p.candidate_id in numbers or p.candidate_id.removeprefix("code:") in files,
                     "reason": p.reason, "related_paths": thinking.real_paths(p.related_paths, files),
                     "dropped_paths": [x for x in p.related_paths if x not in files], "sources": t.sources(p.sources)} for p in t.output.picks]
    out.append(report("scout", t, began, kept))

    # ---- the Keeper on a recent file: its doc-edit draft for that file, as if two readers flagged it unclear (read-only)
    recent = sorted({ch.ref.get("path") for ch in c.index.code_chunks(pid, content=False) if ch.ref.get("path", "").endswith(".py")})
    target = next((p for p in recent if "/" not in p or p.count("/") == 1), recent[0] if recent else None)
    if target:
        from app.agents.specialists.keeper import agent as keeper

        title, excerpt = keeper._excerpt(deps, pid, "code", target)
        flags = [SimpleNamespace(kind="unclear", note="What does this file do, and in which order are its steps run?", created_by="a"),
                 SimpleNamespace(kind="unclear", note="Which inputs does it read and what does it write?", created_by="b")]
        began = time.monotonic()
        fb = SimpleNamespace(target_kind="code", target_ref=target)
        t = keeper.draft_doc_edit(deps, pid, org, fb, title, excerpt, flags)
        kept = None
        if t is not None and t.output is not None:
            text, why_not = t.text(t.output.proposed_text, 2000, keeper._doc_draft(title, flags), numbers=False)
            kept = {"target": target, "proposed_text": text, "dropped": why_not, "rationale": t.output.rationale,
                    "sources": t.sources(t.output.sources)}
        out.append(report("keeper", t, began, kept))

    # ---- the Planner, asked in Ask for a KT session (ask.free): a proposal, nothing booked
    if me is not None:
        began = time.monotonic()
        steps: list = []
        done = planner.kt_session(deps, {"user_id": me.id, "project_id": pid,
                                         "question": "Plan a KT session that walks a new engineer through how the boundary is computed"},
                                  steps, on_step=show)
        fake = SimpleNamespace(out=SimpleNamespace(stopped="done" if done else "no output", steps=steps, tool_calls=sum(x.kind == "tool" for x in steps),
                                                   turns=len(steps), tokens=0)) if steps else None
        out.append(report("planner (KT session)", fake, began, done))

    # ---- with an onboarding: the Planner's plan and the Coach's assessment (no approval, no nudge)
    if hc is not None:
        began = time.monotonic()
        drafted = planner.draft_plan(deps, hc.ob)
        t = drafted[2] if drafted else None
        for s in (t.steps if t else []):
            show(s)
        kept = [{"title": x.title, "kind": x.kind, "due": x.due_in_days, "paths": x.related_paths, "issue": x.issue_number}
                for x in drafted[0]] if drafted else None
        out.append(report("planner", t, began, {"tasks": kept, "rationale": drafted[1].get("rationale") if drafted else None}))
        began = time.monotonic()
        task = ("Assess this new hire's onboarding today: are they stuck, why, and what is the next step? Start with hire_signals.\n\n"
                + embed_data({"onboarding_id": hc.ob.id}))
        t = thinking.think(deps, "coach", org_id=org, project_id=pid, user=None, system=prompts.COACH_AGENT, task=task,
                           tools=REGISTRY.select("hire_signals", "program", "kt_moments", "search_knowledge", "kt_coverage"),
                           output=CoachAssessment, on_step=show, notes={"onboarding_id": hc.ob.id})
        kept = None
        if t is not None and t.output is not None:
            nudge, why_not = t.text(t.output.nudge, 280) if t.output.nudge else (None, None)
            kept = {"stuck": t.output.stuck, "why": t.output.why, "next_step": t.output.next_step, "nudge": nudge, "nudge_dropped": why_not,
                    "alerts": [a.model_dump() for a in t.output.alerts], "concerns": [x.model_dump() for x in t.output.concerns]}
        out.append(report("coach", t, began, kept))
    print(json.dumps({"runs": out}, default=str, ensure_ascii=False)[:6000])
    _ = DocEditDraft  # the Keeper's output schema (imported for the reader of this script)


def demo(only: set[str] | None = None) -> None:
    """The demo world with the real model (see the module docstring). Four agent runs (`only`: some of scout, planner, coach,
    reviewer)."""
    want = only or {"scout", "planner", "coach", "reviewer"}
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    load_env(ROOT / "selfhost.env")
    key = os.environ.pop("GEMINI_API_KEY", "")  # the seed runs with the offline model; the real one only for the checks below
    models = {k: os.environ.get(k.upper()) for k in ("model_fast", "model_smart", "model_fallback") if os.environ.get(k.upper())}
    for name in [k for k in os.environ if k in {"DATABASE_URL", "FIREBASE_PROJECT_ID", "FIREBASE_API_KEY", "GITHUB_TOKEN", "DATA_DIR"}]:
        os.environ.pop(name)
    os.environ["MODE"], os.environ["ENV"] = "demo", "dev"
    if not key:
        sys.exit("No GEMINI_API_KEY in selfhost.env")
    from app.agents.core.llm import GeminiLLM
    from app.config import get_settings
    from app.container import build_container
    from app.lifecycle import hires
    from app.lifecycle.context import Ctx

    get_settings.cache_clear()
    settings = get_settings()
    c = build_container(settings)
    c.deps.llm = GeminiLLM(settings.model_copy(update={"gemini_api_key": key, **models}))
    deps, pid, org = c.deps, "proj-demo", "org-demo"
    me, out = c.store.get_user("u-priya"), []
    hc = hires.load(Ctx.of(c, me, org), "ob-vijay")
    print(f"model: {type(deps.llm).__name__} ({deps.llm.settings.model_fast}); demo world: {len(c.store.onboardings(pid))} onboardings\n")

    if "scout" in want:
        demo_scout(deps, hc, me, org, pid, out)
    if "planner" in want:
        demo_planner(c, deps, hc, me, org, pid, out)
    if "coach" in want:
        demo_coach(deps, hc, org, pid, out)
    if "reviewer" in want:
        demo_reviewer(c, deps, pid, out)
    print(json.dumps({"runs": out}, default=str, ensure_ascii=False)[:8000])


def demo_scout(deps, hc, me, org, pid, out) -> None:
    from app.agents.core import prompts, thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import ScoutShortlist
    from app.agents.specialists.scout import agent as scout
    from app.agents.tools import REGISTRY

    began = time.monotonic()  # the Scout over the hire's real candidates
    task = ("Shortlist at most 3 first tasks for this new hire. Start with first_task_candidates.\n\n"
            + embed_data({"onboarding_id": hc.ob.id, "focus_area": hc.program.focus_area, "role": hc.ob.role_title}))
    t = thinking.think(deps, "scout", org_id=org, project_id=pid, user=me, system=prompts.SCOUT_AGENT, task=task,
                       tools=REGISTRY.select(*scout.TOOLS), output=ScoutShortlist, max_steps=8, on_step=show, notes={"onboarding_id": hc.ob.id})
    out.append(report("scout", t, began, scout.check_picks(deps, hc, t.output, t.sources) if t and t.output else None))


def demo_planner(c, deps, hc, me, org, pid, out) -> None:
    from app.agents.specialists.planner import agent as planner
    from app.lifecycle import enrolment
    from app.lifecycle.context import Ctx

    began = time.monotonic()  # the Planner's plan for a new enrolment
    template = next(x for x in c.lifecycle.templates(org) if x.key == "backend-mid")
    e = enrolment.enrol(Ctx.of(c, me, org), pid, "nia@example.dev", "Nia", None, hc.ctx.clock.today(), template, None, "u-ravi", "engine",
                        "Backend Engineer")
    drafted = planner.draft_plan(deps, e["onboarding"])
    t = drafted[2] if drafted else None
    for x in (t.steps if t else []):
        show(x)
    out.append(report("planner", t, began, {"tasks": [{"title": x.title, "kind": x.kind, "due_day": x.due_in_days, "paths": x.related_paths,
                                                       "issue": x.issue_number} for x in drafted[0]], "rationale": drafted[1].get("rationale"),
                                             "reasons": drafted[1].get("reasons")} if drafted else None))


def demo_coach(deps, hc, org, pid, out) -> None:
    from app.agents.core import prompts, thinking
    from app.agents.core.llm import embed_data
    from app.agents.core.schemas import CoachAssessment
    from app.agents.specialists.coach import agent as coach
    from app.agents.tools import REGISTRY
    from app.lifecycle import hires

    began = time.monotonic()  # the Coach on Vijay (his docker check fails; an alert waits at the mentor stage)
    hires.refresh(hc)
    task = ("Assess this new hire's onboarding today: are they stuck, why, and what is the next step? Start with hire_signals.\n\n"
            + embed_data({"onboarding_id": hc.ob.id, "who_can_help": "Ravi"}))
    t = thinking.think(deps, "coach", org_id=org, project_id=pid, user=None, system=prompts.COACH_AGENT, task=task,
                       tools=REGISTRY.select(*coach.TOOLS), output=CoachAssessment, on_step=show, notes={"onboarding_id": hc.ob.id})
    kept = None
    if t is not None and t.output is not None:
        nudge, why_not = t.text(t.output.nudge, coach.NUDGE_MAX, "Ravi") if t.output.nudge else (None, None)
        kept = {"stuck": t.output.stuck, "why": t.output.why, "next_step": t.output.next_step, "nudge": nudge, "nudge_dropped": why_not,
                "alerts": [a.model_dump() for a in t.output.alerts], "concerns": [x.model_dump() for x in t.output.concerns]}
    out.append(report("coach", t, began, kept))


def demo_reviewer(c, deps, pid, out) -> None:
    from app.agents.specialists.reviewer import agent as reviewer
    from app.lifecycle import presubmit

    began = time.monotonic()  # the Reviewer, privately, on the planted diff of the injection fixtures
    planted = (ROOT / "tests" / "fixtures" / "injection" / "diff_comment.patch").read_text()
    files = presubmit.files_from_diff("diff --git a/engine/validate.py b/engine/validate.py\n" + planted)
    code = next(x for x in c.store.tasks(onboarding_id="ob-vijay") if x.kind == "code")
    done = reviewer.think_review(deps, pid, code, files, [], user=c.store.get_user("u-vijay"))
    result, t = (done if done else (None, None))
    for x in (t.steps if t else []):
        show(x)
    out.append(report("reviewer (private)", t, began, result.model_dump() if result else None))


if __name__ == "__main__":
    if "--demo" in sys.argv:
        picked = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), "")
        demo({x for x in picked.split(",") if x} or None)
    else:
        main(sys.argv[1], "--inventory" in sys.argv)
