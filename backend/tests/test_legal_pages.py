"""The legal documents in the web app: their pages and the agreement the screens send (frontend/src/legal/paths.ts,
frontend/src/site/Legal.tsx, frontend/src/auth/Agree.tsx, frontend/src/consent/).

These read the web app's source, as tests/test_legal.py reads its versions: a page that is published must be served, a
link the server writes into a calendar invitation must lead to a page, and what the sign-up screens send must be what the
API requires. Each of these is one string kept in several files; this is where they are held together."""
from __future__ import annotations

import re
from pathlib import Path

from app import legal
from app.services import recordings

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "frontend"
SRC = WEB / "src"


def _pages() -> dict[str, str]:
    """path -> document id, from src/legal/paths.ts."""
    text = (SRC / "legal" / "paths.ts").read_text(encoding="utf-8")
    found = dict(re.findall(r'\{\s*path:\s*"(/[a-z-]+)",\s*id:\s*"([a-z_]+)"', text))
    assert found, "could not read LEGAL_PAGES from paths.ts"
    return found


def _alternatives(pattern: str, text: str) -> set[str]:
    m = re.search(pattern, text)
    assert m, f"pattern not found: {pattern}"
    return {f"/{part}" for part in m.group(1).split("|")}


def test_every_document_has_a_page_and_every_page_a_document():
    pages = _pages()
    documents = set(re.findall(r"^\s{2}([a-z_]+): versioned\(", (SRC / "legal" / "documents.ts").read_text(encoding="utf-8"), re.M))
    assert documents == {"terms", "privacy", "recording", "dpa", "subprocessors", "acceptable_use"}
    assert set(pages.values()) == documents
    assert len(set(pages)) == len(pages) == len(documents)
    # everything a person or an organisation accepts can be read at an address
    assert {*legal.PERSONAL, *legal.ORGANISATION} <= set(pages.values())


def test_the_web_server_and_the_intro_know_the_same_pages():
    """nginx serves the shell with 200 for exactly these paths (any other unknown path is a 404), and boot-theme.js opens
    them without the intro. Both repeat the list in paths.ts."""
    pages = set(_pages())
    nginx = (WEB / "nginx.conf.template").read_text(encoding="utf-8")
    served = _alternatives(r"location ~ \^/\((terms\|[a-z|-]+)\)/\?\$ \{ rewrite \^ /index\.html last; \}", nginx)
    assert served == pages
    boot = (WEB / "public" / "boot-theme.js").read_text(encoding="utf-8")
    quiet = _alternatives(r"var LEGAL = /\^\\/\(([a-z|-]+)\)\\/\?\$/;", boot)
    assert quiet == pages
    # unknown paths still answer 404 with the shell
    assert "try_files $uri =404;" in nginx and "error_page 404 /index.html;" in nginx


def test_no_legal_page_is_an_address_of_the_app():
    """surfaceFor() (src/surface.ts) gives the app /login, /signup, /github/callback, /app and /welcome/<token>; everything
    else is the site's. A legal page under one of those would never render."""
    for path in _pages():
        assert not re.match(r"^/(login|signup|github/callback|app|welcome)(/|$)", path)
        assert not path.startswith("/api") and not path.startswith("/assets") and not path.startswith("/webhooks")


def test_the_recording_notice_the_invitation_links_to_is_a_page():
    assert _pages().get(recordings.NOTICE_PATH) == "recording"


def test_the_join_screens_send_what_the_api_requires():
    """Creating an organisation and joining by invitation post `accept` with the terms and the privacy policy: without it
    the API creates nothing (legal.require_on_joining), so a web app that stopped sending it would lock sign-up."""
    agree = (SRC / "auth" / "Agree.tsx").read_text(encoding="utf-8")
    m = re.search(r"export const JOIN_DOCUMENTS = \[([^\]]+)\] as const;", agree)
    assert m and tuple(re.findall(r'"([a-z_]+)"', m.group(1))) == legal.REQUIRED
    assert "accept: JOIN_DOCUMENTS" in (SRC / "auth" / "Setup.tsx").read_text(encoding="utf-8")
    assert "accept: JOIN_DOCUMENTS" in (SRC / "auth" / "Invited.tsx").read_text(encoding="utf-8")
    # the box is never ticked for the person: no screen starts it checked
    for name in ("SignIn.tsx", "Setup.tsx", "Invited.tsx"):
        assert "useState(false);\n  const [missed" in (SRC / "auth" / name).read_text(encoding="utf-8"), name
    assert "defaultChecked" not in agree


def test_the_recording_confirmation_shows_the_statement_that_is_recorded():
    """The checkbox before a recording is added says, word for word, what the API records the person as confirming."""
    assert recordings.CONSENT_STATEMENT in " ".join((SRC / "pages" / "Meetings.tsx").read_text(encoding="utf-8").split())


def test_the_consent_screen_asks_only_for_what_a_person_accepts():
    """The gate records through POST /api/legal/accept, which takes the personal documents only; the organisation's
    agreement has its own route, for admins."""
    status = (SRC / "consent" / "legalStatus.ts").read_text(encoding="utf-8")
    m = re.search(r'export type PersonalDoc = ([^;]+);', status)
    assert m and tuple(re.findall(r'"([a-z_]+)"', m.group(1))) == legal.PERSONAL
    assert '"/org/legal/dpa"' in (SRC / "pages" / "settings" / "Legal.tsx").read_text(encoding="utf-8")
