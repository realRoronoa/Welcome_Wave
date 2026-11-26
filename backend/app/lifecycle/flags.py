"""Lifecycle feature flags. FEATURES lists the features that are on: 'all' (the default), or names separated by commas;
a name with a leading '-' turns one off ('all,-surveys'). Read from the environment on every check, so a restart is the
only thing a change needs, and tests can flip one. Exposed at GET /api/lifecycle/config.

Enrolment, programs, homes, visibility and reporting floors are not flags: they are the product, and visibility is a
privacy guarantee that must never be switched off."""
from __future__ import annotations

import os

FEATURES = ("preboarding", "routing", "first_task", "presubmit", "blockers", "areas", "summaries", "feedback", "checkins",
            "surveys", "completion", "handover", "reports", "demo_clock")


def enabled() -> dict[str, bool]:
    words = [w.strip() for w in os.environ.get("FEATURES", "all").split(",") if w.strip()]
    on = set(FEATURES) if "all" in words or not words else {w for w in words if w in FEATURES}
    on -= {w[1:] for w in words if w.startswith("-")}
    return {f: f in on for f in FEATURES}


def is_on(name: str) -> bool:
    return enabled().get(name, False)
