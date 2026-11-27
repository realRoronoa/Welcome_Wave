"""Self-hosted mode's background work, inside the API process. On Google Cloud, Pub/Sub, the GKE workers, Cloud Scheduler
and GitHub's webhooks do this; on one machine these three do it.

LocalQueue - the outbox is the queue. An event is recorded first (as on Google Cloud) and handed to a worker thread, and its
             row is marked sent only once its handler succeeds. So an event whose run failed, or that a restart cut short,
             stays due, the pump hands it out again (30 s after a failure, doubling up to an hour) and the run goes on from
             its last checkpoint. After MAX_ATTEMPTS, or at once if the AI is not set up, the event is given up and logged.
             An event that ended the process itself (out of memory, killed) never reaches that check, so a row claimed more
             than MAX_ATTEMPTS times is given up when it comes round again, its repository, call or document marked failed
             and the project's managers told. An event that waits for its repository or its lease (another run holds it) is
             tried again BUSY_RETRY_S later from memory, not counted as an attempt. A worker survives any error, a database
             one included: the row stays due.

RepoWatcher - repositories connected by URL have no webhooks. Every REPO_POLL_S seconds it asks GitHub for each one's default
             branch head and pull requests, and publishes what the webhooks would have: repo.pushed (or a full re-index when
             GitHub cannot list the changes), pr.opened / pr.updated, pr.merged. Unchanged answers cost no API calls (ETags).
             A repository GitHub will not show (deleted, made private, the token lost access) is skipped and the others are
             still polled; after UNREADABLE_ROUNDS rounds in a row it is marked failed and its managers are told once.

RecordingPoller - every RECORDING_POLL_S seconds, KT sessions that have ended get their Google Drive recording imported
             (Google Calendar sessions), and sessions whose recording never came are marked ended (services/scheduling.py;
             on Google Cloud, Cloud Scheduler calls POST /internal/recordings/poll instead).

RetentionLoop - with RECORDING_RETENTION_DAYS set, raw recordings stored longer ago than that are removed once a day; their
             notes and transcripts stay (services/recordings.py; on Google Cloud the bucket's 30-day rule does it).

TickLoop   - the daily tick (app/agents/supervisor/tick.py): every TICK_EVERY_S seconds it offers each organisation with a program in
             progress its `lifecycle.tick` for today; the event's dedupe key runs it once per day, whichever round gets there
             first (on Google Cloud, Cloud Scheduler calls POST /internal/lifecycle/tick).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

from app.adapters.github import events_from_webhook
from app.adapters.github_url import GitHubByUrl, GitHubLimited, wait_hint

log = logging.getLogger("selfhost")
MAX_ATTEMPTS = 6
PUMP_EVERY_S = 5.0
BUSY_RETRY_S = 30.0  # an event whose repository or lease is busy is tried again this long after
MERGED_WITHIN = timedelta(days=7)  # merged pull requests older than this are history, not news
UNREADABLE_ROUNDS = 3  # rounds in a row GitHub refuses a repository (401, 404, 410, 451) before it is marked failed


def _set_org(org: str | None) -> None:
    try:
        from app.adapters.postgres import current_org

        current_org.set(org)
    except ImportError:
        pass


class LocalQueue:
    """The bus of self-hosted mode (see the module docstring). `owns_outbox` tells app/api/outbox.py to hand rows over instead
    of marking them sent on publish."""

    owns_outbox = True

    def __init__(self, workers: int = 3):
        self.workers = max(1, workers)
        self.container = None
        self.jobs: queue.Queue = queue.Queue()
        self.inflight: set[str] = set()
        self.waiting: dict[str, tuple[float, dict]] = {}  # busy events: row id -> (when to try again, the event)
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.threads: list[threading.Thread] = []

    # the EventBus port, for anything that publishes without the outbox (nothing does today)
    def publish(self, topic: str, event: dict) -> None:
        if self.container is None:
            raise RuntimeError("the local queue is not running")
        from app.api.outbox import publish

        publish(self.container, topic, event)

    def submit(self, row_id: str, topic: str, event: dict) -> None:
        with self.lock:
            if row_id in self.inflight or row_id in self.waiting:
                return
            self.inflight.add(row_id)
        self.jobs.put((row_id, event))

    def start(self, container) -> None:
        self.container = container
        for i in range(self.workers):
            t = threading.Thread(target=self._work, name=f"ww-job-{i}", daemon=True)
            t.start()
            self.threads.append(t)
        pump = threading.Thread(target=self._pump, name="ww-outbox", daemon=True)
        pump.start()
        self.threads.append(pump)

    def stop(self) -> None:
        self.stopping.set()  # running jobs end with the process; their rows stay due and are picked up after the restart

    def alive(self) -> bool:
        """Every worker thread and the pump are running (for a readiness check)."""
        return bool(self.threads) and all(t.is_alive() for t in self.threads)

    def _pump(self) -> None:
        while not self.stopping.wait(PUMP_EVERY_S):
            try:
                self.pump_once()
            except Exception:  # noqa: BLE001 - database briefly unreachable: the next tick tries again
                log.warning("outbox pump failed", exc_info=True)

    def pump_once(self) -> None:
        """Hand out the busy events whose wait is over, then claim due rows for the free workers. A claimed row that has been
        claimed more than MAX_ATTEMPTS times without finishing ended the process each time: it is given up, not run again."""
        now = time.monotonic()
        with self.lock:
            ready = [(rid, ev) for rid, (at, ev) in self.waiting.items() if at <= now]
            for rid, _ in ready:
                del self.waiting[rid]
        for rid, ev in ready:
            self.submit(rid, "", ev)
        with self.lock:
            skip = tuple(self.inflight | set(self.waiting))
            free = max(0, self.workers * 2 - len(self.inflight))
        if not free:
            return
        store = self.container.store
        _set_org(None)
        for row in store.outbox_due(free, skip=skip):
            rid = str(row["id"])
            if store.outbox_attempts(rid) > MAX_ATTEMPTS:
                self._give_up(rid, f"gave up: the process ended {MAX_ATTEMPTS} times while handling it", _fields(rid, row["event"]),
                              row["event"], f"The server stopped {MAX_ATTEMPTS} times while doing this, so Welcome Wave gave up")
                continue
            self.submit(rid, row["topic"], row["event"])

    def _work(self) -> None:
        while not self.stopping.is_set():
            try:
                row_id, event = self.jobs.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self.run(row_id, event)
            except Exception:  # noqa: BLE001 - never lose a worker: the row stays due and is handed out again
                log.exception("event handling failed", extra={"extra_fields": _fields(row_id, event)})
            finally:
                with self.lock:
                    self.inflight.discard(row_id)

    def run(self, row_id: str, event: dict) -> None:
        """Handle one event under its organisation; mark its row sent, leave it due, or give it up. What is recorded about a
        failure is the error's kind, never its text: a database error's message can quote a row (outbox has no row-level
        security and holds ids only)."""
        from app.container import EventBusy
        from app.ports import AIUnavailable, RepoBusy

        store = self.container.store
        fields = _fields(row_id, event)
        _set_org(None)
        try:
            org = store.org_of_project(event["project_id"]) if event.get("project_id") else None
            if not org:
                log.warning("event for unknown project dropped", extra={"extra_fields": fields})
                self._done(row_id, fields)
                return
            _set_org(org)
            out = self.container.handle(event)
        except (EventBusy, RepoBusy):
            log.info("event waits: its repository or lease is busy", extra={"extra_fields": fields})  # stays due
            self._later(row_id, event)
        except AIUnavailable as e:  # our own fixed sentence: it names no data
            self._try(self._give_up, row_id, str(e)[:500], fields, event, str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("event failed", extra={"extra_fields": fields})
            try:
                store.outbox_failed(row_id, type(e).__name__)
                if store.outbox_attempts(row_id) >= MAX_ATTEMPTS:
                    self._give_up(row_id, f"gave up after {MAX_ATTEMPTS} attempts: {type(e).__name__}", fields, event)
            except Exception:  # noqa: BLE001 - the row is due either way
                log.warning("could not record the failure", exc_info=True)
        else:
            if self._done(row_id, fields):
                log.info("event handled", extra={"extra_fields": {**fields, "result": _outcome(out)}})
        finally:
            _set_org(None)

    def _later(self, row_id: str, event: dict) -> None:
        with self.lock:
            self.waiting[row_id] = (time.monotonic() + BUSY_RETRY_S, event)

    def _done(self, row_id: str, fields: dict) -> bool:
        """Mark the row sent. If the database refuses, the row stays due: handled again, its dedupe key answers 'duplicate'."""
        return self._try(self.container.store.outbox_sent, row_id, fields=fields)

    def _try(self, fn, *args, fields: dict | None = None, **kw) -> bool:
        try:
            fn(*args, **kw)
            return True
        except Exception:  # noqa: BLE001
            log.warning("outbox update failed; the event stays due", extra={"extra_fields": fields or {}}, exc_info=True)
            return False

    def _give_up(self, row_id: str, why: str, fields: dict, event: dict | None = None, notice: str | None = None) -> None:
        """Mark the row sent with `why` as its last error, mark the event's work failed and tell its managers `notice`."""
        log.error("event given up", extra={"extra_fields": {**fields, "why": why}})
        self.container.store.outbox_sent(row_id)
        self.container.store.outbox_failed(row_id, why)
        if event:
            try:
                failed(self.container, event, notice or f"Welcome Wave gave up after {MAX_ATTEMPTS} tries")
            except Exception:  # noqa: BLE001 - the event is given up either way
                log.warning("could not mark the event's work failed", extra={"extra_fields": fields}, exc_info=True)
            finally:
                _set_org(None)


def _fields(row_id: str, event: dict) -> dict:
    return {"type": event.get("type"), "dedupe": event.get("dedupe"), "outbox_id": row_id}


def _outcome(out) -> str:
    """The run's outcome for the log: what it did or skipped, and an escalation by its kind only."""
    if not isinstance(out, dict):
        return ""
    result = out.get("result")
    if isinstance(result, dict) and result.get("escalated"):
        return "escalated to a person"
    return str(result or out.get("skipped") or "")[:300]


def failed(container, event: dict, why: str) -> str | None:
    """An event given up for good: mark what it was working on failed (a repository left 'indexing' would say so forever, and
    the watcher would start it again), and tell the project's managers in a fixed sentence (no error text, no content).
    Returns the notice, or None for events with nothing to mark."""
    store, pid, kind = container.store, event.get("project_id"), event.get("type")
    org = store.org_of_project(pid) if pid else None
    if not org:
        return None
    _set_org(org)
    link, what = "/runs", None
    if kind in {"repo.connected", "repo.pushed"} and event.get("repository_id"):
        repo = store.get_repo(event["repository_id"])
        if repo:
            store.update_repo(repo.id, status="failed")
            what = f"Indexing {repo.full_name} stopped. Re-index it from Settings, Repository, once the cause is fixed."
    elif kind == "meeting.recording_ready" and event.get("meeting_id"):
        m = store.get_meeting(event["meeting_id"])
        if m and m.status == "processing":
            store.update_meeting(m.id, status="failed")
            what, link = f"The notes of the KT session '{m.title}' could not be written. Add the recording again to retry.", \
                f"/meetings?call={m.id}"
    elif kind == "document.uploaded" and event.get("document_id"):
        store.update_document(event["document_id"], status="failed")
        what = "An uploaded document could not be read. Upload it again to retry."
    if not what:
        return None
    text = f"{why.rstrip('. ')}. {what}"
    for m in store.managers(pid):
        try:
            store.notify(m.id, "incident", text, f"{link}{'&' if '?' in link else '?'}project={pid}")
        except Exception:  # noqa: BLE001 - best effort, like every notification
            log.warning("notification failed", exc_info=True)
    return text


class RepoWatcher:
    """Polls GitHub for the repositories connected by URL (see the module docstring)."""

    def __init__(self, container, github: GitHubByUrl, every_s: int):
        self.container, self.github, self.every_s = container, github, every_s
        self.sent: set[str] = set()  # dedupe keys published by this process: a failing event is not re-published every poll
        self.unreadable: dict[str, int] = {}  # repository id -> rounds in a row its poll failed
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.every_s <= 0:
            return
        self.thread = threading.Thread(target=self._loop, name="ww-repo-watch", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()

    def _loop(self) -> None:
        wait = 10.0  # the first look soon after startup
        while not self.stopping.wait(wait):
            wait = float(self.every_s)
            try:
                self.poll()
            except Exception as e:  # noqa: BLE001 - GitHub's limit, or the database unreachable: the next round tries again
                log.warning("repository watch failed: %s", type(e).__name__ if not isinstance(e, GitHubLimited) else e)
                wait = max(wait, wait_hint(e))

    def poll(self) -> int:
        """One round over every watched repository. Returns how many events it published. One repository GitHub refuses (or
        any error of one) is logged and skipped: the others are still polled. GitHub's API limit stops the round (every
        repository would hit it), and the loop waits until it resets."""
        _set_org(None)
        published = 0
        for repo in self.container.store.watched_repositories():
            if str(repo.get("full_name") or "").lower() == "demo/mini-policy-engine":
                continue
            rid = str(repo["id"])
            _set_org(str(repo["org_id"]))
            try:
                published += self._branch(repo) + self._pulls(repo)
                self.unreadable.pop(rid, None)
            except GitHubLimited:
                raise
            except Exception as e:  # noqa: BLE001 - one repository must not stop the round
                self._refused(repo, e)
            finally:
                _set_org(None)
        return published

    def _refused(self, repo: dict, e: Exception) -> None:
        rid = str(repo["id"])
        n = self.unreadable[rid] = self.unreadable.get(rid, 0) + 1
        status = getattr(getattr(e, "response", None), "status_code", None)
        if n == 1 or n % 30 == 0:
            log.warning("repository poll failed", extra={"extra_fields": {"repository_id": rid, "error": type(e).__name__,
                                                                          "status": status, "rounds": n}})
        if status in {401, 404, 410, 451} and n == UNREADABLE_ROUNDS and repo.get("status") != "failed":
            self.container.store.update_repo(rid, status="failed")
            for m in self.container.store.managers(str(repo["project_id"])):
                try:
                    self.container.store.notify(
                        m.id, "incident", f"GitHub no longer shows {repo['full_name']} to Welcome Wave (it answered {status}). Check that "
                        "the repository still exists and that the server's GITHUB_TOKEN can read it, then re-index it from Settings.",
                        f"/runs?project={repo['project_id']}")
                except Exception:  # noqa: BLE001 - best effort
                    log.warning("notification failed", exc_info=True)

    def _publish(self, topic: str, event: dict) -> int:
        from app.api.outbox import publish

        key = event["dedupe"]
        if key in self.sent or self.container.store.event_status(key) != "none":
            return 0
        publish(self.container, topic, event)
        self.sent.add(key)
        if len(self.sent) > 20_000:
            self.sent.clear()
        return 1

    def _branch(self, repo: dict) -> int:
        if repo["status"] != "ready" or not repo["last_indexed_sha"]:  # being indexed, or failed (Re-index starts it again)
            return 0
        full, branch, rid = repo["full_name"], repo["default_branch"], str(repo["id"])
        head = self.github.branch_head(full, branch)
        if not head or head == repo["last_indexed_sha"]:
            return 0
        base = {"project_id": str(repo["project_id"]), "repository_id": rid, "full_name": full, "installation_id": None}
        paths = self.github.compare(full, repo["last_indexed_sha"], head)
        if paths is None:  # GitHub cannot list the changes: index the whole repository at the new head
            return self._publish("ingestion", {**base, "type": "repo.connected", "ref": branch, "dedupe": f"watch-index:{rid}:{head}"})
        return self._publish("ingestion", {**base, "type": "repo.pushed", "after": head, "branch": branch, "default_branch": branch,
                                           "changed_paths": paths, "removed_paths": [], "dedupe": f"push:{head}:{rid}"})

    def _pulls(self, repo: dict) -> int:
        full, rid, pid = repo["full_name"], str(repo["id"]), str(repo["project_id"])
        n = 0
        payloads = [{"action": "opened", "pull_request": pr} for pr in self.github.pulls(full, "open")]
        recent = datetime.now(timezone.utc) - MERGED_WITHIN
        for pr in self.github.pulls(full, "closed"):
            merged = _when(pr.get("merged_at"))
            if merged and merged > recent:
                payloads.append({"action": "closed", "pull_request": {**pr, "merged": True}})
        for payload in payloads:
            payload["repository"] = {"full_name": full}
            for ev in events_from_webhook("pull_request", payload, pid, rid):
                ev["dedupe"] = f"{ev['dedupe']}:{rid}"
                n += self._publish("reviews", ev)
        return n


class RecordingPoller:
    """Imports ended sessions' recordings and expires the ones that never got one (see the module docstring)."""

    def __init__(self, container, every_s: int):
        self.container, self.every_s = container, every_s
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.every_s <= 0:
            return
        self.thread = threading.Thread(target=self._loop, name="ww-recordings", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()

    def _loop(self) -> None:
        wait = 30.0  # a first look soon after startup
        while not self.stopping.wait(wait):
            wait = float(self.every_s)
            try:
                out = self.poll()
                if out["imported"] or out["ended"] or out["failed"]:
                    log.info("recording poll", extra={"extra_fields": out})
            except Exception as e:  # noqa: BLE001 - Google or the database unreachable: the next round tries again
                log.warning("recording poll failed: %s", e)

    def poll(self) -> dict:
        from app.services.scheduling import poll_recordings

        try:
            return poll_recordings(self.container)
        finally:
            _set_org(None)


class TickLoop:
    """The daily tick of every organisation (see the module docstring): checked hourly, run once a day each."""

    def __init__(self, container, every_s: int = 3600):
        self.container, self.every_s = container, every_s
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.every_s <= 0:
            return
        self.thread = threading.Thread(target=self._loop, name="ww-daily-tick", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()

    def _loop(self) -> None:
        wait = 120.0  # the first look a little after startup
        while not self.stopping.wait(wait):
            wait = float(self.every_s)
            try:
                self.poll()
            except Exception as e:  # noqa: BLE001 - the database unreachable: the next round tries again
                log.warning("daily tick failed: %s", e)

    def poll(self) -> list[dict]:
        from app.agents.supervisor import tick

        try:
            out = tick.run_all(self.container)
            if any(o.get("ran") or o.get("error") for o in out):
                log.info("daily tick", extra={"extra_fields": {"organisations": len(out), "ran": [o for o in out if o.get("ran")]}})
            return out
        finally:
            _set_org(None)


def _when(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def start(container) -> list:
    """Start self-hosted mode's background work; returns what to stop on shutdown."""
    bus = container.deps.bus
    started = []
    if isinstance(bus, LocalQueue):
        bus.start(container)
        started.append(bus)
    github = getattr(container.deps.repos, "by_url", None)
    if github is not None:
        watcher = RepoWatcher(container, github, container.settings.repo_poll_s)
        watcher.start()
        started.append(watcher)
    recordings = RecordingPoller(container, container.settings.recording_poll_s)
    recordings.start()
    started.append(recordings)
    from app.services import recordings as recording_rules

    started += recording_rules.on_start(container)  # RECORDING_RETENTION_DAYS: raw recordings removed after that many days
    ticks = TickLoop(container, container.settings.tick_every_s)
    ticks.start()
    started.append(ticks)
    return started


def stop(started: list) -> None:
    for s in started:
        s.stop()  # daemon threads: they see the flag, or end with the process
