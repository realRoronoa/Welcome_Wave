"""Text embeddings. Vertex AI in production, a deterministic hashing embedder offline and in tests."""
from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections import deque
from typing import Literal, Protocol

from app.services.chunking import split_identifiers

Task = Literal["query", "document"]


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str], task: Task = "document") -> list[list[float]]: ...


class VertexEmbedder:
    """Gemini embeddings on Vertex AI. Requests go out in parallel; if the model rejects multi-input batches,
    the embedder switches to one input per request for the rest of the process instead of failing ingestion."""

    def __init__(self, settings):
        from google import genai
        from google.genai import types

        opts = types.HttpOptions(timeout=60_000)
        self.client = (
            genai.Client(api_key=settings.gemini_api_key, http_options=opts)
            if settings.gemini_api_key
            else genai.Client(vertexai=True, project=settings.gcp_project,
                              location=settings.embedding_location or settings.gcp_region, http_options=opts)
        )
        self.model, self.dim = settings.embedding_model, settings.embedding_dim
        self.batch, self.workers = max(1, settings.embedding_batch), max(1, settings.embedding_concurrency)
        self.query_deadline_s = float(getattr(settings, "query_embedding_deadline_s", QUERY_DEADLINE_S) or QUERY_DEADLINE_S)
        rpm = settings.embedding_rpm or (90 if settings.gemini_api_key else 0)
        # a tenth of the minute's requests is kept for questions: indexing never takes the whole budget
        self.pace = _Pace(rpm, reserve=max(1, rpm // 10)) if rpm > 0 else None
        if self.pace:  # a per-minute budget: parallel requests only queue up behind it
            self.workers = min(self.workers, 4)

    def _embed_batch(self, texts: list[str], task_type: str, deadline: float | None = None) -> list[list[float]]:
        """One request, retried as the request's kind allows. Indexing (no deadline) waits out Google's per-minute limit, up to
        MAX_RATE_LIMIT_WAITS times. A question (a deadline) waits for nothing long: one retry at most, a few seconds, inside
        its deadline, and then SearchBusy instead of a hung request."""
        from google.genai import types

        errors = limited = 0
        while True:
            if self.pace:
                self.pace.wait(query=deadline is not None, deadline=deadline)
            try:
                extra = {}
                if deadline is not None:
                    extra["http_options"] = types.HttpOptions(timeout=max(1000, int((deadline - time.monotonic()) * 1000)))
                resp = self.client.models.embed_content(
                    model=self.model, contents=texts,
                    config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim, **extra),
                )
                return [normalize(e.values) for e in resp.embeddings]
            except Exception as e:  # noqa: BLE001
                if len(texts) > 1 and _is_bad_request(e):
                    raise _BatchRejected from e
                wait = _rate_limit_wait(e)
                if deadline is not None:
                    busy = wait is not None or _is_timeout(e)
                    pause = min(wait if wait is not None else 0.5, QUERY_RETRY_WAIT_S)
                    if errors + limited == 0 and time.monotonic() + pause + 1.0 < deadline:
                        errors += 1
                        time.sleep(pause)
                        continue
                    if busy:
                        raise SearchBusy() from e
                    raise
                if wait is not None:  # Google's per-minute quota: wait as long as it asks, then carry on
                    limited += 1
                    if limited > MAX_RATE_LIMIT_WAITS:
                        raise
                    time.sleep(wait)
                    continue
                errors += 1
                if errors == 3:
                    raise
                time.sleep(2 ** (errors - 1))

    def embed(self, texts, task="document"):
        from concurrent.futures import ThreadPoolExecutor

        task_type = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
        deadline = time.monotonic() + getattr(self, "query_deadline_s", QUERY_DEADLINE_S) if task == "query" else None
        texts = [t[:8000] or " " for t in texts]  # the model's input limit; an empty string is rejected
        if len(texts) == 1:
            return self._embed_batch(texts, task_type, deadline)
        try:
            groups = [texts[i : i + self.batch] for i in range(0, len(texts), self.batch)]
            with ThreadPoolExecutor(self.workers) as pool:
                return [v for group in pool.map(lambda g: self._embed_batch(g, task_type, deadline), groups) for v in group]
        except _BatchRejected:
            self.batch = 1
            with ThreadPoolExecutor(self.workers) as pool:
                return [v for group in pool.map(lambda t: self._embed_batch([t], task_type, deadline), texts) for v in group]


class _BatchRejected(Exception):
    pass


MAX_RATE_LIMIT_WAITS = 12  # about ten minutes of Google saying "slow down" before ingestion gives up and is retried later
QUERY_DEADLINE_S = 8.0  # a question's embedding, all waits included (Settings.query_embedding_deadline_s)
QUERY_RETRY_WAIT_S = 3.0  # the longest pause before a question's one retry


class DailyQuotaExceeded(RuntimeError):
    """The key's per-day embedding quota is used up: waiting minutes does not help."""


class SearchBusy(RuntimeError):
    """A question's embedding could not be made within its deadline: the minute's requests went to indexing, or Google asked
    to slow down. The question fails at once with this message instead of waiting minutes."""

    def __init__(self, message: str = "Search is busy right now, most likely because a repository is being indexed. "
                                      "Ask again in a minute."):
        super().__init__(message)


class _Pace:
    """At most `rpm` requests in any 60 seconds, shared by every thread of this process. `reserve` of them are kept for
    questions: indexing takes at most rpm - reserve a minute, so a question finds a free slot at once."""

    def __init__(self, rpm: int, reserve: int = 0):
        self.rpm, self.sent, self.lock = rpm, deque(), threading.Lock()
        self.reserve = min(max(0, reserve), max(0, rpm - 1))

    def wait(self, query: bool = False, deadline: float | None = None) -> None:
        """Take a slot. A question may use every slot; indexing all but the reserve. Past `deadline`: SearchBusy."""
        cap = self.rpm if query else self.rpm - self.reserve
        while True:
            with self.lock:
                now = time.monotonic()
                while self.sent and now - self.sent[0] >= 60:
                    self.sent.popleft()
                if len(self.sent) < cap:
                    self.sent.append(now)
                    return
                pause = 60 - (now - self.sent[len(self.sent) - cap]) + 0.05  # until enough slots age out for this one
            if deadline is not None and now + pause > deadline:
                raise SearchBusy()
            time.sleep(pause)


def _is_timeout(e: Exception) -> bool:
    return isinstance(e, TimeoutError) or "timeout" in type(e).__name__.lower() or "timed out" in str(e)[:200].lower()


def _rate_limit_wait(e: Exception) -> float | None:
    """Seconds to wait when Google answered 429 RESOURCE_EXHAUSTED for a per-minute quota, else None."""
    text = str(e)
    if getattr(e, "code", None) != 429 and "RESOURCE_EXHAUSTED" not in text and "429" not in text[:40]:
        return None
    if re.search(r"per[ _]?day|daily", text, re.I):
        raise DailyQuotaExceeded("The Gemini API key's daily embedding quota is used up. Re-index tomorrow, when it resets, "
                                 "or turn on billing for the key.") from e
    m = re.search(r"retry in ([\d.]+)s", text) or re.search(r"retryDelay['\"]?: ?['\"](\d+)s", text)
    return min(float(m.group(1)) + 1, 90.0) if m else 30.0


def _is_bad_request(e: Exception) -> bool:
    return getattr(e, "code", None) == 400 or "400" in str(e)[:20] or "INVALID_ARGUMENT" in str(e)


class NoEmbedder:
    """Self-hosted mode before a Gemini key is added: indexing and search say the AI is off instead of using fake vectors."""

    def __init__(self, dim: int = 768):
        self.dim = dim

    def embed(self, texts, task="document"):
        from app.ports import AI_OFF, AIUnavailable

        raise AIUnavailable(AI_OFF)


class HashEmbedder:
    """Bag of identifier-aware tokens hashed into a fixed vector. Good enough for tests and the offline demo."""

    def __init__(self, dim: int = 768):
        self.dim = dim

    def embed(self, texts, task="document"):
        vectors = []
        for text in texts:
            v = [0.0] * self.dim
            for tok in split_identifiers(text).split():
                if len(tok) < 2:
                    continue
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 8) % 2 else -1.0
            vectors.append(normalize(v))
        return vectors


def normalize(v) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))
