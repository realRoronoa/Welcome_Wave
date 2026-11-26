"""The Guide end to end on the real stack MODE=self_hosted runs: PostgreSQL + pgvector (PgIndex, as the app role under forced
row-level security), Gemini embeddings (VertexEmbedder with the API key) and the real model (GeminiLLM), against the offline
results of eval/rag_eval.py (MemoryIndex(HashEmbedder()) and the offline writer). Never in CI: it spends real quota.

    cd backend
    export LIVE_DB_ADMIN_URL=postgresql://ww:ww@localhost:55432/postgres   # a SCRATCH server's superuser, pgvector installed
    .venv/bin/python -m eval.live_eval setup       --state S.json     # a new database (unique name, its own owner and app
                                                                       # roles), every migration, the demo project ingested
    .venv/bin/python -m eval.live_eval ingest-self --state S.json     # backend/app/**/*.py + docs/*.md as a second project
    .venv/bin/python -m eval.live_eval retrieval   --state S.json --json R.json [--offline-json O.json]
    .venv/bin/python -m eval.live_eval crew        --state S.json --json C.json    # model planner + judge, 12 self holdout cases
    .venv/bin/python -m eval.live_eval guide       --state S.json --json G.json [--offline]   # tier 1, 14 demo cases
    .venv/bin/python -m eval.live_eval teardown    --state S.json     # drop the database and the roles it created

What each step measures:
- retrieval  baseline (Index.search) vs the crew by rules alone (crew.retrieve with a model that cannot think) on
             golden_demo and golden_self, every split, dev and holdout apart; the top-1 and best-of-8 similarity to the
             question as asked on answerable vs unanswerable questions against routing.MIN_SIMILARITY and RELATED_SIMILARITY.
             No generate call. --offline-json takes `rag_eval --json` output and prints the offline numbers beside these.
- crew       the crew with the model planner and judge vs rules alone on 12 golden_self holdout questions (10 answerable,
             stratified by category; 2 unanswerable): metrics, timeouts, invalid outputs, latency, calls.
             --index memory runs it on rag_eval's offline self corpus (MemoryIndex(HashEmbedder()); no database, no
             embedding request), the model stages still on the real model: for when the self corpus cannot be embedded.
- guide      guide.attempt (the agent loop, VERIFY, the citation guard, at most one repair) on 14 golden_demo holdout and
             unanswerable cases (10 answerable across 10 categories, 4 unanswerable, interleaved so a budget cut loses
             both kinds alike): answered/refused, citation precision, guard, what VERIFY and the guard caught, latency, calls
             and tokens per question, and every answer in full for a person to judge.
             --offline runs the same cases with the offline writer on the same database and embeddings (no generate call).

Rules this script keeps:
- No env file is read. GEMINI_API_KEY, the model ids and EMBEDDING_* come from this command's own environment; MODE is set
  to demo while Settings load (so backend/.env is never read), then the settings say self_hosted for the code under test.
- The key is never printed: every line this script prints or saves passes scrub() (the key's shape becomes "[key]"); grep
  saved logs for "AIza" before sharing them all the same.
- Every generate request the model receives is counted (GeminiLLM's client.models.generate_content: structured calls, agent
  turns, retries and fallbacks alike) in a ledger shared by every run (--ledger, default next to the state file); a request
  past --budget (default 120, all runs together) is refused before it is sent, and a run stops starting cases once the
  ledger is nearly used, or when the model has answered 429 several times in a row. A planner or judge call that ran past
  its time box finishes in the background and is still counted, by the ledger, though not always in its case's row.
- Embeddings are cached on disk by (model, size, task, text) (--cache, default next to the state file), saved slice by
  slice, so a re-run spends no embedding request on text already embedded; every request that does go out is counted.
  The AI Studio free tier allows 1,000 embedded texts a day per model (each text of a batch counts; 2026-11-17): the
  self corpus (about 2,900 chunks) does not fit in one day, and once the day's texts are used no question can be embedded
  either. gemini-embedding-2 answers a list of texts with ONE vector: run it with --embed-one (the cache refuses a short
  answer either way, and switches to one text per request).
- The database is the script's own (ww_live_<tag>, owner own_ww_live_<tag>, app role app_ww_live_<tag>, definer role
  def_ww_live_<tag>): it never touches another database or the cluster-wide ww_app and ww_definer roles. The migrations are
  the app's own files with one change, the definer role's name (a copy in a temp folder): tests/test_postgres.py drops
  ww_definer at the start of every run, which would fail while another database holds functions it owns. teardown drops
  what setup created."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
from array import array
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parent
BACKEND = EVAL.parent
ROOT = BACKEND.parent
SAMPLE = BACKEND / "sample_repo"
KEY_SHAPE = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")
DEFAULT_BUDGET = 120
K = 10

# The 14 Guide cases (golden_demo.jsonl): 10 answerable holdout cases, one per category (locate, explain, why, verified, howto,
# people, doc, multi, paraphrase, typo), and the 4 unanswerable ones (2 dev, 2 holdout), interleaved: when the budget stops a
# run early, what is left out is answerable cases of the later categories, never only one kind.
GUIDE_CASES = ("demo-loc-4", "demo-una-1", "demo-exp-4", "demo-una-2", "demo-why-2", "demo-una-3", "demo-ver-2", "demo-una-4",
               "demo-how-2", "demo-ppl-2", "demo-doc-2", "demo-mul-4", "demo-par-2", "demo-typ-2")
CREW_ANSWERABLE, CREW_UNANSWERABLE = 10, 2


def scrub(text: Any) -> str:
    return KEY_SHAPE.sub("[key]", str(text))


def say(*parts) -> None:
    print(scrub(" ".join(str(p) for p in parts)), flush=True)


# ---------------------------------------------------------------- counting the model's requests
class BudgetExhausted(RuntimeError):
    """The ledger is used up: this request was not sent."""


class Meter:
    """Every generate request the real model receives, with its latency, tokens and error, against a budget shared by every
    run (the ledger file). Thread-safe: the crew's model planner runs beside its searches."""

    def __init__(self, ledger: Path, budget: int):
        self.ledger, self.budget = ledger, budget
        self.prior = int(json.loads(ledger.read_text()).get("calls", 0)) if ledger.exists() else 0
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    @property
    def used(self) -> int:
        return self.prior + len(self.calls)

    def left(self) -> int:
        return self.budget - self.used

    def start(self, model: str) -> dict:
        with self.lock:
            if self.used >= self.budget:
                raise BudgetExhausted(f"the ledger's {self.budget} generate requests are used")
            rec = {"model": model, "ms": None, "ok": None}
            self.calls.append(rec)
            return rec

    def finish(self, rec: dict, t0: float, resp=None, error: BaseException | None = None) -> None:
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        rec["ok"] = error is None
        if error is not None:
            code = getattr(error, "code", None)
            rec["status"] = code if isinstance(code, int) else (429 if "RESOURCE_EXHAUSTED" in str(error)[:300] else None)
            rec["error"] = scrub(f"{type(error).__name__}: {error}")[:240]
            return
        u = getattr(resp, "usage_metadata", None)
        for name, attr in (("in", "prompt_token_count"), ("out", "candidates_token_count"), ("thoughts", "thoughts_token_count"),
                           ("total", "total_token_count")):
            rec[name] = int(getattr(u, attr, 0) or 0)

    def mark(self) -> int:
        return len(self.calls)

    def since(self, mark: int) -> dict:
        recs = self.calls[mark:]
        return {"calls": len(recs), "ok": sum(1 for r in recs if r.get("ok")), "429": sum(1 for r in recs if r.get("status") == 429),
                "errors": [r["error"] for r in recs if r.get("error")][:6],
                "tokens_in": sum(r.get("in", 0) for r in recs), "tokens_out": sum(r.get("out", 0) for r in recs),
                "tokens_thoughts": sum(r.get("thoughts", 0) for r in recs), "tokens_total": sum(r.get("total", 0) for r in recs),
                "models": dict(Counter(r["model"] for r in recs)), "ms": [r["ms"] for r in recs]}

    def throttled(self, run: int = 5) -> bool:
        """The model answered 429 to the last `run` requests: the quota, not a bad moment."""
        done = [r for r in self.calls if r.get("ok") is not None]
        return len(done) >= run and all(r.get("status") == 429 for r in done[-run:])

    def save(self) -> None:
        self.ledger.write_text(json.dumps({"calls": self.used, "budget": self.budget,
                                           "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}))


class _Models:
    def __init__(self, real, hook: str, wrap):
        self._real, self._hook, self._wrap = real, hook, wrap

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        return self._wrap(attr) if name == self._hook else attr


class _Client:
    """A genai.Client whose `models.<hook>` goes through `wrap`; everything else is the real client's."""

    def __init__(self, real, hook: str, wrap):
        self._real = real
        self.models = _Models(real.models, hook, wrap)

    def __getattr__(self, name):
        return getattr(self._real, name)


def metered(llm, meter: Meter):
    """GeminiLLM with every generate request counted (and refused past the budget). Still a GeminiLLM: the crew's model
    stages run exactly as in production (crew.can_think)."""
    def wrap(fn):
        def generate_content(*a, **kw):
            rec = meter.start(str(kw.get("model") or (a[0] if a else "?")))
            t0 = time.perf_counter()
            try:
                resp = fn(*a, **kw)
            except BaseException as e:
                meter.finish(rec, t0, error=e)
                raise
            meter.finish(rec, t0, resp=resp)
            return resp
        return generate_content

    llm.client = _Client(llm.client, "generate_content", wrap)
    llm.video_client = _Client(llm.video_client, "generate_content", wrap)
    return llm


class EmbedMeter:
    """Every embedding request that leaves the process: texts, characters, latency, 429s."""

    def __init__(self):
        self.lock = threading.Lock()
        self.requests: list[dict] = []

    def wrap(self, embedder) -> None:
        def wrap(fn):
            def embed_content(*a, **kw):
                contents = kw.get("contents") or []
                contents = contents if isinstance(contents, list) else [contents]
                rec = {"texts": len(contents), "chars": sum(len(str(c)) for c in contents), "ok": None}
                with self.lock:
                    self.requests.append(rec)
                t0 = time.perf_counter()
                try:
                    out = fn(*a, **kw)
                except BaseException as e:
                    rec.update(ok=False, ms=round((time.perf_counter() - t0) * 1000),
                               status=429 if ("RESOURCE_EXHAUSTED" in str(e)[:300] or getattr(e, "code", None) == 429) else getattr(e, "code", None),
                               error=scrub(f"{type(e).__name__}: {e}")[:200])
                    raise
                rec.update(ok=True, ms=round((time.perf_counter() - t0) * 1000))
                return out
            return embed_content

        embedder.client = _Client(embedder.client, "embed_content", wrap)

    def mark(self) -> int:
        return len(self.requests)

    def since(self, mark: int = 0) -> dict:
        recs = [r for r in self.requests[mark:] if r.get("ok") is not None]
        ms = [r["ms"] for r in recs if r.get("ok")]
        return {"requests": len(recs), "ok": sum(1 for r in recs if r["ok"]), "429": sum(1 for r in recs if r.get("status") == 429),
                "other_errors": sorted({r["error"] for r in recs if r.get("ok") is False and r.get("status") != 429})[:4],
                "texts": sum(r["texts"] for r in recs if r["ok"]), "chars": sum(r["chars"] for r in recs if r["ok"]),
                "p50_ms": pct(ms, 0.5), "p95_ms": pct(ms, 0.95)}


class CachedEmbedder:
    """The real embedder behind a disk cache keyed by (model, size, task, text as the model sees it): a re-run, or the same
    question asked by two methods, spends no request. Vectors come back exactly as the model gave them (normalised).

    Missing texts go out in slices, each saved as soon as it returns (a quota stop keeps what was already paid for). A
    model that answers a multi-text request with fewer vectors than texts (gemini-embedding-2 returns one vector for the
    whole list) is never trusted: nothing from that answer is kept, and every text goes in its own request from then on
    (`single`, or from the start with --embed-one)."""

    SLICE = 256

    def __init__(self, inner, path: Path, model: str, single: bool = False):
        self.inner, self.dim, self.model, self.single = inner, inner.dim, model, single
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS e (k TEXT PRIMARY KEY, v BLOB)")
        self.db.commit()
        self.lock = threading.Lock()
        self.hits = self.misses = 0
        self.short_answers = 0  # multi-text requests answered with too few vectors

    def _one_each(self, texts: list[str], task: str) -> list[list[float]]:
        from concurrent.futures import ThreadPoolExecutor

        def one(t):
            v = self.inner.embed([t], task)
            if len(v) != 1:
                raise RuntimeError(f"{self.model} answered one text with {len(v)} vectors")
            return v[0]

        with ThreadPoolExecutor(4) as pool:  # VertexEmbedder paces requests per minute across threads
            return list(pool.map(one, texts))

    def _fetch(self, texts: list[str], task: str) -> list[list[float]]:
        if self.single:
            return self._one_each(texts, task)
        vecs = self.inner.embed(texts, task)
        if len(vecs) == len(texts):
            return vecs
        self.short_answers += 1
        say(f"embedding: {self.model} answered {len(texts)} texts with {len(vecs)} vectors; one text per request from now on")
        self.single = True
        return self._one_each(texts, task)

    def _key(self, text: str, task: str) -> str:
        return hashlib.sha256(f"{self.model}|{self.dim}|{task}|{text}".encode()).hexdigest()

    def _get(self, keys: list[str]) -> dict[str, list[float]]:
        out = {}
        with self.lock:
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                for k, v in self.db.execute(f"SELECT k, v FROM e WHERE k IN ({','.join('?' * len(part))})", part):
                    out[k] = array("d", v).tolist()
        return out

    def embed(self, texts, task="document"):
        shown = [t[:8000] or " " for t in texts]  # what VertexEmbedder sends
        keys = [self._key(t, task) for t in shown]
        found = self._get(list(dict.fromkeys(keys)))
        missing = list(dict.fromkeys(t for t, k in zip(shown, keys) if k not in found))
        for i in range(0, len(missing), self.SLICE):
            part = missing[i:i + self.SLICE]
            vecs = self._fetch(part, task)
            with self.lock:
                self.db.executemany("INSERT OR REPLACE INTO e (k, v) VALUES (?, ?)",
                                    [(self._key(t, task), array("d", v).tobytes()) for t, v in zip(part, vecs)])
                self.db.commit()
            for t, v in zip(part, vecs):
                found[self._key(t, task)] = list(v)
        self.misses += len(missing)
        self.hits += len(shown) - len(missing)
        return [found[k] for k in keys]


# ---------------------------------------------------------------- logs (scrubbed, captured per case)
class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record):
        extra = getattr(record, "extra_fields", None)
        msg = record.getMessage() + (" " + json.dumps(extra, default=str) if extra else "")
        if record.exc_info and record.exc_info[1] is not None:
            msg += f" | {type(record.exc_info[1]).__name__}: {str(record.exc_info[1])[:200]}"
        self.lines.append(scrub(f"{record.name}: {msg}")[:500])


class _Scrubbed(logging.Formatter):
    def format(self, record):
        return scrub(super().format(record))


CAPTURE = Capture()


def _logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    err = logging.StreamHandler(sys.stderr)
    err.setFormatter(_Scrubbed("%(levelname)s %(name)s: %(message)s"))
    root.handlers[:] = [err, CAPTURE]
    for noisy in ("httpx", "httpcore", "google", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


# ---------------------------------------------------------------- statistics
def pct(values, q: float):
    """Nearest-rank percentile (None for no values)."""
    s = sorted(v for v in values if v is not None)
    return s[max(0, math.ceil(q * len(s)) - 1)] if s else None


def mean(values):
    v = [x for x in values if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def f3(x) -> str:
    return "-" if x is None else f"{x:.3f}"


def distribution(values: list[float]) -> dict:
    from app.lifecycle import routing

    v = sorted(values)
    return {"n": len(v), "min": round(v[0], 3) if v else None, "p25": pct(v, 0.25), "p50": pct(v, 0.5), "p75": pct(v, 0.75),
            "max": round(v[-1], 3) if v else None, "mean": mean(v),
            "share_at_min": mean([x >= routing.MIN_SIMILARITY for x in v]),
            "share_at_related": mean([x >= routing.RELATED_SIMILARITY for x in v])}


def separation(pos: list[float], neg: list[float]) -> dict:
    """How well a similarity threshold tells answerable (pos) from unanswerable (neg): AUC, the best threshold by balanced
    accuracy, and what the gate's own thresholds do."""
    from app.lifecycle import routing

    if not pos or not neg:
        return {}
    auc = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg) / (len(pos) * len(neg))
    cuts = sorted(set(pos + neg))
    best = max(((sum(p >= t for p in pos) / len(pos) + sum(n < t for n in neg) / len(neg)) / 2, t) for t in cuts)

    def at(t):
        return {"answerable_pass": round(sum(p >= t for p in pos) / len(pos), 3), "unanswerable_pass": round(sum(n >= t for n in neg) / len(neg), 3)}

    return {"auc": round(auc, 3), "best_threshold": round(best[1], 3), "best_balanced_accuracy": round(best[0], 3),
            "at_min_similarity": at(routing.MIN_SIMILARITY), "at_related_similarity": at(routing.RELATED_SIMILARITY)}


# ---------------------------------------------------------------- the database
def _admin(args) -> str:
    url = args.admin_url or os.environ.get("LIVE_DB_ADMIN_URL") or os.environ.get("TEST_PG_ADMIN_URL")
    if not url:
        raise SystemExit("set LIVE_DB_ADMIN_URL (or --admin-url) to a scratch server's superuser URL")
    return url


DEFINER = re.compile(r"\bww_definer\b")


def create_database(admin_url: str, save) -> dict:
    """A new database with its own owner (a non-superuser, as on Cloud SQL), its own app role (NOBYPASSRLS) and its own
    definer role: every migration applied by app.migrate, as a deploy does, from a copy of db/migrations whose only change
    is the definer role's name. No cluster-wide role (ww_app, ww_definer) is created, granted or changed. `save(state)` runs
    before anything is created, so teardown finds what a failed setup left."""
    import psycopg
    from psycopg import sql

    from app import migrate

    tag = f"{datetime.now():%m%d%H%M}_{secrets.token_hex(3)}"
    name, owner, app, definer = f"ww_live_{tag}", f"own_ww_live_{tag}", f"app_ww_live_{tag}", f"def_ww_live_{tag}"
    owner_pw, app_pw = secrets.token_hex(16), secrets.token_hex(16)
    base = admin_url.rsplit("/", 1)[0]
    host = base.split("@", 1)[1]
    state = {"db": name, "owner": owner, "owner_pw": owner_pw, "app": app, "app_pw": app_pw, "definer": definer, "host": host,
             "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    save(state)
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEROLE NOSUPERUSER PASSWORD {}").format(sql.Identifier(owner), sql.Literal(owner_pw)))
        c.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(owner)))
    with psycopg.connect(f"{base}/{name}", autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        c.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    real_dir, real_role = migrate.MIGRATIONS, migrate.APP_ROLE
    with tempfile.TemporaryDirectory(prefix="ww-live-migrations-") as tmp:
        renamed = 0
        for path in sorted(real_dir.glob("*.sql")):
            text, n = DEFINER.subn(definer, path.read_text())
            renamed += n
            (Path(tmp) / path.name).write_text(text)
        migrate.MIGRATIONS, migrate.APP_ROLE = Path(tmp), app  # this database's own roles, created and checked by the migrator
        t0 = time.perf_counter()
        try:
            state["migrations"] = migrate.run(f"postgresql://{owner}:{owner_pw}@{host}/{name}", app_pw)
        finally:
            migrate.MIGRATIONS, migrate.APP_ROLE = real_dir, real_role
    state["migrate_s"] = round(time.perf_counter() - t0, 1)
    state["definer_renamed"] = renamed
    expected = sorted(p.stem for p in real_dir.glob("*.sql"))
    if state["migrations"] != expected:
        raise SystemExit(f"migrations applied {state['migrations']} != the app's {expected}")
    return state


def drop_database(admin_url: str, state: dict) -> list[str]:
    import psycopg
    from psycopg import sql

    done = []
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(state["db"])))
        done.append(f"dropped database {state['db']}")
        for role in (state.get("app"), state.get("definer"), state.get("owner")):
            if not role:
                continue
            try:
                c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
                done.append(f"dropped role {role}")
            except Exception as e:  # noqa: BLE001 - reported; the database is gone either way
                done.append(f"could not drop role {role}: {type(e).__name__}: {str(e)[:160]}")
    return done


# ---------------------------------------------------------------- the world
@dataclass
class World:
    settings: Any
    state: dict
    engine: Any
    store: Any
    lc: Any
    index: Any
    embedder: CachedEmbedder
    emb: EmbedMeter
    scratch: str
    meter: Meter | None = None
    live_llm: Any = None

    def deps(self, llm, repo_root: Path = SAMPLE):
        from app.adapters.memory import InlineBus, LocalBlobStorage, LocalRepoSource
        from app.agents.core.deps import Deps
        from app.seed import DEMO_ISSUES, DEMO_PR_FILES
        from app.services.redaction import RegexRedactor

        repos = LocalRepoSource(str(repo_root), issues=DEMO_ISSUES, default_pr=DEMO_PR_FILES) if repo_root == SAMPLE else LocalRepoSource(str(repo_root))
        return Deps(settings=self.settings, llm=llm, index=self.index, store=self.store, repos=repos, blobs=LocalBlobStorage(self.scratch),
                    redactor=RegexRedactor(), bus=InlineBus(), lifecycle=self.lc)

    def offline(self, repo_root: Path = SAMPLE):
        from app.agents.core.llm import OfflineLLM

        return self.deps(OfflineLLM(), repo_root)

    def live(self, repo_root: Path = SAMPLE):
        if self.live_llm is None:
            from app.agents.core.llm import GeminiLLM

            self.live_llm = metered(GeminiLLM(self.settings), self.meter)
        return self.deps(self.live_llm, repo_root)


def live_settings(scratch: str):
    from app.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    if not s.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is not in this command's environment (no env file is read)")
    if s.embedding_dim != 768:
        raise SystemExit(f"EMBEDDING_DIM is {s.embedding_dim}; the chunks table holds vector(768)")
    # what the code under test sees: the self-hosted stack, files in a scratch folder, no tracing
    return s.model_copy(update={"mode": "self_hosted", "data_dir": scratch, "langsmith_tracing": False})


def open_world(state: dict, args, scratch: str, budget: bool = False) -> World:
    from app.adapters.lifecycle_pg import PgLifecycleStore
    from app.adapters.postgres import PgIndex, PgStore, current_org, make_engine
    from app.container import configure_tracing
    from app.services.embeddings import VertexEmbedder

    settings = live_settings(scratch)
    configure_tracing(settings)
    engine = make_engine(f"postgresql+psycopg://{state['app']}:{state['app_pw']}@{state['host']}/{state['db']}")
    store = PgStore(engine)
    if store.role_can_bypass_rls():
        raise SystemExit("the app role can bypass row-level security")
    emb = EmbedMeter()
    vertex = VertexEmbedder(settings)
    emb.wrap(vertex)
    embedder = CachedEmbedder(vertex, Path(args.cache), settings.embedding_model, single=args.embed_one)
    if state.get("embedding_model") and state["embedding_model"] != settings.embedding_model:
        raise SystemExit(f"this database was embedded with {state['embedding_model']}, not {settings.embedding_model}: "
                         "set EMBEDDING_MODEL to match")
    if state.get("org_id"):
        current_org.set(state["org_id"])
    meter = Meter(Path(args.ledger), args.budget) if budget else None
    return World(settings, state, engine, store, PgLifecycleStore(engine), PgIndex(engine, embedder), embedder, emb, scratch, meter)


def load_chunks(world: World, project_id: str) -> list:
    from app.adapters.postgres import _s
    from app.agents.core.schemas import Chunk

    rows = world.index.rows("""SELECT id, project_id, source_type::text AS source_type, symbol, ref, content, stale FROM chunks
                               WHERE project_id = :p AND NOT stale ORDER BY id""", p=project_id)
    return [Chunk(**_s(r)) for r in rows]


# ---------------------------------------------------------------- setup: the demo project through the app's own code
SUMMARY = ("Ravi explained the validation flow: validate_resource loops over matching rules, check_labels and check_privileged "
           "return violation strings, and apply_defaults adds labels before validation runs.")  # as app/seed.py writes it


def seed_demo(world: World) -> dict:
    """The demo project (app/seed.py's content) on Postgres: an organisation through create_organization, the seed's people
    as members, the sample repository through the ingestion agent (ingest_repo), KT #1 (its result, KT links and transcript
    chunks, as seed() saves them), and the verified answer as the API stores and indexes one (POST /api/verified-answers)."""
    from app.adapters.postgres import current_org
    from app.agents.core.schemas import ActionItem, Chunk, MeetingResult, Term, Utterance
    from app.agents.specialists.librarian.agent import ingest_repo
    from app.ports import VerifiedAnswer
    from app.seed import MOMENTS, TRANSCRIPT, VIJAY_DAY
    from app.services.chunking import chunk_transcript
    from app.services.kt_linker import link_screen_moments

    store, lc = world.store, world.lc
    deps = world.offline()
    current_org.set(None)
    uid = f"live-eval-{secrets.token_hex(4)}"
    admin = store.create_organization(uid, f"{uid}@live-eval.invalid", "Alex (Admin)", "Live eval", "Mini Policy Engine")
    org = str(admin.org_id)
    current_org.set(org)
    pid = str(store.projects_for(admin)[0].id)
    people = {}
    for key, name, role, login in (("priya", "Priya (Manager)", "manager", "priya-mgr"), ("vijay", "Vijay (New hire)", "new_hire", "vijay"),
                                   ("ravi", "Ravi (Senior)", "expert", "ravi-dev"), ("meera", "Meera (Senior)", "expert", "meera-dev")):
        u = lc.ensure_user(org, f"{key}@live-eval.invalid", name, login)
        store.run("UPDATE users SET role = CAST(:r AS user_role) WHERE id = CAST(:i AS uuid)", r=role, i=u.id)
        store.add_member(pid, u.id)
        people[key] = str(u.id)
    store.run("UPDATE users SET github_login = 'alex-admin' WHERE id = CAST(:i AS uuid)", i=admin.id)
    store.create_onboarding(pid, people["vijay"], people["priya"], date.today() - timedelta(days=VIJAY_DAY), "Software Engineer")

    t0 = time.perf_counter()
    repo = store.create_repo(pid, "demo/mini-policy-engine", None)
    ingested = ingest_repo(deps, repo, "main")
    m = store.create_meeting(pid, "KT #1: How validation works", people["ravi"])
    utterances = [Utterance(start_s=a, end_s=b, speaker=s, text=t) for a, b, s, t in TRANSCRIPT]
    links = link_screen_moments(pid, m.id, m.title, MOMENTS, utterances, world.index, meeting_end_s=270)
    store.save_meeting_result(MeetingResult(
        meeting_id=m.id, project_id=pid, title=m.title, summary=SUMMARY,
        decisions=["Validation returns a list of violations and never raises"],
        action_items=[ActionItem(text="Vijay to read engine/policy.py before Friday", owner_hint="Vijay", at_s=232),
                      ActionItem(text="Vijay to look at issue #12 as a first PR", owner_hint="Vijay", at_s=245)],
        terms=[Term(term="violation", definition="A message describing a rule the resource breaks")],
        utterances=utterances, screen_moments=MOMENTS, kt_links=links))
    store.add_kt_links(pid, links)
    world.index.upsert(chunk_transcript(pid, m.id, m.title, utterances))
    va = lc.add_verified_answer(VerifiedAnswer(id="", project_id=pid, question="Who decides the on-call rota?",
                                               answer="Priya sets it each month; swaps are agreed in the team channel and noted in the rota.",
                                               verified_by=people["ravi"], reuse_count=2))
    ravi = store.get_user(people["ravi"])
    world.index.upsert([Chunk(id=f"va-{va.id}", project_id=pid, source_type="verified_answer",
                              ref={"question": va.question, "expert_id": ravi.id, "expert_name": ravi.name, "verified_answer_id": va.id},
                              content=f"Q: {va.question}\nA (verified by {ravi.name}): {va.answer}")])
    kinds = Counter(c.source_type for c in load_chunks(world, pid))
    return {"org_id": org, "admin_id": str(admin.id), "demo": {"project_id": pid, "meeting_id": str(m.id), "verified_id": str(va.id),
            "users": people, "ingested": ingested, "kt_links": len(links), "chunks": dict(kinds),
            "seconds": round(time.perf_counter() - t0, 1)}}


def ingest_self(world: World, batch: int = 200) -> dict:
    """Welcome Wave's own backend/app/**/*.py and docs/*.md (rag_eval.self_files) as a second project of the organisation,
    chunked as ingestion chunks a connected repository (should_index, chunk_code for every file) and upserted in ingestion's
    batches. Resumable: chunks already in the index are not embedded again."""
    from app.services.chunking import chunk_code, should_index
    from eval.rag_eval import self_files

    state, store = world.state, world.store
    if not state.get("self", {}).get("project_id"):
        p = store.create_project(state["org_id"], "Welcome Wave", "Welcome Wave's own backend and docs", state["admin_id"])
        state["self"] = {"project_id": str(p.id)}
    pid = state["self"]["project_id"]
    chunks, files = [], 0
    for f in self_files():
        rel, text = f.relative_to(ROOT).as_posix(), f.read_text(encoding="utf-8")
        if should_index(rel, len(text.encode())):
            files += 1
            chunks += chunk_code(pid, rel, text)
    have = {r["id"] for r in world.index.rows("SELECT id FROM chunks WHERE project_id = :p", p=pid)}
    todo = [c for c in chunks if c.id not in have]
    say(f"self: {files} files, {len(chunks)} chunks, {len(todo)} to embed")
    t0, mark = time.perf_counter(), world.emb.mark()
    for i in range(0, len(todo), batch):
        world.index.upsert(todo[i:i + batch])
        e = world.emb.since(mark)
        say(f"  {min(i + batch, len(todo))}/{len(todo)} upserted, {time.perf_counter() - t0:.0f} s, embedding requests {e['requests']} "
            f"({e['429']} answered 429)")
    state["self"].update(files=files, chunks=len(chunks), seconds=round(time.perf_counter() - t0, 1), embedding=world.emb.since(mark))
    return state["self"]


# ---------------------------------------------------------------- cases and corpora
def cases_for(world: World, name: str) -> list[dict]:
    from eval.rag_eval import load_cases

    cases = load_cases(EVAL / f"golden_{name}.jsonl")
    if name == "demo":  # the golden set names the demo's in-memory meeting id; here the call has a database id
        mid = world.state["demo"]["meeting_id"]
        for c in cases:
            for e in c["expect"]:
                if e["kind"] == "meeting" and e.get("meeting") == "mtg-1":
                    e["meeting"] = mid
    return cases


def corpus(world: World, name: str, deps):
    from eval.rag_eval import Corpus

    s = world.state
    pid = s["demo" if name == "demo" else "self"]["project_id"]
    user = world.store.get_user(s["demo"]["users"]["vijay"] if name == "demo" else s["admin_id"])
    chunks = load_chunks(world, pid)
    kinds = Counter(c.source_type for c in chunks)
    info = f"{name}: {len(chunks)} chunks in Postgres ({', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))}), Gemini embeddings"
    return Corpus(name, deps, pid, s["org_id"], user, chunks, world.lc.verified_answers(pid), info)


def pick_crew_cases(cases: list[dict]) -> list[dict]:
    """13 answerable holdout cases that name no identifier, round-robin over the categories in file order (where a planner
    can add most), and the first 2 unanswerable holdout cases. Fixed by the golden set, not by any result."""
    from eval.rag_eval import CATEGORIES

    hold = [c for c in cases if c["split"] == "holdout"]
    pools = {cat: [c for c in hold if c["answerable"] and not c.get("named") and c["category"] == cat] for cat in CATEGORIES}
    picked: list[dict] = []
    while len(picked) < CREW_ANSWERABLE and any(pools.values()):
        for cat in CATEGORIES:
            if pools[cat] and len(picked) < CREW_ANSWERABLE:
                picked.append(pools[cat].pop(0))
    return picked + [c for c in hold if c["category"] == "unanswerable"][:CREW_UNANSWERABLE]


# ---------------------------------------------------------------- step 2: retrieval with real embeddings
def similarity_report(rows: list[dict]) -> dict:
    """The gate's evidence on each question: the top hit's similarity, and the best of the first 8 (what a writer is shown),
    answerable vs unanswerable; and the similarity of the first relevant hit (how similar the right source is)."""
    out = {}
    for label, pick in (("top1", lambda r: r["top_similarity"]), ("best_of_8", lambda r: max((g["similarity"] for g in r["got"][:8]), default=0.0))):
        pos = [pick(r) for r in rows if r["answerable"] and not r["error"]]
        neg = [pick(r) for r in rows if not r["answerable"] and not r["error"]]
        out[label] = {"answerable": distribution(pos), "unanswerable": distribution(neg), "separation": separation(pos, neg)}
    rel = [next((g["similarity"] for g in r["got"] if g["relevant"]), None) for r in rows if r["answerable"]]
    out["first_relevant"] = distribution([x for x in rel if x is not None])
    return out


def split_summaries(rows: list[dict]) -> dict:
    from eval.rag_eval import summarize

    out = {"all": summarize(rows)}
    for split in ("dev", "holdout"):
        out[split] = summarize([r for r in rows if r["split"] == split])
    out["all_but_verified"] = summarize([r for r in rows if r["category"] != "verified"])
    return out


def cmd_retrieval(args, scratch: str) -> int:
    from app.rag import crew
    from eval import rag_eval as RE

    state = json.loads(Path(args.state).read_text())
    world = open_world(state, args, scratch)
    names = [n for n in ("demo", "self") if n == "demo" or state.get("self", {}).get("chunks")]
    if args.corpus != "all":
        names = [n for n in names if n == args.corpus]
    results, report = {}, {}
    for name in names:
        deps = world.offline(SAMPLE if name == "demo" else ROOT)  # OfflineLLM: the crew plans and ranks by rules alone
        c = corpus(world, name, deps)
        cases = cases_for(world, name)
        problems = RE.check_golden(c, cases)
        say(c.info, f"; {len(cases)} cases; golden check: {len(problems)} problem(s)", *(f"\n    {p}" for p in problems[:6]))
        results[name] = {}
        for method in ("baseline", "crew"):
            mark = world.emb.mark()
            fn = (lambda q, k, c=c: c.deps.index.search(c.project, q, k=k)) if method == "baseline" else \
                 (lambda q, k, c=c: crew.retrieve(c.deps, c.project, q, k=k, purpose="answer").hits)
            res = RE.run_retrieval(c, method, fn, cases, 0.0)
            res["embedding"] = world.emb.since(mark)
            res["splits"] = split_summaries(res["rows"])
            res["similarity"] = similarity_report(res["rows"])
            results[name][method] = res
            errors = [r for r in res["rows"] if r["error"]]
            if errors:
                say(f"  {name}/{method}: {len(errors)} question(s) failed, first {errors[0]['id']}: {errors[0]['error']}",
                    *(f"\n    {k} x{n}" for k, n in Counter(e["error"][:120] for e in errors).most_common(4)))
    RE.print_retrieval(results, "all")
    RE.print_categories(results)
    offline = json.loads(Path(args.offline_json).read_text())["retrieval"] if args.offline_json else {}
    say("\nBy split (answerable cases; 'offline' = rag_eval's MemoryIndex(HashEmbedder()) on the same golden set):")
    say(f"{'corpus':7}{'method':9}{'split':17}{'n':>4}{'hit@1':>7}{'R@5':>6}{'R@10':>6}{'MRR':>6}{'nDCG':>6}   offline: hit@1  R@5   MRR  nDCG")
    for name, by in results.items():
        for method, res in by.items():
            for split, s in res["splits"].items():
                off = ""
                if name in offline and method in offline[name]:
                    rows = offline[name][method]["rows"]
                    rows = [r for r in rows if (split in ("all", "all_but_verified") or r["split"] == split) and
                            (split != "all_but_verified" or r["category"] != "verified")]
                    o = RE.summarize(rows)
                    off = f"          {o['hit1']:.2f} {o['r5']:.2f} {o['mrr']:.2f} {o['ndcg']:.2f}"
                say(f"{name:7}{method:9}{split:17}{s['cases']:>4}{s['hit1']:>7.2f}{s['r5']:>6.2f}{s['r10']:>6.2f}{s['mrr']:>6.2f}"
                    f"{s['ndcg']:>6.2f}{off}")
    say("\nSimilarity to the question as asked (gate: MIN_SIMILARITY 0.55; RELATED_SIMILARITY 0.80):")
    for name, by in results.items():
        for method, res in by.items():
            for label in ("top1", "best_of_8"):
                r = res["similarity"][label]
                a, u, sep = r["answerable"], r["unanswerable"], r["separation"]
                say(f"  {name}/{method}/{label}: answerable n={a['n']} p25/p50/p75 {f3(a['p25'])}/{f3(a['p50'])}/{f3(a['p75'])} "
                    f"(>=0.55 {f3(a['share_at_min'])}, >=0.80 {f3(a['share_at_related'])}); unanswerable n={u['n']} "
                    f"min/p50/max {f3(u['min'])}/{f3(u['p50'])}/{f3(u['max'])} (>=0.55 {f3(u['share_at_min'])}, >=0.80 "
                    f"{f3(u['share_at_related'])}); AUC {sep.get('auc')}, best cut {sep.get('best_threshold')} "
                    f"(balanced acc {sep.get('best_balanced_accuracy')})")
            fr = res["similarity"]["first_relevant"]
            say(f"  {name}/{method}: similarity of the first relevant hit p25/p50/p75 {fr['p25']}/{fr['p50']}/{fr['p75']} (n={fr['n']})")
        for method, res in by.items():
            e = res["embedding"]
            say(f"  {name}/{method}: embedding requests {e['requests']} ({e['429']} x 429), p50 {e['p50_ms']} ms, p95 {e['p95_ms']} ms; "
                f"retrieval p50 {res['summary']['p50_ms']} ms, p95 {res['summary']['p95_ms']} ms")
    report = {"meta": {"state": state["db"], "cache": {"hits": world.embedder.hits, "misses": world.embedder.misses},
                       "embedding": world.emb.since(0)}, "retrieval": results}
    if args.json:
        write_json(args.json, report)
    return 0


# ---------------------------------------------------------------- step 3: the crew's model stages
def cmd_crew(args, scratch: str) -> int:
    from app.rag import crew
    from eval import rag_eval as RE

    if args.index == "memory":
        # rag_eval's offline self corpus (MemoryIndex(HashEmbedder()), no database, no embedding request) with the real
        # model for the planner and judge: the model stages against rules alone when real embeddings cannot be had
        import dataclasses

        from app.agents.core.llm import GeminiLLM
        from app.container import configure_tracing

        settings = live_settings(scratch)
        configure_tracing(settings)
        meter = Meter(Path(args.ledger), args.budget)
        rules_c = RE.build_self(False, scratch)
        live_deps = dataclasses.replace(rules_c.deps, llm=metered(GeminiLLM(settings), meter))
        cases = pick_crew_cases(RE.load_cases(EVAL / "golden_self.jsonl"))
        embedding, where = None, rules_c.info
    else:
        state = json.loads(Path(args.state).read_text())
        if not state.get("self", {}).get("chunks"):
            raise SystemExit("the self corpus is not ingested: run ingest-self first")
        world = open_world(state, args, scratch, budget=True)
        rules_c = corpus(world, "self", world.offline(ROOT))
        live_deps = world.live(ROOT)
        cases = pick_crew_cases(cases_for(world, "self"))
        meter, embedding, where = world.meter, world.emb, rules_c.info
    say(f"crew: {len(cases)} self holdout cases on {where}; generate ledger {meter.used}/{meter.budget} used")
    rows = []
    for i, case in enumerate(cases):
        if meter.left() < args.reserve or meter.throttled():
            say(f"  stopping before {case['id']}: ledger {meter.used}/{meter.budget}, throttled={meter.throttled()}")
            break
        if i and args.gap:
            time.sleep(args.gap)
        row = {"id": case["id"], "q": case["q"], "category": case["category"], "answerable": case["answerable"]}
        for name, deps in (("rules", rules_c.deps), ("model", live_deps)):
            mark, logs, t0 = meter.mark(), len(CAPTURE.lines), time.perf_counter()
            try:
                r, err = crew.retrieve(deps, rules_c.project, case["q"], k=K, purpose="answer"), None
            except Exception as e:  # noqa: BLE001 - reported per case
                r, err = None, scrub(f"{type(e).__name__}: {e}")[:240]
            ms = round((time.perf_counter() - t0) * 1000)
            hits = r.hits if r else []
            graded = RE.grade_list([h.chunk for h in hits], case["expect"])
            got = {"ms": ms, "error": err, "planner": r.planner if r else None, "judged": r.judged if r else None,
                   "stage_ms": r.ms if r else None, "candidates": r.candidates if r else None,
                   "queries": [{"origin": q.origin, "text": q.text[:200]} for q in (r.analysis.queries if r else [])],
                   "grades_top10": [r.grades.get(h.chunk.id) for h in hits] if r else [],
                   "grade_histogram": dict(Counter(r.grades.values())) if r else {},
                   "top": [{"label": RE.label(h.chunk), "similarity": round(h.similarity, 3), "grade": g, "relevant": rel}
                           for h, (g, rel) in zip(hits[:5], graded[:5])],
                   "warnings": CAPTURE.lines[logs:], "calls": meter.since(mark) if name == "model" else None}
            if case["answerable"]:
                got.update(RE.case_metrics(graded, case["expect"]))
            row[name] = got
        meter.save()
        rows.append(row)
        m = row["model"]
        say(f"  {case['id']:12} rules nDCG {row['rules'].get('ndcg', float('nan')):.2f} | model nDCG {m.get('ndcg', float('nan')):.2f} "
            f"planner={m['planner']} judged={m['judged']} {m['ms']} ms (plan {m['stage_ms'] and m['stage_ms'].get('plan')}, judge "
            f"{m['stage_ms'] and m['stage_ms'].get('judge')}) calls={m['calls']['calls']} warn={len(m['warnings'])}")
    summary = crew_summary(rows)
    say("\n" + json.dumps(summary, indent=1))
    if args.json:
        write_json(args.json, {"meta": {"index": args.index, "corpus": where, "ledger": meter.used, "budget": meter.budget,
                                        "embedding": embedding.since(0) if embedding else None},
                               "summary": summary, "rows": rows})
    return 0


def crew_summary(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    out = {"cases": len(rows), "answerable": len(ans)}
    for name in ("rules", "model"):
        sel = [r[name] for r in ans]
        out[name] = {m: mean(x.get(m) for x in sel) for m in ("hit1", "r5", "r10", "mrr", "ndcg")}
        allr = [r[name] for r in rows]
        out[name].update(p50_ms=pct([x["ms"] for x in allr], 0.5), p95_ms=pct([x["ms"] for x in allr], 0.95),
                         errors=sum(1 for x in allr if x["error"]))
    model = [r["model"] for r in rows]
    out["model"].update(planned=sum(1 for x in model if x["planner"] == "model"), judged=sum(1 for x in model if x["judged"]),
                        calls=sum(x["calls"]["calls"] for x in model), calls_429=sum(x["calls"]["429"] for x in model),
                        tokens_total=sum(x["calls"]["tokens_total"] for x in model),
                        plan_ms_p50=pct([(x["stage_ms"] or {}).get("plan") for x in model], 0.5),
                        plan_ms_p95=pct([(x["stage_ms"] or {}).get("plan") for x in model], 0.95),
                        judge_ms_p50=pct([(x["stage_ms"] or {}).get("judge") for x in model if x["judged"]], 0.5),
                        judge_ms_p95=pct([(x["stage_ms"] or {}).get("judge") for x in model if x["judged"]], 0.95),
                        warnings=sorted({w.split(" (", 1)[0] + (" (" + w.split(" (", 1)[1].split(")")[0] + ")" if " (" in w else "")
                                         for x in model for w in x["warnings"]})[:12])
    out["ndcg_wins_ties_losses"] = [sum(1 for r in ans if (r["model"].get("ndcg") or 0) > (r["rules"].get("ndcg") or 0) + 1e-9),
                                    sum(1 for r in ans if abs((r["model"].get("ndcg") or 0) - (r["rules"].get("ndcg") or 0)) <= 1e-9),
                                    sum(1 for r in ans if (r["model"].get("ndcg") or 0) < (r["rules"].get("ndcg") or 0) - 1e-9)]
    out["unanswerable_max_grade"] = {r["id"]: max((g for g in r["model"]["grade_histogram"]), default=None) for r in rows if not r["answerable"]}
    out["answerable_max_grade"] = Counter(str(max((int(g) for g in r["model"]["grade_histogram"]), default=None)) for r in ans)
    return out


# ---------------------------------------------------------------- step 4: the Guide end to end
CITE = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
CODE_SPAN = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def _markers(answer: str) -> list[int]:
    prose = CODE_SPAN.sub("", answer or "")
    out = []
    for m in CITE.finditer(prose):
        part = m.group(1)
        if re.search(r"[–-]", part):
            a, b = (int(x) for x in re.findall(r"\d+", part)[:2])
            out += list(range(min(a, b), max(a, b) + 1))
        else:
            out += [int(x) for x in re.findall(r"\d+", part)]
    return sorted(set(out))


def guide_case(world: World, c, ctx, case: dict) -> dict:
    from app.agents.specialists.guide import agent as guide
    from eval import rag_eval as RE

    steps: list = []
    mark = world.meter.mark() if world.meter else 0
    logs, t0 = len(CAPTURE.lines), time.perf_counter()
    try:
        ai, error = guide.attempt(ctx, c.project, case["q"], False, None, steps, None), None
    except Exception as e:  # noqa: BLE001 - reported per case
        ai, error = {}, scrub(f"{type(e).__name__}: {e}")[:300]
    ms = round((time.perf_counter() - t0) * 1000)
    result, check, out = ai.get("result"), ai.get("check"), ai.get("outcome")
    cited = RE._cited_chunks(c, result) if result else []
    good = [any(RE.match(e, ch) == "full" for e in case["expect"]) for ch in cited]
    answer = result.answer if result else ""
    markers = _markers(answer)
    texts = [(getattr(s, "kind", ""), getattr(s, "tool", None), scrub(getattr(s, "text", ""))[:300], getattr(s, "ms", 0)) for s in steps]
    verify = [t for k, _, t, _ in texts if k == "verify" and t.startswith("Verifying")]
    claims = [tuple(int(x) for x in re.findall(r"\d+", v)[:2]) for v in verify]
    return {"id": case["id"], "q": case["q"], "category": case["category"], "split": case["split"], "answerable": case["answerable"],
            "answered": bool(result and result.status == "answered"), "error": error, "ms": ms,
            "writer": ai.get("writer"), "agentic": ai.get("agentic"), "crew": ai.get("crew"),
            "answer": scrub(answer), "draft": scrub(ai.get("draft") or "")[:1200] if not result else "",
            "citations": [{"label": RE.label(ch), "ok": ok} for ch, ok in zip(cited, good)], "citations_ok": good,
            "markers": markers, "markers_ok": bool(result) and all(1 <= n <= len(cited) for n in markers)
            and set(range(1, len(cited) + 1)) <= set(markers),
            "guard_ran": check is not None, "guard_ok": bool(check and check.get("ok")),
            "guard_unsupported": [r["text"] for r in (check or {}).get("sentences", []) if r.get("claim") and not r.get("sources")],
            "gate": ai.get("retrieval"), "why": [scrub(w) for w in ai.get("why") or []],
            "verify": verify, "verify_claims": claims, "repaired": any("rewritten once" in w for w in ai.get("why") or []),
            "repair_attempted": any(t.startswith("Rewriting the answer") for _, _, t, _ in texts),
            "related": [r.get("id") for r in ai.get("related") or []], "route": (ai.get("route") or {}).get("name"),
            "loop": {"stopped": out.stopped, "turns": out.turns, "tool_calls": out.tool_calls, "tokens": out.tokens,
                     "error": scrub(out.error) if out.error else None} if out is not None else None,
            "steps": [{"kind": k, "tool": tl, "text": t, "ms": m} for k, tl, t, m in texts],
            "calls": world.meter.since(mark) if world.meter else None, "warnings": CAPTURE.lines[logs:]}


def guide_summary(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    answered = [r for r in ans if r["answered"]]
    cites = [ok for r in rows if r["answered"] for ok in r["citations_ok"]]
    guarded = [r for r in rows if r["guard_ran"]]
    calls = [r["calls"]["calls"] for r in rows if r.get("calls")]
    tokens = [r["calls"]["tokens_total"] for r in rows if r.get("calls")]
    return {"cases": len(rows), "answerable": len(ans), "unanswerable": len(una),
            "answered_rate": mean([r["answered"] for r in ans]), "refusal_rate": mean([not r["answered"] for r in una]),
            "citation_precision": mean(cites), "cites_expected": mean([any(r["citations_ok"]) for r in answered]),
            "guard_pass_rate": mean([r["guard_ok"] for r in guarded]), "guard_runs": len(guarded),
            "markers_ok": mean([r["markers_ok"] for r in rows if r["answered"]]),
            # "Verifying N claims: M supported": M < N when the guard or VERIFY found a claim unsupported (VERIFY runs only
            # when the guard passed, so which one it was is in each row's steps)
            "verify_ran": sum(1 for r in rows if r["verify"]),
            "claims_flagged": sum(1 for r in rows if any(n > held for n, held in r["verify_claims"])),
            "repair_attempted": sum(1 for r in rows if r["repair_attempted"]), "repaired": sum(1 for r in rows if r["repaired"]),
            "guard_refused": sum(1 for r in rows if r["guard_ran"] and not r["guard_ok"] and not r["answered"]),
            "loop_stopped": dict(Counter((r["loop"] or {}).get("stopped", "no loop") for r in rows)),
            "writers": dict(Counter(str(r["writer"]) for r in rows)),
            "p50_ms": pct([r["ms"] for r in rows], 0.5), "p95_ms": pct([r["ms"] for r in rows], 0.95),
            "calls_per_question": {"mean": mean(calls), "p50": pct(calls, 0.5), "max": max(calls) if calls else None, "total": sum(calls)},
            "tokens_per_question": {"mean": mean(tokens), "p50": pct(tokens, 0.5), "max": max(tokens) if tokens else None, "total": sum(tokens)},
            "errors": sum(1 for r in rows if r["error"])}


def cmd_guide(args, scratch: str) -> int:
    from app.agents.core import registry as R

    state = json.loads(Path(args.state).read_text())
    world = open_world(state, args, scratch, budget=not args.offline)
    deps = world.offline() if args.offline else world.live()
    c = corpus(world, "demo", deps)
    by_id = {x["id"]: x for x in cases_for(world, "demo")}
    cases = [by_id[i] for i in (args.cases.split(",") if args.cases else GUIDE_CASES)]
    gdeps = R.deps_for(deps, c.org, "guide")
    ctx = R.lifecycle_ctx(gdeps, c.org, c.user)
    say(f"guide ({'offline writer' if args.offline else 'LIVE model'}): {len(cases)} cases"
        + (f"; generate ledger {world.meter.used}/{world.meter.budget} used" if world.meter else ""))
    rows = []
    for i, case in enumerate(cases):
        if world.meter and (world.meter.left() < args.reserve or world.meter.throttled()):
            say(f"  stopping before {case['id']}: ledger {world.meter.used}/{world.meter.budget}, throttled={world.meter.throttled()}")
            break
        if i and args.gap and not args.offline:
            time.sleep(args.gap)
        row = guide_case(world, c, ctx, case)
        rows.append(row)
        if world.meter:
            world.meter.save()
        calls = row["calls"] or {}
        say(f"  {row['id']:11} {'ANSWERED' if row['answered'] else 'refused '} {row['ms']:>6} ms cites={sum(row['citations_ok'])}/"
            f"{len(row['citations_ok'])} guard={row['guard_ok'] if row['guard_ran'] else '-'} verify={row['verify_claims']} "
            f"repair={row['repair_attempted']} loop={(row['loop'] or {}).get('stopped')}/{(row['loop'] or {}).get('turns')}t "
            f"calls={calls.get('calls', 0)} tok={calls.get('tokens_total', 0)} err={bool(row['error'])}")
        if args.partial:
            write_json(args.partial, {"rows": rows})
    summary = guide_summary(rows)
    say("\n" + json.dumps(summary, indent=1))
    say("\nEvery answer (judge each against the sources):")
    for r in rows:
        say(f"\n[{r['id']}] ({'answerable' if r['answerable'] else 'UNANSWERABLE'}, {r['category']}, {r['split']}) {r['q']}")
        say(f"  -> {'ANSWERED' if r['answered'] else 'not answered'} writer={r['writer']} gate={r['gate']} {r['ms']} ms")
        if r["answer"]:
            say("  answer: " + r["answer"].replace("\n", "\n          "))
        for j, cit in enumerate(r["citations"], 1):
            say(f"    [{j}] {cit['label']} {'(expected)' if cit['ok'] else ''}")
        if r["draft"]:
            say("  draft: " + r["draft"][:600].replace("\n", " "))
        for w in r["why"]:
            say(f"  why: {w[:300]}")
        if r["guard_unsupported"]:
            say(f"  guard found unsupported: {r['guard_unsupported']}")
        for s in r["steps"]:
            say(f"    . {s['kind']:6} {s['tool'] or '':16} {s['text'][:160]} ({s['ms']} ms)")
        for w in r["warnings"][:6]:
            say(f"    ! {w[:240]}")
    if args.json:
        write_json(args.json, {"meta": {"mode": "offline" if args.offline else "live", "ledger": world.meter.used if world.meter else None,
                                        "embedding": world.emb.since(0)}, "summary": summary, "rows": rows})
    return 0


# ---------------------------------------------------------------- main
def write_json(path: str, payload: dict) -> None:
    text = scrub(json.dumps(payload, indent=1, default=str))
    Path(path).write_text(text)
    say(f"wrote {path}")


def cmd_setup(args, scratch: str) -> int:
    state_path = Path(args.state)
    if state_path.exists():
        raise SystemExit(f"{state_path} exists: tear that database down first, or name another state file")
    model = live_settings(scratch).embedding_model  # the key and the embedding size are checked before anything is created
    state = create_database(_admin(args), lambda s: state_path.write_text(json.dumps(s, indent=1)))
    state["embedding_model"] = model
    state_path.write_text(json.dumps(state, indent=1))
    say(f"database {state['db']}: {len(state['migrations'])} migrations in {state['migrate_s']} s (app role {state['app']}, no BYPASSRLS)")
    world = open_world(state, args, scratch)
    state.update(seed_demo(world))
    state_path.write_text(json.dumps(state, indent=1))
    say(f"demo: {json.dumps(state['demo'], default=str)}")
    say(f"embedding ({model}): {json.dumps(world.emb.since(0))}; short answers {world.embedder.short_answers}")
    return 0


def cmd_ingest_self(args, scratch: str) -> int:
    state_path = Path(args.state)
    state = json.loads(state_path.read_text())
    world = open_world(state, args, scratch)
    try:
        ingest_self(world)
    finally:
        state_path.write_text(json.dumps(state, indent=1, default=str))
    say(f"self: {json.dumps(state['self'], default=str)}")
    return 0


def cmd_teardown(args, scratch: str) -> int:
    state = json.loads(Path(args.state).read_text())
    for line in drop_database(_admin(args), state):
        say(line)
    Path(args.state).rename(Path(args.state).with_suffix(".dropped.json"))
    return 0


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m eval.live_eval", description="The Guide on Postgres + Gemini embeddings + the real model.")
    p.add_argument("command", choices=("setup", "ingest-self", "retrieval", "crew", "guide", "teardown"))
    p.add_argument("--state", required=True, help="the run's state file (database name, its roles, the projects' ids)")
    p.add_argument("--admin-url", help="a scratch server's superuser URL (default: LIVE_DB_ADMIN_URL, else TEST_PG_ADMIN_URL)")
    p.add_argument("--json", help="write every case's results here")
    p.add_argument("--ledger", help="the generate-request ledger shared by every run (default: <state>.calls.json)")
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"generate requests allowed, all runs together (default {DEFAULT_BUDGET})")
    p.add_argument("--reserve", type=int, default=None, help="stop starting cases with fewer requests than this left (crew 4, guide 12)")
    p.add_argument("--cache", help="the embedding cache (default: <state>.embeddings.sqlite)")
    p.add_argument("--embed-one", action="store_true",
                   help="one text per embedding request from the start (models that merge a list into one vector)")
    p.add_argument("--gap", type=float, default=5.0, help="seconds between live cases (free-tier per-minute limits; default 5)")
    p.add_argument("--corpus", choices=("demo", "self", "all"), default="all", help="retrieval: which corpora")
    p.add_argument("--offline-json", help="retrieval: rag_eval --json output to print beside the live numbers")
    p.add_argument("--index", choices=("pg", "memory"), default="pg",
                   help="crew: the self corpus in this run's database (pg), or rag_eval's offline MemoryIndex(HashEmbedder()) "
                        "(memory: no database, no embedding request; the model stages still call the real model)")
    p.add_argument("--offline", action="store_true", help="guide: the offline writer instead of the model (no generate request)")
    p.add_argument("--cases", help="guide: comma-separated case ids instead of the fixed 14")
    p.add_argument("--partial", help="guide: rewrite this file after every case")
    a = p.parse_args(argv)
    stem = str(Path(a.state).with_suffix(""))
    a.ledger = a.ledger or stem + ".calls.json"
    a.cache = a.cache or stem + ".embeddings.sqlite"
    if a.reserve is None:
        a.reserve = 4 if a.command == "crew" else 12
    return a


def main(argv: list[str]) -> int:
    args = parse(argv)
    sys.dont_write_bytecode = True
    os.environ["MODE"] = "demo"  # only while Settings load: demo mode reads no env file (backend/.env is never read)
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    _logging()
    with tempfile.TemporaryDirectory(prefix="ww-live-eval-") as scratch:
        os.environ["DATA_DIR"] = scratch
        cmd = {"setup": cmd_setup, "ingest-self": cmd_ingest_self, "retrieval": cmd_retrieval, "crew": cmd_crew, "guide": cmd_guide,
               "teardown": cmd_teardown}[args.command]
        return cmd(args, scratch)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
