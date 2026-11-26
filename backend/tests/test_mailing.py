"""Email (app/adapters/mailer.py, app/services/mailing.py): SMTP with smtplib mocked, an in-memory mailer, and what is sent when.

Nothing is sent without SMTP. With it: the invitation after a plan is approved (through the outbox, ids only), the pre-boarding
link when a manager asks (from memory, after the response), the expert digest and the weekly summary (from the daily tick). A mail
failure never fails the request, and a message's body and links are never logged."""
from __future__ import annotations

import logging
import smtplib
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.adapters.mailer import MailError, OutboxMailer, SmtpMailer, build, mailer_for
from app.config import Settings, get_settings
from app.main import create_app

PID = "proj-demo"
ADMIN, PRIYA, VIJAY, RAVI, ARJUN = ({"X-Demo-User": u} for u in ("u-admin", "u-priya", "u-vijay", "u-ravi", "u-arjun"))
LINK_BASE = "https://app.example.com"


# ---------------------------------------------------------------- SMTP itself, with smtplib mocked
class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail = None  # an exception to raise at login, or at send

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.calls, self.sent, self.context = host, port, [], [], context
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if FakeSMTP.fail == "auth":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, msg):
        self.calls.append("send")
        if FakeSMTP.fail == "send":
            raise smtplib.SMTPRecipientsRefused({"x@example.com": (550, b"no")})
        self.sent.append(msg)

    def quit(self):
        self.calls.append("quit")


class FakeSMTPSSL(FakeSMTP):
    def __init__(self, host, port, timeout=None, context=None):
        super().__init__(host, port, timeout, context)
        self.calls.append("ssl")


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances, FakeSMTP.fail = [], None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


def test_587_connects_in_the_clear_upgrades_with_starttls_then_logs_in(smtp):
    SmtpMailer("smtp.example.com", 587, "me@example.com", "app-password", "Welcome Wave <ww@example.com>").send(
        "nina@example.org", "Hello", "Plain text", "<p>Rich</p>")
    [conn] = smtp.instances
    assert type(conn) is FakeSMTP and (conn.host, conn.port) == ("smtp.example.com", 587)
    assert conn.calls == ["ehlo", "starttls", "ehlo", ("login", "me@example.com", "app-password"), "send", "quit"]  # login only after TLS
    [msg] = conn.sent
    assert (msg["From"], msg["To"], msg["Subject"]) == ("Welcome Wave <ww@example.com>", "nina@example.org", "Hello")
    assert msg.get_body(("plain",)).get_content().strip() == "Plain text" and "Rich" in msg.get_body(("html",)).get_content()


def test_465_is_ssl_from_the_start(smtp):
    SmtpMailer("smtp.example.com", 465, "me@example.com", "pw", "ww@example.com").send("nina@example.org", "Hello", "Body")
    [conn] = smtp.instances
    assert type(conn) is FakeSMTPSSL and conn.calls == ["ssl", ("login", "me@example.com", "pw"), "send", "quit"]  # no STARTTLS


def test_no_user_means_no_login(smtp):
    SmtpMailer("relay.internal", 587, "", "", "ww@example.com").send("a@example.org", "Hi", "Body")
    assert not any(isinstance(c, tuple) for c in smtp.instances[0].calls)


def test_a_failure_raises_without_the_message_and_logs_no_body(smtp, caplog):
    smtp.fail = "auth"
    mailer = SmtpMailer("smtp.example.com", 587, "me@example.com", "pw", "ww@example.com")
    with caplog.at_level(logging.INFO), pytest.raises(MailError) as e:
        mailer.send("nina@example.org", "Secret subject", "Body with https://app.example.com/welcome/TOKEN123")
    assert "TOKEN123" not in str(e.value) and "Secret subject" not in str(e.value)
    assert not any("TOKEN123" in str(r.__dict__) or "Secret subject" in str(r.__dict__) or "nina@example.org" in str(r.__dict__)
                   for r in caplog.records)  # the error's type and the recipient's domain only
    with pytest.raises(MailError, match="refused the user name"):
        mailer.check()
    smtp.fail = "send"
    with pytest.raises(MailError):
        mailer.send("nina@example.org", "Hi", "Body")


def test_a_line_break_in_a_header_is_refused():
    with pytest.raises(MailError):
        build("ww@example.com", "a@example.org\nBcc: evil@example.org", "Hi", "Body")
    with pytest.raises(MailError):
        build("ww@example.com", "a@example.org", "Hi\r\nBcc: evil@example.org", "Body")


def test_check_connects_and_logs_in_without_sending(smtp):
    note = SmtpMailer("smtp.example.com", 587, "me@example.com", "pw", "ww@example.com").check()
    assert "smtp.example.com:587" in note and "logged in" in note and "send" not in smtp.instances[0].calls


def test_the_mailer_follows_the_settings():
    off = mailer_for(Settings(mode="demo"))
    assert isinstance(off, OutboxMailer) and off.enabled is False
    assert isinstance(mailer_for(Settings(mode="demo", smtp_host="smtp.example.com")), OutboxMailer)  # a host alone is not enough: no From
    on = mailer_for(Settings(mode="demo", smtp_host="smtp.example.com", smtp_user="me@example.com"))  # From defaults to the user
    assert isinstance(on, SmtpMailer) and on.sender == "me@example.com" and on.port == 587
    assert Settings(mode="demo", smtp_host="h", smtp_from="ww@example.com").email_ready


# ---------------------------------------------------------------- what is sent, and when
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAT_RATE_PER_MINUTE", "1000")
    monkeypatch.setenv("PUBLIC_URL", LINK_BASE)
    monkeypatch.setenv("AGENT_RETRIES", "1")
    monkeypatch.setenv("RETRY_SLEEP_S", "0")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _c(client):
    return client.app.state.container


def _on(client) -> OutboxMailer:
    """Email switched on for this app: an in-memory mailer that records what would leave."""
    mailer = OutboxMailer(enabled=True)
    _c(client).deps.mailer = mailer
    return mailer


def _enrol(client, email="nina@example.com"):
    template = next(t for t in client.get("/api/programs/templates", headers=PRIYA).json() if t["key"] == "backend-junior")
    r = client.post("/api/onboardings/enrol", json={"project_id": PID, "email": email, "name": "Nina Rao", "start_date": date.today().isoformat(),
                                                    "template_id": template["id"], "mentor_id": "u-ravi"}, headers=PRIYA)
    assert r.status_code == 201, r.text
    return r.json()


def _approve(client):
    plan = next(a for a in client.get(f"/api/approvals?project_id={PID}", headers=PRIYA).json()
                if a["kind"] == "task_plan" and any(p.get("enrolment") for p in a["payload"]["plans"]))
    tasks = [t["id"] for p in plan["payload"]["plans"] for t in p["tasks"]]
    r = client.post(f"/api/approvals/{plan['id']}/decide", json={"approve": tasks}, headers=PRIYA)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_config_says_whether_email_works(client):
    assert client.get("/api/config").json()["email"] is False  # no SMTP: the web app shows links and never says "emailed"
    _on(client)
    assert client.get("/api/config").json()["email"] is True


def test_with_no_smtp_nothing_is_sent_and_the_ui_still_gets_the_link(client):
    mailer = _c(client).deps.mailer
    assert mailer.enabled is False
    _enrol(client)
    out = _approve(client)
    inv = out["result"]["enrolments"][0]["invitation"]
    assert inv["sent"] is False and inv["email_queued"] is False and inv["link"] == f"{LINK_BASE}/login" and "No email is sent" in inv["note"]
    assert mailer.sent == [] and not any(r["event"]["type"].startswith("mail.") for r in _c(client).store.outbox.values())
    link = client.post("/api/onboardings/ob-vijay/preboarding-link", json={"email": True}, headers=PRIYA)
    assert link.status_code == 201 and link.json()["emailed"] is False and link.json()["url"].startswith(f"{LINK_BASE}/welcome/")
    assert mailer.sent == []


def test_an_approved_plan_emails_the_invitation_through_the_outbox(client):
    mailer = _on(client)
    _enrol(client)
    out = _approve(client)
    inv = out["result"]["enrolments"][0]["invitation"]
    assert inv["email_queued"] is True and inv["sent"] is False and "on its way" in inv["note"]
    [row] = [r for r in _c(client).store.outbox.values() if r["event"]["type"] == "mail.invitation"]
    assert set(row["event"]) == {"type", "project_id", "onboarding_id", "invitation_id", "dedupe"}  # ids only: no address, no link
    assert row["sent_at"] is not None  # the (demo's inline) worker handled it
    [mail] = mailer.sent
    assert mail.to == "nina@example.com" and "Mini Policy Engine" in mail.subject
    assert f"{LINK_BASE}/login" in mail.text and "Hi Nina," in mail.text and "Priya" in mail.text and "Do not reply" in mail.text


def test_a_failing_mail_server_never_fails_the_decision_and_the_outbox_keeps_the_event(client):
    class Down(OutboxMailer):
        def send(self, *a, **k):
            raise MailError("the mail server could not be reached")

    _c(client).deps.mailer = Down(enabled=True)
    _enrol(client)
    out = _approve(client)  # 200: the plan is approved, the invitation exists, its link is returned
    assert out["approval"]["status"] == "approved" and out["result"]["enrolments"][0]["invitation"]["link"]
    [row] = [r for r in _c(client).store.outbox.values() if r["event"]["type"] == "mail.invitation"]
    assert row["sent_at"] is None  # still due: the outbox retries it


def test_an_invitation_already_accepted_is_not_emailed(client):
    mailer = _on(client)
    _enrol(client)
    _approve(client)
    mailer.sent.clear()
    c = _c(client)
    [row] = [r for r in c.store.outbox.values() if r["event"]["type"] == "mail.invitation"]
    inv_id = row["event"]["invitation_id"]
    for i in c.store.invites.values():
        if i["id"] == inv_id:
            i["accepted_at"] = "now"
    assert c.handle({**row["event"], "dedupe": "mail:again"}) == {"skipped": "the invitation is gone or already accepted"}
    assert mailer.sent == []


def test_the_preboarding_link_is_emailed_when_the_manager_asks_and_never_stored_with_the_message(client, caplog):
    mailer = _on(client)
    with caplog.at_level(logging.INFO):
        r = client.post("/api/onboardings/ob-arjun/preboarding-link", json={"email": True}, headers=PRIYA)
    assert r.status_code == 201 and r.json()["emailed"] is True
    token = r.json()["token"]
    [mail] = mailer.sent  # TestClient runs the after-response task before returning
    assert mail.to == "arjun@demo.dev" and mail.subject == "Your pre-boarding page for Mini Policy Engine"
    assert f"{LINK_BASE}/welcome/{token}" in mail.text and "Hi Arjun," in mail.text
    assert not any(token in str(r.__dict__) for r in caplog.records if not r.name.startswith("httpx"))  # no log line has it
    assert token not in str(list(_c(client).store.outbox.values())) and token not in str(_c(client).store.audit_rows)  # nor the outbox, nor the audit
    audit = next(a for a in reversed(_c(client).store.audit_rows) if a["action"] == "preboarding.link_created")
    assert audit["detail"]["emailed"] is True and "arjun@demo.dev" not in str(audit)
    # without the flag, a link is made and nothing is sent
    mailer.sent.clear()
    assert client.post("/api/onboardings/ob-arjun/preboarding-link", headers=PRIYA).json()["emailed"] is False
    assert client.post("/api/onboardings/ob-arjun/preboarding-link", json={"email": False}, headers=PRIYA).json()["emailed"] is False
    assert mailer.sent == []


def test_a_mail_failure_on_the_preboarding_link_does_not_fail_the_request(client):
    class Down(OutboxMailer):
        def send(self, *a, **k):
            raise MailError("down")

    _c(client).deps.mailer = Down(enabled=True)
    r = client.post("/api/onboardings/ob-arjun/preboarding-link", json={"email": True}, headers=PRIYA)
    assert r.status_code == 201 and r.json()["token"]


def test_the_expert_digest_is_emailed_from_the_tick_as_a_count_and_a_link(client):
    mailer = _on(client)
    c = _c(client)
    c.lifecycle.save_expert_preferences("org-demo", type(c.lifecycle.expert_preferences("u-ravi"))(user_id="u-ravi", max_interruptions_per_day=0,
                                                                                                    digest=True))
    asked = client.post("/api/chat", json={"project_id": PID, "question": "How is the mutate module benchmarked?"}, headers=VIJAY).json()
    assert asked["routed_to"] == "u-ravi"
    mailer.sent.clear()
    assert client.post("/api/agents/tick", headers=PRIYA).status_code == 200
    digest = [m for m in mailer.sent if m.to == "ravi@demo.dev" and "wait" in m.subject]
    assert len(digest) == 1
    assert "question" in digest[0].text and f"{LINK_BASE}/#/experts?project={PID}" in digest[0].text
    assert "mutate" not in digest[0].text and "benchmarked" not in digest[0].text  # never what the hire asked
    mailer.sent.clear()
    client.post("/api/agents/tick", headers=PRIYA)
    assert not [m for m in mailer.sent if m.to == "ravi@demo.dev" and "wait" in m.subject]  # once per question


def test_the_weekly_summary_goes_to_the_hire_and_the_manager_when_the_week_closes(client):
    mailer = _on(client)
    assert client.post("/api/demo/advance", json={"days": 8}, headers=PRIYA).status_code == 200
    to = {m.to: m for m in mailer.sent if "week" in m.subject.lower()}
    assert "vijay@demo.dev" in to and "priya@demo.dev" in to
    assert to["vijay@demo.dev"].subject.startswith("Your week:") and "Vijay's week:" in " ".join(m.subject for m in mailer.sent if m.to == "priya@demo.dev")
    assert "Hi Vijay," in to["vijay@demo.dev"].text and f"{LINK_BASE}/#/program/ob-vijay?project={PID}" in to["vijay@demo.dev"].text
    per_hire = [m for m in mailer.sent if m.to == "vijay@demo.dev" and m.subject.startswith("Your week:")]
    assert len(per_hire) == 1  # the newest closed week only, not a backfill of the older ones


def test_a_failure_in_the_tick_does_not_stop_it(client):
    class Down(OutboxMailer):
        def send(self, *a, **k):
            raise MailError("down")

    _c(client).deps.mailer = Down(enabled=True)
    assert client.post("/api/demo/advance", json={"days": 8}, headers=PRIYA).status_code == 200
    assert _c(client).lifecycle.summaries("ob-vijay")  # the summaries were written all the same
