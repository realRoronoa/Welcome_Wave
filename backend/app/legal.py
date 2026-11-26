"""The legal documents: their versions, who must accept which, and the record of each acceptance.

The documents themselves (terms of service, privacy policy, data-processing agreement, recording notice, sub-processor
list) are written in the web app, as structured content: frontend/src/legal/documents.ts. This module holds what the
server needs to know about them and nothing of their text:

  VERSIONS      the current version of each document, a date. The same table is in documents.ts; tests/test_legal.py reads
                that file and fails when the two differ, so the version a person is shown is the version that is recorded.
                Changing a document means changing its date in BOTH places: everyone is then asked again, once.
  PERSONAL      what each person accepts for themselves: the terms, the privacy policy, and the recording notice (an
                acknowledgement that KT sessions here are recorded and what happens to the recordings).
  REQUIRED      the two of those that sign-up, joining by invitation and LEGAL_ENFORCE are about: terms and privacy.
  ORGANISATION  what an admin accepts for the whole organisation: the data-processing agreement.
  (the sub-processor list has a version, so a change to it can be shown and dated; nobody "accepts" it.)

An acceptance is one append-only row (db/migrations/019_legal.sql; ports.AppStore.record_legal_acceptance): who, which
organisation, which document, which version, when, how (sign-up, invitation, the in-app prompt, or for the organisation),
and two facts about where it came from, kept deliberately small:

  ip_prefix     the network, never the address: IPv4 cut to /24, IPv6 to /48. Enough to say "from the customer's office
                network" if an acceptance is ever disputed; not enough to single out a device or a home.
  user_agent    the browser's own description, cut to 200 characters: that it was a click in a browser, and which.

The person is already signed in with a verified login when they accept, so the account is the proof of who; the full
address would add little and is personal data, so it is never stored.

Erasure. A row names a person by id only. Erasing a person replaces their name and email on the user row and leaves their
acceptances standing under the erased identity, as it leaves their onboarding records: the organisation can still show that
an acceptance happened, and when. Deleting the organisation deletes its acceptances with everything else. Both are
described in docs/specs/legal-consent.md.

The operator's own details (legal name, address, contact addresses, governing law, courts) are settings, never text in the
code: LEGAL_OPERATOR_NAME and the rest (app/config.py). `details()` cleans them; one that is not set is None, and the pages
then say "the company that operates this service" rather than a made-up name or a placeholder. None of them is needed to
start the server; `settings_report()` says which are missing (./scripts/selfhost.sh check).
"""
from __future__ import annotations

import ipaddress
import re
import threading
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app.ports import User

VERSIONS: dict[str, str] = {
    "terms": "2026-11-19",
    "privacy": "2026-11-19",
    "dpa": "2026-11-19",
    "recording": "2026-11-19",
    "subprocessors": "2026-11-19",
}
PERSONAL = ("terms", "privacy", "recording")
REQUIRED = ("terms", "privacy")
ORGANISATION = ("dpa",)
TITLES = {"terms": "Terms of Service", "privacy": "Privacy Policy", "dpa": "Data Processing Agreement",
          "recording": "Recording Notice", "subprocessors": "Sub-processors"}
METHODS = ("signup", "invitation", "prompt", "organisation")
NEUTRAL_OPERATOR = "the company that operates this service"
USER_AGENT_MAX = 200

LEGAL_REQUIRED = "legal_required"
ACCEPT_FIRST = "Accept the Terms of Service and the Privacy Policy to continue."


# ---------------------------------------------------------------- the operator's details (settings)
# (the /api/config key, the setting, what it is, what to do when it is missing)
_DETAILS = (
    ("operator", "legal_operator_name", "The company's registered legal name",
     "the documents say \"the company that operates this service\""),
    ("address", "legal_operator_address", "Its registered address", "the documents give no postal address"),
    ("contact_email", "legal_contact_email", "Where legal notices and questions go", "the documents give no contact address"),
    ("privacy_email", "legal_privacy_email", "Where privacy requests go (access, erasure, complaints)",
     "the privacy policy gives no address for privacy requests"),
    ("governing_law", "legal_governing_law", "The law the terms are governed by (decide with your lawyer)",
     "the terms name no governing law"),
    ("courts", "legal_courts", "The courts that hear disputes (decide with your lawyer)", "the terms name no courts"),
)
_EMAIL_KEYS = frozenset({"contact_email", "privacy_email"})
_EMAIL = re.compile(r"^[^@\s<>\[\]:]+@[^@\s<>\[\]]+\.[^@\s<>\[\]]+$")  # no ":" before the @: "mailto:x@y.co" is a link, not an address
# A value somebody left in from a template is not a value: [COMPANY], <your company>, "TODO", "unused", "changeme".
_PLACEHOLDER = re.compile(r"^(\[.*\]|<.*>|\{.*\}|x{3,}|todo|tbd|unused|none|n/?a|change[-_ ]?me|"
                          r"your[ _-](company|legal|registered|name|address|email|law|courts?)\b.*)$", re.IGNORECASE)


# Also a placeholder wherever it sits in the value: an ellipsis (the documentation's own examples read "the laws of …"), an
# unfilled {{token}}, text made only of punctuation, or the words people leave in a template.
_PLACEHOLDER_INSIDE = re.compile(r"…|\.{3,}|\{\{|\}\}|replace[ _-]?me|place[ _-]?holder|company[ _-]?name|lorem ipsum|"
                                 r"\b(insert|fill[ _-]?in)\b|\byour[ _-](company|legal|registered)\b", re.IGNORECASE)


def _clean(key: str, value) -> str | None:
    text = " ".join(str(value or "").split())
    if not text or _PLACEHOLDER.match(text) or _PLACEHOLDER_INSIDE.search(text) or not any(ch.isalnum() for ch in text):
        return None
    if key in _EMAIL_KEYS and not _EMAIL.match(text):
        return None
    return text[:500]


def details(settings) -> dict:
    """The operator's details as the documents may show them: each a cleaned string, or None when it is not set (or is a
    left-over placeholder, or is not an email address where one is expected)."""
    return {key: _clean(key, getattr(settings, attr, "")) for key, attr, _what, _missing in _DETAILS}


def public(settings) -> dict:
    """The `legal` block of GET /api/config: the operator's details, the current version of every document, and how this
    server is set up where the documents' words depend on it (`deployment`)."""
    return {**details(settings), "versions": dict(VERSIONS), "enforced": bool(getattr(settings, "legal_enforce", False)),
            "deployment": deployment(settings)}


# ---------------------------------------------------------------- how this server is set up (what the documents branch on)
def _tracing_region() -> str:
    """Where LangSmith keeps traces, by its endpoint: 'us' (the default), 'eu', or 'other' (a self-hosted LangSmith)."""
    import os
    from urllib.parse import urlsplit

    host = (urlsplit(os.environ.get("LANGSMITH_ENDPOINT") or os.environ.get("LANGCHAIN_ENDPOINT") or "").hostname or "").lower()
    if not host or host == "api.smith.langchain.com":
        return "us"
    return "eu" if host == "eu.api.smith.langchain.com" else "other"


def deployment(settings) -> dict:
    """The facts the documents leave open ("on Google Cloud ... / on a single server ...", "only when the operator turns it
    on", "the number of days the operator has set"), as this server is set up. The pages show them as "On this service", so
    a reader knows which branch of the text is theirs; `settings_report` warns when one adds a sub-processor. Nothing here is
    secret: each is something the sub-processor list or the privacy policy must tell a reader anyway.

      hosting                   'google_cloud' (MODE=gcp), 'single_server' (MODE=self_hosted) or 'demo'
      region                    Google Cloud: GCP_REGION, where the database and the file store are
      ai                        'vertex_ai', 'gemini_api' (a GEMINI_API_KEY is set: it is used in either mode), 'offline' (the
                                demo's stand-in) or 'none' (self-hosted without a key: the AI is off)
      ai_location               Vertex AI: VERTEX_LOCATION ('global' lets Google serve from any region)
      email                     an SMTP provider is set up, so invitations and summaries are emailed
      tracing, tracing_region   LANGSMITH_TRACING is on, and where LangSmith keeps the traces
      recording_retention_days  single server: RECORDING_RETENTION_DAYS, 0 when recordings are kept until someone removes
                                them. Google Cloud: None, because the file store's own rule deletes them (30 days in
                                infra/terraform/data.tf), which this process cannot see
      audit_retention_days      AUDIT_RETENTION_DAYS, never below 30
      verified_email            joining or creating an organisation needs a verified email address (REQUIRE_VERIFIED_EMAIL)
    """
    mode = getattr(settings, "mode", "demo")
    hosting = {"gcp": "google_cloud", "self_hosted": "single_server"}.get(mode, "demo")
    key = bool(getattr(settings, "gemini_api_key", ""))
    ai = "gemini_api" if key else {"google_cloud": "vertex_ai", "single_server": "none"}.get(hosting, "offline")
    tracing = bool(getattr(settings, "langsmith_tracing", False))
    recording_days: int | None = None
    audit_days = 365
    try:  # their own settings classes (the same environment); imported here because both modules import this one
        from app.adapters.audit_store import MIN_RETENTION_DAYS
        from app.api.audit import audit_settings
        from app.services.recordings import recording_settings

        audit_days = max(MIN_RETENTION_DAYS, audit_settings().audit_retention_days)
        if hosting == "single_server":
            r = recording_settings()
            recording_days = max(0, r.recording_retention_days) if r.recording_retention_every_s > 0 else 0
    except Exception:  # noqa: BLE001 - a fact that cannot be read is left out, never guessed
        audit_days = None
    return {
        "hosting": hosting,
        "region": (getattr(settings, "gcp_region", "") or None) if hosting == "google_cloud" else None,
        "ai": ai,
        "ai_location": (getattr(settings, "vertex_location", "") or None) if ai == "vertex_ai" else None,
        "email": bool(getattr(settings, "email_ready", False)),
        "tracing": tracing,
        "tracing_region": _tracing_region() if tracing else None,
        "recording_retention_days": recording_days,
        "audit_retention_days": audit_days,
        "verified_email": bool(getattr(settings, "require_verified_email", True)),
    }


def deployment_report(settings) -> list[dict]:
    """What ./scripts/selfhost.sh check says about the settings that change what the legal documents must tell a reader:
    {env, level ('note' or 'warn'), detail}. A provider that handles customers' data and is turned on by a setting is a
    sub-processor: the data-processing agreement promises admins 30 days' notice before one starts."""
    d, out = deployment(settings), []
    if d["tracing"]:
        out.append({"env": "LANGSMITH_TRACING", "level": "warn",
                    "detail": "Tracing is on: every agent run's prompts and outputs go to LangSmith, a sub-processor. Tell each "
                              "customer's admins 30 days before turning it on for their data (the data-processing agreement, "
                              "section 7). Traces stay at LangSmith under its own retention: removing a recording, erasing a "
                              "person or deleting an organisation here does not remove them there."})
    if d["email"]:
        out.append({"env": "SMTP_HOST", "level": "warn",
                    "detail": "Email is on: the mail provider receives recipients' addresses and the messages, so it is a "
                              "sub-processor. Name it in the sub-processor list (docs/LEGAL.md) and give admins 30 days' notice "
                              "before changing it."})
    if d["hosting"] == "google_cloud" and d["ai"] == "gemini_api":
        out.append({"env": "GEMINI_API_KEY", "level": "warn",
                    "detail": "A Gemini API key is set on Google Cloud: it is used in place of Vertex AI, so AI requests leave "
                              "under the Gemini API's terms and Google chooses the region. Unset it to use Vertex AI, which the "
                              "sub-processor list names for Google Cloud."})
    if d["ai"] == "gemini_api" and d["hosting"] != "demo":
        out.append({"env": "GEMINI_API_KEY", "level": "note",
                    "detail": "Use a paid key for real data: on the free tier Google may use what is sent, recordings included, "
                              "and people at Google may read it (docs/INTEGRATIONS.md)."})
    if d["ai"] == "vertex_ai" and (d["ai_location"] or "global") == "global":
        out.append({"env": "VERTEX_LOCATION", "level": "note",
                    "detail": "global: Google may serve AI requests from any region. Set a region where a customer needs its "
                              "data processed in one place."})
    if d["hosting"] == "single_server" and not d["recording_retention_days"]:
        out.append({"env": "RECORDING_RETENTION_DAYS", "level": "note",
                    "detail": "Not set: recording video files are kept until someone removes them. Set a number of days you can "
                              "defend; the recording notice shows it."})
    if not d["verified_email"]:
        out.append({"env": "REQUIRE_VERIFIED_EMAIL", "level": "warn",
                    "detail": "Off: a login with an unverified address can join or create an organisation. The data-processing "
                              "agreement's security annex describes it as on by default."})
    return out


def settings_report(settings) -> list[dict]:
    """One line per operator detail for ./scripts/selfhost.sh check: {env, set, detail}. Never the value itself beyond
    what the documents show anyway; a value that was given and rejected says why."""
    found, out = details(settings), []
    for key, attr, what, missing in _DETAILS:
        raw = " ".join(str(getattr(settings, attr, "") or "").split())
        env = attr.upper()
        if found[key]:
            out.append({"env": env, "set": True, "detail": f"{what}."})
        elif raw and key in _EMAIL_KEYS and not _PLACEHOLDER.match(raw):
            out.append({"env": env, "set": False, "detail": f"{what}. The value given is not an email address, so it is not shown: {missing}."})
        elif raw:
            out.append({"env": env, "set": False, "detail": f"{what}. The value given is a placeholder, so it is not shown: {missing}."})
        else:
            out.append({"env": env, "set": False, "detail": f"{what}. Until it is set, {missing}."})
    return out


# ---------------------------------------------------------------- where an acceptance came from (kept small)
def ip_prefix(address: str | None) -> str | None:
    """The network an address belongs to, never the address: IPv4 /24, IPv6 /48 (an IPv4-mapped IPv6 address as IPv4).
    None for anything that is not an address."""
    try:
        ip = ipaddress.ip_address((address or "").strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    bits = 24 if ip.version == 4 else 48
    return str(ipaddress.ip_network(f"{ip}/{bits}", strict=False))


def user_agent(value: str | None) -> str | None:
    """The browser's description as one printable line of at most 200 characters, or None."""
    text = "".join(ch if ch.isprintable() else " " for ch in (value or ""))
    text = " ".join(text.split())[:USER_AGENT_MAX].strip()
    return text or None


def one_line(value, limit: int = 320) -> str:
    """Any text as one printable line (line breaks and control characters become spaces, runs of spaces one), for a record
    that states one fact per line."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(text.split())[:limit].strip()


def client_facts(request: Request | None) -> tuple[str | None, str | None]:
    """(ip_prefix, user_agent) of the request, by the address the rate limits trust (never a client-written header)."""
    if request is None:
        return None, None
    from app.ratelimit import request_address

    try:
        address = request_address(request.scope)
    except Exception:  # noqa: BLE001 - where it came from is optional; the acceptance is not
        address = None
    return ip_prefix(address), user_agent(request.headers.get("user-agent"))


# ---------------------------------------------------------------- what a person has accepted, and still needs to
def _iso(at) -> str | None:
    if isinstance(at, datetime):
        return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(at) if at else None


def accepted_versions(rows: list[dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["document"], set()).add(r["version"])
    return out


def needs(c, user: User) -> list[str]:
    """The personal documents whose current version this person has not accepted, in PERSONAL order."""
    have = accepted_versions(c.store.legal_acceptances(user.org_id, user_id=user.id))
    return [d for d in PERSONAL if VERSIONS[d] not in have.get(d, ())]


def status(c, user: User) -> dict:
    """GET /api/legal/status: what the caller still needs to accept, and everything they have accepted (newest first)."""
    rows = [r for r in c.store.legal_acceptances(user.org_id, user_id=user.id) if r["document"] in PERSONAL]
    have = accepted_versions(rows)
    return {
        "needs": [d for d in PERSONAL if VERSIONS[d] not in have.get(d, ())],
        "accepted": [{"document": r["document"], "version": r["version"], "at": _iso(r["accepted_at"])} for r in rows],
        "versions": dict(VERSIONS),
        "required": list(REQUIRED),
        "enforced": bool(c.settings.legal_enforce),
    }


def has_acknowledged(c, user: User, document: str = "recording") -> bool:
    """This person accepted the current version of one personal document (the recording notice, for the recording routes)."""
    have = accepted_versions(c.store.legal_acceptances(user.org_id, user_id=user.id, document=document))
    return VERSIONS[document] in have.get(document, ())


# ---------------------------------------------------------------- recording an acceptance
def check_documents(documents: list[str], allowed: tuple[str, ...]) -> list[str]:
    """The asked documents, once each, in a fixed order. 422 for one that cannot be accepted here."""
    asked = list(dict.fromkeys(str(d).strip().lower() for d in documents))
    if "dpa" in asked and "dpa" not in allowed:
        raise HTTPException(422, detail={"code": "dpa_is_organisation", "message": "The Data Processing Agreement is accepted for the whole "
                                         "organisation, by an admin: POST /api/org/legal/dpa."})
    unknown = [d for d in asked if d not in allowed]
    if unknown:
        raise HTTPException(422, detail={"code": "unknown_document", "message": f"Not a document to accept here: {', '.join(unknown)}.",
                                         "documents": list(allowed)})
    return [d for d in (*PERSONAL, *ORGANISATION) if d in asked]


def check_versions(shown: dict[str, str] | None, documents: list[str]) -> None:
    """The version the person was shown is still the current one: 409 `version_changed` when a document changed between the
    page loading and the click, so nobody accepts a version they did not see. Optional: without `versions` the current ones apply."""
    stale = {d: VERSIONS[d] for d in documents if shown and d in shown and shown[d] != VERSIONS[d]}
    if stale:
        raise HTTPException(409, detail={"code": "version_changed", "message": "This document changed since the page loaded. Read the "
                                         "current version, then accept it.", "versions": stale})


def require_on_joining(accept: list[str], allowed: tuple[str, ...] = PERSONAL) -> list[str]:
    """Sign-up and joining by invitation: the request itself must say the person accepts the terms and the privacy policy
    (422 `legal_required` otherwise, and nothing is created). Returns the documents to record, which may also hold the
    recording notice (and the data-processing agreement, where `allowed` has it)."""
    documents = check_documents(accept or [], allowed)
    missing = [d for d in REQUIRED if d not in documents]
    if missing:
        raise HTTPException(422, detail={"code": LEGAL_REQUIRED, "message": ACCEPT_FIRST, "needs": missing, "versions": dict(VERSIONS)})
    return documents


def record(c, user: User, documents: list[str], method: str, request: Request | None = None) -> list[str]:
    """Record this person's acceptance of the current version of each document, and audit it. Idempotent: a version already
    accepted is not recorded (or audited) again. Returns the documents newly recorded. 'dpa' in `documents` records the
    organisation's acceptance, by this person (the caller has checked that they are an admin)."""
    prefix, agent = client_facts(request)
    personal, done = [], []
    for d in documents:
        how = "organisation" if d in ORGANISATION else method
        if c.store.record_legal_acceptance(user.org_id, user.id, d, VERSIONS[d], how, prefix, agent) is not None:
            done.append(d)
            if d not in ORGANISATION:
                personal.append(d)
    if personal:
        c.store.audit(user.org_id, user.id, "legal.accepted", user.id, {"documents": {d: VERSIONS[d] for d in personal}, "method": method})
    if "dpa" in done:
        c.store.audit(user.org_id, user.id, "legal.dpa_accepted", user.org_id, {"version": VERSIONS["dpa"], "method": method})
    if done:
        _satisfied.forget(user.id)
    return done


# ---------------------------------------------------------------- LEGAL_ENFORCE: the API refuses the workspace until accepted
# What a person who has not accepted yet may still call: who they are, the documents' status and the acceptance itself,
# leaving, their notifications, and (admins) the organisation's export, deletion and data-processing agreement. Signing in,
# reading the documents (the web app's own pages) and GET /api/config need no account at all.
_EXEMPT = re.compile(r"^/api/(me|me/leave|legal(/.*)?|org|org/export|org/legal(/.*)?|notifications(/read)?|live)/?$")


class _Satisfied:
    """People known to have accepted the current REQUIRED versions, so an enforced server reads the table once per person
    and process, not on every request. Rows are never removed or changed, so a yes stays a yes until VERSIONS change (a
    new release, a new process)."""

    def __init__(self, cap: int = 50_000):
        self._lock, self._ids, self._cap = threading.Lock(), set(), cap

    def has(self, user_id: str) -> bool:
        with self._lock:
            return user_id in self._ids

    def add(self, user_id: str) -> None:
        with self._lock:
            if len(self._ids) >= self._cap:
                self._ids.clear()
            self._ids.add(user_id)

    def forget(self, user_id: str | None = None) -> None:
        with self._lock:
            self._ids.clear() if user_id is None else self._ids.discard(user_id)


_satisfied = _Satisfied()


def enforce(c, user: User, method: str, path: str) -> None:
    """With LEGAL_ENFORCE on: 403 `legal_required` (with what is needed) on every workspace route for a person who has not
    accepted the current terms and privacy policy. Off (the default), the web app asks at sign-in and the API refuses nothing."""
    if not c.settings.legal_enforce or _EXEMPT.match(path) or _satisfied.has(user.id):
        return
    have = accepted_versions(c.store.legal_acceptances(user.org_id, user_id=user.id))
    missing = [d for d in REQUIRED if VERSIONS[d] not in have.get(d, ())]
    if not missing:
        _satisfied.add(user.id)
        return
    raise HTTPException(403, detail={"code": LEGAL_REQUIRED, "message": ACCEPT_FIRST, "needs": missing, "versions": dict(VERSIONS)})


# ---------------------------------------------------------------- the organisation's data-processing agreement
def _person(c, org_id: str, user_id: str) -> dict:
    u = c.store.get_user(user_id)
    if u is None or u.org_id != org_id:
        return {"id": user_id, "name": None, "email": None}
    return {"id": u.id, "name": u.name, "email": u.email}


def org_status(c, org_id: str) -> dict:
    """GET /api/org/legal: whether the organisation accepted the current data-processing agreement, by whom and when, and the
    versions it accepted before."""
    rows = c.store.legal_acceptances(org_id, document="dpa")
    entry = lambda r: {"version": r["version"], "accepted_at": _iso(r["accepted_at"]), "accepted_by": _person(c, org_id, r["user_id"])}  # noqa: E731
    current = next((r for r in rows if r["version"] == VERSIONS["dpa"]), None)
    return {
        "dpa": {"version": VERSIONS["dpa"], "accepted": current is not None,
                "accepted_at": _iso(current["accepted_at"]) if current else None,
                "accepted_by": _person(c, org_id, current["user_id"]) if current else None,
                "previous": [entry(r) for r in rows if r["version"] != VERSIONS["dpa"]]},
        "versions": dict(VERSIONS),
        "operator": details(c.settings),
    }


def dpa_record(c, org_id: str, version: str | None = None) -> tuple[str, str] | None:
    """The text record of the organisation's acceptance of the data-processing agreement: (file name, text), or None when
    that version (default: the newest accepted) was never accepted. It records the acceptance, not the agreement's text."""
    rows = c.store.legal_acceptances(org_id, document="dpa")
    row = next((r for r in rows if version is None or r["version"] == version), None)
    if row is None:
        return None
    who, d = _person(c, org_id, row["user_id"]), details(c.settings)
    # one fact per line: a name or an organisation's name is collapsed to one printable line, so it cannot add a line
    # that reads as another fact ("Version: …", a second "Accepted by: …")
    name, email, org = one_line(who["name"]), one_line(who["email"]), one_line(c.store.org_name(org_id))
    by = f"{name} <{email}>" if name else "a person whose account no longer exists"
    lines = [
        "Welcome Wave",
        "Data Processing Agreement: record of acceptance",
        "",
        f"Organisation:      {org}",
        f"Organisation id:   {org_id}",
        f"Document:          {TITLES['dpa']}",
        f"Version:           {row['version']}",
        f"Accepted at (UTC): {_iso(row['accepted_at'])}",
        f"Accepted by:       {by}",
        f"Their user id:     {who['id']}",
        "Their role then:   admin (only an admin can accept for the organisation)",
        f"Operator:          {d['operator'] or NEUTRAL_OPERATOR}",
    ]
    if d["address"]:
        lines.append(f"Operator address:  {d['address']}")
    if row.get("ip_prefix"):
        lines.append(f"Network:           {row['ip_prefix']} (the network only, never the full address)")
    lines += [
        f"Record number:     {row['id']}",
        f"Record made:       {_iso(datetime.now(timezone.utc))}",
        "",
        "What this is. A record that the person above, signed in as an admin of the organisation, accepted the version of",
        "the Data Processing Agreement named above, as it was published in the product on that date. It is the record of",
        "the acceptance, not the text of the agreement: keep a copy of that version's text with this file.",
        "If the person has since been erased from the organisation, their name and email are shown as erased; the user id,",
        "the version and the time stay.",
        "",
    ]
    return f"welcome-wave-dpa-acceptance-{row['version']}.txt", "\n".join(lines)
