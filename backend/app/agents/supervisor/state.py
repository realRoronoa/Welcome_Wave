"""The Supervisor's state: what one run of the graph (graph.py) carries from node to node.

LangGraph starts a run with {"event": ...} and merges each node's returned dict into this state; the conditional edges read
it to choose the next node. Every field but one is last-write-wins. `decided` has a reducer (operator.add): the approval
branches of one run (one Send per proposal, decided in any order) each append their decision instead of overwriting.

A run that pauses for a person is saved by the checkpointer (Postgres in production) and read back when the decision
comes, possibly days later. So the state holds plain values only (dicts, lists, strings, ids), never an object of our
code: nothing a paused run stored depends on how the modules are laid out.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class State(TypedDict, total=False):
    event: dict
    route: str
    result: dict
    error: str | None
    action_items: list[dict]
    approval_id: str
    review_id: str
    task_id: str
    kt_before: list[dict]  # repo.pushed: what the KT-linked lines were before the Librarian re-indexed (the Keeper compares)
    followup: dict  # what a chained agent did after the first one (the Keeper after the Librarian)
    proposals: list[str]  # approvals of the new kinds this run parks on, one interrupt each (await_proposal)
    decided: Annotated[list[dict], operator.add]  # one entry per proposal decided: {approval_id, kind, decision, result}
    tick: dict  # lifecycle.tick: what ran so far, and what is due next
    decision: dict  # ask.free: which agent the Supervisor picked, why, its proposal, and the steps of that decision
