"""The dashboard (GET /api/dashboard): KT coverage by module, KT gaps and the questions several new hires ask. It uses the
lifecycle's clock and status (app/lifecycle), so it agrees with Home and Team, and holds no per-hire activity metrics."""
from __future__ import annotations

from collections import Counter

from app.lifecycle.clock import program_day
from app.services.chunking import split_identifiers
from app.services.coverage import coverage_map, gaps

STOP = {"the", "a", "an", "is", "of", "to", "in", "how", "what", "does", "do", "where", "why", "i", "we", "it", "this", "and", "for"}


def expert_answered(turn: dict) -> bool:
    """The assistant handed the question to a person, and they answered it (chat_history's `expert`, both adapters)."""
    return (turn.get("expert") or {}).get("status") == "answered"


def unanswered(turn: dict) -> bool:
    """Still waiting for an answer: the assistant could not answer it from sources, and no expert has answered it since."""
    return turn.get("status") == "not_enough_information" and not expert_answered(turn)


def _topic(question: str) -> str:
    words = [w for w in split_identifiers(question).split() if w not in STOP]
    return " ".join(sorted(set(words))[:4])


def _first_task_status(ctx, hc) -> str | None:
    """Where the hire's first pull request stands, from the program's selected first task (what Home shows), else from a task
    the plan marked as the first pull request."""
    chosen = (hc.program.first_task or {}).get("task_id") if hc else None
    task = ctx.store.get_task(chosen) if chosen else None
    if task is None:
        task = next((t for t in ctx.store.tasks(onboarding_id=hc.ob.id) if t.origin == "first_pr"), None) if hc else None
    return task.status if task else None


def build_dashboard(ctx, project_id: str) -> dict:
    """The KT coverage view: which modules the recorded sessions explained and for how many minutes, the gaps, the questions
    several new hires asked, and what waits for approval. `people` is identity and status only, from the lifecycle's clock and
    rules (the same day and 'on track' Home and Team show); it carries no activity counts (tasks done, questions asked), because
    coverage, not a person's output, is what this page is about."""
    from app.lifecycle import hires, visibility  # imported here: visibility imports this module's helpers

    store, index = ctx.store, ctx.index
    people = []
    for ob in store.onboardings(project_id):
        hire = store.get_user(ob.new_hire_id)
        hc = hires.load(ctx, ob.id)
        status = visibility.FIELDS["status"].build(hires.refresh(hc)) if hc else None
        people.append({
            "onboarding_id": ob.id, "name": hire.name if hire else ob.new_hire_id,
            "day": hc.day if hc else program_day(ob.start_date, ctx.clock.today()),
            "on_track": status["on_track"] if status else None, "why": status["why"] if status else [],
            "first_pr_status": _first_task_status(ctx, hc),
        })

    topics: Counter = Counter()
    askers: dict[str, set] = {}
    for ob in store.onboardings(project_id):
        for h in store.chat_history(project_id, ob.new_hire_id, limit=200):
            t = _topic(h["question"])
            topics[t] += 1
            askers.setdefault(t, set()).add(ob.new_hire_id)
    cov = coverage_map(index.all_paths(project_id), store.kt_links(project_id))
    return {
        "people": people,
        "coverage": [r.model_dump() for r in cov],
        "kt_gaps": gaps(cov),
        # a topic only once at least two different hires asked it: never one person's own question
        "repeated_questions": [{"topic": t, "count": n} for t, n in topics.most_common() if n > 1 and len(askers[t]) > 1][:5],
        "pending_approvals": len(store.approvals(project_id)),
    }
