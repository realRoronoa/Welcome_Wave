"""Loading a hire's program for a request, and bringing it up to date before it is shown: auto milestones whose
evidence now exists are marked done, and blocker rules run (when their flags are on). Reads never wait for a scheduler."""
from __future__ import annotations

from app.lifecycle import blockers, flags, progress
from app.lifecycle.context import Ctx, HireCtx


def load(ctx: Ctx, onboarding_id: str) -> HireCtx | None:
    ob = ctx.store.get_onboarding(onboarding_id)
    program = ctx.lc.get_program(onboarding_id) if ob else None
    return HireCtx(ctx, ob, program) if program else None


def refresh(hc: HireCtx) -> HireCtx:
    if hc.ob.status == "active":
        progress.evaluate_milestones(hc)
        if flags.is_on("blockers"):
            blockers.detect(hc)
    return hc


def of_projects(ctx: Ctx, project_ids: list[str], active_only: bool = True) -> list[HireCtx]:
    out = []
    for pid in project_ids:
        for ob in ctx.store.onboardings(pid, active_only=active_only):
            program = ctx.lc.get_program(ob.id)
            if program:
                out.append(HireCtx(ctx, ob, program))
    return out
