"""The onboarding lifecycle: programs from versioned templates, enrolment and the 30/60/90 plan, pre-boarding, tiered
answer routing, the first task, progress by area, blockers, weekly summaries, documentation feedback, check-ins and
surveys, completion, offboarding handover, what a hire can see about themselves, and leadership reporting.

Services here use the core stores (ports.AppStore), the LifecycleStore (ports.LifecycleStore) and the agents' tools
(retrieval, the QA and Review agents, the Task agent through the Supervisor). Every rule is deterministic and returns its
reasons as `why: [...]`; every feature works with the AI off. "Today" always comes from clock.py. The API is
app/api/lifecycle.py; the specs are docs/specs/."""
