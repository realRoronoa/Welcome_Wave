"""Google Meet's own transcript, read into the utterances the notes are written from.

Meet saves a transcript as a Google Doc beside the recording; the Drive export gives it as plain text. The layout varies a
little between Meet versions, so the reader is tolerant and takes what it understands:

    00:00:05                    a time on its own line, then the words (the speaker is the line before, or after, the time)
    Asha Rao: Let's start.      "Speaker: words", with an optional time in front: "[00:01:10] Asha Rao: ..."
    Asha Rao                    a short line with no full stop, followed by a time line, is a speaker's name

Times are seconds from the start of the call. A line with no time gets one estimated from the words before it (about two and
a half words a second), and none runs past the recording's length. Nothing here calls a model: the speech is exactly what
Google wrote, which the Note-taker then redacts like any other transcript text. The text is untrusted data throughout.
"""
from __future__ import annotations

import re

from app.agents.core.schemas import Utterance
from app.ports import TRANSCRIPT_SUFFIX

MAX_BYTES = 1_500_000  # a transcript larger than this is not read (the notes are written from the recording instead)
MAX_UTTERANCES = 6000
MIN_CHARS = 20  # less speech than this is not a transcript worth using
WORDS_PER_S = 2.5

_TIME = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})"
_ONLY_TIME = re.compile(rf"^[\[(]?{_TIME}[\])]?$")
_SPEAKER_LINE = re.compile(rf"^(?:[\[(]?{_TIME}[\])]?\s+)?([^:\n]{{1,60}}?):\s+(\S.*)$")


def blob_uri(recording_uri: str) -> str:
    """Where the Google transcript of a stored recording is kept, so whatever removes the recording removes it too."""
    return recording_uri + TRANSCRIPT_SUFFIX


def _seconds(h, m, s) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def _is_name(line: str) -> bool:
    return len(line) <= 60 and not re.search(r"[.!?,;]$", line) and len(line.split()) <= 6


def _looks_like_person(line: str) -> bool:
    words = line.split()
    return _is_name(line) and len(words) <= 4 and all(w[:1].isupper() for w in words)


def _body(lines: list[str]) -> list[str]:
    """Without the document's heading (its title, date, the word "Transcript"): from the first line that carries a time."""
    for i, ln in enumerate(lines):
        if _ONLY_TIME.match(ln) or (_SPEAKER_LINE.match(ln) and _SPEAKER_LINE.match(ln).group(2) is not None):
            before = [x for x in lines[:i] if x]
            return ([before[-1]] if before and _ONLY_TIME.match(ln) and _is_name(before[-1]) else []) + lines[i:]
    return lines


def parse(text: str, duration_s: float | None = None) -> list[Utterance]:
    """The transcript's utterances in order, or [] when it holds no speech."""
    lines = [ln.strip() for ln in (text or "").replace("\r", "").split("\n")]
    lines = _body(lines)
    raw: list[list] = []  # [start or None, speaker, words]
    speaker, at = "Speaker", None
    for i, ln in enumerate(lines):
        if not ln:
            continue
        t = _ONLY_TIME.match(ln)
        if t:
            at = _seconds(*t.groups())
            continue
        m = _SPEAKER_LINE.match(ln)
        if m:
            h, mi, s, who, words = m.groups()
            if h is not None or s is not None:
                at = _seconds(h, mi, s)
            speaker = who.strip()
            raw.append([at, speaker, words.strip()])
            at = None
            continue
        nxt = next((x for x in lines[i + 1:] if x), "")
        if nxt and _ONLY_TIME.match(nxt) and _is_name(ln) and not at:  # "Asha Rao" then "00:00:05": a turn begins
            speaker = ln
            continue
        if at is not None and nxt and not _ONLY_TIME.match(nxt) and _looks_like_person(ln):  # "00:00:05", "Asha Rao", words
            speaker = ln
            continue
        if raw and raw[-1][1] == speaker and at is None:
            raw[-1][2] += " " + ln  # the same turn, wrapped onto another line
        else:
            raw.append([at, speaker, ln])
            at = None
    out: list[Utterance] = []
    clock = 0.0
    for start, who, words in raw[:MAX_UTTERANCES]:
        words = " ".join(words.split())
        if not words:
            continue
        begin = float(start) if start is not None else clock
        begin = max(begin, out[-1].start_s if out else 0.0)
        clock = begin + max(1.0, len(words.split()) / WORDS_PER_S)
        out.append(Utterance(start_s=begin, end_s=clock, speaker=who[:80], text=words))
    # an utterance ends when the next begins (never earlier than its own start), and nothing runs past the recording
    for a, b in zip(out, out[1:]):
        a.end_s = max(a.start_s, min(a.end_s, b.start_s)) if b.start_s > a.start_s else a.end_s
    if duration_s:
        for u in out:
            u.start_s = min(u.start_s, max(0.0, duration_s - 0.001))
            u.end_s = min(max(u.end_s, u.start_s), duration_s)
    if sum(len(u.text) for u in out) < MIN_CHARS:
        return []
    return out


def window(utterances: list[Utterance], start_s: float, end_s: float) -> list[dict]:
    """The utterances that begin inside [start_s, end_s), as the plain dicts a prompt's data block carries."""
    return [{"at_s": round(u.start_s - start_s, 1), "speaker": u.speaker, "text": u.text}
            for u in utterances if start_s <= u.start_s < end_s]
