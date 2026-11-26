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
        rpm = settings.embedding_rpm or (90 if settings.gemini_api_key else 0)
        self.pace = _Pace(rpm) if rpm > 0 else None
        if self.pace:  # a per-minute budget: parallel requests only queue up behind it
            self.workers = min(self.workers, 4)

    def _embed_batch(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        errors = limited = 0
        while True:
            if self.pace:
                self.pace.wait()
            try:
                resp = self.client.models.embed_content(
                    model=self.model, contents=texts,
                    config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim),
                )
                return [normalize(e.values) for e in resp.embeddings]
            except Exception as e:  # noqa: BLE001
                if len(texts) > 1 and _is_bad_request(e):
                    raise _BatchRejected from e
                wait = _rate_limit_wait(e)
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
        texts = [t[:8000] or " " for t in texts]  # the model's input limit; an empty string is rejected
        if len(texts) == 1:
            return self._embed_batch(texts, task_type)
        try:
            groups = [texts[i : i + self.batch] for i in range(0, len(texts), self.batch)]
            with ThreadPoolExecutor(self.workers) as pool:
                return [v for group in pool.map(lambda g: self._embed_batch(g, task_type), groups) for v in group]
        except _BatchRejected:
            self.batch = 1
            with ThreadPoolExecutor(self.workers) as pool:
                return [v for group in pool.map(lambda t: self._embed_batch([t], task_type), texts) for v in group]


class _BatchRejected(Exception):
    pass


MAX_RATE_LIMIT_WAITS = 12  # about ten minutes of Google saying "slow down" before ingestion gives up and is retried later


class DailyQuotaExceeded(RuntimeError):
    """The key's per-day embedding quota is used up: waiting minutes does not help."""


class _Pace:
    """At most `rpm` requests in any 60 seconds, shared by every thread of this process."""

    def __init__(self, rpm: int):
        self.rpm, self.sent, self.lock = rpm, deque(), threading.Lock()

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.sent and now - self.sent[0] >= 60:
                    self.sent.popleft()
                if len(self.sent) < self.rpm:
                    self.sent.append(now)
                    return
                pause = 60 - (now - self.sent[0]) + 0.05
            time.sleep(pause)


def _rate_limit_wait(e: Exception) -> float | None:
    """Seconds to wait when Google answered 429 RESOURCE_EXHAUSTED for a per-minute quota, else None."""
    text = str(e)
    if getattr(e, "code", None) != 429 and "RESOURCE_EXHAUSTED" not in text and "429" not in text[:40]:
        return None
    if re.search(r"per[ _]?day|daily", text, re.I):
        raise DailyQuotaExceeded("The Gemini API key's daily embedding quota is used up; indexing resumes tomorrow, "
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
