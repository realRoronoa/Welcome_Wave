"""Who wrote a chat answer (QAResult.writer, .crew; db/migrations/017_chat_writer.sql) is kept with the turn on Postgres, as
the memory store keeps it, so the Ask page's label is the same after a reload. Runs with TEST_PG_ADMIN_URL (tests/test_postgres.py)."""
from __future__ import annotations

import os

import pytest

from app.agents.core.schemas import QAResult
from tests.test_postgres import _org, db, engine, store  # noqa: F401 - the Postgres fixtures

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_ADMIN_URL"), reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")


def test_writer_and_crew_are_kept_with_the_chat_turn(store):  # noqa: F811 - the fixture imported above
    admin, project = _org(store, "writer")
    crew = {"intent": "explain", "kept": 3, "degraded": False}
    quoted = store.save_chat(project.id, admin.id, "Which function reports missing labels?",
                             QAResult(status="answered", answer="`check_labels` ... [1].", confidence=0.6, writer="extractive", crew=crew))
    offered = store.save_chat(project.id, admin.id, "How do I deploy?", QAResult(status="not_enough_information", answer=""))
    rows = {r["id"]: r for r in store.chat_history(project.id, admin.id)}
    assert rows[quoted]["writer"] == "extractive" and rows[quoted]["crew"] == crew
    assert rows[offered]["writer"] is None and rows[offered]["crew"] is None
    assert {r["id"]: r["writer"] for r in store.chat_histories(project.id, [admin.id])[admin.id]} == {quoted: "extractive", offered: None}
