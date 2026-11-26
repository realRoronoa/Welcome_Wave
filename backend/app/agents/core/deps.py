from __future__ import annotations

from dataclasses import dataclass

from app.agents.core.llm import LLM
from app.config import Settings
from app.ports import BlobStorage, EventBus, Index, LifecycleStore, Mailer, RepoSource, Store
from app.services.redaction import Redactor


@dataclass
class Deps:
    """Everything an agent may touch. Agents get nothing else - this is the tool allowlist."""

    settings: Settings
    llm: LLM
    index: Index
    store: Store
    repos: RepoSource  # read-only by construction: no write methods exist
    blobs: BlobStorage
    redactor: Redactor
    bus: EventBus
    # the onboarding lifecycle's store (programs, blockers, feedback, templates...): the lifecycle agents (Guide, Planner,
    # Scout, Coach, Keeper) read and propose through it. None where no lifecycle is wired (unit tests of the core agents).
    lifecycle: LifecycleStore | None = None
    # email (app/adapters/mailer.py): SMTP when configured, else a mailer that sends nothing. Agents do not send mail themselves;
    # the daily tick and the outbox's mail events do, through app/services/mailing.py.
    mailer: Mailer | None = None
    # app/live.LiveHub (or anything with publish(change)): an agent run's steps stream to its own user on topic
    # `agent:<run_id>` (runtime.publisher). None where nothing streams (workers, unit tests).
    live: object | None = None
