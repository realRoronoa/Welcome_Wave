"""The shared read-only tools agents call in their loop (app/agents/core/runtime.py; docs/AGENTS.md, "Tools").

Every tool:
  - reads only the project of its ToolCtx (the model never names a project) and re-checks the acting user's access
    (ToolCtx.check, run by the runtime before each call);
  - writes nothing: side effects are never tools (an agent proposes them in its output);
  - returns its text as untrusted data (runtime.untrusted -> embed_data), the sources it showed, a one-line summary for the
    asker's trace and a `shared` line for everyone else's (no argument the model chose, no untrusted text);
  - numbers what it shows (ToolCtx.cite) so the agent can cite it, and names the people it found (ToolCtx.people), the
    only people an agent may suggest a question goes to.

    knowledge.py  search_knowledge, read_file, find_symbol, verified_answers
    people.py     kt_moments, code_owners, who_knows, kt_coverage
    repo.py       issues, recent_changes, tests_for
    hire.py       hire_signals (the Coach's, sensitive), program, first_task_candidates, cohort_evidence, pr_diff, kt_changes,
                  feedback_target, person_footprint (Track K's specialists)

Use: `from app.agents.tools import REGISTRY; tools = REGISTRY.select("search_knowledge", "read_file")`.
"""
from app.agents.core.runtime import REGISTRY
from app.agents.tools import hire, knowledge, people, repo  # noqa: F401 - importing registers the tools

GUIDE_TOOLS = ("search_knowledge", "read_file", "find_symbol", "kt_moments", "verified_answers", "code_owners", "who_knows")

__all__ = ["GUIDE_TOOLS", "REGISTRY"]
