"""Release round, privacy: an error's text never carries a hire's words to managers or to the outbox, KT notes are redacted
in every field (not only the transcript), the hire is told about the record their private reviews leave, Calendly bookings
need a member who books, uploaded documents are not unpacked without limit, and the audit log is append-only for the app.

The Postgres half (append-only audit log, organisation deletion and export) runs with TEST_PG_ADMIN_URL."""
from __future__ import annotations

import io
import uuid
import zipfile

import psycopg
import pytest
from sqlalchemy.exc import OperationalError

from app.agents.specialists.librarian import agent as ingestion
from app.agents.supervisor.graph import public_error, run_event
from app.config import Settings
from app.container import build_container
from app.ports import TooLarge
from app.selfhost import LocalQueue
from app.services import documents, scheduling
from tests.test_postgres import ADMIN, OWNER_URL, _org, db, engine, store  # noqa: F401 - Postgres fixtures

pg = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run the Postgres integration tests")
QUESTION = "Is it normal that my manager Priya ignores me? check_labels"


# ---------------------------------------------------------------- PRIV-1: errors carry no data
def test_a_database_error_never_puts_the_hires_question_in_an_incident_or_run(world, graph):
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")

    def down(project_id, user_id, question, result):
        raise OperationalError("INSERT INTO chat_messages (q) VALUES (%(q)s)", {"q": question},
                               Exception(f"server closed the connection unexpectedly; row {question}"))

    world.store.save_chat = down
    out = run_event(graph, {"type": "chat.question", "project_id": "p1", "user_id": "u-hire", "question": QUESTION}, "chat:priv1")
    assert out["result"]["escalated"]
    assert "ignores me" not in out["result"]["error"] and "OperationalError" in out["result"]["error"]
    told = [n for n in world.store.notifications if n["user_id"] == "u-mgr" and n["kind"] == "incident"]
    assert told and not any("ignores me" in n["text"] for n in told)
    failed = [r for r in world.store.runs if r["status"] == "failed"]
    assert failed and all("ignores me" not in str(r["detail"]) for r in failed)
    assert failed[-1]["detail"]["error_type"] == "OperationalError"


def test_our_own_messages_are_shown_as_they_are():
    from app.ports import AI_OFF, AIUnavailable

    assert public_error(AIUnavailable(AI_OFF)) == AI_OFF
    assert public_error(TooLarge("o/r has 9 files; the limit is 1 (INDEX_MAX_FILES).")).endswith("(INDEX_MAX_FILES).")
    assert public_error(ValueError("ravi@corp.com")) == "an unexpected error (ValueError)"


def test_the_outbox_records_the_kind_of_error_only():
    c = build_container(Settings(mode="demo", agent_retries=1, retry_sleep_s=0))
    q = LocalQueue(1)
    c.deps.bus, q.container = q, c

    def boom(ev):
        raise RuntimeError(f"duplicate key: {QUESTION}")

    c.handle = boom
    from app.api.outbox import publish

    publish(c, "reviews", {"type": "noop", "project_id": "proj-demo", "dedupe": "priv1:outbox"})
    row_id, event = q.jobs.get_nowait()
    q.run(row_id, event)
    assert c.store.outbox[row_id]["last_error"] == "RuntimeError"


# ---------------------------------------------------------------- PRIV-2: every note field redacted
def test_kt_notes_are_redacted_in_every_field(world, graph):
    ingestion.ingest_repo(world, world.store.get_repo("r1"), "main")
    key = "AKIAIOSFODNN7EXAMPLE"
    world.llm.responders["SegmentAnalysis"] = lambda p, media: {
        "utterances": [{"start_s": 5, "end_s": 20, "speaker": "Ravi", "text": f"Mail me at ravi@corp.com, key {key}"}],
        "screen_moments": [{"at_s": 10, "description": f"terminal showing export AWS_ACCESS_KEY_ID={key}",
                            "visible_paths": ["engine/validate.py"], "visible_symbols": ["check_labels"]}],
        "decisions": ["Escalations go to ravi@corp.com or +1 415 555 0100"],
        "action_items": [{"text": f"Vijay to email ravi@corp.com for the {key} key", "owner_hint": "ravi@corp.com", "at_s": 12}],
        "terms": [{"term": "oncall", "definition": "call +1 415 555 0100"}],
    }
    summaries = []

    def summary(prompt, media):
        summaries.append(prompt)
        return {"summary": "Write to ravi@corp.com for access.", "decisions": ["Page +1 415 555 0100 first"]}

    world.llm.responders["MeetingSummary"] = summary
    world.llm.responders["TaskPlan"] = lambda p, m: {"tasks": [], "first_pr": None}
    run_event(graph, {"type": "meeting.recording_ready", "project_id": "p1", "meeting_id": "m1",
                      "recording_uri": "gs://bucket/recordings/m1.mp4", "duration_s": 60}, "meeting:priv2")
    notes = world.store.meeting_notes("m1")
    everything = notes.model_dump_json()
    for secret in ("ravi@corp.com", key, "555 0100"):
        assert secret not in everything, secret
        assert secret not in summaries[0], secret  # the summary model is given redacted decisions too
    assert notes.screen_moments[0].visible_paths == ["engine/validate.py"]  # what links moments to code is kept


# ---------------------------------------------------------------- PRIV-13: the private review's record is listed
def test_the_hire_sees_the_record_their_private_reviews_leave():
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        c = client.app.state.container
        c.store.record_run("proj-demo", "reviewer", "presubmit.requested", "succeeded",
                           {"summary": "A private review ran.", "user_id": "u-vijay", "visibility": "private"})
        c.store.record_run("proj-demo", "reviewer", "presubmit.requested", "succeeded",
                           {"summary": "A private review ran.", "user_id": "u-meera", "visibility": "private"})
        body = client.get("/api/onboardings/ob-vijay/visibility", headers={"X-Demo-User": "u-vijay"}).json()
    get_settings.cache_clear()
    field = body["data"]["private_reviews"]
    assert set(field["visible_to"]) == {"hire", "manager"} and len(field["value"]) == 1
    assert set(field["value"][0]) == {"at", "status"}
    assert not any("never stored" in p for p in body["private"])
    assert any("pre-submit" in p and "recorded" in p for p in body["private"])


# ---------------------------------------------------------------- INPUT-09: Calendly bookings
class Booking:
    def __init__(self, invitee, host, kind="created", uri=None):
        self.kind, self.event_uri, self.name = kind, uri or f"https://api.calendly.com/scheduled_events/{uuid.uuid4().hex}", "KT"
        self.starts_at = self.ends_at = self.join_url = self.kt_key = None
        self.project_id = "proj-demo"
        self.invitee, self.hosts, self.guests, self.rescheduled = {"email": invitee, "name": "I"}, [{"email": host, "name": "H"}], [], False


def test_a_booking_needs_a_member_who_books_and_the_audit_names_the_host():
    c = build_container(Settings(mode="demo"))
    st = c.store
    priya, meera = st.get_user("u-priya"), st.get_user("u-meera")
    assert scheduling.handle_calendly(c, Booking("outsider@else.dev", meera.email)) == {"ignored": "the invitee is not in this project"}
    out = scheduling.handle_calendly(c, Booking(priya.email, meera.email))
    m = st.get_meeting(out["created"])
    assert m.created_by == priya.id  # whoever booked may manage the session
    entry = next(a for a in reversed(st.audit_rows) if a["action"] == "meeting.created" and a["target"] == m.id)
    assert entry["actor_id"] == meera.id and entry["detail"]["booked_as"] == priya.id and entry["detail"]["booker_verified"] is False


# ---------------------------------------------------------------- INPUT-11: document limits
def _docx(xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_a_word_file_that_unpacks_to_far_more_than_it_weighs_is_not_read():
    bomb = _docx(b"<w>" + b"a" * (40 * 1024 * 1024) + b"</w>")
    assert len(bomb) < 1024 * 1024
    with pytest.raises(TooLarge) as e:
        documents.extract_pages(bomb, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "unpack" in str(e.value)


def test_a_pdf_over_the_page_limit_is_not_read(monkeypatch):
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(4):
        w.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    w.write(buf)
    monkeypatch.setattr(documents, "MAX_PDF_PAGES", 3)
    with pytest.raises(TooLarge) as e:
        documents.extract_pages(buf.getvalue(), "application/pdf")
    assert "4 pages" in str(e.value)
    monkeypatch.setattr(documents, "MAX_PDF_PAGES", 4)
    assert len(documents.extract_pages(buf.getvalue(), "application/pdf")) == 4


# ---------------------------------------------------------------- Postgres: the audit log and the organisation's data
@pg
def test_the_app_can_neither_change_nor_remove_audit_entries(store, engine):  # noqa: F811
    """PRIV-7 / AUTH-5: ww_app may INSERT and SELECT audit_log, never UPDATE or DELETE (the migrator re-revokes after its
    blanket grant and refuses to finish otherwise)."""
    from sqlalchemy import text

    from app import migrate
    from app.adapters.postgres import current_org

    admin, _ = _org(store, "au")
    store.audit(admin.org_id, admin.id, "member.role_changed", "u-x", {"to": "manager"})
    for stmt in ("UPDATE audit_log SET detail = '{}'::jsonb", "DELETE FROM audit_log"):
        with pytest.raises(Exception) as e:
            with engine.begin() as c:
                c.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": admin.org_id})
                c.execute(text(stmt))
        assert "permission denied" in str(e.value)
    with pytest.raises(Exception) as e:  # the definer function will not empty a live organisation's log
        store.one("SELECT delete_org_audit(CAST(:o AS uuid)) AS n", o=admin.org_id)
    assert "deleted first" in str(e.value)
    assert store.rows("SELECT action FROM audit_log WHERE action = 'member.role_changed'")
    with psycopg.connect(OWNER_URL["url"], autocommit=True) as c:
        migrate.verify(c)  # passes: ww_app holds neither privilege
        c.execute("GRANT UPDATE ON audit_log TO ww_app")
        try:
            with pytest.raises(SystemExit) as stop:
                migrate.verify(c)
            assert "audit" in str(stop.value)
        finally:
            c.execute("REVOKE UPDATE ON audit_log FROM ww_app")
    current_org.set(None)


@pg
def test_deleting_an_organisation_removes_its_audit_entries_and_event_keys(store, engine):  # noqa: F811
    """PRIV-7 with E2E-12: the organisation's audit entries go through delete_org_audit() once the organisation row is gone,
    and the dedupe keys naming its onboardings, documents, meetings and repositories go too; another organisation's stay."""
    from app.adapters.lifecycle_pg import PgLifecycleStore
    from app.adapters.postgres import current_org

    lc = PgLifecycleStore(engine)
    a, a_project = _org(store, "dl")
    store.audit(a.org_id, a.id, "member.role_changed", "x", {})
    meeting = store.create_meeting(a_project.id, "KT", a.id)
    doc = store.create_document(a_project.id, "Guide", "gs://local/docs/x.md", "text/markdown", a.id)
    b, b_project = _org(store, "dk")
    b_key = f"doc:{uuid.uuid4()}"
    store.claim_event(b_key, 300)
    store.claim_event(f"connect:{b_project.id}:x", 300)
    current_org.set(a.org_id)
    a_keys = [f"rec:{meeting.id}:abc", f"doc:{doc}", f"tick:{a.org_id}:2026-11-17", f"ob:{uuid.uuid4()}"]
    for k in a_keys:
        store.claim_event(k, 300)
        store.finish_event(k)
    counts = lc.delete_org(a.org_id)
    assert counts["audit_log"] >= 2 and counts["processed_events"] == 3  # the random onboarding id names nobody's
    left = {r["dedupe_key"] for r in store.rows("SELECT dedupe_key FROM processed_events")}
    assert not ({f"rec:{meeting.id}:abc", f"doc:{doc}", f"tick:{a.org_id}:2026-11-17"} & left)
    assert {b_key, f"connect:{b_project.id}:x"} <= left
    with psycopg.connect(OWNER_URL["url"], autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM audit_log WHERE org_id = %s", (a.org_id,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM audit_log WHERE org_id = %s", (b.org_id,)).fetchone()[0] >= 1
    current_org.set(None)


@pg
def test_the_export_leaves_out_the_search_index(store, engine):  # noqa: F811
    """PRIV-11: chunks (a copy of every indexed file, rebuilt from the sources) are not exported."""
    from app.adapters.lifecycle_pg import PgLifecycleStore
    from app.adapters.postgres import current_org

    lc = PgLifecycleStore(engine)
    a, project = _org(store, "ex")
    store.run("INSERT INTO chunks (id, org_id, project_id, source_type, ref, content, search_text, embedding) "
              "VALUES (:i, :o, :p, 'code', '{\"path\": \"a.py\"}', 'secret code', 'secret code', CAST(:v AS vector))",
              i=uuid.uuid4().hex[:20], o=a.org_id, p=project.id, v="[" + ",".join(["0"] * 768) + "]")
    data = lc.export_org(a.org_id)
    assert "chunks" not in data and {"projects", "users", "audit_log"} <= set(data)
    current_org.set(None)
