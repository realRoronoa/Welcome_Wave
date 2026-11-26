"""The Note-taker (the meeting agent): watches a KT recording (speech + screen), writes notes, and links moments to code.

Redaction. Everything the notes keep that came from the recording goes through the redactor before it is stored, embedded
or sent to the summary model: the transcript, and the decisions, action items (and their owner hints), terms and their
definitions, and the screen moments' descriptions, which the model writes from what it saw (a terminal showing a key, an
email read out). The summary the model writes back is redacted too.

Progress. Each segment's analysis is kept in this process, by meeting, recording and clip, until the notes are saved: a run
retried after segment 17 of 24 failed (the Supervisor retries a node) starts at segment 17, not at the beginning. At most
SEGMENT_CACHE_RECORDINGS recordings are kept, each for at most SEGMENT_CACHE_TTL_S, so a run that never finishes does not
hold memory.

Google's transcript. When Google Meet saved a transcript beside the recording (services/recordings.py, services/transcripts.py)
the speech comes from it, exactly as Google wrote it, and the model is not asked to transcribe the video: it still watches
each clip for what is on screen, and for decisions, action items and terms, with that clip's part of the transcript in its
data block (the transcript is untrusted text, so it goes only there). Every transcript text goes through the redactor first,
as the model's own transcription does. When the transcript is missing, unreadable or empty, the run is exactly the one
without it, and the session records which source its notes came from (`transcript_source`).

The copy at the model. Where the model cannot read the recording in place (self-hosted with a Gemini API key), the model
adapter uploads a copy for the analysis. When the clips are done, or a clip fails, the Note-taker tells the adapter
(`release_media`) and the copy is deleted there; a retried run uploads it again."""
from __future__ import annotations

import logging
import math
import threading
import time
import weakref
from collections import OrderedDict

from app.agents.core import prompts
from app.agents.core.deps import Deps
from app.agents.core.llm import MediaRef, embed_data
from app.agents.core.schemas import ActionItem, MeetingResult, MeetingSummary, ScreenMoment, SegmentAnalysis, Term, Utterance
from app.services import transcripts
from app.services.chunking import chunk_transcript
from app.services.kt_linker import link_screen_moments

log = logging.getLogger("note_taker")


def _shift(a: SegmentAnalysis, offset: float, clip_len: float) -> SegmentAnalysis:
    """The model reports times from the clip start; convert to times from the meeting start.
    If a model returns absolute times anyway (larger than the clip), keep them as they are."""

    def fix(t):
        return t if t is None or t > clip_len + 1 else t + offset

    return SegmentAnalysis(
        utterances=[u.model_copy(update={"start_s": fix(u.start_s), "end_s": fix(u.end_s)}) for u in a.utterances],
        screen_moments=[m.model_copy(update={"at_s": fix(m.at_s)}) for m in a.screen_moments],
        decisions=a.decisions,
        action_items=[i.model_copy(update={"at_s": fix(i.at_s)}) for i in a.action_items],
        terms=a.terms,
    )


SEGMENT_CACHE_RECORDINGS = 8
SEGMENT_CACHE_TTL_S = 6 * 3600
# per store (one database, one demo world): (meeting, recording) -> (since, {clip: analysis})
_segments: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_segments_lock = threading.Lock()


def _cache(store) -> OrderedDict:
    if store not in _segments:
        _segments[store] = OrderedDict()
    return _segments[store]


def _done_segments(store, meeting_id: str, recording_uri: str) -> dict:
    """The clips of this recording analysed so far in this process (a dict the caller adds to)."""
    key, now = (meeting_id, recording_uri), time.monotonic()
    with _segments_lock:
        cache = _cache(store)
        for k in [k for k, (since, _) in cache.items() if now - since > SEGMENT_CACHE_TTL_S]:
            del cache[k]
        if key not in cache:
            cache[key] = (now, {})
            while len(cache) > SEGMENT_CACHE_RECORDINGS:
                cache.popitem(last=False)
        cache.move_to_end(key)
        return cache[key][1]


def _forget_segments(store, meeting_id: str, recording_uri: str) -> None:
    with _segments_lock:
        _cache(store).pop((meeting_id, recording_uri), None)


def _redacted(deps: Deps, a: SegmentAnalysis) -> SegmentAnalysis:
    """Every text of a clip's analysis through the redactor (the module docstring): kept only redacted, even in memory."""
    def r(text: str | None) -> str | None:
        return deps.redactor.redact(text)[0] if text else text

    return SegmentAnalysis(
        utterances=[u.model_copy(update={"text": r(u.text)}) for u in a.utterances],
        screen_moments=[m.model_copy(update={"description": r(m.description)}) for m in a.screen_moments],
        decisions=[r(d) for d in a.decisions],
        action_items=[i.model_copy(update={"text": r(i.text), "owner_hint": r(i.owner_hint)}) for i in a.action_items],
        terms=[t.model_copy(update={"term": r(t.term), "definition": r(t.definition)}) for t in a.terms],
    )


def _google_transcript(deps: Deps, meeting_id: str, transcript_uri: str | None, duration_s: float) -> list[Utterance] | None:
    """Google's transcript as redacted utterances, or None (no transcript, unreadable, or no speech in it: the video is then
    transcribed by the model, as it is for any recording)."""
    if not transcript_uri:
        return None
    try:
        text = deps.blobs.get(transcript_uri).decode("utf-8", errors="replace")
        found = transcripts.parse(text, duration_s)
    except Exception as e:  # noqa: BLE001 - tolerant: the recording alone is enough
        log.warning("google transcript not read", extra={"extra_fields": {"meeting_id": meeting_id, "error": type(e).__name__}})
        return None
    return [u.model_copy(update={"text": deps.redactor.redact(u.text)[0]}) for u in found] or None


def process_recording(deps: Deps, project_id: str, meeting_id: str, recording_uri: str, duration_s: float,
                      transcript_uri: str | None = None) -> MeetingResult:
    meeting = deps.store.get_meeting(meeting_id)
    title = meeting.title if meeting else "KT call"
    deps.store.update_meeting(meeting_id, status="processing")
    repo_paths = deps.index.all_paths(project_id)[:400]
    seg = deps.settings.meeting_segment_s

    utterances: list[Utterance] = []
    moments: list[ScreenMoment] = []
    decisions: list[str] = []
    actions: list[ActionItem] = []
    terms: dict[str, Term] = {}
    google = _google_transcript(deps, meeting_id, transcript_uri, duration_s)
    cache_key = recording_uri + ("#google" if google else "")  # a run with Google's transcript and one without differ in kind
    done = _done_segments(deps.store, meeting_id, cache_key)

    try:
        clips = _analyse_clips(deps, title, repo_paths, recording_uri, duration_s, seg, done, google)
    finally:  # finished or failed: no copy of the recording stays with the model (the summary below is text only)
        release = getattr(deps.llm, "release_media", None)
        if callable(release):
            release(recording_uri)
    for clip in clips:
        a = done[clip]
        utterances += a.utterances
        moments += a.screen_moments
        decisions += a.decisions
        actions += a.action_items
        for t in a.terms:
            terms.setdefault(t.term.lower(), t)

    if google:
        utterances = list(google)  # the speech is Google's; the clips contributed screen moments, decisions, actions and terms
    utterances = sorted(utterances, key=lambda u: u.start_s)  # every text redacted already, clip by clip (_redacted)

    summary = deps.llm.structured(
        embed_data({"title": title, "utterances": [u.model_dump() for u in utterances][:600], "decisions": decisions}),
        MeetingSummary,
        system=prompts.MEETING_SUMMARY,
    )
    summary_text, _ = deps.redactor.redact(summary.summary or "")
    summary_decisions = [deps.redactor.redact(d)[0] for d in summary.decisions]
    links = link_screen_moments(project_id, meeting_id, title, moments, utterances, deps.index, meeting_end_s=duration_s)

    result = MeetingResult(
        meeting_id=meeting_id, project_id=project_id, title=title, summary=summary_text,
        decisions=list(dict.fromkeys(summary_decisions or decisions)), action_items=actions,
        terms=list(terms.values()), utterances=utterances, screen_moments=moments, kt_links=links,
    )
    deps.store.save_meeting_result(result)
    deps.store.update_meeting(meeting_id, transcript_source="google_meet" if google else "recording")
    deps.store.add_kt_links(project_id, links)
    deps.index.upsert(chunk_transcript(project_id, meeting_id, title, utterances))
    _forget_segments(deps.store, meeting_id, cache_key)
    return result


def _analyse_clips(deps: Deps, title: str, repo_paths: list[str], recording_uri: str, duration_s: float, seg: int, done: dict,
                   google: list[Utterance] | None = None) -> list:
    """Analyse every clip of the recording not analysed yet, into `done` ({(start, end): the clip's redacted analysis}).
    With Google's transcript the model is not asked for the speech: it gets the clip's part of the transcript in its data
    block and returns what is on screen, the decisions, action items and terms. Returns the recording's clips in order."""
    clips = []
    for start in range(0, max(1, math.ceil(duration_s)), seg):
        end = min(duration_s, start + seg)
        if duration_s - end < 2:  # a last clip under 2 s is folded into this one rather than sent on its own
            end = duration_s
        if end - start < (2 if start else 1):  # a stray tail, or a recording with nothing in it: no zero-length clip
            continue
        clip = (start, round(end, 3))
        if clip not in done:
            # the call's title is untrusted too (a calendar invite can set it): it travels inside the data block
            data = {"title": title, "repository_files": repo_paths}
            if google:
                data["transcript"] = transcripts.window(google, start, end)  # already redacted; untrusted, so only in the data block
            prompt = f"Clip {start}s-{int(end)}s of the call whose title is in the data.\n" + embed_data(data)
            raw = deps.llm.structured(
                prompt, SegmentAnalysis, media=[MediaRef(uri=recording_uri, start_s=start, end_s=end)],
                system=prompts.MEETING_SEGMENT_TRANSCRIBED if google else prompts.MEETING_SEGMENT
            )
            if google:
                raw = raw.model_copy(update={"utterances": []})
            done[clip] = _redacted(deps, _shift(raw, start, end - start))
        clips.append(clip)
    return clips
