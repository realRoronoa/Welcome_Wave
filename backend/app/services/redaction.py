"""Remove personal data and secrets from transcripts and call notes before they are stored or embedded.

The Meeting agent runs every text it keeps from a recording through this: the transcript, and the decisions, action items,
terms and screen descriptions the model writes from what it heard and saw (a terminal showing a key, an address read out).
Production can swap `RegexRedactor` for Google Cloud Sensitive Data Protection behind the same interface.
"""
from __future__ import annotations

import re
from typing import Protocol

PATTERNS = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d[\d\s-]{8,}\d)(?!\d)")),
    ("API_KEY", re.compile(r"\b(?:(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{22,}"
                           r"|glpat-[0-9A-Za-z_-]{20,}|xox[abprs]-[0-9A-Za-z-]{10,}|ya29\.[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{20,}"
                           r"|eyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,})")),
    # a value given to anything named like a credential: password: x, AWS_SECRET_ACCESS_KEY=x, api_key = x, "token": "x"
    ("PASSWORD", re.compile(r"(?i)\b[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[\w.-]*"
                            r"[\"']?\s*[:=]\s*\S+")),
]


class Redactor(Protocol):
    def redact(self, text: str) -> tuple[str, int]: ...


# a phone number has at least 9 digits: "lines 1200 - 1350" in a screen description is not one
MIN_DIGITS = {"PHONE": 9}


class RegexRedactor:
    def redact(self, text: str) -> tuple[str, int]:
        count = 0
        for label, pattern in PATTERNS:
            least = MIN_DIGITS.get(label, 0)

            def swap(m: re.Match, label=label, least=least) -> str:
                nonlocal count
                if least and sum(ch.isdigit() for ch in m.group(0)) < least:
                    return m.group(0)
                count += 1
                return f"[{label}]"

            text = pattern.sub(swap, text)
        return text, count
