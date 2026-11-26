"""Email: SMTP (smtplib, no extra dependency) and an in-memory mailer for the demo and tests.

SmtpMailer   port 465 opens an SSL connection; any other port (587 by default) connects in the clear and upgrades with STARTTLS
             before logging in: credentials never cross the network unencrypted. A message is one short connection.
OutboxMailer keeps what it was asked to send in a list. `enabled` is False by default, which is the demo and every
             installation without SMTP: callers then send nothing at all, and /api/config says `email: false`. Tests make one
             with enabled=True to read what would have been sent.

Neither logs a message's subject, body, links or recipient: a failure is logged as its error type and the recipient's domain.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

log = logging.getLogger("mailer")
TIMEOUT_S = 20.0


class MailError(RuntimeError):
    """The message could not be handed to the mail server. Carries no message text."""


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1][:80] if "@" in address else ""


def build(sender: str, to: str, subject: str, text: str, html: str | None = None) -> EmailMessage:
    """A plain-text message, with an HTML alternative when given. Header values cannot carry a newline (no header injection)."""
    if any(c in f"{sender}{to}{subject}" for c in "\r\n"):
        raise MailError("a header value contained a line break")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg["Date"], msg["Message-ID"] = formatdate(localtime=False), make_msgid(domain=_domain(sender) or "welcome-wave.local")
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


class SmtpMailer:
    enabled = True

    def __init__(self, host: str, port: int, user: str, password: str, sender: str, timeout: float = TIMEOUT_S):
        self.host, self.port, self.user, self.password, self.sender, self.timeout = host, int(port), user, password, sender, timeout

    def _connect(self):
        if self.port == 465:
            return smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ssl.create_default_context())
        smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        smtp.ehlo()
        smtp.starttls(context=ssl.create_default_context())
        smtp.ehlo()
        return smtp

    def send(self, to, subject, text, html=None):
        msg = build(self.sender, to, subject, text, html)
        try:
            smtp = self._connect()
            try:
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
            finally:
                try:
                    smtp.quit()
                except (smtplib.SMTPException, OSError):
                    pass
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            log.warning("mail not sent", extra={"extra_fields": {"error": type(e).__name__, "to_domain": _domain(to)}})
            raise MailError(f"the mail server refused or could not be reached ({type(e).__name__})") from None
        log.info("mail sent", extra={"extra_fields": {"to_domain": _domain(to)}})

    def check(self) -> str:
        """Connect and log in (no message is sent); what a person needs to see in the status check. Raises MailError."""
        try:
            smtp = self._connect()
            try:
                if self.user:
                    smtp.login(self.user, self.password)
            finally:
                try:
                    smtp.quit()
                except (smtplib.SMTPException, OSError):
                    pass
        except smtplib.SMTPAuthenticationError:
            raise MailError("the server refused the user name or password") from None
        except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
            raise MailError(f"could not connect ({type(e).__name__})") from None
        return f"connected to {self.host}:{self.port}" + (" and logged in" if self.user else "")


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str
    html: str | None = None


class OutboxMailer:
    """What was asked to be sent, in memory (the newest `keep`)."""

    def __init__(self, enabled: bool = False, keep: int = 200):
        self.enabled, self.keep = enabled, keep
        self.sent: list[Mail] = []

    def send(self, to, subject, text, html=None):
        self.sent.append(Mail(to, subject, text, html))
        del self.sent[:-self.keep]


def mailer_for(settings):
    """SMTP when configured, else a mailer that sends nothing."""
    if settings.email_ready:
        return SmtpMailer(settings.smtp_host, settings.smtp_port, settings.smtp_user, settings.smtp_password, settings.smtp_sender)
    return OutboxMailer(enabled=False)
