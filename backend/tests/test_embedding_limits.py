"""Gemini's free tier allows 100 embedding requests a minute: ingestion paces itself and waits out a 429 instead of failing
(a real repository failed to index in 30 seconds before this)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import embeddings as E


class Quota(Exception):
    def __init__(self, text, code=429):
        super().__init__(text)
        self.code = code


PER_MINUTE = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generativelanguage.googleapis.com/embed_content_free_tier_requests,"
              " limit: 100 ... Please retry in 53.76s.")
PER_DAY = "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: embed_content_free_tier_requests_per_day, limit: 1000"


def test_the_wait_comes_from_googles_retry_hint_and_is_capped():
    assert E._rate_limit_wait(Quota(PER_MINUTE)) == pytest.approx(54.76)
    assert E._rate_limit_wait(Quota("429 RESOURCE_EXHAUSTED retry in 400s")) == 90.0
    assert E._rate_limit_wait(Quota("429 RESOURCE_EXHAUSTED, no hint")) == 30.0
    assert E._rate_limit_wait(Quota("500 internal", code=500)) is None


def test_a_daily_quota_says_so_instead_of_waiting():
    with pytest.raises(E.DailyQuotaExceeded):
        E._rate_limit_wait(Quota(PER_DAY))


def _embedder(responses, sleeps):
    emb = object.__new__(E.VertexEmbedder)
    emb.model, emb.dim, emb.batch, emb.workers, emb.pace = "m", 3, 64, 1, None

    def embed_content(**kw):
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 0.0, 0.0]) for _ in kw["contents"]])

    emb.client = SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))
    return emb


def test_a_429_is_waited_out_and_ingestion_carries_on(monkeypatch):
    sleeps = []
    monkeypatch.setattr(E.time, "sleep", sleeps.append)
    emb = _embedder([Quota(PER_MINUTE), Quota(PER_MINUTE), "ok"], sleeps)
    assert emb.embed(["a", "b"]) == [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert sleeps == [pytest.approx(54.76), pytest.approx(54.76)]


def test_other_errors_still_give_up_after_three_tries(monkeypatch):
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    emb = _embedder([Quota("503 unavailable", 503)] * 3, [])
    with pytest.raises(Quota):
        emb.embed(["a", "b"])


def test_pace_allows_rpm_requests_a_minute_then_waits(monkeypatch):
    clock = {"t": 1000.0}
    slept = []
    monkeypatch.setattr(E.time, "monotonic", lambda: clock["t"])

    def sleep(s):
        slept.append(s)
        clock["t"] += s
    monkeypatch.setattr(E.time, "sleep", sleep)
    pace = E._Pace(3)
    for _ in range(3):
        pace.wait()
    assert slept == []
    pace.wait()  # the fourth in the same minute waits for the first to age out
    assert slept and slept[0] == pytest.approx(60.05)

