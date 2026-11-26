"""The milestone board on PostgreSQL (db/migrations/018_board.sql), as ww_app under row-level security: templates keep their
board milestones, a hire's milestones, placements and moves round-trip, the first read writes the milestones once, a deleted
milestone hands its placements on, another organisation sees none of it, the board service runs end to end, and the
organisation's export and deletion cover the new tables. Skipped without TEST_PG_ADMIN_URL (run it through pg_tests.sh)."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.adapters.postgres import current_org
from app.agents.core.schemas import ProposedTask
from app.config import Settings
from app.lifecycle import board as B
from app.lifecycle import hires
from app.lifecycle import templates as T
from app.lifecycle.clock import Clock
from app.lifecycle.context import Ctx
from app.ports import Phase, ProgramTemplate, TaskMove, TaskPlacement
from tests.test_lifecycle_postgres import NOW, _program, lc
from tests.test_postgres import ADMIN, _org, db, engine, store

FIXTURES = (db, engine, store, lc)  # the Postgres fixtures, reused here by name

pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")


def test_templates_keep_their_board_milestones(store, lc):
    admin, *_ = _program(store, lc, "bt")
    phases = [{"key": "two_weeks", "title": "Two weeks in", "due_day": 14}, {"key": "quarter", "title": "A quarter in", "due_day": 90}]
    t = lc.create_template(ProgramTemplate(id="", org_id=admin.org_id, key="custom-board", title="Custom", role_track="Backend", level="Mid",
                                           phases=phases))
    assert lc.get_template(t.id).phases == phases
    v2 = lc.new_template_version(t.id, phases=phases[:1])
    assert v2.phases == phases[:1] and lc.get_template(t.id).phases == phases  # the old version keeps its own
    listed = {x.key: x for x in T.list_for(lc, admin.org_id)}
    assert [p["due_day"] for p in listed["backend-mid"].phases] == [15, 30, 45, 60, 90]  # the defaults, for a template without its own


def test_milestones_placements_and_moves_round_trip(store, lc):
    admin, project, hire, ob, *_ = _program(store, lc, "bp")
    first = [Phase(onboarding_id=ob.id, key=k, title=k.title(), due_day=d, position=i) for i, (k, d) in enumerate([("a", 15), ("b", 30)])]
    assert [p.key for p in lc.ensure_phases(ob.id, first)] == ["a", "b"]
    assert [p.key for p in lc.ensure_phases(ob.id, [Phase(onboarding_id=ob.id, key="z", title="Z", due_day=5)])] == ["a", "b"]
    assert lc.add_phase(Phase(onboarding_id=ob.id, key="c", title="C", due_day=60, position=2)).key == "c"
    assert lc.add_phase(Phase(onboarding_id=ob.id, key="a", title="Again", due_day=70)) is None
    assert lc.update_phase(ob.id, "c", title="Sixty", position=0).title == "Sixty"
    assert [p.key for p in lc.phases(ob.id)] == ["a", "c", "b"]  # by position, then day: a and c share position 0
    [t1, t2] = store.create_tasks(ob.id, [ProposedTask(title="One", description="", kind="learn", due_in_days=3),
                                          ProposedTask(title="Two", description="", kind="meet", due_in_days=20)], ob.start_date)
    lc.save_placements(ob.id, [TaskPlacement(task_id=t1.id, onboarding_id=ob.id, phase_key="a", position=1),
                               TaskPlacement(task_id=t2.id, onboarding_id=ob.id, position=0)])
    lc.save_placements(ob.id, [TaskPlacement(task_id=t1.id, onboarding_id=ob.id, phase_key="a", position=0)])  # one per task
    assert sorted((p.task_id == t1.id, p.phase_key, p.position) for p in lc.placements(ob.id)) == [(False, None, 0), (True, "a", 0)]
    move = lc.add_task_move(TaskMove(onboarding_id=ob.id, task_id=t1.id, from_phase="a", to_phase="b", from_title="A", to_title="B",
                                     from_due=ob.start_date, to_due=ob.start_date + timedelta(days=30), actor_id=admin.id, at=NOW))
    assert move.id and [m.to_phase for m in lc.task_moves(ob.id)] == ["b"] and lc.task_moves(ob.id)[0].to_due == ob.start_date + timedelta(days=30)
    assert lc.delete_phase(ob.id, "a", "b") and not lc.delete_phase(ob.id, "a", "b")
    assert next(p for p in lc.placements(ob.id) if p.task_id == t1.id).phase_key == "b"
    # another organisation sees none of it
    other, _ = _org(store, "bp2")
    current_org.set(other.org_id)
    assert lc.phases(ob.id) == [] and lc.placements(ob.id) == [] and lc.task_moves(ob.id) == []
    assert lc.update_phase(ob.id, "b", title="Taken") is None
    current_org.set(admin.org_id)
    assert next(p for p in lc.phases(ob.id) if p.key == "b").title == "B"


def test_the_board_service_runs_on_postgres(engine, store, lc):
    admin, project, hire, ob, *_ = _program(store, lc, "bs")
    ns = SimpleNamespace(lifecycle=lc, store=store, index=None, deps=None, settings=Settings(mode="demo"))
    ctx = Ctx(ns, Clock(), admin)
    [task] = store.create_tasks(ob.id, [ProposedTask(title="Read the README", description="", kind="learn", due_in_days=5)], ob.start_date)
    store.update_task(task.id, status="approved")
    hc = hires.load(ctx, ob.id)
    view = B.view(hc, True)
    assert [m["key"] for m in view["milestones"]] == ["settle_in", "first_change", "on_your_own", "own_an_area", "fully_on_board"]
    assert [(t["id"], t["milestone"], t["column"]) for t in view["tasks"]] == [(task.id, "settle_in", "todo")]
    view = B.move(hc, admin, task.id, True, column="in_progress", milestone="first_change")
    card = view["tasks"][0]
    assert (card["status"], card["milestone"], card["moved"]["from_key"]) == ("in_progress", "first_change", "settle_in")
    assert store.get_task(task.id).due_date == ob.start_date + timedelta(days=30)
    view = B.add_phase(hires.load(ctx, ob.id), admin, "Leads a review", 75)
    assert "leads_a_review" in [m["key"] for m in view["milestones"]]
    view = B.remove_phase(hires.load(ctx, ob.id), admin, "first_change", None)
    assert view["tasks"][0]["milestone"] == "on_your_own" and len(view["tasks"][0]["history"]) == 2


def test_export_and_delete_cover_the_board_tables(store, lc):
    admin, project, hire, ob, *_ = _program(store, lc, "bx")
    lc.ensure_phases(ob.id, [Phase(onboarding_id=ob.id, key="a", title="A", due_day=15)])
    [t] = store.create_tasks(ob.id, [ProposedTask(title="T", description="", kind="learn", due_in_days=3)], ob.start_date)
    lc.save_placements(ob.id, [TaskPlacement(task_id=t.id, onboarding_id=ob.id, phase_key="a")])
    lc.add_task_move(TaskMove(onboarding_id=ob.id, task_id=t.id, from_phase=None, to_phase="a", to_title="A", actor_id=admin.id))
    data = lc.export_org(admin.org_id)
    assert len(data["onboarding_phases"]) == 1 and len(data["task_placements"]) == 1 and len(data["task_moves"]) == 1
    counts = lc.delete_org(admin.org_id)
    assert counts["onboarding_phases"] == 1 and counts["task_moves"] == 1
    assert all(rows == [] for rows in lc.export_org(admin.org_id).values())
