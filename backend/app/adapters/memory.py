"""In-memory implementations of every port. Used by tests and by `MODE=demo` (no cloud needed)."""
from __future__ import annotations

import hashlib
import heapq
import os
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import ports
from app.agents.core.schemas import Chunk, Hit, KTLink, MeetingResult, ProposedTask, QAResult, ReviewResult
from app.ports import Approval, CodeChunks, Meeting, Onboarding, Project, Repository, ReviewRow, TaskRow, User, link_matches, norm_question
from app.rag import text as rag_text
from app.rag.bm25 import BM25
from app.rag.fuse import rrf
from app.services.chunking import should_index, split_identifiers
from app.services.embeddings import Embedder, cosine
from app.services.kt_linker import CodeRef

RRF_K = 60
GRID_DEPTH = 50  # search_grid: each channel's list (vector, BM25) before fusion, as search() takes


class _Corpus:
    """One project's non-stale chunks as search_grid reads them: their ids and kinds, an inverted index of the vectors'
    non-zero dimensions (the hashing embedder's vectors are sparse, so a query touches only the chunks that share one of
    its dimensions), and BM25 over their tokens. Built lazily and rebuilt when the project's chunks change (`gen`)."""

    __slots__ = ("gen", "ids", "kinds", "vec", "bm25")

    def __init__(self, gen: int, ids: list[str], kinds: list[str], vec: dict[int, list[tuple[int, float]]], bm25: BM25):
        self.gen, self.ids, self.kinds, self.vec, self.bm25 = gen, ids, kinds, vec, bm25

    def cosines(self, qv: list[float]) -> dict[int, float]:
        """Chunk number -> its vector's dot product with qv (the cosine: both are unit vectors), for chunks that share a
        non-zero dimension with it; every other chunk's is 0."""
        acc: dict[int, float] = {}
        for d, x in enumerate(qv):
            if not x:
                continue
            for i, y in self.vec.get(d, ()):
                acc[i] = acc.get(i, 0.0) + x * y
        return acc


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _current_tenant() -> str | None:
    """The organisation the request runs as (set by app/api/auth.py for every mode), for the few reads that rely on
    row-level security in Postgres (MemoryStore.org_installations)."""
    try:
        from app.adapters.postgres import current_org
    except ImportError:
        return None
    return current_org.get()


class MemoryIndex:
    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.chunks: dict[str, Chunk] = {}
        self.vectors: dict[str, list[float]] = {}
        self.tokens: dict[str, set[str]] = {}
        # search_grid's per-project corpora, rebuilt when a project's generation moves (upsert, delete_paths,
        # delete_document). Each chunk's sparse vector and BM25 term counts are kept with the object they were computed
        # from, so a replaced chunk or vector is never read through an old entry.
        self._gens: dict[str, int] = defaultdict(int)
        self._corpora: dict[str, _Corpus] = {}
        self._sparse: dict[str, tuple[list[float], dict[int, float]]] = {}
        self._codemaps: dict[str, tuple[int, dict[str, list[str]], dict[str, list[str]]]] = {}  # _code_map, per generation
        self._terms: dict[str, tuple[Chunk, dict[str, int]]] = {}
        self._build = threading.Lock()

    def upsert(self, chunks):
        if not chunks:
            return 0
        vecs = self.embedder.embed([c.content for c in chunks], "document")
        for c, v in zip(chunks, vecs):
            self.chunks[c.id], self.vectors[c.id] = c, v
            self.tokens[c.id] = set(split_identifiers(c.content + " " + (c.symbol or "")).split())
        self._changed({c.project_id for c in chunks})
        return len(chunks)

    def delete_paths(self, project_id, paths):
        gone = [cid for cid, c in self.chunks.items() if c.project_id == project_id and c.source_type == "code" and c.ref.get("path") in set(paths)]
        self._drop(project_id, gone)
        return gone

    def delete_document(self, project_id, document_id):
        gone = [cid for cid, c in self.chunks.items() if c.project_id == project_id and c.source_type == "doc"
                and str(c.ref.get("document_id")) == str(document_id)]
        self._drop(project_id, gone)
        return gone

    def delete_meeting(self, project_id, meeting_id):
        gone = [cid for cid, c in self.chunks.items() if c.project_id == project_id and c.source_type == "meeting"
                and str(c.ref.get("meeting_id")) == str(meeting_id)]
        self._drop(project_id, gone)
        return gone

    def _drop(self, project_id, ids):
        for cid in ids:
            self.chunks.pop(cid), self.vectors.pop(cid), self.tokens.pop(cid)
            self._sparse.pop(cid, None), self._terms.pop(cid, None)
        if ids:
            self._changed({project_id})

    def _changed(self, project_ids):
        """After the chunks changed (never before): a corpus built meanwhile carries the old generation and is rebuilt."""
        for pid in project_ids:
            self._gens[pid] += 1

    def _pool(self, project_id, source_types):
        return [c for c in self.chunks.values() if c.project_id == project_id and not c.stale and (not source_types or c.source_type in source_types)]

    def search(self, project_id, query, k=8, source_types=None):
        pool = self._pool(project_id, source_types)
        if not pool:
            return []
        return self._rank(pool, query, self.embedder.embed([query], "query")[0], k)

    def search_many(self, project_id, queries, source_types=None):
        """search() per query: the hashing embedder makes no network call, so batching saves nothing here (PgIndex batches),
        and going through search() keeps a test's stand-in for it in the path."""
        return [self.search(project_id, q, k, source_types) for q, k in queries]

    def _rank(self, pool, query, qv, k):
        qt = set(split_identifiers(query).split()) - {"the", "a", "an", "is", "of", "to", "in", "how", "what", "does", "do", "where"}
        sims = {c.id: cosine(qv, self.vectors[c.id]) for c in pool}
        vec_rank = [cid for cid in sorted(sims, key=sims.get, reverse=True) if sims[cid] > 0][:50]
        kw = {c.id: len(qt & self.tokens[c.id]) for c in pool}
        kw_rank = [cid for cid in sorted(kw, key=kw.get, reverse=True) if kw[cid] > 0][:50]
        fused: dict[str, float] = defaultdict(float)
        for rank, cid in enumerate(vec_rank, 1):
            fused[cid] += 1 / (RRF_K + rank)
        for rank, cid in enumerate(kw_rank, 1):
            fused[cid] += 1 / (RRF_K + rank)
        best = sorted(fused, key=fused.get, reverse=True)[:k]
        return [Hit(chunk=self.chunks[cid], score=fused[cid], similarity=max(0.0, sims[cid])) for cid in best]

    # --- search_grid: the research crew's batched retrieval (app/rag, docs/specs/rag-crew.md) ---
    def search_grid(self, project_id, queries, kinds, k, semantic=True):
        """For each query, for each kind, the top k[kind] hybrid hits: the vector list and a BM25 keyword list (top
        GRID_DEPTH each) fused by RRF. One embedding call for every query. Every Hit.similarity is the cosine to
        queries[0], the question as asked (the retrieval gate's evidence), whichever query found the hit.
        semantic=False: the keyword list alone, no embedding call, every similarity 0.0 (the crew's fallback when the query
        embedding fails). A kind that is not a source type has no hits."""
        if not queries:
            return []
        corpus = self._corpus(project_id)
        vecs = self.embedder.embed(list(queries), "query") if semantic else [None] * len(queries)
        want = list(dict.fromkeys(kinds))
        anchor: dict[int, float] = {}
        out = []
        for qi, (query, qv) in enumerate(zip(queries, vecs)):
            sims = corpus.cosines(qv) if qv is not None else {}
            if qi == 0:
                anchor = sims
            vec_by_kind = self._by_kind(sims, corpus, want)
            kw_by_kind = self._by_kind(corpus.bm25.scores(rag_text.index_terms(query)), corpus, want)
            row = {}
            for kind in want:
                live: dict[int, Chunk] = {}
                vec_rank = self._best(project_id, corpus, vec_by_kind[kind], kind, live)
                kw_rank = self._best(project_id, corpus, kw_by_kind[kind], kind, live)
                fused = rrf([(vec_rank, 1.0), (kw_rank, 1.0)], RRF_K)
                best = sorted(fused, key=lambda i: (-fused[i], i))[: max(0, int(k.get(kind, 8)))]
                row[kind] = [Hit(chunk=live[i], score=fused[i], similarity=max(0.0, anchor.get(i, 0.0))) for i in best]
            out.append(row)
        return out

    @staticmethod
    def _by_kind(scores: dict[int, float], corpus, kinds: list[str]) -> dict[str, list[tuple[float, int]]]:
        """One pass over a channel's scores: (-score, chunk number) per wanted kind, positive scores only."""
        out: dict[str, list[tuple[float, int]]] = {kd: [] for kd in kinds}
        ck = corpus.kinds
        for i, x in scores.items():
            if x > 0:
                bucket = out.get(ck[i])
                if bucket is not None:
                    bucket.append((-x, i))
        return out

    def _best(self, project_id, corpus, scored: list[tuple[float, int]], kind: str, live: dict) -> list[int]:
        """The GRID_DEPTH best chunk numbers of one kind (best score first, ties by number), skipping any chunk that went
        stale or was deleted since the corpus was built (search() leaves stale chunks out the same way). `live` collects
        the chunks. Only the top of the list is sorted (a heap), widened while stale chunks leave it short."""
        out: list[int] = []
        n = GRID_DEPTH * 2
        while True:
            top = heapq.nsmallest(n, scored) if len(scored) > n else sorted(scored)
            out.clear()
            for _, i in top:
                c = self.chunks.get(corpus.ids[i])
                if c is None or c.stale or c.project_id != project_id or c.source_type != kind:
                    continue
                live[i] = c
                out.append(i)
                if len(out) == GRID_DEPTH:
                    return out
            if len(top) == len(scored):
                return out
            n *= 4

    def _corpus(self, project_id) -> _Corpus:
        gen = self._gens[project_id]
        built = self._corpora.get(project_id)
        if built is not None and built.gen == gen:
            return built
        with self._build:
            built = self._corpora.get(project_id)
            if built is not None and built.gen == gen:
                return built
            ids, kinds, docs = [], [], []
            vec: dict[int, list[tuple[int, float]]] = {}
            for cid, c in list(self.chunks.items()):
                if c.project_id != project_id or c.stale:
                    continue
                v = self.vectors.get(cid)
                if v is None:
                    continue
                i = len(ids)
                ids.append(cid)
                kinds.append(c.source_type)
                sp = self._sparse.get(cid)
                if sp is None or sp[0] is not v:
                    sp = (v, {d: x for d, x in enumerate(v) if x})
                    self._sparse[cid] = sp
                for d, x in sp[1].items():
                    vec.setdefault(d, []).append((i, x))
                tm = self._terms.get(cid)
                if tm is None or tm[0] is not c:
                    counts: dict[str, int] = {}
                    for t in rag_text.index_terms(c.content + " " + (c.symbol or "")):
                        counts[t] = counts.get(t, 0) + 1
                    tm = (c, counts)
                    self._terms[cid] = tm
                docs.append(tm[1])
            built = _Corpus(gen, ids, kinds, vec, BM25(docs))
            self._corpora[project_id] = built
            return built

    # --- CodeIndex for the KT linker ---
    def _code(self, project_id):
        return [c for c in self.chunks.values() if c.project_id == project_id and c.source_type == "code"]

    def _code_map(self, project_id) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """The project's code chunk ids by path (line order) and by symbol (path, then line order), built once per
        generation, as the corpus is: file_chunks, symbols_named and symbols_in read a few ids instead of every chunk. Each
        id is looked up again when read, so a chunk marked stale in place is seen as it is now."""
        gen = self._gens[project_id]
        built = self._codemaps.get(project_id)
        if built is not None and built[0] == gen:
            return built[1], built[2]
        with self._build:
            built = self._codemaps.get(project_id)
            if built is not None and built[0] == gen:
                return built[1], built[2]
            code = sorted(self._code(project_id), key=lambda c: (c.ref["path"], c.ref.get("start_line") or 0, c.id))
            by_path: dict[str, list[str]] = {}
            by_name: dict[str, list[str]] = {}
            for c in code:
                by_path.setdefault(c.ref["path"], []).append(c.id)
                if c.symbol:
                    by_name.setdefault(c.symbol, []).append(c.id)
            self._codemaps[project_id] = (gen, by_path, by_name)
            return by_path, by_name

    def _mapped(self, project_id, ids, path=None, symbol=None) -> list[Chunk]:
        out = []
        for cid in ids:
            c = self.chunks.get(cid)
            if c is None or c.project_id != project_id or c.source_type != "code":
                continue
            if (path is not None and c.ref.get("path") != path) or (symbol is not None and c.symbol != symbol):
                continue
            out.append(c)
        return out

    def all_paths(self, project_id):
        return sorted(self._code_map(project_id)[0])

    def _ref(self, c: Chunk) -> CodeRef:
        return CodeRef(path=c.ref["path"], symbol=c.symbol, start_line=c.ref.get("start_line"), end_line=c.ref.get("end_line"), chunk_id=c.id)

    def symbols_named(self, project_id, name):
        """In one order whatever the history of the index: by path (code point), then start line (PgIndex the same)."""
        return [self._ref(c) for c in self._mapped(project_id, self._code_map(project_id)[1].get(name, ()), symbol=name)]

    def symbols_in(self, project_id, path):
        return [self._ref(c) for c in self._mapped(project_id, self._code_map(project_id)[0].get(path, ()), path=path) if c.symbol]

    def semantic_code(self, project_id, text, k=3):
        return [(self._ref(h.chunk), h.similarity) for h in self.search(project_id, text, k, ["code"])]

    def code_chunks(self, project_id, content=True):
        """Whole files in path order, up to ports.CODE_CHUNK_CAP chunks (the cut PgIndex makes in SQL)."""
        live = sorted((c for c in self._code(project_id) if not c.stale), key=lambda c: (c.ref["path"], c.ref.get("start_line") or 0))
        cap, kept = ports.CODE_CHUNK_CAP, len(live)
        if kept > cap:
            kept = cap
            while kept and live[kept - 1].ref["path"] == live[kept].ref["path"]:  # never half a file
                kept -= 1
        return CodeChunks((c if content else c.model_copy(update={"content": ""}) for c in live[:kept]), truncated=kept < len(live))

    def file_chunks(self, project_id, path):
        return sorted((c for c in self._mapped(project_id, self._code_map(project_id)[0].get(path, ()), path=path) if not c.stale),
                      key=lambda c: c.ref.get("start_line") or 0)

    def code_version(self, project_id):
        h, n = hashlib.blake2b(digest_size=12), 0
        for c in sorted((c for c in self._code(project_id) if not c.stale), key=lambda c: c.id):
            r, n = c.ref, n + 1
            h.update(f"{c.id}|{c.symbol}|{r.get('start_line')}|{r.get('end_line')}|{r.get('body_end')}|{r.get('commit') or ''}\n".encode())
        return f"{n}:{h.hexdigest()}"  # 'count:digest', as PgIndex returns it


class MemoryStore:
    def __init__(self):
        self.projects: dict[str, Project] = {}
        self.users: dict[str, User] = {}
        self.project_members: dict[str, set[str]] = defaultdict(set)
        self.onboard: dict[str, Onboarding] = {}
        self.repos: dict[str, Repository] = {}
        self.owners_text: dict[str, str] = {}
        self.meetings: dict[str, Meeting] = {}
        self.notes: dict[str, MeetingResult] = {}
        self.calendar_conns: dict[tuple[str, str], dict] = {}  # (org, provider) -> connection (scheduling)
        self.sched_links: dict[tuple[str, str], dict] = {}  # (user, provider) -> {"org_id", "url"} (scheduling)
        self.links: dict[str, list[KTLink]] = defaultdict(list)
        self.task_rows: dict[str, TaskRow] = {}
        self.approval_rows: dict[str, Approval] = {}
        self.review_rows: dict[str, ReviewRow] = {}
        self.chats: dict[str, dict] = {}
        self.expert_requests: dict[str, dict] = {}
        self.notifications: list[dict] = []
        self.runs: list[dict] = []
        self.events: dict[str, dict] = {}
        self.document_rows: dict[str, dict] = {}
        self.orgs: dict[str, str] = {}
        self.firebase: dict[str, str] = {}  # firebase uid -> user id
        self.left: set[str] = set()  # users who left their organisation (in Postgres: a row with no login bound)
        self.invites: dict[str, dict] = {}
        self.installations: dict[str, dict] = {}
        self.audit_rows: list[dict] = []
        self.legal_rows: list[dict] = []  # legal acceptances (app/legal.py): appended, never changed or removed
        self.approval_claims: set[str] = set()
        self.installation_senders: dict[str, dict] = {}
        self.org_created: dict[str, date] = {}
        self.task_created: dict[str, datetime] = {}
        self.outbox: dict[str, dict] = {}
        self._ingest_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    # ---- setup helpers (used by seed + tests) ----
    def add_project(self, p: Project):
        self.projects[p.id] = p
        self.orgs.setdefault(p.org_id, p.name)
        return p

    def add_user(self, u: User, project_id: str | None = None):
        self.users[u.id] = u
        if project_id:
            self.project_members[project_id].add(u.id)
        return u

    def add_onboarding(self, o: Onboarding):
        self.onboard[o.id] = o
        return o

    def add_repo(self, r: Repository):
        self.repos[r.id] = r
        return r

    def add_meeting(self, m: Meeting):
        self.meetings[m.id] = m
        return m

    # ---- people and projects ----
    def get_project(self, project_id):
        return self.projects[project_id]

    def get_user(self, user_id):
        return self.users.get(user_id)

    def users_by_github(self, org_id, logins):
        wanted = {l.lower() for l in logins}
        return [u for u in self.users.values() if u.org_id == org_id and (u.github_login or "").lower() in wanted]

    def managers(self, project_id):
        return sorted((self.users[u] for u in self.project_members[project_id] if self.users[u].role in {"manager", "admin"}),
                      key=lambda u: (u.role != "manager", u.name))

    def members(self, project_id):
        return [self.users[u] for u in self.project_members[project_id]]

    def onboardings(self, project_id, active_only=True):
        return [o for o in self.onboard.values() if o.project_id == project_id and (o.status == "active" or not active_only)]

    def get_onboarding(self, onboarding_id):
        return self.onboard.get(onboarding_id)

    # ---- repositories ----
    def get_repo(self, repo_id):
        return self.repos.get(repo_id)

    def repo_by_name(self, full_name):
        return next((r for r in self.repos.values() if r.full_name == full_name), None)

    def repos_for(self, project_id):
        return [r for r in self.repos.values() if r.project_id == project_id]

    def update_repo(self, repo_id, **fields):
        self.repos[repo_id] = self.repos[repo_id].model_copy(update=fields)

    @contextmanager
    def ingest_lock(self, repo_id):
        with self._guard:
            lock = self._ingest_locks[repo_id]
        with lock:
            yield

    def set_codeowners(self, project_id, text):
        self.owners_text[project_id] = text

    def codeowners(self, project_id):
        return self.owners_text.get(project_id, "")

    # ---- meetings ----
    def get_meeting(self, meeting_id):
        return self.meetings.get(meeting_id)

    def update_meeting(self, meeting_id, **fields):
        self.meetings[meeting_id] = self.meetings[meeting_id].model_copy(update=fields)

    def save_meeting_result(self, result):
        self.notes[result.meeting_id] = result
        self.update_meeting(result.meeting_id, status="ready", summary=result.summary)

    def meeting_notes(self, meeting_id):
        return self.notes.get(meeting_id)

    def meeting_notes_many(self, meeting_ids):
        return {m: self.notes[m] for m in meeting_ids if m in self.notes}

    def add_kt_links(self, project_id, links):
        keep = [l for l in self.links[project_id] if l.meeting_id not in {x.meeting_id for x in links}]
        self.links[project_id] = keep + list(links)

    def erase_meeting_content(self, meeting_id):
        m = self.meetings.get(meeting_id)
        if m is None:
            return
        self.notes.pop(meeting_id, None)
        self.links[m.project_id] = [l for l in self.links[m.project_id] if l.meeting_id != meeting_id]
        self.update_meeting(meeting_id, recording_uri=None, summary=None, status="ended", transcript_source=None)

    def kt_links(self, project_id, path=None, chunk_ids=None):
        out = self.links[project_id]
        if path:
            out = [l for l in out if l.file_path == path]
        if chunk_ids is not None:
            ids = set(chunk_ids)
            paths = {l.file_path for l in out if l.chunk_id in ids}
            out = [l for l in out if l.chunk_id in ids or (l.chunk_id is None and l.file_path in paths)]
        return sorted(out, key=lambda l: (-l.confidence, l.start_s))

    def mark_kt_code_changed(self, project_id, paths):
        for l in self.links[project_id]:
            if l.file_path in set(paths):
                l.code_changed_since = True

    # ---- tasks / approvals / reviews ----
    def create_tasks(self, onboarding_id, tasks: list[ProposedTask], start: date):
        rows = []
        for t in tasks:
            row = TaskRow(
                id=new_id(), onboarding_id=onboarding_id, title=t.title, description=t.description, kind=t.kind,
                status="proposed", origin=t.source, due_date=start + timedelta(days=t.due_in_days),
                acceptance_criteria=t.acceptance_criteria, related_paths=t.related_paths, github_issue_number=t.issue_number,
            )
            self.task_rows[row.id] = row
            self.task_created[row.id] = datetime.now(timezone.utc)
            rows.append(row)
        return rows

    def get_task(self, task_id):
        return self.task_rows.get(task_id)

    def tasks(self, onboarding_id=None, project_id=None):
        rows = list(self.task_rows.values())
        if onboarding_id:
            rows = [t for t in rows if t.onboarding_id == onboarding_id]
        if project_id:
            ids = {o.id for o in self.onboardings(project_id, active_only=False)}
            rows = [t for t in rows if t.onboarding_id in ids]
        return sorted(rows, key=lambda t: (t.due_date or date.max, t.title))

    def update_task(self, task_id, **fields):
        self.task_rows[task_id] = self.task_rows[task_id].model_copy(update=fields)

    def create_approval(self, project_id, kind, thread_id, payload):
        a = Approval(id=new_id(), project_id=project_id, kind=kind, thread_id=thread_id, payload=payload)
        self.approval_rows[a.id] = a
        return a

    def get_approval(self, approval_id):
        return self.approval_rows.get(approval_id)

    def approvals(self, project_id, status="pending"):
        return [a for a in self.approval_rows.values() if a.project_id == project_id and (status is None or a.status == status)]

    def decide_approval(self, approval_id, status, user_id):
        self.approval_rows[approval_id] = self.approval_rows[approval_id].model_copy(
            update={"status": status, "decided_by": user_id, "decided_at": datetime.now(timezone.utc)}
        )

    def pending_approval_for_thread(self, thread_id, kind):
        rows = [a for a in self.approval_rows.values() if a.thread_id == thread_id and a.kind == kind and a.status == "pending"]
        return max(rows, key=lambda a: a.created_at) if rows else None

    def discard_orphan_proposals(self, onboarding_id, grace_s=0):
        pending = {t["id"] for a in self.approval_rows.values() if a.status == "pending" and a.kind == "task_plan"
                   for p in a.payload.get("plans", []) for t in p.get("tasks", [])}
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=grace_s)
        n = 0
        for t in list(self.task_rows.values()):
            created = self.task_created.get(t.id, datetime.min.replace(tzinfo=timezone.utc))
            if t.onboarding_id == onboarding_id and t.status == "proposed" and t.id not in pending and created <= cutoff:
                self.update_task(t.id, status="rejected")
                n += 1
        return n

    def supersede_review_approvals(self, task_id):
        mine = [a for a in self.approval_rows.values() if a.kind == "review" and a.payload.get("task_id") == task_id]
        if not mine:
            return []
        keep = max(mine, key=lambda a: (a.payload.get("pr_updated_at") or "", a.created_at, a.id))
        old = [a for a in mine if a.status == "pending" and a.id != keep.id]
        for a in old:
            self.decide_approval(a.id, "rejected", None)
        return [self.approval_rows[a.id] for a in old]

    def close_review_approvals(self, task_id):
        mine = [a for a in self.approval_rows.values() if a.kind == "review" and a.payload.get("task_id") == task_id and a.status == "pending"]
        for a in mine:
            self.decide_approval(a.id, "rejected", None)
        return [self.approval_rows[a.id] for a in mine]

    def review_approvals(self, task_id):
        return sorted((a for a in self.approval_rows.values() if a.kind == "review" and a.payload.get("task_id") == task_id),
                      key=lambda a: a.created_at)

    def latest_reviews(self, task_ids):
        wanted, out = set(task_ids), {}
        for r in self.review_rows.values():  # insertion order: the last one seen per task is its newest
            if r.task_id in wanted:
                out[r.task_id] = r.id
        return out

    def save_review(self, task_id, pr_number, pr_url, head_sha, result: ReviewResult):
        old = next((r for r in self.review_rows.values() if r.task_id == task_id and r.head_sha == head_sha), None)
        row = ReviewRow(id=old.id if old else new_id(), task_id=task_id, pr_number=pr_number, pr_url=pr_url, head_sha=head_sha, result=result)
        self.review_rows[row.id] = row
        return row

    def get_review(self, review_id):
        return self.review_rows.get(review_id)

    def decide_review(self, review_id, decision, user_id):
        self.review_rows[review_id] = self.review_rows[review_id].model_copy(update={"manager_decision": decision})

    # ---- chat ----
    def save_chat(self, project_id, user_id, question, result: QAResult):
        mid = new_id()
        self.chats[mid] = {"id": mid, "project_id": project_id, "user_id": user_id, "question": question, "result": result, "stale": False,
                           "created_at": datetime.now(timezone.utc)}
        return mid

    def mark_answers_stale(self, chunk_ids):
        ids, n = set(chunk_ids), 0
        for c in self.chats.values():
            if any(cit.chunk_id in ids for cit in c["result"].citations):
                c["stale"] = True
                n += 1
        return n

    def create_expert_request(self, project_id, question, expert_id, asked_by, message_id, draft=None):
        rid = new_id()
        asker = self.users.get(asked_by)
        self.expert_requests[rid] = {"id": rid, "project_id": project_id, "question": question, "expert_id": expert_id,
                                     "asked_by": asked_by, "asked_by_name": asker.name if asker else None, "message_id": message_id,
                                     "status": "open", "answer": None, "created_at": datetime.now(timezone.utc), "draft": draft}
        return rid

    # ---- cross-cutting ----
    def notify(self, user_id, kind, text, link=""):
        self.notifications.append({"id": str(len(self.notifications)), "user_id": user_id, "kind": kind, "text": text, "link": link,
                                   "read_at": None, "at": datetime.now(timezone.utc)})

    def close_notifications(self, link_prefix):
        n = 0
        for row in self.notifications:
            if not row.get("read_at") and link_matches(row["link"], link_prefix):
                row["read_at"] = datetime.now(timezone.utc)
                n += 1
        return n

    def notification_recipients(self, link_prefix, kind):
        return list(dict.fromkeys(r["user_id"] for r in self.notifications if r["kind"] == kind and link_matches(r["link"], link_prefix)))

    def record_run(self, project_id, agent, trigger, status, detail, run_id=None):
        rid = run_id if run_id and not any(r["id"] == run_id for r in self.runs) else new_id()
        self.runs.append({"id": rid, "project_id": project_id, "agent": agent, "trigger": trigger, "status": status, "detail": detail,
                          "at": datetime.now(timezone.utc), "trace_id": detail.get("trace_id")})  # the LangSmith trace, when traced
        return rid

    def claim_event(self, dedupe_key, lease_s):
        now = datetime.now(timezone.utc)
        e = self.events.get(dedupe_key)
        if e and e["status"] == "done":
            return "done"
        if e and (now - e["at"]).total_seconds() < lease_s:
            return "busy"
        self.events[dedupe_key] = {"status": "processing", "at": now, "lease_s": lease_s}
        return "new"

    def touch_event(self, dedupe_key):
        if dedupe_key in self.events:
            self.events[dedupe_key]["at"] = datetime.now(timezone.utc)

    def finish_event(self, dedupe_key):
        self.events[dedupe_key] = {"status": "done", "at": datetime.now(timezone.utc)}

    def release_event(self, dedupe_key):
        if self.events.get(dedupe_key, {}).get("status") == "processing":
            self.events.pop(dedupe_key)

    def event_status(self, dedupe_key):
        e = self.events.get(dedupe_key)
        if not e:
            return "none"
        if e["status"] == "done":
            return "done"
        return "busy" if (datetime.now(timezone.utc) - e["at"]).total_seconds() < e.get("lease_s", 0) else "none"

    def reset_event(self, dedupe_key):
        if self.event_status(dedupe_key) == "busy":
            return False
        self.events.pop(dedupe_key, None)
        return True

    # ---- AppStore (API-only) ----
    def create_repo(self, project_id, full_name, installation_id, default_branch="main"):
        old = next((r for r in self.repos.values() if r.project_id == project_id), None)
        if old and old.full_name.lower() != full_name.lower():
            return None  # one repository per project (Postgres enforces it with a unique index)
        if old:
            self.update_repo(old.id, installation_id=installation_id, default_branch=default_branch, status="pending")
            return self.repos[old.id]
        return self.add_repo(Repository(id=new_id(), project_id=project_id, full_name=full_name, installation_id=installation_id, default_branch=default_branch))

    def create_document(self, project_id, title, uri, mime, uploaded_by):
        did = new_id()
        self.document_rows[did] = {"id": did, "project_id": project_id, "title": title, "uri": uri, "mime": mime, "uploaded_by": uploaded_by,
                               "status": "pending", "created_at": datetime.now(timezone.utc)}
        return did

    def create_onboarding(self, project_id, new_hire_id, manager_id, start, role_title="Software Engineer"):
        self.project_members[project_id] |= {new_hire_id, manager_id}
        with self._guard:  # one active onboarding per person and project (Postgres: a partial unique index)
            active = next((o for o in self.onboardings(project_id) if o.new_hire_id == new_hire_id), None)
            return active or self.add_onboarding(Onboarding(id=new_id(), project_id=project_id, new_hire_id=new_hire_id, manager_id=manager_id,
                                                            start_date=start, role_title=role_title))

    def create_meeting(self, project_id, title, created_by, kt_key=None, *, provider="manual", join_url=None, calendar_event_id=None,
                       starts_at=None, ends_at=None, attendees=None):
        with self._guard:  # Postgres: a partial unique index allows one scheduled, live or processing call per KT session
            if kt_key and any(m.project_id == project_id and m.kt_key == kt_key and m.status in {"scheduled", "live", "processing"}
                              for m in self.meetings.values()):
                return None
            org = self.projects[project_id].org_id if project_id in self.projects else None
            if calendar_event_id and any(m.provider == provider and m.calendar_event_id == calendar_event_id
                                         and self.org_of_project(m.project_id) == org for m in self.meetings.values()):
                return None  # Postgres: meetings_calendar_event (a redelivered webhook)
            return self.add_meeting(Meeting(id=new_id(), project_id=project_id, title=title, room=f"kt-{uuid.uuid4()}",
                                            started_at=starts_at or datetime.now(timezone.utc), kt_key=kt_key, created_by=created_by,
                                            provider=provider, join_url=join_url, calendar_event_id=calendar_event_id,
                                            starts_at=starts_at, ends_at=ends_at, attendees=list(attendees or [])))

    def cancel_meeting(self, meeting_id):
        with self._guard:
            m = self.meetings.get(meeting_id)
            if not m or m.status != "scheduled":
                return False
            self.update_meeting(meeting_id, status="cancelled")
            return True

    def end_meeting(self, meeting_id):
        with self._guard:
            m = self.meetings.get(meeting_id)
            if not m or m.status != "live":
                return False
            self.update_meeting(meeting_id, status="ended")
            return True

    def list_meetings(self, project_id):
        return sorted((m for m in self.meetings.values() if m.project_id == project_id),
                      key=lambda m: m.started_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # ---- scheduling (docs/specs/scheduling.md) ----
    def meeting_by_event(self, provider, calendar_event_id):
        return next((m for m in self.meetings.values() if m.provider == provider and m.calendar_event_id == calendar_event_id), None)

    def claim_meeting_import(self, meeting_id, recording_uri, duration_s):
        with self._guard:
            m = self.meetings.get(meeting_id)
            if not m or m.status not in {"scheduled", "ended", "failed"}:
                return False
            self.update_meeting(meeting_id, status="processing", recording_uri=recording_uri, duration_s=duration_s)
            return True

    def expire_meeting(self, meeting_id):
        with self._guard:
            m = self.meetings.get(meeting_id)
            if not m or m.status != "scheduled":
                return False
            self.update_meeting(meeting_id, status="ended")
            return True

    def discard_meeting(self, meeting_id):
        with self._guard:
            m = self.meetings.get(meeting_id)
            if not m or m.status != "scheduled" or m.recording_uri:
                return False
            del self.meetings[meeting_id]
            return True

    def meetings_awaiting_recording(self, grace_s, wait_s, limit=20):
        now = datetime.now(timezone.utc)
        rows = [m for m in self.meetings.values() if m.status == "scheduled" and m.ends_at and m.ends_at < now - timedelta(seconds=grace_s)
                and ((m.provider == "google" and m.calendar_event_id) or m.ends_at < now - timedelta(seconds=wait_s))]
        return [{"id": m.id, "org_id": self.org_of_project(m.project_id), "project_id": m.project_id, "provider": m.provider,
                 "calendar_event_id": m.calendar_event_id, "ends_at": m.ends_at} for m in sorted(rows, key=lambda m: m.ends_at)[:limit]]

    def calendar_connection(self, org_id, provider="google"):
        row = self.calendar_conns.get((org_id, provider))
        return dict(row) if row else None

    def save_calendar_connection(self, org_id, provider, account_email, refresh_token_enc, scopes, connected_by):
        self.calendar_conns[(org_id, provider)] = {"org_id": org_id, "provider": provider, "account_email": account_email,
                                                   "refresh_token_enc": refresh_token_enc, "scopes": scopes,
                                                   "connected_by": connected_by, "connected_at": datetime.now(timezone.utc)}

    def delete_calendar_connection(self, org_id, provider="google"):
        return self.calendar_conns.pop((org_id, provider), None) is not None

    def scheduling_link(self, user_id, provider="calendly"):
        row = self.sched_links.get((user_id, provider))
        return row["url"] if row else None

    def set_scheduling_link(self, org_id, user_id, provider, url):
        if url:
            self.sched_links[(user_id, provider)] = {"org_id": org_id, "url": url}
        else:
            self.sched_links.pop((user_id, provider), None)

    def scheduling_links(self, project_id, provider="calendly"):
        members = self.project_members.get(project_id, set())
        return {uid: row["url"] for (uid, prov), row in self.sched_links.items() if prov == provider and uid in members}

    def _chats(self, project_id, user_id, limit):
        rows = [c for c in self.chats.values() if c["project_id"] == project_id and c["user_id"] == user_id]
        return [{"id": c["id"], "message_id": c["id"], "question": c["question"], "stale": c["stale"], "feedback": c.get("feedback"),
                 "created_at": c.get("created_at"), **c["result"].model_dump()} for c in rows][-limit:]

    def chat_history(self, project_id, user_id, limit=50):
        """With `expert` on a question the assistant handed to a person: the request for this message (or, for the same
        question asked again, the earlier one), answered first: {status, name, answer, answered_at}."""
        out = self._chats(project_id, user_id, limit)
        for row in out:
            row["expert"] = self._expert_for(row) if row["status"] == "not_enough_information" else None
        return out

    def _expert_for(self, chat):
        q = norm_question(chat["question"])
        mine = [r for r in self.expert_requests.values() if str(r["project_id"]) == str(self.chats[chat["id"]]["project_id"])
                and r["asked_by"] == self.chats[chat["id"]]["user_id"] and r["status"] in {"open", "answered"}
                and (r["message_id"] == chat["id"] or norm_question(r["question"]) == q)]
        if not mine:
            return None
        r = max(mine, key=lambda r: (r["status"] == "answered", r["message_id"] == chat["id"], r["created_at"]))
        expert = self.users.get(r["expert_id"])
        return {"status": r["status"], "name": expert.name if expert else None, "answer": r["answer"], "answered_at": r.get("answered_at")}

    def chat_histories(self, project_id, user_ids, limit=50):
        return {u: self._chats(project_id, u, limit) for u in dict.fromkeys(user_ids)}

    def notifications_for(self, user_id):
        return [n for n in reversed(self.notifications) if n["user_id"] == user_id][:50]

    def expert_requests_for(self, expert_id):
        return [r for r in self.expert_requests.values() if r["expert_id"] == expert_id and r["status"] == "open"]

    def open_expert_requests(self, project_id):
        return [r for r in self.expert_requests.values() if str(r.get("project_id")) == str(project_id) and r["status"] == "open"]

    def expert_requests_by(self, project_id, asked_by):
        rows = [r for r in self.expert_requests.values() if str(r.get("project_id")) == str(project_id) and r["asked_by"] == asked_by]
        return [{**r, "expert_name": e.name if (e := self.users.get(r["expert_id"])) else None}
                for r in sorted(rows, key=lambda r: r["created_at"])]

    @contextmanager
    def reading(self):
        yield  # Postgres runs a page's reads in one transaction; memory has nothing to share

    def answer_expert_request(self, request_id, answer):
        r = self.expert_requests[request_id]
        r.update(status="answered", answer=answer, answered_at=datetime.now(timezone.utc))
        return r

    def list_runs(self, project_id, limit=50):
        return [r for r in reversed(self.runs) if r["project_id"] == project_id][:limit]

    def get_run(self, run_id):
        return next((r for r in self.runs if r["id"] == run_id), None)

    # ---- accounts ----
    def user_by_firebase(self, uid):
        return self.users.get(self.firebase.get(uid, ""))

    def _live_invites(self, email, org_id=None):
        now = datetime.now(timezone.utc)
        return sorted((i for i in self.invites.values() if i["email"] == email.lower() and not i["accepted_at"] and i["expires_at"] > now
                       and (org_id is None or i["org_id"] == org_id)), key=lambda i: i["created_at"], reverse=True)

    def pending_invitations(self, email):
        out = {}
        for i in self._live_invites(email):  # newest first: the first seen per org sets the role
            inviter = self.users.get(i["invited_by"] or "")
            row = out.setdefault(i["org_id"], {"org_id": i["org_id"], "org_name": self.orgs.get(i["org_id"], ""), "role": i["role"],
                                               "invited_by_name": getattr(inviter, "name", None), "invited_by_email": getattr(inviter, "email", None),
                                               "org_created_at": self.org_created.get(i["org_id"], date.today()).isoformat(), "projects": []})
            if i["project_id"] in self.projects:
                row["projects"].append(self.projects[i["project_id"]].name)
        return sorted(out.values(), key=lambda r: r["org_name"])

    def accept_invitations(self, uid, email, name, org_id):
        if uid in self.firebase:
            raise ValueError("login already has an account")
        live = self._live_invites(email, org_id)
        if not live:
            return None
        newest = live[0]
        user = next((u for u in self.users.values() if u.org_id == org_id and u.email.lower() == email.lower()), None)
        if user and user.id in self.firebase.values():
            return None
        if not user:
            user = self.add_user(User(id=new_id(), org_id=org_id, email=email.lower(), name=newest["name"] or name or email.split("@")[0],
                                      role=newest["role"], github_login=newest["github_login"]))
        else:  # someone coming back: the new invitation sets the role, not the one they had
            user = self.users[user.id] = user.model_copy(update={"role": newest["role"], "name": newest["name"] or user.name,
                                                                   "github_login": newest["github_login"] or user.github_login})
            self.left.discard(user.id)
        self.firebase[uid] = user.id
        for i in live:
            if i["project_id"]:
                self.project_members[i["project_id"]].add(user.id)
            i["accepted_at"] = datetime.now(timezone.utc)
        return user

    def create_organization(self, uid, email, name, org_name, project_name):
        if uid in self.firebase:
            raise ValueError("login already has an account")
        org = new_id()
        self.orgs[org] = org_name
        self.org_created[org] = date.today()
        user = self.add_user(User(id=new_id(), org_id=org, email=email.lower(), name=name, role="admin"))
        self.firebase[uid] = user.id
        self.create_project(org, project_name, "", user.id)
        return user

    def org_name(self, org_id):
        return self.orgs.get(org_id, "")

    # ---- projects, membership, invitations ----
    def projects_for(self, user):
        if user.role == "admin":
            return sorted((p for p in self.projects.values() if p.org_id == user.org_id), key=lambda p: p.name)
        return sorted((p for p in self.projects.values() if user.id in self.project_members[p.id]), key=lambda p: p.name)

    def create_project(self, org_id, name, description, creator_id):
        p = self.add_project(Project(id=new_id(), org_id=org_id, name=name, description=description))
        self.project_members[p.id].add(creator_id)
        return p

    def is_member(self, project_id, user_id):
        return user_id in self.project_members[project_id]

    def add_member(self, project_id, user_id):
        self.project_members[project_id].add(user_id)

    def user_by_email(self, org_id, email):
        return next((u for u in self.users.values() if u.org_id == org_id and u.email.lower() == email.lower() and u.id not in self.left), None)

    def create_invitation(self, org_id, project_id, email, role, name, github_login, invited_by):
        """Re-inviting updates the open invitation (same organisation, email and project) and makes it the newest."""
        now = datetime.now(timezone.utc)
        old = next((i for i in self.invites.values() if (i["org_id"], i["email"], i["project_id"]) == (org_id, email.lower(), project_id)
                    and not i["accepted_at"]), None)
        inv = {"id": old["id"] if old else new_id(), "org_id": org_id, "project_id": project_id, "email": email.lower(), "role": role,
               "name": name, "github_login": github_login, "invited_by": invited_by, "accepted_at": None, "created_at": now,
               "expires_at": now + timedelta(days=14)}
        self.invites[inv["id"]] = inv
        return inv

    def invitations(self, project_id):
        return [i for i in self.invites.values() if i["project_id"] == project_id and not i["accepted_at"]]

    def revoke_invitation(self, invitation_id, project_id):
        inv = self.invites.get(invitation_id)
        if not inv or inv["project_id"] != project_id or inv["accepted_at"]:
            return False
        return self.invites.pop(invitation_id) is not None

    def leave_organization(self, user_id):
        user = self.users.get(user_id)
        logins = set(self.firebase.values())
        bound = [u for u in self.users.values() if user and u.org_id == user.org_id and u.id in logins]
        if {u.id for u in bound if u.role == "admin" and u.status == "active"} == {user_id} and len(bound) > 1:
            return False
        self.firebase = {uid: u for uid, u in self.firebase.items() if u != user_id}
        self.left.add(user_id)
        for members in self.project_members.values():
            members.discard(user_id)
        return True

    # ---- members administration ----
    def _members_of(self, org_id):
        """Users of the organisation with a login bound: not someone who left, nor someone enrolled who has not joined (both
        are in `left`, as a row with no firebase_uid is in Postgres)."""
        return [u for u in self.users.values() if u.org_id == org_id and u.id not in self.left]

    def _member_row(self, u):
        projects = sorted((p for p in self.projects.values() if p.org_id == u.org_id and u.id in self.project_members[p.id]),
                          key=lambda p: (p.name, p.id))
        return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "status": u.status,
                "projects": [{"id": p.id, "name": p.name} for p in projects]}

    def org_members(self, org_id):
        return [self._member_row(u) for u in sorted(self._members_of(org_id), key=lambda u: (u.name, u.id))]

    def org_member(self, org_id, user_id):
        u = next((u for u in self._members_of(org_id) if u.id == user_id), None)
        return self._member_row(u) if u else None

    def _change_member(self, org_id, user_id, **update):
        """One member's role or status, refused (LastAdmin) if no active admin would be left."""
        with self._guard:
            members = self._members_of(org_id)
            user = next((u for u in members if u.id == user_id), None)
            if user is None:
                return None
            after = user.model_copy(update=update)
            admins = {u.id for u in members if u.role == "admin" and u.status == "active"}
            if user.id in admins and (after.role != "admin" or after.status != "active") and len(admins) == 1:
                raise ports.LastAdmin(user_id)
            self.users[user_id] = after
            return after

    def set_member_role(self, org_id, user_id, role):
        return self._change_member(org_id, user_id, role=role)

    def set_member_status(self, org_id, user_id, status):
        return self._change_member(org_id, user_id, status=status)

    def find_person(self, org_id, email):
        return next((u for u in self.users.values() if u.org_id == org_id and u.email.lower() == email.strip().lower()), None)

    def erase_person(self, org_id, user_id):
        user = self.users.get(user_id)
        if user is None or user.org_id != org_id:
            return {}
        email = user.email.lower()
        chats = [k for k, m in self.chats.items() if m["user_id"] == user_id]
        asked = [k for k, r in self.expert_requests.items() if r.get("asked_by") == user_id]
        for k in chats:
            del self.chats[k]
        for k in asked:
            del self.expert_requests[k]
        before = len(self.notifications)
        self.notifications = [n for n in self.notifications if n["user_id"] != user_id]
        links = [k for k in self.sched_links if k[0] == user_id]
        for k in links:
            del self.sched_links[k]
        invites = [k for k, i in self.invites.items() if i["org_id"] == org_id and i["email"] == email]
        for k in invites:
            del self.invites[k]
        self.firebase = {uid: u for uid, u in self.firebase.items() if u != user_id}
        self.left.add(user_id)
        for members in self.project_members.values():
            members.discard(user_id)
        # the reasons they wrote when asking for a recording to be removed are their own words: blanked, the rows stay
        reasons = [r for r in self.__dict__.get("recording_removal_rows", {}).values()
                   if r.get("org_id") == org_id and r.get("requested_by") == user_id and r.get("reason")]
        for r in reasons:
            r["reason"] = ""
        self.users[user_id] = user.model_copy(update={"name": "Erased person", "email": f"erased-{user_id}@erased.invalid",
                                                      "github_login": None, "status": "deactivated"})
        return {"chat_messages": len(chats), "expert_requests": len(asked), "notifications": before - len(self.notifications),
                "scheduling_links": len(links), "invitations": len(invites), "recording_removal_reasons": len(reasons)}

    def set_github_login(self, org_id, user_id, login):
        user = self.users.get(user_id)
        if user is None or user.org_id != org_id:
            return None
        self.users[user_id] = user.model_copy(update={"github_login": login})
        return self.users[user_id]

    # ---- documents ----
    def documents(self, project_id):
        return [d for d in self.document_rows.values() if d["project_id"] == project_id]

    def update_document(self, document_id, **fields):
        if document_id in self.document_rows:
            self.document_rows[document_id].update(fields)

    def get_document(self, document_id):
        return self.document_rows.get(document_id)

    def delete_document(self, document_id):
        self.document_rows.pop(document_id, None)

    # ---- approvals, experts, notifications, feedback ----
    def claim_approval(self, approval_id, decision=None):
        a = self.approval_rows.get(approval_id)
        if not a or a.status != "pending" or approval_id in self.approval_claims:
            return False
        self.approval_claims.add(approval_id)
        if decision is not None and a.decision is None:
            self.approval_rows[approval_id] = a.model_copy(update={"decision": decision})
        return True

    def release_approval(self, approval_id):
        self.approval_claims.discard(approval_id)

    def get_expert_request(self, request_id):
        return self.expert_requests.get(request_id)

    def mark_notifications_read(self, user_id, ids):
        n = 0
        for i, row in enumerate(self.notifications):
            if row["user_id"] == user_id and not row.get("read_at") and (ids is None or str(i) in ids):
                row["read_at"] = datetime.now(timezone.utc)
                n += 1
        return n

    def chat_feedback(self, message_id, user_id, value):
        c = self.chats.get(message_id)
        if not c or c["user_id"] != user_id:
            return False
        c["feedback"] = value or None  # 0 takes a vote back
        return True

    def audit(self, org_id, actor_id, action, target, detail=None):
        self.audit_rows.append({"org_id": org_id, "actor_id": actor_id, "action": action, "target": target, "detail": detail or {}})

    # ---- legal acceptances (append-only, as in 019_legal.sql) ----
    def record_legal_acceptance(self, org_id, user_id, document, version, method, ip_prefix=None, user_agent=None, at=None):
        with self._guard:
            for r in self.legal_rows:
                if r["document"] == document and r["version"] == version and (
                        r["org_id"] == org_id if document == "dpa" else r["user_id"] == user_id):
                    return None
            row = {"id": len(self.legal_rows) + 1, "org_id": org_id, "user_id": user_id, "document": document, "version": version,
                   "method": method, "accepted_at": at or datetime.now(timezone.utc), "ip_prefix": ip_prefix, "user_agent": user_agent}
            self.legal_rows.append(row)
            return dict(row)

    def legal_acceptances(self, org_id, user_id=None, document=None):
        rows = [dict(r) for r in self.legal_rows if r["org_id"] == org_id and (user_id is None or r["user_id"] == user_id)
                and (document is None or r["document"] == document)]
        return sorted(rows, key=lambda r: (r["accepted_at"], r["id"]), reverse=True)

    # ---- installations and pre-tenant lookups ----
    def record_installation_sender(self, installation_id, sender_id, sender_login, account_login):
        self.installation_senders[str(installation_id)] = {"sender_id": int(sender_id), "sender_login": sender_login, "account_login": account_login}

    def installation_sender(self, installation_id):
        return self.installation_senders.get(str(installation_id))

    def unbind_installation(self, installation_id):
        self.installations.pop(str(installation_id), None)
        self.installation_senders.pop(str(installation_id), None)

    def org_installations(self):
        """The calling organisation's installations only, as row-level security gives Postgres: the tenant the request set
        (app/api/auth.py _set_tenant). No tenant set: none."""
        org = _current_tenant()
        return [{"installation_id": k, "account_login": v["account"]} for k, v in self.installations.items()
                if org is not None and v["org_id"] == org]

    def organization_exists(self):
        """Any organisation at all (a self-hosted server lets sign-up create only the first one)."""
        return bool(self.orgs)

    def installation_org(self, installation_id):
        return (self.installations.get(installation_id) or {}).get("org_id")

    def bind_installation(self, installation_id, org_id, account, user_id):
        self.installations.setdefault(installation_id, {"org_id": org_id, "account": account, "connected_by": user_id})
        return self.installations[installation_id]["org_id"] == org_id

    def repos_by_full_name(self, full_name):
        return [{"id": r.id, "org_id": self.projects[r.project_id].org_id, "project_id": r.project_id, "installation_id": r.installation_id}
                for r in self.repos.values() if r.full_name.lower() == full_name.lower()]

    def watched_repositories(self):
        return [{"id": r.id, "org_id": self.projects[r.project_id].org_id, "project_id": r.project_id, "full_name": r.full_name,
                 "default_branch": r.default_branch, "last_indexed_sha": r.last_indexed_sha, "status": r.status}
                for r in self.repos.values() if not r.installation_id]

    def org_of_project(self, project_id):
        p = self.projects.get(project_id)
        return p.org_id if p else None

    def claim_meeting_recording(self, meeting_id):
        with self._guard:
            m = self.meetings.get(meeting_id)
            if not m or m.status != "scheduled":
                return False
            self.update_meeting(meeting_id, status="live", started_at=m.started_at or datetime.now(timezone.utc))
            return True

    # ---- outbox ----
    def outbox_add(self, topic, event):
        old = next((k for k, r in self.outbox.items() if event.get("dedupe") and r["event"].get("dedupe") == event["dedupe"]), None)
        if old and not self.outbox[old]["sent_at"]:
            return old
        oid = old or new_id()  # a key sent already is a deliberate re-send: due again
        self.outbox[oid] = {"id": oid, "topic": topic, "event": event, "attempts": 0, "sent_at": None, "last_error": None,
                            "next_attempt_at": datetime.now(timezone.utc) + timedelta(minutes=1)}
        return oid

    def outbox_due(self, limit=50, skip=()):
        now = datetime.now(timezone.utc)
        due = sorted((r for r in self.outbox.values() if not r["sent_at"] and r["next_attempt_at"] <= now and r["id"] not in skip),
                     key=lambda r: r["next_attempt_at"])[:limit]
        for r in due:
            r["next_attempt_at"] = now + timedelta(seconds=min(3600, 30 * 2 ** min(r["attempts"], 7)))
            r["attempts"] += 1
        return [{"id": r["id"], "topic": r["topic"], "event": r["event"]} for r in due]

    def outbox_sent(self, outbox_id):
        self.outbox[outbox_id].update(sent_at=datetime.now(timezone.utc), last_error=None)

    def outbox_failed(self, outbox_id, error):
        self.outbox[outbox_id]["last_error"] = error

    def outbox_attempts(self, outbox_id):
        return self.outbox[outbox_id]["attempts"]

    def ping(self):
        return True


class LocalRepoSource:
    """Reads a repository from a folder. Lets the whole golden path run offline against `sample_repo/`."""

    def __init__(self, root: str, issues: list[dict] | None = None, prs: dict[int, list[dict]] | None = None,
                 default_pr: list[dict] | None = None):
        self.root = Path(root)
        self.issues = issues or []
        self.prs = prs or {}
        self.default_pr = default_pr or []  # demo: the files of any pull request not in `prs`, so every PR has a diff to review

    def files(self, full_name, ref, installation_id=None):
        out = {}
        for p in self.root.rglob("*"):
            if p.is_file():
                rel = p.relative_to(self.root).as_posix()
                if should_index(rel, p.stat().st_size) or rel.endswith("CODEOWNERS"):
                    out[rel] = p.read_text(errors="replace")
        return out

    def file(self, full_name, path, ref, installation_id=None):
        p = self.root / path
        return p.read_text(errors="replace") if p.exists() else None

    def pr_files(self, full_name, number, installation_id=None):
        return self.prs.get(number, self.default_pr)

    def check_runs(self, full_name, sha, installation_id=None):
        return []

    def good_first_issues(self, full_name, installation_id=None):
        return self.issues

    def repo_info(self, full_name, installation_id=None):
        return {"full_name": full_name, "default_branch": "main", "private": False}

    def head_sha(self, full_name, ref, installation_id=None):
        """A stable stand-in for the branch head: it changes exactly when the folder's contents do."""
        h = hashlib.sha1()
        for path, text in sorted(self.files(full_name, ref).items()):
            h.update(path.encode() + b"\0" + text.encode() + b"\0")
        return h.hexdigest()

    def installation_repositories(self, installation_id):
        return [{"full_name": "demo/mini-policy-engine", "default_branch": "main", "private": False}]


class LocalBlobStorage:
    def __init__(self, root: str = ".blobs"):
        self.root = root

    def _path(self, uri: str) -> str:
        root = os.path.realpath(self.root)
        path = os.path.realpath(os.path.join(root, uri.replace("gs://", "").replace("local://", "")))
        if not path.startswith(root + os.sep):
            raise ValueError(f"Path escapes storage root: {uri}")
        return path

    def get(self, uri):
        with open(self._path(uri), "rb") as f:
            return f.read()

    def put(self, uri, data, content_type="application/octet-stream"):
        path = self._path(uri)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return uri

    def put_file(self, uri, src, content_type="application/octet-stream"):
        """Copy a (large) file object in, block by block: a call recording never sits in memory whole."""
        import shutil

        path = self._path(uri)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            shutil.copyfileobj(src, f, 1 << 20)
        return uri

    def path(self, uri) -> str:
        return self._path(uri)

    def delete(self, uri):
        """Remove the file, and the folders it leaves empty (up to the storage root)."""
        path = self._path(uri)
        try:
            os.remove(path)
        except FileNotFoundError:
            return False
        root, folder = os.path.realpath(self.root), os.path.dirname(path)
        while folder != root and folder.startswith(root + os.sep):
            try:
                os.rmdir(folder)
            except OSError:  # not empty: other files live here
                break
            folder = os.path.dirname(folder)
        return True


class ServedLocalStorage(LocalBlobStorage):
    """Self-hosted mode's file storage: files on disk, and short-lived signed links (GET /api/blobs/<token>) so the browser can
    play a call recording without its sign-in header, as Cloud Storage's signed URLs do on Google Cloud."""

    def __init__(self, root: str, secret: bytes):
        super().__init__(root)
        self.secret = secret

    def _sign(self, payload: bytes) -> str:
        import base64
        import hmac

        return base64.urlsafe_b64encode(hmac.new(self.secret, payload, hashlib.sha256).digest()).decode().rstrip("=")

    def signed_url(self, uri: str, minutes: int = 60) -> str:
        import base64
        import time

        payload = f"{uri}|{int(time.time()) + minutes * 60}".encode()
        return f"/api/blobs/{base64.urlsafe_b64encode(payload).decode().rstrip('=')}.{self._sign(payload)}"

    def verify(self, token: str) -> str | None:
        """The blob a signed link names, or None if the link is forged, altered or expired."""
        import base64
        import hmac
        import time

        try:
            body, sig = token.split(".", 1)
            payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            uri, exp = payload.decode().rsplit("|", 1)
        except (ValueError, UnicodeDecodeError):
            return None
        if not hmac.compare_digest(self._sign(payload), sig) or int(exp) < time.time():
            return None
        return uri


def signing_key(data_dir: str, configured: str = "") -> bytes:
    """The key signed links are made with: BLOB_SIGNING_KEY, or one generated once and kept in the data folder."""
    import secrets

    if configured:
        return configured.encode()
    path = os.path.join(data_dir, ".signing-key")
    if not os.path.exists(path):
        os.makedirs(data_dir, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    with open(path) as f:
        return f.read().strip().encode()


class MemoryScheduler:
    """The Scheduler port in memory (demo and tests). Books Meet-style links that are stable for a session
    (https://meet.google.com/xxx-xxxx-xxx from its id) and cancels them. With `finds_recordings` (the demo) every booked
    session "has" a recording, so the demo plays the whole flow without keys: import, notes, KT links. Tests add
    recordings one at a time with add_recording instead. A session that has not started yet has no recording, even in the demo."""

    provider = "google"
    DEMO_VIDEO = b"\x00\x00\x00\x18ftypmp42 welcome-wave demo recording"

    def __init__(self, finds_recordings: bool = False):
        self.finds_recordings = finds_recordings
        self.events: dict[str, ports.CalendarEvent] = {}
        self.cancelled: list[str] = []
        self.recordings: dict[str, tuple[ports.RecordingFile, bytes]] = {}  # event id -> (file, bytes)
        self.transcripts: dict[str, str] = {}  # event id -> the transcript text Meet saved beside the recording
        self._lock = threading.Lock()

    @staticmethod
    def meet_link(meeting_id: str) -> str:
        h = hashlib.sha256(meeting_id.encode()).digest()
        letters = "".join("abcdefghijklmnopqrstuvwxyz"[b % 26] for b in h[:10])
        return f"https://meet.google.com/{letters[:3]}-{letters[3:7]}-{letters[7:]}"

    def create_event(self, event):
        event_id = "mem-" + hashlib.sha256(event.meeting_id.encode()).hexdigest()[:16]
        with self._lock:
            self.events[event_id] = event
        return ports.CalendarBooking(event_id=event_id, join_url=self.meet_link(event.meeting_id))

    def cancel_event(self, event_id):
        with self._lock:
            if self.events.pop(event_id, None) is not None:
                self.cancelled.append(event_id)

    def add_recording(self, event_id: str, data: bytes, duration_s: float | None = None, mime: str = "video/mp4"):
        self.recordings[event_id] = (ports.RecordingFile(file_id=f"file-{event_id}", name="Recording.mp4", mime=mime,
                                                         duration_s=duration_s, size=len(data)), data)

    def add_transcript(self, event_id: str, text: str):
        """Tests: Meet saved this transcript beside the recording of the event."""
        self.transcripts[event_id] = text

    def find_transcript(self, event_id):
        return ports.TranscriptFile(file_id=f"transcript-{event_id}", name="Transcript") if event_id in self.transcripts else None

    def fetch_transcript(self, file, max_bytes):
        text = next((t for e, t in self.transcripts.items() if f"transcript-{e}" == file.file_id), None)
        if text is None:
            raise ports.RecordingNotShared()
        if len(text.encode()) > max_bytes:
            raise ValueError("The transcript is larger than the limit")
        return text

    def find_recording(self, event_id):
        if event_id in self.recordings:
            return self.recordings[event_id][0]
        ev = self.events.get(event_id)
        if not (self.finds_recordings and ev):
            return None
        starts = ev.starts_at if ev.starts_at.tzinfo else ev.starts_at.replace(tzinfo=timezone.utc)
        if starts > datetime.now(timezone.utc):
            return None  # a session that has not started has nothing recorded: no stand-in video for it
        length = max(60.0, (ev.ends_at - ev.starts_at).total_seconds())
        return ports.RecordingFile(file_id=f"demo-{event_id}", name=f"{ev.title}.mp4", duration_s=length, size=len(self.DEMO_VIDEO))

    def download(self, file, sink, max_bytes=None):
        data = next((d for f, d in self.recordings.values() if f.file_id == file.file_id), self.DEMO_VIDEO)
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("The recording is larger than the upload limit")
        sink.write(data)
        return len(data)


class InlineBus:
    """Demo mode: an event is handled immediately in-process instead of going through Pub/Sub."""

    def __init__(self):
        self.handler = None
        self.published: list[tuple[str, dict]] = []

    def publish(self, topic, event):
        self.published.append((topic, event))
        if self.handler:
            self.handler(event)
