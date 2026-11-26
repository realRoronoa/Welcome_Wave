"""Keeping credentials out of logs. Some URLs ARE credentials: the pre-boarding link (/welcome/<token>, whose API form is
/api/preboarding/<token>/...), a signed file link (/api/blobs/<token>) and an OAuth callback's `code` and `state`. A log line
that names one lets whoever reads the logs use it, so every place that logs a path (the request log, the rate limiter, the
server's own access log) goes through `redact`."""
from __future__ import annotations

import logging
import re

_PATH = re.compile(r"(/api/preboarding/|/welcome/|/api/blobs/)[^/?#\s\"']+")
_QUERY = re.compile(r"([?&](?:code|state|token|access_token|id_token)=)[^&#\s\"']*")


def redact(text: str) -> str:
    return _QUERY.sub(r"\1[redacted]", _PATH.sub(r"\1[redacted]", text))


class AccessLogFilter(logging.Filter):
    """For uvicorn's access log, which prints each request line (method, full path and query) before the app logs anything."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True
