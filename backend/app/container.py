"""Wires adapters, agents and the Supervisor together for the chosen mode (demo, self_hosted or gcp)."""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.core.deps import Deps
from app.adapters.mailer import mailer_for
from app.agents.core.llm import SYNC_CALL_S, ClaudeLLM, GeminiLLM, HybridLLM, NoLLM, OfflineLLM, call_budget
from app.agents.supervisor.graph import SYNC_EVENTS, build_supervisor, resume, run_event
from app.config import Settings
from app.services.redaction import RegexRedactor

log = logging.getLogger("container")


class EventBusy(RuntimeError):
    """Another handler holds a live lease on this event. The worker nacks so Pub/Sub retries later."""


class ApprovalBusy(RuntimeError):
    """The approval is already decided, or a decision for it is being applied right now."""


class ApprovalStale(RuntimeError):
    """The paused run is not waiting for this approval any more (it was superseded); deciding it would apply to the wrong one."""


class ApprovalNotReady(RuntimeError):
    """The run that created this approval has not reached its pause yet. Nothing was changed; try again in a moment."""


class _Heartbeat:
    """Keeps an event lease alive while a long handler (a meeting video, a big repository) runs."""

    def __init__(self, store, key: str, every_s: float):
        self.store, self.key, self.every_s = store, key, every_s
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(self.every_s):
            try:
                self.store.touch_event(self.key)
            except Exception:  # noqa: BLE001 - a missed heartbeat only risks a duplicate, never a loss
                log.warning("heartbeat failed", exc_info=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()


@dataclass
class Container:
    settings: Settings
    deps: Deps
    graph: object  # checkpointed: events that may pause for a manager
    chat_graph: object = None  # same graph without a checkpointer: a chat answer never pauses, so it writes nothing
    checkpointer: object = None
    extras: dict = field(default_factory=dict)
    lifecycle: object = None  # ports.LifecycleStore: programs, plans, progress and reporting (app/lifecycle)

    def __post_init__(self):
        if self.lifecycle is not None and getattr(self.deps, "lifecycle", None) is None:
            self.deps.lifecycle = self.lifecycle  # the lifecycle agents reach it through their Deps

    @property
    def store(self):
        return self.deps.store

    @property
    def index(self):
        return self.deps.index

    def handle(self, event: dict) -> dict:
        """Run one event through the Supervisor, exactly once per dedupe key.

        The key is claimed before the run, kept alive by a heartbeat during it, and marked done only after it
        succeeds. If the process dies mid-run the lease expires and Pub/Sub's redelivery runs it again.
        """
        if event.get("type") in SYNC_EVENTS:  # answered while the caller waits; never paused, so never checkpointed
            with call_budget(SYNC_CALL_S):  # each structured model call fits the wait: one try per model, no backoff
                return run_event(self.chat_graph or self.graph, event, f"{event['type']}:{uuid.uuid4().hex}")
        key = event.get("dedupe")
        if not key:
            return self._run(event, f"{event['type']}:{uuid.uuid4().hex}")
        claim = self.store.claim_event(key, self.settings.event_lease_s)
        if claim == "done":
            return {"skipped": "duplicate"}
        if claim == "busy":
            raise EventBusy(key)
        try:
            with _Heartbeat(self.store, key, max(5.0, self.settings.event_lease_s / 3)):
                out = self._start(event, key)
        except BaseException:
            self.store.release_event(key)
            raise
        self.store.finish_event(key)
        return out

    def _mail(self, event: dict) -> dict | None:
        """`mail.*` events (invitation emails) are not an agent's work: the mailing service sends them, no run is recorded."""
        if str(event.get("type", "")).startswith("mail."):
            from app.services import mailing

            return mailing.handle(self, event)
        return None

    def _start(self, event: dict, thread_id: str) -> dict:
        """Run the event, or pick up what an earlier delivery left on its thread. A run parked at a manager approval is
        left alone (deciding the approval resumes it). A run that stopped mid-graph (pod killed, a node raised) is
        continued from its last checkpoint: never marked done unrun, never restarted on top of its saved state."""
        mailed = self._mail(event)
        if mailed is not None:
            return mailed
        if self.checkpointer:
            cfg = {"configurable": {"thread_id": thread_id}}
            state = self.graph.get_state(cfg)  # a failure propagates: the event is released and retried, not run blind
            if any(t.interrupts for t in state.tasks):
                return {"skipped": "waiting for approval"}
            if state.next:
                log.info("continuing an interrupted run from its last checkpoint: %s", thread_id)
                out = self.graph.invoke(None, cfg)
                self._forget_if_finished(thread_id)
                return out
            if state.values:  # a finished run whose cleanup failed: start clean, not on top of its leftover state
                self.checkpointer.delete_thread(thread_id)
        return self._run(event, thread_id)

    def _run(self, event: dict, thread_id: str) -> dict:
        mailed = self._mail(event)
        if mailed is not None:
            return mailed
        out = run_event(self.graph, event, thread_id)
        self._forget_if_finished(thread_id)
        return out

    def decide(self, approval, decision: dict) -> dict:
        """Resume the run paused at a manager approval. A claim makes a double click a no-op, not a double resume.

        The claim also stores the decision, and the resume always uses the stored one: once a resume has reached the
        graph, LangGraph replays that first value if the run fails, so a retry with a different decision would be
        reported but not applied. A retry therefore re-applies the first decision, which is what the graph does."""
        state = self.graph.get_state({"configurable": {"thread_id": approval.thread_id}})
        pauses = self._pauses(state)
        waiting = set(pauses)
        if not waiting and (state.next or self.store.event_status(approval.thread_id) == "busy"):
            raise ApprovalNotReady(approval.id)  # the run created the approval but has not paused on it yet
        if not self.store.claim_approval(approval.id, decision):
            current = self.store.get_approval(approval.id)
            if current and current.status == "rejected" and not current.decided_by:
                raise ApprovalStale(approval.id)  # superseded by a newer review of the same task
            raise ApprovalBusy(approval.id)
        try:
            if approval.id not in waiting:  # the run finished, or it is paused on a different approval
                self.store.decide_approval(approval.id, "rejected", decision.get("user_id", ""))
                raise ApprovalStale(approval.id)
            # a run parked on several proposals (one per KT call and speaker) is resumed for this one only
            iid = pauses[approval.id] if len(pauses) > 1 else None
            out = resume(self.graph, approval.thread_id, self.store.get_approval(approval.id).decision or decision, iid)
        except BaseException:
            self.store.release_approval(approval.id)
            raise
        self._forget_if_finished(approval.thread_id)
        mine = next((d for d in out.get("decided") or [] if d.get("approval_id") == approval.id), None)
        return {**out, "result": mine["result"]} if mine else out

    @staticmethod
    def _waiting_for(state) -> set[str]:
        """Approval ids the paused run's interrupts are waiting on."""
        return set(Container._pauses(state))

    @staticmethod
    def _pauses(state) -> dict[str, str]:
        """Approval id -> the id of the interrupt waiting on it."""
        return {i.value.get("approval_id"): i.id for t in state.tasks for i in (t.interrupts or ()) if isinstance(i.value, dict)}

    def tick(self, org_id: str, project_id: str | None = None, day=None, force: bool = False) -> dict:
        """The daily tick of one organisation (app/agents/supervisor/tick.py)."""
        from app.agents.supervisor import tick

        return tick.run(self, org_id, project_id, day, force)

    def _forget_if_finished(self, thread_id: str) -> None:
        """Checkpoints exist so a run can pause and resume. Once a run ends, its checkpoints are dead weight."""
        if not self.checkpointer:
            return
        try:
            state = self.graph.get_state({"configurable": {"thread_id": thread_id}})
            if not state.next:
                self.checkpointer.delete_thread(thread_id)
        except Exception:  # noqa: BLE001 - cleanup must never fail the event
            log.warning("checkpoint cleanup failed", exc_info=True)


def _postgres(settings: Settings):
    """The database (Postgres + pgvector, row-level security per organisation) and LangGraph's checkpoints in it."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    from app.adapters.postgres import PgStore, make_engine

    engine = make_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    store = PgStore(engine)
    if store.role_can_bypass_rls():
        raise RuntimeError("The database role can bypass row-level security. Connect as ww_app (see app/migrate.py).")
    pool = ConnectionPool(conninfo=settings.database_url.replace("postgresql+psycopg://", "postgresql://"), min_size=1,
                          max_size=settings.checkpoint_pool_size, kwargs={"autocommit": True, "prepare_threshold": 0}, open=True)
    checkpointer = PostgresSaver(pool)  # tables are created by the migrator (app/migrate.py); the app role cannot create them
    return engine, store, pool, checkpointer


def llm_for(settings: Settings, local_files=None):
    """Build the runtime LLM: Claude as primary when ANTHROPIC_API_KEY is set, paired with Gemini (for video media and
    resilient fallback) when GEMINI_API_KEY or Vertex AI is also available."""
    claude = ClaudeLLM(settings) if settings.anthropic_api_key else None
    gemini = GeminiLLM(settings, local_files=local_files) if (settings.gemini_api_key or settings.mode == "gcp") else None
    if claude and gemini:
        return HybridLLM(claude, gemini)
    if claude:
        return claude
    if gemini:
        return gemini
    return OfflineLLM() if settings.is_demo else NoLLM()


def _self_hosted(settings: Settings) -> Container:
    """The real application on one machine: Postgres, Firebase sign-in, Claude/Gemini through an API key (or nothing, said plainly),
    files on disk, background work in this process (app/selfhost.py), repositories connected by URL and watched for changes."""
    from app.adapters.github import GitHubApp
    from app.adapters.github_url import GitHubByUrl, RepoRouter
    from app.adapters.lifecycle_pg import PgLifecycleStore
    from app.adapters.memory import LocalRepoSource, ServedLocalStorage, signing_key
    from app.adapters.postgres import PgIndex
    from app.seed import DEMO_ISSUES, DEMO_PR_FILES
    from app.selfhost import LocalQueue
    from app.services.embeddings import HashEmbedder, NoEmbedder, VertexEmbedder

    engine, store, pool, checkpointer = _postgres(settings)
    lifecycle = PgLifecycleStore(engine)
    blobs = ServedLocalStorage(settings.data_path, signing_key(settings.data_path, settings.blob_signing_key))
    app_client = GitHubApp(settings.github_app_id, settings.github_private_key) if settings.github_app_id and settings.github_private_key else None
    repo_root = Path(settings.demo_repo_path)
    if not repo_root.is_absolute():
        repo_root = Path(__file__).resolve().parents[1] / repo_root
    local_repo = LocalRepoSource(str(repo_root), issues=DEMO_ISSUES, default_pr=DEMO_PR_FILES) if settings.demo_showcase and repo_root.exists() else None
    embedder = VertexEmbedder(settings) if settings.gemini_api_key else (HashEmbedder(settings.embedding_dim) if settings.anthropic_api_key else NoEmbedder(settings.embedding_dim))
    deps = Deps(
        settings=settings,
        llm=llm_for(settings, local_files=blobs),
        index=PgIndex(engine, embedder),
        store=store,
        repos=RepoRouter(GitHubByUrl(settings.github_token), app_client, local=local_repo),
        blobs=blobs,
        redactor=RegexRedactor(),
        bus=LocalQueue(settings.local_workers),
        lifecycle=lifecycle,
        mailer=mailer_for(settings),
    )
    return Container(settings, deps, build_supervisor(deps, checkpointer), build_supervisor(deps, None), checkpointer,
                     extras={"engine": engine, "pool": pool}, lifecycle=lifecycle)


def _streams(container: Container, live) -> Container:
    """Agent steps stream through this process's live hub (topic agent:<run_id>, to the run's own user only; app/live.py)."""
    container.deps.live = live
    return container


def configure_tracing(settings: Settings) -> None:
    """LangSmith traces agent runs only when the organisation turned it on (Settings.langsmith_tracing). This is LangSmith's
    process-wide switch, and it wins over every LANGSMITH_* or LANGCHAIN_* environment variable: tracing off means off."""
    import langsmith

    langsmith.configure(enabled=bool(settings.langsmith_tracing))


def build_container(settings: Settings, serves_http: bool = True, live=None) -> Container:
    """`live` (app/live.py LiveHub): where the in-memory store announces its changes. Postgres announces its own."""
    problems = settings.production_problems(serves_http)
    if problems:
        raise RuntimeError("Unsafe configuration:\n- " + "\n- ".join(problems))
    configure_tracing(settings)
    if settings.is_self_hosted:
        return _streams(_self_hosted(settings), live)

    if settings.is_demo:
        from langgraph.checkpoint.memory import MemorySaver

        from app.adapters.lifecycle_memory import MemoryLifecycleStore
        from app.adapters.memory import InlineBus, LocalBlobStorage, LocalRepoSource, MemoryIndex, MemoryStore
        from app.seed import DEMO_ISSUES, DEMO_PR_FILES, seed
        from app.services.embeddings import HashEmbedder

        repo_root = Path(settings.demo_repo_path)
        if not repo_root.is_absolute():
            repo_root = Path(__file__).resolve().parents[1] / repo_root
        bus = InlineBus()
        store = core = MemoryStore()
        if live is not None:
            from app.live import LiveStore

            store = LiveStore(store, live)
        lifecycle = MemoryLifecycleStore(core, live.publish if live is not None else None)
        deps = Deps(
            settings=settings,
            llm=llm_for(settings),
            index=MemoryIndex(HashEmbedder(settings.embedding_dim)),
            store=store,
            repos=LocalRepoSource(str(repo_root), issues=DEMO_ISSUES, default_pr=DEMO_PR_FILES),
            blobs=LocalBlobStorage(".blobs"),
            redactor=RegexRedactor(),
            bus=bus,
            lifecycle=lifecycle,
            mailer=mailer_for(settings),  # nothing is sent unless SMTP_* is in the command's own environment, on purpose
        )
        saver = MemorySaver()
        container = Container(settings, deps, build_supervisor(deps, saver), build_supervisor(deps, None), saver, lifecycle=lifecycle)
        bus.handler = container.handle
        seed(deps, container.graph, container.lifecycle)
        return _streams(container, live)

    from app.adapters.gcp import GcsStorage, PubSubBus
    from app.adapters.github import GitHubApp
    from app.adapters.lifecycle_pg import PgLifecycleStore
    from app.adapters.postgres import PgIndex
    from app.services.embeddings import VertexEmbedder

    engine, store, pool, checkpointer = _postgres(settings)
    lifecycle = PgLifecycleStore(engine)
    deps = Deps(
        settings=settings,
        llm=GeminiLLM(settings),
        index=PgIndex(engine, VertexEmbedder(settings)),
        store=store,
        repos=GitHubApp(settings.github_app_id, settings.github_private_key),
        blobs=GcsStorage(settings.gcs_bucket),
        redactor=RegexRedactor(),
        bus=PubSubBus(settings.gcp_project, settings.pubsub_prefix),
        lifecycle=lifecycle,
        mailer=mailer_for(settings),
    )
    return _streams(Container(settings, deps, build_supervisor(deps, checkpointer), build_supervisor(deps, None), checkpointer,
                              extras={"engine": engine, "pool": pool}, lifecycle=lifecycle), live)
