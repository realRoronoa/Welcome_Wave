"""POST /api/client-errors: the web app's crash reports (frontend/src/report.ts), one log line each, never stored.

The browser sends them with navigator.sendBeacon, so the route takes no sign-in (a crash on the sign-in page, or during
unload, still reports) and no Authorization header ever arrives with one. In its place: a rate limit per address (group
`client_errors`, app/ratelimit.py), a small body limit (`body_limit`, same file), and a strict schema. Every text field is
cut to its length, URLs lose their query and fragment, and emails, keys and tokens are replaced (the transcript redactor,
then the log redactor for credential paths such as /welcome/<token>) before the one structured line is written. The
address, the user agent and any credential are not logged. The answer is always 204 for a report that fits the schema."""
from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

from app.logsafe import redact
from app.services.redaction import RegexRedactor

router = APIRouter()
log = logging.getLogger("client")

# a URL's query and fragment (they can carry tokens, search text or a hash route's own query); then any other ?a=b
_URL_TAIL = re.compile(r"((?:https?://|/)[^\s\"'`()<>?#]*)[?#][^\s\"'`()<>]*")
_QUERY = re.compile(r"\?[^\s\"'`()<>]*=[^\s\"'`()<>]*")
_redactor = RegexRedactor()


def clean(text: str) -> str:
    """No query strings, emails, keys, tokens or credential paths."""
    text = _QUERY.sub("", _URL_TAIL.sub(r"\1", text))
    return redact(_redactor.redact(text)[0])


def _cut(limit: int):
    def cut(value):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value[:limit]
    return BeforeValidator(cut)


class ClientError(BaseModel):
    """What report.ts sends. Unknown fields are refused; over-long text is cut, not refused (the browser already cuts)."""
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, _cut(16)] = ""
    kind: Literal["render", "error", "rejection"]
    name: Annotated[str, _cut(100)] = ""
    message: Annotated[str, _cut(500)] = ""
    stack: Annotated[str, _cut(2000)] = ""
    component: Annotated[str, _cut(1000)] = ""
    page: Annotated[str, _cut(300)] = ""
    build: Annotated[str, _cut(200)] = ""
    at: Annotated[str, _cut(40)] = ""


_REF = re.compile(r"[0-9a-z]{1,16}")


@router.post("/client-errors", status_code=204)
async def client_error(request: Request) -> Response:
    """A crash report from the web app: logged as one line, never stored. Read raw, so a beacon sent as text/plain counts too."""
    try:
        report = ClientError.model_validate(json.loads(await request.body() or b"null"))
    except (ValueError, ValidationError):  # json.JSONDecodeError is a ValueError
        raise HTTPException(422, "Not a crash report.")
    page = report.page.split("?")[0]  # the path and the hash route, without either one's query
    log.warning("client error", extra={"extra_fields": {
        "ref": report.id if _REF.fullmatch(report.id) else "",
        "kind": report.kind,
        "name": clean(report.name),
        "error_message": clean(report.message),
        "stack": clean(report.stack),
        "component": clean(report.component),
        "page": redact(_redactor.redact(page)[0]),
        "build": clean(report.build),
        "at": clean(report.at),
    }})
    return Response(status_code=204)
