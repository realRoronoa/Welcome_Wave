"""Load test for the chat endpoint. Target: p95 under 8 s at 50 concurrent users (TRD, non-functional requirements).

Demo mode (local):
    CHAT_RATE_PER_MINUTE=10000 MODE=demo uvicorn app.main:app --port 8080
    locust -f load/locustfile.py --host http://localhost:8080 --users 50 --spawn-rate 5 --run-time 5m --headless

Deployed environment: each simulated user needs a real sign-in, and the per-user chat limit
(CHAT_RATE_PER_MINUTE, default 12) must be raised on the environment under test or the run measures the limiter:
    WW_PROJECT=<project uuid> WW_TOKENS=tokens.txt locust -f load/locustfile.py --host https://<api> ...
where tokens.txt holds one Identity Platform ID token per line (users are assigned round-robin; tokens last 1 hour).
"""
import itertools
import os
import random

from locust import HttpUser, between, task

QUESTIONS = [
    "Where is a resource checked against every rule?",
    "How is a policy loaded?",
    "Where do default labels get added?",
    "What does the webhook return on a violation?",
]
PROJECT = os.environ.get("WW_PROJECT", "proj-demo")
_tokens = [t.strip() for t in open(os.environ["WW_TOKENS"]).read().splitlines() if t.strip()] if os.environ.get("WW_TOKENS") else []
_next_token = itertools.cycle(_tokens or [None])


class NewHire(HttpUser):
    wait_time = between(2, 6)

    def on_start(self):
        token = next(_next_token)
        self.headers = {"Authorization": f"Bearer {token}"} if token else {"X-Demo-User": "u-vijay"}

    @task
    def ask(self):
        with self.client.post("/api/chat", json={"project_id": PROJECT, "question": random.choice(QUESTIONS)},
                              headers=self.headers, name="/api/chat", catch_response=True) as r:
            if r.status_code == 429:
                r.failure("rate limited - raise CHAT_RATE_PER_MINUTE on the environment under test")
            elif r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")
