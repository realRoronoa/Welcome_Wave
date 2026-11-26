"""Rate limits (app/ratelimit.py): token buckets per caller and route group, 429 with Retry-After. Off in the other tests
(conftest.py); these turn it on with small limits."""
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.ratelimit import RateLimitSettings, TokenBuckets, client_address, group_for


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_a_bucket_allows_a_burst_then_refills_continuously():
    clock = Clock()
    b = TokenBuckets(clock=clock)
    assert [b.take("k", 3) for _ in range(3)] == [0, 0, 0]
    assert b.take("k", 3) == pytest.approx(20.0)  # 3 a minute: one token every 20 s
    clock.t += 20
    assert b.take("k", 3) == 0 and b.take("k", 3) > 0
    assert b.take("other", 3) == 0  # keys are independent


def test_buckets_are_bounded():
    b = TokenBuckets(max_keys=2, clock=Clock())
    for k in "abc":
        b.take(k, 1)
    assert b.take("a", 1) == 0  # "a" was the least recently used: dropped, so it starts full again
    assert b.take("c", 1) > 0


@pytest.mark.parametrize("method,path,group", [
    ("POST", "/api/chat", "chat"), ("POST", "/api/ask/route", "chat"), ("GET", "/api/chat/history", "default"),
    ("POST", "/api/tasks/t1/presubmit-review", "chat"), ("PATCH", "/api/tasks/t1", "default"),
    ("POST", "/api/demo/login", "auth"), ("POST", "/api/signup", "auth"), ("POST", "/api/invitations/accept", "auth"),
    ("GET", "/api/preboarding/tok123", "preboarding"), ("POST", "/api/preboarding/tok123/acknowledge", "preboarding"),
    ("POST", "/webhooks/github", "webhooks"), ("GET", "/api/projects", "default"),
])
def test_routes_fall_into_their_groups(method, path, group):
    assert group_for(method, path).name == group


def test_the_client_address_trusts_only_the_configured_proxies():
    scope = {"client": ("10.0.0.9", 1), "headers": [(b"x-forwarded-for", b"6.6.6.6, 203.0.113.7, 10.0.0.2")]}
    assert client_address(scope, 0) == "10.0.0.9"  # what the server resolved
    assert client_address(scope, 2) == "203.0.113.7"  # two proxies of ours appended; the spoofable left part is ignored


@pytest.fixture
def limited(monkeypatch):
    def make(**limits):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        for k, v in limits.items():
            monkeypatch.setenv(f"RATE_LIMIT_{k.upper()}", str(v))
        get_settings.cache_clear()
        return TestClient(create_app())

    yield make
    get_settings.cache_clear()


def test_sign_in_attempts_are_limited_per_address_with_retry_after(limited):
    with limited(auth_per_minute=2) as client:
        bad = {"email": "nobody@demo.dev", "password": "wrong"}
        assert [client.post("/api/demo/login", json=bad).status_code for _ in range(2)] == [401, 401]
        r = client.post("/api/demo/login", json=bad, headers={"Origin": "https://example.com"})
        assert r.status_code == 429
        assert int(r.headers["retry-after"]) >= 1 and r.json()["retry_after"] == int(r.headers["retry-after"])
        assert r.headers["x-request-id"] and r.headers["content-security-policy"].startswith("default-src 'none'")
        assert r.headers["access-control-allow-origin"]  # inside CORS: the browser can read the 429
        assert client.get("/health").status_code == 200  # never limited
        assert client.get("/api/config").status_code == 200  # another group has its own bucket


def test_signed_in_people_get_their_own_bucket_and_made_up_ones_do_not(limited):
    with limited(default_per_minute=3) as client:
        priya = {"X-Demo-User": "u-priya"}
        assert client.get("/api/me", headers=priya).status_code == 200  # unknown credential yet: charged to the address
        assert [client.get("/api/me", headers=priya).status_code for _ in range(4)] == [200, 200, 200, 429]  # now her own
        assert client.get("/api/me", headers={"X-Demo-User": "u-vijay"}).status_code == 200  # her limit is not his
        forged = [client.get("/api/me", headers={"X-Demo-User": f"u-fake-{i}"}).status_code for i in range(3)]
        assert forged == [403, 429, 429]  # never verified, so all charged to the one address


def test_turned_off_it_never_limits(limited):
    with limited(enabled="false", auth_per_minute=1) as client:
        assert {client.post("/api/demo/login", json={"email": "x@y.z", "password": "no"}).status_code for _ in range(4)} == {401}


def test_settings_have_safe_defaults(monkeypatch):
    for k in ("ENABLED", "DEFAULT_PER_MINUTE", "CHAT_PER_MINUTE", "AUTH_PER_MINUTE"):
        monkeypatch.delenv(f"RATE_LIMIT_{k}", raising=False)
    s = RateLimitSettings(_env_file=None)
    assert s.enabled and s.default_per_minute == 120 and s.chat_per_minute < s.default_per_minute and s.auth_per_minute <= 10
