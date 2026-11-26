"""Cloud SQL (PostgreSQL + pgvector) implementations of Index and Store.

Tenancy: the current organisation lives in a context variable set by the API (after login) or by a worker
(from the event's project). Every transaction copies it into `app.org_id`, and row-level security does the
rest - a bug in a query cannot leak another company's data.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app import ports
from app.agents.core.schemas import Chunk, Hit, KTLink, MeetingResult, QAResult, ReviewResult
from app.ports import Approval, CodeChunks, Meeting, Onboarding, Project, RepoBusy, Repository, ReviewRow, TaskRow, User
from app.services.chunking import split_identifiers
from app.services.embeddings import Embedder
from app.services.kt_linker import CodeRef

current_org: ContextVar[str | None] = ContextVar("current_org", default=None)
# (engine, thread id, connection) while a reading() block runs: that thread's statements on that engine share its transaction
_pinned: ContextVar[tuple | None] = ContextVar("pg_reading", default=None)


def session_options(settings=None) -> dict:
    """The app's database session limits (Settings db_*; 0 turns one off): a statement, a wait for a lock and a transaction
    left open are cancelled past them, so a slow query or a migration's ALTER cannot hold every pooled connection."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    limits = {"statement_timeout": settings.db_statement_timeout_ms, "lock_timeout": settings.db_lock_timeout_ms,
              "idle_in_transaction_session_timeout": settings.db_idle_in_transaction_timeout_ms}
    out: dict = {"options": " ".join(f"-c {k}={int(v)}" for k, v in limits.items() if v and v > 0)}
    if settings.db_connect_timeout_s and settings.db_connect_timeout_s > 0:
        out["connect_timeout"] = int(settings.db_connect_timeout_s)
    return {k: v for k, v in out.items() if v}


def quiet_errors(engine: Engine) -> Engine:
    """Database errors without the values they were about. SQLAlchemy's message carries the bound parameters (hidden by
    hide_parameters) and PostgreSQL's carries a DETAIL line that quotes the row ("Key (email)=(new.hire@example.com) already
    exists"). Either would reach a log line, a run record or an incident notice whenever the error is printed. The error
    keeps its type and primary message; the detail stays readable in code as `orig.diag.message_detail`."""
    from sqlalchemy import event

    @event.listens_for(engine, "handle_error")
    def _primary_only(ctx) -> None:
        orig = ctx.original_exception
        primary = getattr(getattr(orig, "diag", None), "message_primary", None)
        if not primary or not getattr(orig, "args", None) or orig.args[0] == primary:
            return
        orig.args = (primary,)
        wrapped = ctx.sqlalchemy_exception
        if wrapped is not None and len(wrapped.args) == 1:
            wrapped.args = (f"({type(orig).__module__}.{type(orig).__name__}) {primary}",)

    return engine


def make_engine(url: str, pool_size: int = 5, max_overflow: int = 5, settings=None) -> Engine:
    return quiet_errors(create_engine(url, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True, pool_recycle=1800,
                                      pool_timeout=10, hide_parameters=True, connect_args=session_options(settings)))


class _Db:
    def __init__(self, engine: Engine):
        self.engine = engine

    def _pin(self):
        pin = _pinned.get()
        return pin[2] if pin is not None and pin[0] is self.engine and pin[1] == threading.get_ident() else None

    @contextmanager
    def tx(self):
        pinned = self._pin()
        if pinned is not None:  # inside reading(): the page's one transaction (other threads never share it)
            yield pinned
            return
        with self.engine.begin() as c:
            org = current_org.get()
            if org:
                c.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": org})
            yield c

    @contextmanager
    def reading(self):
        """Every statement this thread makes in the block runs on one pooled connection, in one read-only transaction with
        one snapshot: one checkout, one pre-ping and one set_config for all of a page's reads instead of one each. Keep
        only reads in it, and nothing slow (an embedding call, building a plan): the connection is held until it ends."""
        if self._pin() is not None:
            yield
            return
        with self.engine.begin() as c:
            c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            org = current_org.get()
            if org:
                c.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": org})
            token = _pinned.set((self.engine, threading.get_ident(), c))
            try:
                yield
            finally:
                _pinned.reset(token)

    @contextmanager
    def writing(self):
        """Keep related writes on one tenant-scoped transaction, including lifecycle adapter calls."""
        if self._pin() is not None:
            yield
            return
        with self.engine.begin() as c:
            org = current_org.get()
            if org:
                c.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": org})
            token = _pinned.set((self.engine, threading.get_ident(), c))
            try:
                yield
            finally:
                _pinned.reset(token)

    def rows(self, sql, **p):
        with self.tx() as c:
            return [dict(r._mapping) for r in c.execute(text(sql), p)]

    def one(self, sql, **p):
        r = self.rows(sql, **p)
        return r[0] if r else None

    def run(self, sql, **p):
        with self.tx() as c:
            return c.execute(text(sql), p).rowcount


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def _ids(fn):
    """A malformed id (e.g. from a URL) means "not found", not a database error: the wrapped lookup returns None."""
    import functools

    @functools.wraps(fn)
    def wrapper(self, the_id, *a, **kw):
        return fn(self, the_id, *a, **kw) if _is_uuid(the_id) else None
    return wrapper


STOP_WORDS = {"the", "a", "an", "is", "of", "to", "in", "how", "what", "does", "do", "where", "why", "and", "or", "for", "on",
              "it", "this", "that", "with", "be", "are", "can", "i", "we", "which", "when", "who"}


def keyword_query(text_: str) -> str | None:
    """OR of the question's identifier-split words, for to_tsquery('simple', ...). A question's words rarely ALL appear
    in one chunk, so requiring all of them (websearch/plainto) almost never matches; ts_rank_cd still ranks overlap."""
    words = [w for w in re.findall(r"[a-z0-9]+", split_identifiers(text_).lower()) if len(w) > 1 and w not in STOP_WORDS]
    return " | ".join(dict.fromkeys(words)) or None


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


# The columns of a User, from `users` (USER_COLS) or `users u` (USER_COLS_U). `status` is derived from deactivated_at (013).
USER_COLS = ("id, org_id, email, name, role::text AS role, github_login, "
             "CASE WHEN deactivated_at IS NULL THEN 'active' ELSE 'deactivated' END AS status")
USER_COLS_U = ("u.id, u.org_id, u.email, u.name, u.role::text AS role, u.github_login, "
               "CASE WHEN u.deactivated_at IS NULL THEN 'active' ELSE 'deactivated' END AS status")


def _s(row: dict) -> dict:
    """uuid -> str (in any column, e.g. decided_by, asked_by) so rows fit the pydantic models and JSON."""
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in row.items()}


SOURCE_TYPES = frozenset({"code", "doc", "meeting", "verified_answer"})  # the source_type enum (db/migrations/001_init.sql)


class PgIndex(_Db):
    def __init__(self, engine, embedder: Embedder):
        super().__init__(engine)
        self.embedder = embedder

    def upsert(self, chunks):
        if not chunks:
            return 0
        vecs = self.embedder.embed([c.content for c in chunks], "document")
        with self.tx() as c:
            for ch, v in zip(chunks, vecs):
                c.execute(text("""
                    INSERT INTO chunks (id, org_id, project_id, source_type, symbol, ref, content, search_text, embedding)
                    VALUES (:id, :org, :pid, :st, :sym, CAST(:ref AS jsonb), :content, :search, CAST(:emb AS vector))
                    ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, ref = EXCLUDED.ref,
                        search_text = EXCLUDED.search_text, embedding = EXCLUDED.embedding, stale = false"""),
                    {"id": ch.id, "org": current_org.get(), "pid": ch.project_id, "st": ch.source_type, "sym": ch.symbol,
                     "ref": json.dumps(ch.ref), "content": ch.content,
                     "search": split_identifiers(f"{ch.symbol or ''} {ch.ref.get('path', '')} {ch.content}") + " " + ch.content,
                     "emb": _vec(v)})
        return len(chunks)

    def delete_paths(self, project_id, paths):
        if not paths:
            return []
        rows = self.rows("DELETE FROM chunks WHERE project_id = :p AND source_type = 'code' AND path = ANY(:paths) RETURNING id",
                         p=project_id, paths=list(paths))
        return [r["id"] for r in rows]

    def delete_document(self, project_id, document_id):
        rows = self.rows("""DELETE FROM chunks WHERE project_id = :p AND source_type = 'doc' AND ref->>'document_id' = :d RETURNING id""",
                         p=project_id, d=str(document_id))
        return [r["id"] for r in rows]

    def delete_meeting(self, project_id, meeting_id):
        rows = self.rows("""DELETE FROM chunks WHERE project_id = :p AND source_type = 'meeting' AND ref->>'meeting_id' = :m RETURNING id""",
                         p=project_id, m=str(meeting_id))
        return [r["id"] for r in rows]

    def search(self, project_id, query, k=8, source_types=None):
        qv = self.embedder.embed([query], "query")[0]
        with self.tx() as c:
            return self._hybrid(c, project_id, query, qv, k, source_types)

    def search_many(self, project_id, queries, source_types=None):
        """One embedding call for every query (Vertex batches them), then each hybrid query on one connection."""
        if not queries:
            return []
        vecs = self.embedder.embed([q for q, _ in queries], "query")
        with self.tx() as c:
            return [self._hybrid(c, project_id, q, qv, k, source_types) for (q, k), qv in zip(queries, vecs)]

    def _hybrid(self, c, project_id, query, qv, k, source_types):
        types = source_types or ["code", "doc", "meeting", "verified_answer"]
        rows = [dict(r._mapping) for r in c.execute(text("""
            WITH v AS (
                SELECT id, 1 - (embedding <=> CAST(:qv AS vector)) AS sim,
                       row_number() OVER (ORDER BY embedding <=> CAST(:qv AS vector)) AS r
                FROM chunks WHERE project_id = :p AND NOT stale AND source_type::text = ANY(:types)
                ORDER BY embedding <=> CAST(:qv AS vector) LIMIT 50),
            kw AS (
                SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC) AS r
                FROM chunks c, to_tsquery('simple', coalesce(CAST(:q AS text), '')) q
                WHERE CAST(:q AS text) IS NOT NULL AND c.project_id = :p AND NOT c.stale AND c.source_type::text = ANY(:types) AND c.tsv @@ q
                ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT 50)
            SELECT c.id, c.project_id, c.source_type::text AS source_type, c.symbol, c.ref, c.content, c.stale,
                   COALESCE(1.0 / (60 + v.r), 0) + COALESCE(1.0 / (60 + kw.r), 0) AS score, COALESCE(v.sim, 0) AS sim
            FROM chunks c LEFT JOIN v ON v.id = c.id LEFT JOIN kw ON kw.id = c.id
            WHERE v.id IS NOT NULL OR kw.id IS NOT NULL
            ORDER BY score DESC LIMIT :k"""),
            {"qv": _vec(qv), "q": keyword_query(query), "p": project_id, "types": types, "k": k})]
        return [Hit(chunk=Chunk(**_s({k2: r[k2] for k2 in ("id", "project_id", "source_type", "symbol", "ref", "content", "stale")})),
                    score=float(r["score"]), similarity=max(0.0, float(r["sim"]))) for r in rows]

    # --- search_grid: the research crew's batched retrieval (app/rag, docs/specs/rag-crew.md) ---
    # search()'s hybrid query for one kind, reading only the candidates' rows (a join from the candidate ids, where search()
    # joins from the whole table) and comparing source_type as the enum, so the (project_id, source_type) index serves it
    _GRID = text("""
        WITH v AS (
            SELECT id, row_number() OVER (ORDER BY embedding <=> CAST(:qv AS vector)) AS r
            FROM chunks WHERE project_id = :p AND NOT stale AND source_type = CAST(:kind AS source_type)
            ORDER BY embedding <=> CAST(:qv AS vector) LIMIT 50),
        kw AS (
            SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id) AS r
            FROM chunks c, to_tsquery('simple', coalesce(CAST(:q AS text), '')) q
            WHERE CAST(:q AS text) IS NOT NULL AND c.project_id = :p AND NOT c.stale AND c.source_type = CAST(:kind AS source_type)
              AND c.tsv @@ q
            ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id LIMIT 50),
        ids AS (SELECT id FROM v UNION SELECT id FROM kw)
        SELECT c.id, c.project_id, c.source_type::text AS source_type, c.symbol, c.ref, c.content, c.stale,
               COALESCE(1.0 / (60 + v.r), 0) + COALESCE(1.0 / (60 + kw.r), 0) AS score,
               1 - (c.embedding <=> CAST(:anchor AS vector)) AS sim
        FROM ids JOIN chunks c ON c.id = ids.id LEFT JOIN v ON v.id = c.id LEFT JOIN kw ON kw.id = c.id
        WHERE NOT c.stale
        ORDER BY score DESC, c.id LIMIT :k""")

    # the keyword half alone, for search_grid(semantic=False): no vector, every similarity 0.0
    _GRID_KW = text("""
        SELECT c.id, c.project_id, c.source_type::text AS source_type, c.symbol, c.ref, c.content, c.stale,
               1.0 / (60 + row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id)) AS score, 0.0 AS sim
        FROM chunks c, to_tsquery('simple', coalesce(CAST(:q AS text), '')) q
        WHERE CAST(:q AS text) IS NOT NULL AND c.project_id = :p AND NOT c.stale AND c.source_type = CAST(:kind AS source_type)
          AND c.tsv @@ q
        ORDER BY ts_rank_cd(c.tsv, q) DESC, c.id LIMIT :k""")

    def search_grid(self, project_id, queries, kinds, k, semantic=True):
        """For each query, for each kind, the top k[kind] hits of the hybrid query search() runs (pgvector and the keyword
        OR-query, fused by RRF), each with `1 - (embedding <=> anchor)` as its similarity, anchor being queries[0]'s
        vector: the question as asked. One embedding call for every query, then every (query, kind) on one connection.
        semantic=False: the keyword query alone, no embedding call, every similarity 0.0 (the crew's fallback when the
        query embedding fails). A kind that is not a source type has no hits (as in MemoryIndex), never a DataError."""
        if not queries:
            return []
        vecs = self.embedder.embed(list(queries), "query") if semantic else [None] * len(queries)
        anchor = _vec(vecs[0]) if semantic else None
        want = list(dict.fromkeys(kinds))
        out = []
        with self.tx() as c:
            for query, qv in zip(queries, vecs):
                q = keyword_query(query)
                row = {}
                for kind in want:
                    n = max(0, int(k.get(kind, 8)))
                    if n == 0 or kind not in SOURCE_TYPES:
                        rows = []
                    elif semantic:
                        rows = c.execute(self._GRID, {"qv": _vec(qv), "q": q, "p": project_id, "kind": kind, "k": n,
                                                      "anchor": anchor}).mappings().all()
                    else:
                        rows = [] if q is None else c.execute(self._GRID_KW, {"q": q, "p": project_id, "kind": kind, "k": n}).mappings().all()
                    row[kind] = [Hit(chunk=Chunk(**_s({k2: r[k2] for k2 in ("id", "project_id", "source_type", "symbol", "ref", "content",
                                                                              "stale")})),
                                     score=float(r["score"]), similarity=max(0.0, float(r["sim"]))) for r in rows]
                out.append(row)
        return out

    def all_paths(self, project_id):
        """Sorted by code point like MemoryIndex (COLLATE "C"): coverage_map lists tied modules in this order."""
        return [r["path"] for r in self.rows("""SELECT DISTINCT path COLLATE "C" AS path FROM chunks
                                                WHERE project_id = :p AND source_type = 'code' ORDER BY 1""", p=project_id)]

    def _refs(self, rows):
        return [CodeRef(path=r["path"], symbol=r["symbol"], start_line=(r["ref"] or {}).get("start_line"),
                        end_line=(r["ref"] or {}).get("end_line"), chunk_id=r["id"]) for r in rows]

    def symbols_named(self, project_id, name):
        """By path (code point), then start line: the same definitions in the same order as MemoryIndex, whatever order a
        re-index left the rows in (the crew reads the first few)."""
        return self._refs(self.rows("""SELECT id, path, symbol, ref FROM chunks WHERE project_id = :p AND symbol = :s
                                       ORDER BY path COLLATE "C", (ref->>'start_line')::int, id""", p=project_id, s=name))

    def symbols_in(self, project_id, path):
        return self._refs(self.rows("""SELECT id, path, symbol, ref FROM chunks WHERE project_id = :p AND path = :f AND symbol IS NOT NULL
                                       ORDER BY (ref->>'start_line')::int, id""", p=project_id, f=path))

    def semantic_code(self, project_id, text_, k=3):
        return [(CodeRef(path=h.chunk.ref["path"], symbol=h.chunk.symbol, start_line=h.chunk.ref.get("start_line"),
                         end_line=h.chunk.ref.get("end_line"), chunk_id=h.chunk.id), h.similarity)
                for h in self.search(project_id, text_, k, ["code"])]

    _CODE = "project_id = :p AND source_type = 'code' AND NOT stale"

    def file_chunks(self, project_id, path):
        rows = self.rows(f"""SELECT id, project_id, source_type::text AS source_type, symbol, ref, content, stale FROM chunks
                             WHERE {self._CODE} AND path = :f ORDER BY (ref->>'start_line')::int""", p=project_id, f=path)
        return [Chunk(**_s(dict(r))) for r in rows]

    def code_chunks(self, project_id, content=True):
        """Every symbol span in one query (symbols_in is one query per file). COLLATE "C" orders paths by code point, the
        way MemoryIndex's Python sort does, so both adapters return the same order whatever the database's locale; the
        chunks_code_order index serves that order. Past ports.CODE_CHUNK_CAP chunks, whole files are kept in path order
        until the next file would not fit: never half a file. The cut is worked out from per-file counts first and the
        rows fetched with `path = ANY(...)`: a join against the counts can be planned as a nested loop over every chunk
        when the table has no statistics yet (a big repository just indexed), which ran for minutes."""
        cap = ports.CODE_CHUNK_CAP
        cols = """SELECT id, project_id, source_type::text AS source_type, symbol, ref,
                         CASE WHEN :c THEN content ELSE '' END AS content, stale FROM chunks"""
        order = """ORDER BY path COLLATE "C", (ref->>'start_line')::int"""
        p = {"p": project_id, "c": bool(content)}
        with self.tx() as c:
            counts = c.execute(text(f"""SELECT path COLLATE "C" AS path, count(*) AS n FROM chunks WHERE {self._CODE}
                                       GROUP BY 1 ORDER BY 1"""), p).all()
            total = sum(n for _, n in counts)
            if total <= cap:
                rows = c.execute(text(f"{cols} WHERE {self._CODE} {order}"), p).mappings().all()
            else:
                kept, upto = [], 0
                for path, n in counts:
                    if upto + n > cap:
                        break
                    kept.append(path)
                    upto += n
                rows = c.execute(text(f"{cols} WHERE {self._CODE} AND path = ANY(:paths) {order}"), {**p, "paths": kept}).mappings().all()
        return CodeChunks((Chunk(**_s(dict(r))) for r in rows), truncated=len(rows) < total)

    def code_version(self, project_id):
        """count and an order-free sum of hashes over the non-stale code chunks: one scan, no rows sent back. A re-index
        stamps every chunk it writes with its commit, so any change to the code changes this."""
        r = self.one(f"""SELECT count(*) AS n, coalesce(sum(hashtextextended(id || '|' || coalesce(symbol, '') || '|' ||
                            coalesce(ref->>'commit', '') || '|' || coalesce(ref->>'body_end', ''), 0)), 0) AS h
                         FROM chunks WHERE {self._CODE}""", p=project_id)
        return f"{r['n']}:{r['h']}"


class PgStore(_Db):
    # ---- people and projects ----
    def get_project(self, project_id):
        """None for an id no project has (or one row-level security hides); a malformed id is a LookupError. A database error
        is raised as it is, so callers report an outage, not a missing project."""
        if not _is_uuid(project_id):
            raise LookupError(project_id)
        r = self.one("SELECT id, org_id, name, description FROM projects WHERE id = :p", p=project_id)
        return Project(**_s(r)) if r else None

    @_ids
    def get_user(self, user_id):
        r = self.one(f"SELECT {USER_COLS} FROM users WHERE id = :u", u=user_id)
        return User(**_s(r)) if r else None

    def user_by_firebase(self, uid):
        r = self.one(f"SELECT {USER_COLS} FROM auth_lookup(:u)", u=uid)
        return User(**_s(r)) if r else None

    def users_by_github(self, org_id, logins):
        rows = self.rows(f"SELECT {USER_COLS} FROM users WHERE lower(github_login) = ANY(:l)",
                         l=[x.lower() for x in logins])
        return [User(**_s(r)) for r in rows]

    def managers(self, project_id):
        rows = self.rows(f"""SELECT {USER_COLS_U} FROM users u
                            JOIN project_members m ON m.user_id = u.id WHERE m.project_id = :p AND u.role IN ('manager', 'admin')
                            ORDER BY u.role <> 'manager', u.name""", p=project_id)
        return [User(**_s(r)) for r in rows]

    def onboardings(self, project_id, active_only=True):
        rows = self.rows("SELECT * FROM onboardings WHERE project_id = :p AND (:all OR status = 'active')", p=project_id, all=not active_only)
        return [Onboarding(**_s(r)) for r in rows]

    @_ids
    def get_onboarding(self, onboarding_id):
        r = self.one("SELECT * FROM onboardings WHERE id = :i", i=onboarding_id)
        return Onboarding(**_s(r)) if r else None

    # ---- repositories ----
    def _repo(self, r):
        return Repository(**_s({k: r[k] for k in ("id", "project_id", "full_name", "installation_id", "default_branch", "last_indexed_sha")}),
                          status=str(r["status"]))

    @_ids
    def get_repo(self, repo_id):
        r = self.one("SELECT * FROM repositories WHERE id = :i", i=repo_id)
        return self._repo(r) if r else None

    def repo_by_name(self, full_name):
        r = self.one("SELECT * FROM repositories WHERE full_name = :n", n=full_name)
        return self._repo(r) if r else None

    def repos_for(self, project_id):
        return [self._repo(r) for r in self.rows("SELECT * FROM repositories WHERE project_id = :p", p=project_id)]

    def update_repo(self, repo_id, **fields):
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        self.run(f"UPDATE repositories SET {sets} WHERE id = :id", id=repo_id, **fields)

    @contextmanager
    def ingest_lock(self, repo_id):
        """A session-level advisory lock on a connection of its own, held (outside any transaction) for the whole ingestion.
        Not waited for: a worker waiting on it would hold one of its few message slots (and a connection) for as long as
        the other ingestion runs, a full index of a large repository included."""
        key = {"k": f"ingest:{repo_id}"}
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            if not c.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), key).scalar():
                raise RepoBusy(repo_id)
            try:
                yield
            finally:
                c.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), key)

    def set_codeowners(self, project_id, text_):
        self.run("UPDATE repositories SET codeowners = :t WHERE project_id = :p", t=text_, p=project_id)

    def codeowners(self, project_id):
        return "\n".join(r["codeowners"] for r in self.rows("SELECT codeowners FROM repositories WHERE project_id = :p", p=project_id))

    # ---- meetings and KT links ----
    _MEETING_COLS = """id, project_id, title, room, status::text AS status, recording_uri, duration_s, started_at, summary,
                       kt_session_key AS kt_key, created_by, provider, join_url, calendar_event_id, starts_at, ends_at, attendees,
                       CASE WHEN recording_consent_at IS NOT NULL THEN jsonb_build_object(
                           'by', recording_consent_by, 'at', recording_consent_at, 'notice_version', recording_notice_version,
                           'by_name', (SELECT u.name FROM users u WHERE u.id = meetings.recording_consent_by),
                           'kind', coalesce(recording_consent_kind, 'confirmed'))
                       END AS recording_consent, transcript_source"""  # 020, 021; consent written by adapters/recording_store.py

    @_ids
    def get_meeting(self, meeting_id):
        r = self.one(f"SELECT {self._MEETING_COLS} FROM meetings WHERE id = :i", i=meeting_id)
        return Meeting(**_s(r)) if r else None

    def update_meeting(self, meeting_id, **fields):
        if "attendees" in fields:  # a jsonb list of Attendee
            fields["attendees"] = json.dumps([a.model_dump() if hasattr(a, "model_dump") else a for a in fields["attendees"] or []])
        sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "attendees" else f"{k} = :{k}" for k in fields)
        self.run(f"UPDATE meetings SET {sets} WHERE id = :id", id=meeting_id, **fields)

    def save_meeting_result(self, result: MeetingResult):
        org = current_org.get()
        with self.tx() as c:
            m = {"m": result.meeting_id, "o": org}
            for t in ("transcript_segments", "screen_moments", "action_items"):
                c.execute(text(f"DELETE FROM {t} WHERE meeting_id = :m"), m)
            for u in result.utterances:
                c.execute(text("INSERT INTO transcript_segments (org_id, meeting_id, start_s, end_s, speaker, text) VALUES (:o, :m, :a, :b, :s, :t)"),
                          {**m, "a": u.start_s, "b": u.end_s, "s": u.speaker, "t": u.text})
            for s in result.screen_moments:
                c.execute(text("INSERT INTO screen_moments (org_id, meeting_id, at_s, description, visible_paths, visible_symbols) VALUES (:o, :m, :a, :d, :vp, :vs)"),
                          {**m, "a": s.at_s, "d": s.description, "vp": s.visible_paths, "vs": s.visible_symbols})
            for a in result.action_items:
                c.execute(text("INSERT INTO action_items (org_id, meeting_id, text, owner_hint, at_s) VALUES (:o, :m, :t, :h, :a)"),
                          {**m, "t": a.text, "h": a.owner_hint, "a": a.at_s})
            c.execute(text("""INSERT INTO meeting_notes (meeting_id, org_id, decisions, terms, result)
                              VALUES (:m, :o, CAST(:d AS jsonb), CAST(:t AS jsonb), CAST(:r AS jsonb))
                              ON CONFLICT (meeting_id) DO UPDATE SET decisions = EXCLUDED.decisions, terms = EXCLUDED.terms, result = EXCLUDED.result"""),
                      {**m, "d": json.dumps(result.decisions), "t": json.dumps([t.model_dump() for t in result.terms]), "r": result.model_dump_json()})
            c.execute(text("UPDATE meetings SET status = 'ready', summary = :s WHERE id = :m"), {**m, "s": result.summary})

    def erase_meeting_content(self, meeting_id):
        if not _is_uuid(meeting_id):
            return
        with self.tx() as c:
            m = {"m": meeting_id}
            for t in ("transcript_segments", "screen_moments", "action_items", "meeting_notes", "kt_links"):
                c.execute(text(f"DELETE FROM {t} WHERE meeting_id = CAST(:m AS uuid)"), m)
            c.execute(text("""UPDATE meetings SET recording_uri = NULL, summary = NULL, status = 'ended', transcript_source = NULL,
                              ended_at = coalesce(ended_at, now()) WHERE id = CAST(:m AS uuid)"""), m)

    def meeting_notes(self, meeting_id):
        r = self.one("SELECT result FROM meeting_notes WHERE meeting_id = :m", m=meeting_id)
        return MeetingResult.model_validate(r["result"]) if r else None

    def meeting_notes_many(self, meeting_ids):
        ids = [m for m in dict.fromkeys(meeting_ids) if _is_uuid(m)]
        if not ids:
            return {}
        rows = self.rows("SELECT meeting_id, result FROM meeting_notes WHERE meeting_id = ANY(CAST(:m AS uuid[]))", m=ids)
        return {str(r["meeting_id"]): MeetingResult.model_validate(r["result"]) for r in rows}

    def add_kt_links(self, project_id, links):
        """A link whose chunk was re-indexed away keeps its file and symbol. FOR KEY SHARE makes the lookup wait for a delete
        that is still running (and then see NULL) instead of passing it a chunk id the foreign key check would reject."""
        org = current_org.get()
        with self.tx() as c:
            for mid in {l.meeting_id for l in links}:
                c.execute(text("DELETE FROM kt_links WHERE meeting_id = :m"), {"m": mid})
            for l in links:
                c.execute(text("""INSERT INTO kt_links (org_id, project_id, meeting_id, file_path, symbol, chunk_id, start_line, end_line,
                                  start_s, end_s, confidence, method)
                                  VALUES (:o, :p, :m, :f, :s, (SELECT id FROM chunks WHERE id = :c FOR KEY SHARE), :sl, :el, :a, :b, :conf, :meth)"""),
                          {"o": org, "p": project_id, "m": l.meeting_id, "f": l.file_path, "s": l.symbol, "c": l.chunk_id, "sl": l.start_line,
                           "el": l.end_line, "a": l.start_s, "b": l.end_s, "conf": l.confidence, "meth": l.method})

    def kt_links(self, project_id, path=None, chunk_ids=None):
        rows = self.rows("""SELECT k.meeting_id, m.title AS meeting_title, k.file_path, k.symbol, k.chunk_id, k.start_line, k.end_line,
                                   k.start_s, k.end_s, k.confidence, k.method::text AS method, k.code_changed_since
                            FROM kt_links k JOIN meetings m ON m.id = k.meeting_id
                            WHERE k.project_id = :p AND (CAST(:f AS text) IS NULL OR k.file_path = :f)
                              AND (CAST(:c AS text[]) IS NULL OR k.chunk_id = ANY(:c))
                            ORDER BY k.confidence DESC, k.start_s""", p=project_id, f=path, c=chunk_ids)
        return [KTLink(**_s(r)) for r in rows]

    def mark_kt_code_changed(self, project_id, paths):
        self.run("UPDATE kt_links SET code_changed_since = true WHERE project_id = :p AND file_path = ANY(:f)", p=project_id, f=list(paths))

    # ---- tasks, approvals, reviews ----
    _TASK_COLS = """id, onboarding_id, title, description, kind::text AS kind, status::text AS status, origin, due_date,
                    acceptance_criteria, related_paths, github_issue_number, pr_number, pr_url"""

    def create_tasks(self, onboarding_id, tasks, start: date):
        out = []
        for t in tasks:
            r = self.one(f"""INSERT INTO tasks (org_id, onboarding_id, title, description, kind, origin, due_date, acceptance_criteria, related_paths,
                                                github_issue_number)
                             VALUES (:o, :ob, :t, :d, CAST(:k AS task_kind), :src, :due, CAST(:ac AS jsonb), :rp, :issue) RETURNING {self._TASK_COLS}""",
                         o=current_org.get(), ob=onboarding_id, t=t.title, d=t.description, k=t.kind, src=t.source, issue=t.issue_number,
                         due=start + timedelta(days=t.due_in_days), ac=json.dumps(t.acceptance_criteria), rp=t.related_paths)
            out.append(TaskRow(**_s(r)))
        return out

    @_ids
    def get_task(self, task_id):
        r = self.one(f"SELECT {self._TASK_COLS} FROM tasks WHERE id = :i", i=task_id)
        return TaskRow(**_s(r)) if r else None

    def tasks(self, onboarding_id=None, project_id=None):
        """MemoryStore's order: due date, title by code point (COLLATE "C"), then creation order. A cohort's plans share
        titles and due dates, and Postgres sorts ties in no fixed order; knowledge.demand lists their reasons in this order."""
        rows = self.rows(f"""SELECT {self._TASK_COLS} FROM tasks t WHERE (CAST(:ob AS uuid) IS NULL OR t.onboarding_id = :ob)
                             AND (CAST(:p AS uuid) IS NULL OR t.onboarding_id IN (SELECT id FROM onboardings WHERE project_id = :p))
                             ORDER BY due_date NULLS LAST, title COLLATE "C", created_at, id""", ob=onboarding_id, p=project_id)
        return [TaskRow(**_s(r)) for r in rows]

    def update_task(self, task_id, **fields):
        casts = {"status": "CAST(:status AS task_status)"}
        sets = ", ".join(f"{k} = {casts.get(k, ':' + k)}" for k in fields)
        self.run(f"UPDATE tasks SET {sets}, updated_at = now() WHERE id = :id", id=task_id, **fields)

    _APPROVAL_COLS = "id, project_id, kind::text AS kind, thread_id, payload, status::text AS status, decided_by, decided_at, created_at, decision"

    def create_approval(self, project_id, kind, thread_id, payload):
        r = self.one(f"""INSERT INTO approvals (org_id, project_id, kind, thread_id, payload)
                         VALUES (:o, :p, CAST(:k AS approval_kind), :t, CAST(:pl AS jsonb)) RETURNING {self._APPROVAL_COLS}""",
                     o=current_org.get(), p=project_id, k=kind, t=thread_id, pl=json.dumps(payload, default=str))
        return Approval(**_s(r))

    @_ids
    def get_approval(self, approval_id):
        r = self.one(f"SELECT {self._APPROVAL_COLS} FROM approvals WHERE id = :i", i=approval_id)
        return Approval(**_s(r)) if r else None

    def approvals(self, project_id, status="pending"):
        rows = self.rows(f"""SELECT {self._APPROVAL_COLS} FROM approvals
                             WHERE project_id = :p AND (CAST(:s AS text) IS NULL OR status::text = :s) ORDER BY created_at DESC""",
                         p=project_id, s=status)
        return [Approval(**_s(r)) for r in rows]

    def decide_approval(self, approval_id, status, user_id):
        self.run("UPDATE approvals SET status = CAST(:s AS approval_status), decided_by = :u, decided_at = now() WHERE id = :i",
                 s=status, u=user_id or None, i=approval_id)

    def close_review_approvals(self, task_id):
        rows = self.rows(f"""UPDATE approvals SET status = 'rejected', decided_by = NULL, decided_at = now()
                             WHERE kind = 'review' AND status = 'pending' AND payload->>'task_id' = :t RETURNING {self._APPROVAL_COLS}""",
                         t=str(task_id))
        return [Approval(**_s(r)) for r in rows]

    def review_approvals(self, task_id):
        rows = self.rows(f"""SELECT {self._APPROVAL_COLS} FROM approvals WHERE kind = 'review' AND payload->>'task_id' = :t
                             ORDER BY created_at, id""", t=str(task_id))
        return [Approval(**_s(r)) for r in rows]

    def latest_reviews(self, task_ids):
        ids = [t for t in dict.fromkeys(task_ids) if _is_uuid(t)]
        if not ids:
            return {}
        rows = self.rows("""SELECT DISTINCT ON (task_id) task_id, id FROM reviews WHERE task_id = ANY(CAST(:t AS uuid[]))
                            ORDER BY task_id, created_at DESC, id DESC""", t=ids)
        return {str(r["task_id"]): str(r["id"]) for r in rows}

    def save_review(self, task_id, pr_number, pr_url, head_sha, result: ReviewResult):
        r = self.one("""INSERT INTO reviews (org_id, task_id, pr_number, pr_url, head_sha, recommendation, summary, findings, questions, dropped_findings)
                        VALUES (:o, :t, :n, :u, :sha, CAST(:rec AS recommendation), :sum, CAST(:f AS jsonb), CAST(:q AS jsonb), :d)
                        ON CONFLICT (task_id, head_sha) DO UPDATE SET pr_number = EXCLUDED.pr_number, pr_url = EXCLUDED.pr_url,
                            recommendation = EXCLUDED.recommendation, summary = EXCLUDED.summary, findings = EXCLUDED.findings,
                            questions = EXCLUDED.questions, answers = NULL, dropped_findings = EXCLUDED.dropped_findings,
                            manager_decision = NULL, decided_by = NULL, decided_at = NULL
                        RETURNING id""",
                     o=current_org.get(), t=task_id, n=pr_number, u=pr_url, sha=head_sha, rec=result.recommendation, sum=result.summary,
                     f=json.dumps([f.model_dump() for f in result.findings]), q=json.dumps(result.questions), d=result.dropped_findings)
        return ReviewRow(id=str(r["id"]), task_id=task_id, pr_number=pr_number, pr_url=pr_url, head_sha=head_sha, result=result)

    @_ids
    def get_review(self, review_id):
        r = self.one("SELECT * FROM reviews WHERE id = :i", i=review_id)
        if not r:
            return None
        res = ReviewResult(recommendation=str(r["recommendation"]), summary=r["summary"], findings=r["findings"],
                           questions=r["questions"], dropped_findings=r["dropped_findings"])
        return ReviewRow(id=str(r["id"]), task_id=str(r["task_id"]), pr_number=r["pr_number"], pr_url=r["pr_url"], head_sha=r["head_sha"],
                         result=res, manager_decision=str(r["manager_decision"]) if r["manager_decision"] else None)

    def decide_review(self, review_id, decision, user_id):
        self.run("UPDATE reviews SET manager_decision = CAST(:d AS review_decision), decided_by = :u, decided_at = now() WHERE id = :i",
                 d=decision, u=user_id or None, i=review_id)

    # ---- chat and experts ----
    def save_chat(self, project_id, user_id, question, result: QAResult):
        r = self.one("""INSERT INTO chat_messages (org_id, project_id, user_id, question, status, answer, citations, kt_clips, cited_chunk_ids, confidence,
                                                   writer, crew)
                        VALUES (:o, :p, :u, :q, :s, :a, CAST(:c AS jsonb), CAST(:k AS jsonb), :ids, :conf, :w, CAST(:cr AS jsonb)) RETURNING id""",
                     o=current_org.get(), p=project_id, u=user_id, q=question, s=result.status, a=result.answer,
                     c=json.dumps([c.model_dump() for c in result.citations]), k=json.dumps([k.model_dump() for k in result.kt_clips]),
                     ids=[c.chunk_id for c in result.citations if c.chunk_id], conf=result.confidence, w=result.writer,
                     cr=json.dumps(result.crew) if result.crew is not None else None)
        return str(r["id"])

    def mark_answers_stale(self, chunk_ids):
        return self.run("UPDATE chat_messages SET stale = true WHERE cited_chunk_ids && :ids", ids=list(chunk_ids)) if chunk_ids else 0

    def create_expert_request(self, project_id, question, expert_id, asked_by, message_id, draft=None):
        r = self.one("""INSERT INTO expert_requests (org_id, project_id, message_id, question, asked_by, expert_id, draft)
                        VALUES (:o, :p, :m, :q, :a, :e, :d) RETURNING id""",
                     o=current_org.get(), p=project_id, m=message_id, q=question, a=asked_by, e=expert_id, d=draft)
        return str(r["id"])

    # ---- cross-cutting ----
    def notify(self, user_id, kind, text_, link=""):
        self.run("INSERT INTO notifications (org_id, user_id, kind, text, link) VALUES (:o, :u, :k, :t, :l)",
                 o=current_org.get(), u=user_id, k=kind, t=text_, l=link)

    # ports.link_matches in SQL: the link is the target, or the target followed by its query string
    _LINK = "(link = :l OR left(link, length(:l) + 1) IN (:l || '?', :l || '&'))"

    def close_notifications(self, link_prefix):
        return self.run(f"UPDATE notifications SET read_at = now() WHERE read_at IS NULL AND {self._LINK}", l=link_prefix)

    def notification_recipients(self, link_prefix, kind):
        rows = self.rows(f"SELECT user_id FROM notifications WHERE kind = :k AND {self._LINK} ORDER BY id", k=kind, l=link_prefix)
        return [str(u) for u in dict.fromkeys(r["user_id"] for r in rows)]

    def record_run(self, project_id, agent, trigger, status, detail, run_id=None):
        values = dict(o=current_org.get(), p=project_id or None, a=agent, t=trigger, s=status, d=json.dumps(detail, default=str),
                      tr=detail.get("trace_id"))
        if run_id and _is_uuid(run_id):  # the client's id; taken already (by anyone): a new one instead, never an overwrite
            r = self.one("""INSERT INTO agent_runs (id, org_id, project_id, agent, trigger, status, detail, langsmith_run_id)
                            VALUES (CAST(:i AS uuid), :o, :p, :a, :t, CAST(:s AS run_status), CAST(:d AS jsonb), :tr)
                            ON CONFLICT (id) DO NOTHING RETURNING id""", i=run_id, **values)
            if r:
                return str(r["id"])
        r = self.one("""INSERT INTO agent_runs (org_id, project_id, agent, trigger, status, detail, langsmith_run_id)
                        VALUES (:o, :p, :a, :t, CAST(:s AS run_status), CAST(:d AS jsonb), :tr) RETURNING id""", **values)
        return str(r["id"])

    def get_run(self, run_id):
        if not _is_uuid(run_id):
            return None
        r = self.one("""SELECT id, project_id, agent, trigger, status::text AS status, detail, created_at AS at,
                               langsmith_run_id AS trace_id FROM agent_runs WHERE id = CAST(:i AS uuid)""", i=run_id)
        return _s(r) if r else None

    def claim_event(self, dedupe_key, lease_s):
        """Atomically take the event unless it is done or another handler holds a live lease."""
        claimed = self.one("""
            INSERT INTO processed_events (dedupe_key, status, claimed_at, lease_s) VALUES (:k, 'processing', now(), :lease)
            ON CONFLICT (dedupe_key) DO UPDATE SET claimed_at = now(), lease_s = EXCLUDED.lease_s
                WHERE processed_events.status = 'processing' AND processed_events.claimed_at < now() - make_interval(secs => :lease)
            RETURNING dedupe_key""", k=dedupe_key, lease=lease_s)
        if claimed:
            return "new"
        row = self.one("SELECT status FROM processed_events WHERE dedupe_key = :k", k=dedupe_key)
        return "done" if row and row["status"] == "done" else "busy"

    def touch_event(self, dedupe_key):
        self.run("UPDATE processed_events SET claimed_at = now() WHERE dedupe_key = :k AND status = 'processing'", k=dedupe_key)

    def finish_event(self, dedupe_key):
        self.run("UPDATE processed_events SET status = 'done', claimed_at = now() WHERE dedupe_key = :k", k=dedupe_key)

    def release_event(self, dedupe_key):
        self.run("DELETE FROM processed_events WHERE dedupe_key = :k AND status = 'processing'", k=dedupe_key)

    _LEASE_LIVE = "status = 'processing' AND claimed_at >= now() - make_interval(secs => lease_s)"

    def event_status(self, dedupe_key):
        r = self.one(f"SELECT status = 'done' AS done, {self._LEASE_LIVE} AS live FROM processed_events WHERE dedupe_key = :k", k=dedupe_key)
        return "none" if not r else "done" if r["done"] else "busy" if r["live"] else "none"

    def reset_event(self, dedupe_key):
        self.run(f"DELETE FROM processed_events WHERE dedupe_key = :k AND NOT ({self._LEASE_LIVE})", k=dedupe_key)
        return self.event_status(dedupe_key) != "busy"

    # ---- AppStore (API-only) ----
    def members(self, project_id):
        rows = self.rows(f"""SELECT {USER_COLS_U} FROM users u
                            JOIN project_members m ON m.user_id = u.id WHERE m.project_id = :p ORDER BY u.name""", p=project_id)
        return [User(**_s(r)) for r in rows]

    def create_repo(self, project_id, full_name, installation_id, default_branch="main"):
        """Connect (or reconnect) the project's repository. None if the project already has a different one: the
        unique index on project_id makes this atomic, so concurrent connects cannot both win."""
        r = self.one("""INSERT INTO repositories (org_id, project_id, full_name, installation_id, default_branch)
                        VALUES (:o, :p, :n, :i, :b)
                        ON CONFLICT (project_id) DO UPDATE SET installation_id = EXCLUDED.installation_id,
                            default_branch = EXCLUDED.default_branch, status = 'pending'
                        WHERE lower(repositories.full_name) = lower(EXCLUDED.full_name)
                        RETURNING *""", o=current_org.get(), p=project_id, n=full_name, i=installation_id, b=default_branch)
        return self._repo(r) if r else None

    def create_document(self, project_id, title, uri, mime, uploaded_by):
        r = self.one("INSERT INTO documents (org_id, project_id, title, gcs_uri, mime_type, uploaded_by) VALUES (:o, :p, :t, :u, :m, :b) RETURNING id",
                     o=current_org.get(), p=project_id, t=title, u=uri, m=mime, b=uploaded_by)
        return str(r["id"])

    def create_onboarding(self, project_id, new_hire_id, manager_id, start, role_title="Software Engineer"):
        org = current_org.get()
        with self.tx() as c:
            for uid in (new_hire_id, manager_id):
                c.execute(text("INSERT INTO project_members (project_id, user_id, org_id) VALUES (:p, :u, :o) ON CONFLICT DO NOTHING"),
                          {"p": project_id, "u": uid, "o": org})
            # one active onboarding per person and project (a partial unique index): a second start returns the first
            p = {"o": org, "p": project_id, "n": new_hire_id, "m": manager_id, "s": start, "r": role_title}
            r = c.execute(text("""INSERT INTO onboardings (org_id, project_id, new_hire_id, manager_id, start_date, role_title)
                                  VALUES (:o, :p, :n, :m, :s, :r)
                                  ON CONFLICT (project_id, new_hire_id) WHERE status = 'active' DO NOTHING RETURNING *"""), p).mappings().first()
            if r is None:
                r = c.execute(text("SELECT * FROM onboardings WHERE project_id = :p AND new_hire_id = :n AND status = 'active'"), p).mappings().one()
        return Onboarding(**_s(dict(r)))

    def create_meeting(self, project_id, title, created_by, kt_key=None, *, provider="manual", join_url=None, calendar_event_id=None,
                       starts_at=None, ends_at=None, attendees=None):
        """A KT planner booking (kt_key) that meets a scheduled, live or processing call of the same session inserts nothing
        (meetings_one_live_kt_session) and returns None: two simultaneous bookings make one call, with no lock. So does a
        calendar event already recorded (meetings_calendar_event): a redelivered Calendly webhook makes one session."""
        people = json.dumps([a.model_dump() if hasattr(a, "model_dump") else a for a in attendees or []])
        r = self.one(f"""INSERT INTO meetings (org_id, project_id, title, room, created_by, started_at, kt_session_key, provider, join_url,
                                               calendar_event_id, starts_at, ends_at, attendees)
                         VALUES (:o, :p, :t, 'kt-' || gen_random_uuid(), :u, coalesce(CAST(:s AS timestamptz), now()), :k, :pv, :j,
                                 :e, CAST(:s AS timestamptz), CAST(:en AS timestamptz), CAST(:a AS jsonb))
                         ON CONFLICT DO NOTHING RETURNING {self._MEETING_COLS}""",
                     o=current_org.get(), p=project_id, t=title, u=created_by, k=kt_key, pv=provider, j=join_url, e=calendar_event_id,
                     s=starts_at, en=ends_at, a=people)
        return Meeting(**_s(r)) if r else None

    def cancel_meeting(self, meeting_id):
        if not _is_uuid(meeting_id):
            return False
        return bool(self.one("UPDATE meetings SET status = 'cancelled' WHERE id = CAST(:i AS uuid) AND status = 'scheduled' RETURNING id",
                             i=meeting_id))

    def end_meeting(self, meeting_id):
        if not _is_uuid(meeting_id):
            return False
        return bool(self.one("UPDATE meetings SET status = 'ended', ended_at = now() WHERE id = CAST(:i AS uuid) AND status = 'live' RETURNING id",
                             i=meeting_id))

    def list_meetings(self, project_id):
        rows = self.rows(f"SELECT {self._MEETING_COLS} FROM meetings WHERE project_id = :p ORDER BY started_at DESC NULLS LAST", p=project_id)
        return [Meeting(**_s(r)) for r in rows]

    # ---- scheduling (docs/specs/scheduling.md) ----
    def meeting_by_event(self, provider, calendar_event_id):
        r = self.one(f"SELECT {self._MEETING_COLS} FROM meetings WHERE provider = :pv AND calendar_event_id = :e",
                     pv=provider, e=calendar_event_id)
        return Meeting(**_s(r)) if r else None

    def claim_meeting_import(self, meeting_id, recording_uri, duration_s):
        if not _is_uuid(meeting_id):
            return False
        return bool(self.one("""UPDATE meetings SET status = 'processing', recording_uri = :u, duration_s = :d
                                WHERE id = CAST(:i AS uuid) AND status IN ('scheduled', 'ended', 'failed') RETURNING id""",
                             i=meeting_id, u=recording_uri, d=duration_s))

    def expire_meeting(self, meeting_id):
        if not _is_uuid(meeting_id):
            return False
        return bool(self.one("UPDATE meetings SET status = 'ended' WHERE id = CAST(:i AS uuid) AND status = 'scheduled' RETURNING id",
                             i=meeting_id))

    def discard_meeting(self, meeting_id):
        if not _is_uuid(meeting_id):
            return False
        return bool(self.one("DELETE FROM meetings WHERE id = CAST(:i AS uuid) AND status = 'scheduled' AND recording_uri IS NULL RETURNING id",
                             i=meeting_id))

    def meetings_awaiting_recording(self, grace_s, wait_s, limit=20):
        return [_s(r) for r in self.rows("SELECT * FROM meetings_awaiting_recording(:g, :w, :n)", g=int(grace_s), w=int(wait_s), n=int(limit))]

    def calendar_connection(self, org_id, provider="google"):
        r = self.one("""SELECT org_id, provider, account_email, refresh_token_enc, scopes, connected_by, connected_at
                        FROM calendar_connections WHERE org_id = CAST(:o AS uuid) AND provider = :p""", o=org_id, p=provider)
        return _s(r) if r else None

    def save_calendar_connection(self, org_id, provider, account_email, refresh_token_enc, scopes, connected_by):
        self.run("""INSERT INTO calendar_connections (org_id, provider, account_email, refresh_token_enc, scopes, connected_by)
                    VALUES (CAST(:o AS uuid), :p, :e, :t, :s, CAST(:u AS uuid))
                    ON CONFLICT (org_id, provider) DO UPDATE SET account_email = EXCLUDED.account_email,
                        refresh_token_enc = EXCLUDED.refresh_token_enc, scopes = EXCLUDED.scopes,
                        connected_by = EXCLUDED.connected_by, connected_at = now()""",
                 o=org_id, p=provider, e=account_email, t=refresh_token_enc, s=scopes, u=connected_by)

    def delete_calendar_connection(self, org_id, provider="google"):
        return bool(self.run("DELETE FROM calendar_connections WHERE org_id = CAST(:o AS uuid) AND provider = :p", o=org_id, p=provider))

    def scheduling_link(self, user_id, provider="calendly"):
        if not _is_uuid(user_id):
            return None
        r = self.one("SELECT url FROM scheduling_links WHERE user_id = CAST(:u AS uuid) AND provider = :p", u=user_id, p=provider)
        return r["url"] if r else None

    def set_scheduling_link(self, org_id, user_id, provider, url):
        if not url:
            self.run("DELETE FROM scheduling_links WHERE user_id = CAST(:u AS uuid) AND provider = :p", u=user_id, p=provider)
            return
        self.run("""INSERT INTO scheduling_links (org_id, user_id, provider, url) VALUES (CAST(:o AS uuid), CAST(:u AS uuid), :p, :l)
                    ON CONFLICT (user_id, provider) DO UPDATE SET url = EXCLUDED.url, updated_at = now()""",
                 o=org_id, u=user_id, p=provider, l=url)

    def scheduling_links(self, project_id, provider="calendly"):
        rows = self.rows("""SELECT l.user_id, l.url FROM scheduling_links l JOIN project_members m ON m.user_id = l.user_id
                            WHERE m.project_id = :pr AND l.provider = :p""", pr=project_id, p=provider)
        return {str(r["user_id"]): r["url"] for r in rows}

    _CHAT_COLS = "id, id AS message_id, question, status, answer, citations, kt_clips, confidence, writer, crew, stale, feedback, created_at"

    # ports.norm_question in SQL: case, runs of whitespace and closing punctuation do not count
    _NORM_Q = "rtrim(lower(btrim(regexp_replace({0}, '\\s+', ' ', 'g'))), '?.! ')"

    def chat_history(self, project_id, user_id, limit=50):
        """MemoryStore.chat_history: `expert` is the request a not-enough-information turn was handed to (or, for the same
        question asked again, the earlier one), an answered one first."""
        norm_r, norm_c = self._NORM_Q.format("r.question"), self._NORM_Q.format("c.question")
        rows = self.rows(f"""SELECT c.id, c.id AS message_id, c.question, c.status, c.answer, c.citations, c.kt_clips, c.confidence, c.writer,
                                    c.crew, c.stale, c.feedback, c.created_at, e.expert FROM chat_messages c
                             LEFT JOIN LATERAL (
                               SELECT jsonb_build_object('status', r.status, 'name', u.name, 'answer', r.answer, 'answered_at', r.answered_at) AS expert
                               FROM expert_requests r LEFT JOIN users u ON u.id = r.expert_id
                               WHERE c.status = 'not_enough_information' AND r.project_id = c.project_id AND r.asked_by = c.user_id
                                 AND r.status IN ('open', 'answered') AND (r.message_id = c.id OR {norm_r} = {norm_c})
                               ORDER BY r.status = 'answered' DESC, r.message_id = c.id DESC, r.created_at DESC LIMIT 1) e ON true
                             WHERE c.project_id = :p AND c.user_id = :u
                             ORDER BY c.created_at DESC LIMIT :n""", p=project_id, u=user_id, n=limit)
        return [_s(r) for r in reversed(rows)]

    def chat_histories(self, project_id, user_ids, limit=50):
        """chat_history for many users in one query: each user's newest `limit` rows, oldest first."""
        ids = [u for u in dict.fromkeys(user_ids) if _is_uuid(u)]
        out: dict[str, list[dict]] = {u: [] for u in dict.fromkeys(user_ids)}
        if not ids:
            return out
        rows = self.rows(f"""SELECT * FROM (SELECT {self._CHAT_COLS}, user_id AS for_user,
                                    row_number() OVER (PARTITION BY user_id ORDER BY created_at DESC) AS nth
                               FROM chat_messages WHERE project_id = :p AND user_id = ANY(CAST(:u AS uuid[]))) recent
                             WHERE nth <= :n ORDER BY for_user, created_at""", p=project_id, u=ids, n=limit)
        for r in rows:
            uid = str(r.pop("for_user"))
            r.pop("nth")
            out.setdefault(uid, []).append(_s(r))
        return out

    def notifications_for(self, user_id):
        return [{**_s(r), "id": str(r["id"])} for r in self.rows(
            "SELECT id, kind, text, link, read_at, created_at AS at FROM notifications WHERE user_id = :u ORDER BY created_at DESC LIMIT 50", u=user_id)]

    def expert_requests_for(self, expert_id):
        return [_s(r) for r in self.rows("""SELECT r.*, u.name AS asked_by_name FROM expert_requests r LEFT JOIN users u ON u.id = r.asked_by
                                           WHERE r.expert_id = :e AND r.status = 'open' ORDER BY r.created_at""", e=expert_id)]

    def expert_requests_by(self, project_id, asked_by):
        if not (_is_uuid(project_id) and _is_uuid(asked_by)):
            return []
        return [_s(r) for r in self.rows("""SELECT r.*, e.name AS expert_name FROM expert_requests r LEFT JOIN users e ON e.id = r.expert_id
                                           WHERE r.project_id = CAST(:p AS uuid) AND r.asked_by = CAST(:a AS uuid)
                                           ORDER BY r.created_at, r.id""", p=project_id, a=asked_by)]

    def open_expert_requests(self, project_id):
        return [_s(r) for r in self.rows("""SELECT r.*, u.name AS asked_by_name FROM expert_requests r LEFT JOIN users u ON u.id = r.asked_by
                                           WHERE r.project_id = :p AND r.status = 'open' ORDER BY r.created_at""", p=project_id)]

    def answer_expert_request(self, request_id, answer):
        r = self.one("""UPDATE expert_requests SET status = 'answered', answer = :a, answered_at = now()
                          WHERE id = CAST(:i AS uuid) AND status = 'open' RETURNING *""", a=answer, i=request_id)
        return _s(r) if r else None

    def list_runs(self, project_id, limit=50):
        return [_s(r) for r in self.rows("""SELECT id, agent, trigger, status::text AS status, detail, created_at AS at,
                                                  langsmith_run_id AS trace_id FROM agent_runs
                                           WHERE project_id = :p ORDER BY created_at DESC LIMIT :n""", p=project_id, n=limit)]

    # ---- accounts (definer functions: they run before the tenant is known) ----
    _USER_COLS = USER_COLS

    def pending_invitations(self, email):
        return [_s(r) for r in self.rows("""SELECT org_id, org_name, role::text AS role, invited_by_name, invited_by_email,
                                                   to_char(org_created_at, 'YYYY-MM-DD') AS org_created_at, projects
                                            FROM pending_invitations(:e) ORDER BY org_name""", e=email)]

    def accept_invitations(self, uid, email, name, org_id):
        if not _is_uuid(org_id):
            return None
        r = self.one("SELECT accept_invitations(:u, :e, :n, CAST(:o AS uuid)) AS id", u=uid, e=email, n=name, o=org_id)
        return self.user_by_firebase(uid) if r and r["id"] else None

    def create_organization(self, uid, email, name, org_name, project_name):
        self.one("SELECT * FROM create_organization(:u, :e, :n, :o, :p)", u=uid, e=email, n=name, o=org_name, p=project_name)
        return self.user_by_firebase(uid)

    def organization_exists(self):
        """Any organisation at all, across tenants (organization_exists(), 015_signup_gate.sql): a self-hosted server lets
        sign-up create only the first one."""
        r = self.one("SELECT organization_exists() AS yes")
        return bool(r and r["yes"])

    def org_name(self, org_id):
        r = self.one("SELECT name FROM organizations WHERE id = :o", o=org_id)
        return r["name"] if r else ""

    def org_of_project(self, project_id):
        try:
            uuid.UUID(str(project_id))
        except ValueError:
            return None
        r = self.one("SELECT project_org(CAST(:p AS uuid)) AS org", p=project_id)
        return str(r["org"]) if r and r["org"] else None

    # ---- projects, membership, invitations ----
    def projects_for(self, user):
        if user.role == "admin":
            rows = self.rows("SELECT id, org_id, name, description FROM projects ORDER BY name")
        else:
            rows = self.rows("""SELECT p.id, p.org_id, p.name, p.description FROM projects p
                                JOIN project_members m ON m.project_id = p.id WHERE m.user_id = :u ORDER BY p.name""", u=user.id)
        return [Project(**_s(r)) for r in rows]

    def create_project(self, org_id, name, description, creator_id):
        with self.tx() as c:
            r = c.execute(text("INSERT INTO projects (org_id, name, description) VALUES (:o, :n, :d) RETURNING id, org_id, name, description"),
                          {"o": org_id, "n": name, "d": description}).mappings().one()
            c.execute(text("INSERT INTO project_members (project_id, user_id, org_id) VALUES (:p, :u, :o)"), {"p": r["id"], "u": creator_id, "o": org_id})
        return Project(**_s(dict(r)))

    def is_member(self, project_id, user_id):
        if not (_is_uuid(project_id) and _is_uuid(user_id)):
            return False
        return bool(self.one("SELECT 1 AS ok FROM project_members WHERE project_id = CAST(:p AS uuid) AND user_id = CAST(:u AS uuid)",
                             p=project_id, u=user_id))

    def add_member(self, project_id, user_id):
        self.run("INSERT INTO project_members (project_id, user_id, org_id) VALUES (:p, :u, :o) ON CONFLICT DO NOTHING",
                 p=project_id, u=user_id, o=current_org.get())

    def user_by_email(self, org_id, email):
        """A member with this email. A row with no login bound is someone who left: only an invitation brings them back."""
        r = self.one(f"""SELECT {self._USER_COLS} FROM users WHERE org_id = CAST(:o AS uuid) AND lower(email) = lower(:e)
                         AND firebase_uid IS NOT NULL""", o=org_id, e=email)
        return User(**_s(r)) if r else None

    def create_invitation(self, org_id, project_id, email, role, name, github_login, invited_by):
        """Re-inviting updates the open invitation and makes it the newest: the newest one sets the role and GitHub login."""
        r = self.one("""INSERT INTO invitations (org_id, project_id, email, role, name, github_login, invited_by)
                        VALUES (:o, :p, lower(:e), CAST(:r AS user_role), :n, :g, :b)
                        ON CONFLICT (org_id, email, project_id) WHERE accepted_at IS NULL
                        DO UPDATE SET role = EXCLUDED.role, name = EXCLUDED.name, github_login = EXCLUDED.github_login,
                                      invited_by = EXCLUDED.invited_by, created_at = now(), expires_at = now() + interval '14 days'
                        RETURNING id, project_id, email, role::text AS role, name, github_login, created_at, expires_at""",
                     o=org_id, p=project_id, e=email, r=role, n=name, g=github_login, b=invited_by)
        return _s(r)

    def invitations(self, project_id):
        return [_s(r) for r in self.rows("""SELECT id, project_id, email, role::text AS role, name, github_login, created_at, expires_at
                                            FROM invitations WHERE project_id = :p AND accepted_at IS NULL ORDER BY created_at""", p=project_id)]

    def revoke_invitation(self, invitation_id, project_id):
        if not (_is_uuid(invitation_id) and _is_uuid(project_id)):
            return False
        return self.run("DELETE FROM invitations WHERE id = CAST(:i AS uuid) AND project_id = CAST(:p AS uuid) AND accepted_at IS NULL",
                        i=invitation_id, p=project_id) > 0

    def leave_organization(self, user_id):
        """Unbind the login and drop the project memberships, so the person can join another organisation. The last admin may
        leave only an organisation nobody else uses (one made by mistake). The bound members are locked first, so two
        admins leaving at once cannot leave the organisation with none. A deactivated admin does not count as one."""
        with self.tx() as c:
            bound = c.execute(text("SELECT id, role::text, deactivated_at IS NULL FROM users WHERE firebase_uid IS NOT NULL FOR UPDATE")).all()
            if {str(i) for i, role, active in bound if role == "admin" and active} == {str(user_id)} and len(bound) > 1:
                return False
            c.execute(text("UPDATE users SET firebase_uid = NULL WHERE id = CAST(:u AS uuid)"), {"u": user_id})
            c.execute(text("DELETE FROM project_members WHERE user_id = CAST(:u AS uuid)"), {"u": user_id})
        return True

    # ---- members administration ----
    _MEMBER_SQL = """SELECT u.id, u.name, u.email, u.role::text AS role,
                            CASE WHEN u.deactivated_at IS NULL THEN 'active' ELSE 'deactivated' END AS status,
                            coalesce((SELECT jsonb_agg(jsonb_build_object('id', p.id, 'name', p.name) ORDER BY p.name, p.id)
                                        FROM project_members m JOIN projects p ON p.id = m.project_id
                                       WHERE m.user_id = u.id), '[]'::jsonb) AS projects
                       FROM users u WHERE u.org_id = CAST(:o AS uuid) AND u.firebase_uid IS NOT NULL"""

    def org_members(self, org_id):
        """MemoryStore.org_members: users with a login bound (a row without one left, or was enrolled and has not joined)."""
        return [_s(r) for r in self.rows(self._MEMBER_SQL + " ORDER BY u.name, u.id", o=org_id)]

    def org_member(self, org_id, user_id):
        if not _is_uuid(user_id):
            return None
        r = self.one(self._MEMBER_SQL + " AND u.id = CAST(:u AS uuid)", o=org_id, u=user_id)
        return _s(r) if r else None

    def _change_member(self, org_id, user_id, sets: str, **p):
        """One member's role or status in one transaction. The organisation's members are locked first (as leave_organization
        locks them), so two admins demoting or deactivating each other at once cannot leave it without an active admin."""
        if not _is_uuid(user_id):
            return None
        with self.tx() as c:
            bound = c.execute(text("""SELECT id::text, role::text, deactivated_at IS NULL FROM users
                                      WHERE org_id = CAST(:o AS uuid) AND firebase_uid IS NOT NULL FOR UPDATE"""), {"o": org_id}).all()
            admins = {i for i, role, active in bound if role == "admin" and active}
            if not any(i == str(user_id) for i, _, _ in bound):
                return None
            _, role, active = c.execute(text(f"""UPDATE users SET {sets} WHERE id = CAST(:u AS uuid)
                                                 RETURNING id, role::text, deactivated_at IS NULL"""), {"u": user_id, **p}).one()
            if admins == {str(user_id)} and not (role == "admin" and active):
                raise ports.LastAdmin(user_id)  # rolls the update back
        return self.get_user(user_id)

    def set_member_role(self, org_id, user_id, role):
        return self._change_member(org_id, user_id, "role = CAST(:r AS user_role)", r=role)

    def set_member_status(self, org_id, user_id, status):
        sets = "deactivated_at = coalesce(deactivated_at, now())" if status == "deactivated" else "deactivated_at = NULL"
        return self._change_member(org_id, user_id, sets)

    def find_person(self, org_id, email):
        r = self.one(f"SELECT {USER_COLS} FROM users WHERE org_id = CAST(:o AS uuid) AND lower(email) = lower(:e)",
                     o=org_id, e=email.strip())
        return User(**_s(r)) if r else None

    def erase_person(self, org_id, user_id):
        """MemoryStore.erase_person, in one transaction (the row is locked first, so a sign-in cannot bind it meanwhile)."""
        if not _is_uuid(user_id):
            return {}
        p = {"u": user_id, "o": org_id}
        with self.tx() as c:
            row = c.execute(text("SELECT lower(email) FROM users WHERE id = CAST(:u AS uuid) AND org_id = CAST(:o AS uuid) FOR UPDATE"),
                            p).first()
            if row is None:
                return {}
            counts = {}
            for kind, sql in (("chat_messages", "DELETE FROM chat_messages WHERE user_id = CAST(:u AS uuid)"),
                              ("expert_requests", "DELETE FROM expert_requests WHERE asked_by = CAST(:u AS uuid)"),
                              ("notifications", "DELETE FROM notifications WHERE user_id = CAST(:u AS uuid)"),
                              ("scheduling_links", "DELETE FROM scheduling_links WHERE user_id = CAST(:u AS uuid)"),
                              ("invitations", "DELETE FROM invitations WHERE org_id = CAST(:o AS uuid) AND lower(email) = :e"),
                              # their own words when asking for a recording to be removed: blanked, the rows stay
                              ("recording_removal_reasons", "UPDATE recording_removal_requests SET reason = '' "
                                                            "WHERE requested_by = CAST(:u AS uuid) AND reason <> ''")):
                counts[kind] = c.execute(text(sql), {**p, "e": row[0]}).rowcount
            c.execute(text("DELETE FROM project_members WHERE user_id = CAST(:u AS uuid)"), p)
            c.execute(text("""UPDATE users SET name = 'Erased person', email = 'erased-' || id::text || '@erased.invalid',
                              github_login = NULL, firebase_uid = NULL, deactivated_at = coalesce(deactivated_at, now())
                              WHERE id = CAST(:u AS uuid)"""), p)
        return counts

    def set_github_login(self, org_id, user_id, login):
        if not _is_uuid(user_id):
            return None
        r = self.one(f"""UPDATE users SET github_login = :g WHERE id = CAST(:u AS uuid) AND org_id = CAST(:o AS uuid)
                         RETURNING {USER_COLS}""", g=login, u=user_id, o=org_id)
        return User(**_s(r)) if r else None

    # ---- documents ----
    def documents(self, project_id):
        return [_s(r) for r in self.rows("""SELECT id, project_id, title, mime_type AS mime, status, page_count, created_at, uploaded_by
                                            FROM documents WHERE project_id = :p ORDER BY created_at DESC""", p=project_id)]

    def update_document(self, document_id, **fields):
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        self.run(f"UPDATE documents SET {sets} WHERE id = :id", id=document_id, **fields)

    @_ids
    def get_document(self, document_id):
        r = self.one("""SELECT id, project_id, title, gcs_uri AS uri, mime_type AS mime, status, page_count, created_at, uploaded_by
                        FROM documents WHERE id = CAST(:i AS uuid)""", i=document_id)
        return _s(r) if r else None

    def delete_document(self, document_id):
        if not _is_uuid(document_id):
            return
        with self.tx() as c:
            c.execute(text("DELETE FROM chunks WHERE source_type = 'doc' AND ref->>'document_id' = :i"), {"i": str(document_id)})
            c.execute(text("DELETE FROM documents WHERE id = CAST(:i AS uuid)"), {"i": document_id})

    # ---- approvals, experts, notifications, feedback ----
    def pending_approval_for_thread(self, thread_id, kind):
        r = self.one(f"""SELECT {self._APPROVAL_COLS} FROM approvals WHERE thread_id = :t AND kind = CAST(:k AS approval_kind) AND status = 'pending'
                         ORDER BY created_at DESC LIMIT 1""", t=thread_id, k=kind)
        return Approval(**_s(r)) if r else None

    def discard_orphan_proposals(self, onboarding_id, grace_s=0):
        """Tasks left 'proposed' by an attempt that failed before its approval was saved are not in any pending approval.
        Only those older than `grace_s` count: a concurrent run creates its tasks some time before it saves their approval."""
        return self.run("""UPDATE tasks t SET status = 'rejected', updated_at = now()
                           WHERE t.onboarding_id = CAST(:ob AS uuid) AND t.status = 'proposed'
                             AND t.created_at < now() - make_interval(secs => :grace) AND NOT EXISTS (
                               SELECT 1 FROM approvals a WHERE a.status = 'pending' AND a.kind = 'task_plan'
                                 AND jsonb_path_exists(a.payload, '$.plans[*].tasks[*] ? (@.id == $id)', jsonb_build_object('id', t.id::text)))""",
                        ob=onboarding_id, grace=grace_s)

    def supersede_review_approvals(self, task_id):
        """Close every pending review approval of the task except the one for the newest state of the pull request (the
        PR's updated_at in the event; reviews finish in any order), decided ones included in the comparison. Concurrent
        callers agree on that one, so whichever runs last, only it stays."""
        rows = self.rows(f"""UPDATE approvals SET status = 'rejected', decided_by = NULL, decided_at = now()
                             WHERE kind = 'review' AND status = 'pending' AND payload->>'task_id' = :t AND id <> (
                               SELECT id FROM approvals WHERE kind = 'review' AND payload->>'task_id' = :t
                               ORDER BY coalesce(payload->>'pr_updated_at', '') DESC, created_at DESC, id DESC LIMIT 1)
                             RETURNING {self._APPROVAL_COLS}""", t=str(task_id))
        return [Approval(**_s(r)) for r in rows]

    def claim_approval(self, approval_id, decision=None):
        """Claim a pending approval for deciding. The first decision given is stored with the claim and kept from then on."""
        if not _is_uuid(approval_id):
            return False
        return bool(self.one("""UPDATE approvals SET claimed_at = now(), decision = coalesce(decision, CAST(:d AS jsonb))
                                WHERE id = CAST(:i AS uuid) AND status = 'pending' AND (claimed_at IS NULL OR claimed_at < now() - interval '5 minutes')
                                RETURNING id""", i=approval_id, d=json.dumps(decision, default=str) if decision is not None else None))

    def release_approval(self, approval_id):
        self.run("UPDATE approvals SET claimed_at = NULL WHERE id = CAST(:i AS uuid) AND status = 'pending'", i=approval_id)

    @_ids
    def get_expert_request(self, request_id):
        r = self.one("SELECT * FROM expert_requests WHERE id = CAST(:i AS uuid)", i=request_id)
        return _s(r) if r else None

    def mark_notifications_read(self, user_id, ids):
        if ids is None:
            return self.run("UPDATE notifications SET read_at = now() WHERE user_id = :u AND read_at IS NULL", u=user_id)
        return self.run("UPDATE notifications SET read_at = now() WHERE user_id = :u AND read_at IS NULL AND id = ANY(:ids)",
                        u=user_id, ids=[int(i) for i in ids if str(i).isdigit()])

    def chat_feedback(self, message_id, user_id, value):
        if not _is_uuid(message_id):
            return False
        return self.run("UPDATE chat_messages SET feedback = :v WHERE id = CAST(:m AS uuid) AND user_id = CAST(:u AS uuid)",
                        v=value or None, m=message_id, u=user_id) > 0  # 0 takes a vote back

    def audit(self, org_id, actor_id, action, target, detail=None):
        self.run("INSERT INTO audit_log (org_id, actor_id, action, target, detail) VALUES (:o, :a, :ac, :t, CAST(:d AS jsonb))",
                 o=org_id, a=actor_id, ac=action, t=target, d=json.dumps(detail or {}, default=str))

    # ---- legal acceptances (019_legal.sql): the app role may insert and read, never change or remove ----
    _LEGAL_COLS = "id, org_id, user_id, document, version, method, accepted_at, ip_prefix, user_agent"

    def record_legal_acceptance(self, org_id, user_id, document, version, method, ip_prefix=None, user_agent=None, at=None):
        if not (_is_uuid(org_id) and _is_uuid(user_id)):
            return None
        r = self.one(f"""INSERT INTO legal_acceptances (org_id, user_id, document, version, method, ip_prefix, user_agent, accepted_at)
                         VALUES (CAST(:o AS uuid), CAST(:u AS uuid), :d, :v, :m, :ip, :ua, coalesce(CAST(:at AS timestamptz), now()))
                         ON CONFLICT DO NOTHING RETURNING {self._LEGAL_COLS}""",
                     o=org_id, u=user_id, d=document, v=version, m=method, ip=ip_prefix, ua=user_agent, at=at)
        return _s(r) if r else None

    def legal_acceptances(self, org_id, user_id=None, document=None):
        if not _is_uuid(org_id) or (user_id is not None and not _is_uuid(user_id)):
            return []
        rows = self.rows(f"""SELECT {self._LEGAL_COLS} FROM legal_acceptances
                             WHERE org_id = CAST(:o AS uuid) AND (CAST(:u AS uuid) IS NULL OR user_id = CAST(:u AS uuid))
                               AND (CAST(:d AS text) IS NULL OR document = :d)
                             ORDER BY accepted_at DESC, id DESC""", o=org_id, u=user_id, d=document)
        return [_s(r) for r in rows]

    # ---- installations and pre-tenant lookups ----
    def installation_org(self, installation_id):
        r = self.one("SELECT installation_org(:i) AS org", i=str(installation_id))
        return str(r["org"]) if r and r["org"] else None

    def record_installation_sender(self, installation_id, sender_id, sender_login, account_login):
        self.run("""INSERT INTO github_installation_senders (installation_id, sender_id, sender_login, account_login)
                    VALUES (:i, :s, :l, :a) ON CONFLICT (installation_id) DO UPDATE
                    SET sender_id = EXCLUDED.sender_id, sender_login = EXCLUDED.sender_login, account_login = EXCLUDED.account_login""",
                 i=str(installation_id), s=int(sender_id), l=sender_login, a=account_login)

    def installation_sender(self, installation_id):
        r = self.one("SELECT sender_id, sender_login, account_login FROM github_installation_senders WHERE installation_id = :i",
                     i=str(installation_id))
        return dict(r) if r else None

    def unbind_installation(self, installation_id):
        self.one("SELECT unbind_installation(:i) AS done", i=str(installation_id))

    def org_installations(self):
        return [_s(r) for r in self.rows("SELECT installation_id, account_login, created_at FROM github_installations ORDER BY created_at")]

    def bind_installation(self, installation_id, org_id, account, user_id):
        r = self.one("SELECT bind_installation(:i, CAST(:o AS uuid), :a, CAST(:u AS uuid)) AS ok", i=str(installation_id), o=org_id, a=account, u=user_id)
        return bool(r and r["ok"])

    def repos_by_full_name(self, full_name):
        return [_s(r) for r in self.rows("SELECT * FROM repo_by_full_name(:n)", n=full_name)]

    def watched_repositories(self):
        return [_s(r) for r in self.rows("SELECT * FROM watched_repositories()")]

    def claim_meeting_recording(self, meeting_id):
        return bool(self.one("UPDATE meetings SET status = 'live', started_at = coalesce(started_at, now()) "
                             "WHERE id = CAST(:i AS uuid) AND status = 'scheduled' RETURNING id", i=meeting_id))

    # ---- outbox (not tenant data: no org, no row-level security) ----
    def outbox_add(self, topic, event):
        """Record an event before publishing it. Idempotent on event["dedupe"]: the same event is recorded once. Adding a key
        that was sent already (a deliberate re-send of a reset event) makes it due again, so a failed re-send is retried."""
        with self.tx() as c:
            p = {"t": topic, "e": json.dumps(event, default=str), "d": event.get("dedupe") or None}
            r = c.execute(text("""INSERT INTO outbox (topic, event, dedupe) VALUES (:t, CAST(:e AS jsonb), :d)
                                  ON CONFLICT (dedupe) DO UPDATE SET topic = EXCLUDED.topic, event = EXCLUDED.event, sent_at = NULL,
                                      attempts = 0, last_error = NULL, next_attempt_at = EXCLUDED.next_attempt_at
                                  WHERE outbox.sent_at IS NOT NULL RETURNING id"""), p).first()
            if r is None:
                r = c.execute(text("SELECT id FROM outbox WHERE dedupe = :d"), p).one()
        return str(r[0])

    def outbox_due(self, limit=50, skip=()):
        """Claim unsent events whose next attempt is due. Each claim moves the next attempt forward (30 s, doubling, at most an
        hour), so a concurrent flusher skips them and a failed publish is retried later."""
        self.run("DELETE FROM outbox WHERE sent_at < now() - interval '7 days'")
        self._forget_old_events()
        rows = self.rows("""UPDATE outbox o SET attempts = o.attempts + 1,
                                  next_attempt_at = now() + least(interval '1 hour', make_interval(secs => 30 * power(2, least(o.attempts, 7))))
                              FROM (SELECT id FROM outbox WHERE sent_at IS NULL AND next_attempt_at <= now()
                                          AND NOT (id::text = ANY(:skip))
                                    ORDER BY next_attempt_at LIMIT :n FOR UPDATE SKIP LOCKED) due
                              WHERE o.id = due.id RETURNING o.id, o.topic, o.event""", n=limit, skip=list(skip))
        return [_s(r) for r in rows]

    def outbox_sent(self, outbox_id):
        self.run("UPDATE outbox SET sent_at = now(), last_error = NULL WHERE id = CAST(:i AS uuid)", i=outbox_id)

    def outbox_failed(self, outbox_id, error):
        self.run("UPDATE outbox SET last_error = left(:e, 2000) WHERE id = CAST(:i AS uuid)", i=outbox_id, e=error)

    def outbox_attempts(self, outbox_id):
        r = self.one("SELECT attempts FROM outbox WHERE id = CAST(:i AS uuid)", i=outbox_id)
        return r["attempts"] if r else 0

    EVENTS_KEPT_DAYS = 90
    _events_swept: float | None = None  # time.monotonic() of this process's last sweep

    def _forget_old_events(self) -> None:
        """At most hourly (the outbox is read every few seconds): forget the dedupe keys of events handled more than
        EVENTS_KEPT_DAYS ago; nothing redelivers an event that old. Pull request keys stay: the self-hosted watcher lists every
        open pull request each round, and its key is what says that head was reviewed already."""
        import time

        if PgStore._events_swept is not None and time.monotonic() - PgStore._events_swept < 3600:
            return
        PgStore._events_swept = time.monotonic()
        self.run("""DELETE FROM processed_events WHERE status = 'done' AND dedupe_key NOT LIKE 'pr:%'
                    AND claimed_at < now() - make_interval(days => :d)""", d=self.EVENTS_KEPT_DAYS)

    def ping(self):
        return bool(self.one("SELECT 1 AS ok"))

    def role_can_bypass_rls(self) -> bool:
        r = self.one("SELECT rolbypassrls OR rolsuper AS bypass FROM pg_roles WHERE rolname = current_user")
        return bool(r and r["bypass"])
