"""Interfaces between the agents and the outside world.

Agents only talk to these protocols. `adapters/memory.py` implements them in memory (demo + tests);
`adapters/postgres.py` implements them on Cloud SQL + pgvector (production). Swapping one for the other
never touches agent code - that is what keeps the agents unit-testable.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date, datetime, timezone
from typing import BinaryIO, Literal, Protocol

from pydantic import BaseModel, Field

from app.agents.core.schemas import Chunk, Hit, KTLink, MeetingResult, ProposedTask, QAResult, ReviewResult
from app.services.kt_linker import CodeRef

Role = Literal["manager", "new_hire", "expert", "admin", "executive"]  # executive: read-only, organisation-level reports
EventClaim = Literal["new", "busy", "done"]  # new: handle it; busy: another worker holds a live lease; done: already handled
EventStatus = Literal["none", "busy", "done"]  # none: never claimed (or released / lease lapsed); busy and done as above
TaskStatus = Literal["proposed", "approved", "in_progress", "submitted", "changes_requested", "done", "rejected"]
MemberStatus = Literal["active", "deactivated"]


# Index.code_chunks returns at most this many chunks, cut at a whole file: files late in path order are left out, never half
# a file. Knowledge risk, the KT planner and the code tour report the cut as `index_truncated`.
CODE_CHUNK_CAP = 50_000


class AIUnavailable(RuntimeError):
    """No model is set up on this server (self-hosted without GEMINI_API_KEY). Retrying does not help until an admin adds one."""


AI_OFF = "The AI is not set up on this server yet: an admin adds a Gemini API key (GEMINI_API_KEY) and restarts Welcome Wave."


class RepoBusy(RuntimeError):
    """Another ingestion of this repository is running. Nothing was changed; handle the event again later."""


class LastAdmin(RuntimeError):
    """The change would leave the organisation without an active admin. Nothing was changed."""


class SourceTooLarge(RuntimeError):
    """A repository is over an indexing limit while it is being read (before all of it is held in memory). The message says
    which limit and the setting that raises it; ingestion reports it as its own TooLarge."""


class TooLarge(RuntimeError):
    """Work over one of the indexing limits (the Librarian's, and a document's in app/services/documents.py). The message is
    written for the people it is shown to (managers): which limit, its value, the setting that raises it, and that nothing
    was changed. It names no file content."""


def norm_question(text: str) -> str:
    """A question as compared for "asked already": case, spacing and closing punctuation do not count. PgStore spells the
    same rule in SQL (_NORM_Q)."""
    return " ".join((text or "").lower().split()).rstrip("?.! ")


def link_matches(link: str, prefix: str) -> bool:
    """A notification link names this target: the path itself, or with a query after it ('/approvals/<id>?project=..',
    '/experts?message=<id>&project=..'). Never a longer id that merely starts the same."""
    return link == prefix or link.startswith(prefix + "?") or link.startswith(prefix + "&")


class CodeChunks(list):
    """Index.code_chunks' answer: the chunks, plus `truncated` when CODE_CHUNK_CAP left files out."""

    truncated: bool = False

    def __init__(self, chunks=(), truncated: bool = False):
        super().__init__(chunks)
        self.truncated = truncated


class User(BaseModel):
    id: str
    org_id: str
    email: str
    name: str
    role: Role
    github_login: str | None = None
    # An admin deactivates a member (POST /api/org/members/{id}/deactivate): they cannot sign in or call the API, their data
    # stays, and nobody routes new work to them. Reactivating sets it back.
    status: MemberStatus = "active"


class Project(BaseModel):
    id: str
    org_id: str
    name: str
    description: str = ""


class Onboarding(BaseModel):
    id: str
    project_id: str
    new_hire_id: str
    manager_id: str
    role_title: str = "Software Engineer"
    start_date: date
    status: Literal["active", "completed", "paused"] = "active"


class Repository(BaseModel):
    id: str
    project_id: str
    full_name: str
    installation_id: str | None = None
    default_branch: str = "main"
    last_indexed_sha: str | None = None
    status: Literal["pending", "indexing", "ready", "failed"] = "pending"


class Attendee(BaseModel):
    """Someone invited to a KT session. A Calendly invitee or guest may be nobody in the organisation (user_id None)."""
    email: str
    name: str = ""
    user_id: str | None = None


MeetingProvider = Literal["google", "calendly", "manual"]


class RecordingConsent(BaseModel):
    """Who confirmed that everyone in a KT session was told it is recorded and agreed, when, and the version of the
    recording notice in force then (app/services/recordings.py). A session's recording is not taken in without one."""
    by: str | None = None  # the person who confirmed; None once that account no longer exists
    by_name: str | None = None
    at: datetime
    notice_version: str
    # 'confirmed': someone confirmed after the call that everyone was told and agreed. 'promised_at_booking': the person who
    # booked a Meet session promised, before it, to tell everyone and to stop the recording if anyone objects. A promise is
    # not proof that anyone agreed. Both let the recording in.
    kind: Literal["confirmed", "promised_at_booking"] = "confirmed"


class Meeting(BaseModel):
    id: str
    project_id: str
    title: str
    room: str  # an internal handle, unique per meeting ('kt-' + a UUID)
    # scheduled: booked, its recording not in yet; processing: the Meeting agent is writing its notes; ready: notes written;
    # ended: held, but no recording came (nothing to process, no notes will follow); failed; cancelled. 'live' is kept for
    # calls from before scheduling: nothing sets it any more. docs/specs/scheduling.md.
    status: Literal["scheduled", "live", "processing", "ready", "failed", "cancelled", "ended"] = "scheduled"
    recording_uri: str | None = None
    duration_s: float | None = None
    started_at: datetime | None = None  # when the session starts (its scheduled start), else when it was created
    summary: str | None = None
    kt_key: str | None = None  # the KT planner session this call was booked for (POST /api/kt-plan/schedule), else None
    created_by: str | None = None  # who started or booked the call: they may cancel it before it starts
    # where it is held: Google Calendar with a Google Meet link, a Calendly booking, or anywhere else ('manual', recording uploaded)
    provider: MeetingProvider = "manual"
    join_url: str | None = None  # the Meet (or Calendly location, or any) link people join with
    calendar_event_id: str | None = None  # Google: the Calendar event id; Calendly: the scheduled event's URI
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    attendees: list[Attendee] = []
    recording_consent: RecordingConsent | None = None  # see RecordingConsent; None: nobody has confirmed or promised it yet
    # where the notes' transcript came from: Google Meet's own transcript, or the model listening to the recording; None: no notes yet
    transcript_source: Literal["google_meet", "recording"] | None = None


class TaskRow(BaseModel):
    id: str
    onboarding_id: str
    title: str
    description: str
    kind: Literal["learn", "code", "meet"]
    status: TaskStatus = "proposed"
    origin: str = "agent"
    due_date: date | None = None
    acceptance_criteria: list[str] = []
    related_paths: list[str] = []
    github_issue_number: int | None = None
    pr_number: int | None = None
    pr_url: str | None = None


class Approval(BaseModel):
    id: str
    project_id: str
    # task_plan, review, kt_followup; and the multi-agent model's proposals (docs/AGENTS.md): rerecord (the Keeper: an
    # explanation is possibly outdated), doc_edit (the Keeper: drafted wording for a flagged document or file, never pushed),
    # template_change (the Planner: a program template change from completion data)
    kind: Literal["task_plan", "review", "kt_followup", "rerecord", "doc_edit", "template_change"]
    thread_id: str
    payload: dict
    status: Literal["pending", "approved", "rejected", "partially_approved"] = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision: dict | None = None  # the first decision claimed for it: a retried resume replays this, not a different one


class ReviewRow(BaseModel):
    id: str
    task_id: str
    pr_number: int
    pr_url: str
    head_sha: str
    result: ReviewResult
    manager_decision: Literal["approved", "changes_requested"] | None = None


class Index(Protocol):
    """Chunk store + hybrid retriever + code lookup (implements kt_linker.CodeIndex)."""

    def upsert(self, chunks: list[Chunk]) -> int: ...
    def delete_paths(self, project_id: str, paths: list[str]) -> list[str]: ...
    def delete_document(self, project_id: str, document_id: str) -> list[str]: ...  # its chunks; returns their ids
    def delete_meeting(self, project_id: str, meeting_id: str) -> list[str]: ...  # its transcript windows; returns their ids
    def search(self, project_id: str, query: str, k: int = 8, source_types: list[str] | None = None) -> list[Hit]: ...
    def all_paths(self, project_id: str) -> list[str]: ...
    def symbols_named(self, project_id: str, name: str) -> list[CodeRef]: ...  # by path (code point), then start line
    def symbols_in(self, project_id: str, path: str) -> list[CodeRef]: ...
    def semantic_code(self, project_id: str, text: str, k: int = 3) -> list[tuple[CodeRef, float]]: ...
    # Many searches with one embedding call: one list of hits per (query, k), in order.
    def search_many(self, project_id: str, queries: list[tuple[str, int]], source_types: list[str] | None = None) -> list[list[Hit]]: ...
    # The research crew's batched retrieval (app/rag): for each query, for each kind (a source type), the top k[kind] hybrid
    # hits, with one embedding call for every query. Every Hit.similarity is the cosine to queries[0] (the question as asked),
    # not to the query that found it. Stale chunks and other projects' chunks are never returned; a kind that is not a source
    # type has no hits. semantic=False: the keyword channel alone, with no embedding call and every similarity 0.0 (the
    # crew's fallback when the query embedding fails).
    def search_grid(self, project_id: str, queries: list[str], kinds: list[str], k: dict[str, int],
                    semantic: bool = True) -> list[dict[str, list[Hit]]]: ...
    # Every non-stale code chunk of the project in one read, ordered by (path, start_line); content='' when content=False.
    # At most CODE_CHUNK_CAP chunks, whole files only (`truncated` says files were left out).
    def code_chunks(self, project_id: str, content: bool = True) -> CodeChunks: ...
    # A cheap digest of the project's non-stale code chunks (ids and commit labels): it changes whenever code_chunks would.
    def code_version(self, project_id: str) -> str: ...
    # One file's non-stale code chunks, in line order (read_file in app/agents/tools reads a file through them).
    def file_chunks(self, project_id: str, path: str) -> list[Chunk]: ...


class Store(Protocol):
    # people and projects
    def get_project(self, project_id: str) -> Project: ...
    def get_user(self, user_id: str) -> User | None: ...
    def users_by_github(self, org_id: str, logins: list[str]) -> list[User]: ...
    def managers(self, project_id: str) -> list[User]: ...
    def onboardings(self, project_id: str, active_only: bool = True) -> list[Onboarding]: ...
    def get_onboarding(self, onboarding_id: str) -> Onboarding | None: ...
    # repositories and documents
    def get_repo(self, repo_id: str) -> Repository | None: ...
    def repo_by_name(self, full_name: str) -> Repository | None: ...
    def repos_for(self, project_id: str) -> list[Repository]: ...
    def update_repo(self, repo_id: str, **fields) -> None: ...
    # One ingestion of a repository at a time, across processes. Raises RepoBusy rather than wait (the in-process demo waits).
    def ingest_lock(self, repo_id: str) -> AbstractContextManager[None]: ...
    def set_codeowners(self, project_id: str, text: str) -> None: ...
    def codeowners(self, project_id: str) -> str: ...
    # meetings and KT links
    def get_meeting(self, meeting_id: str) -> Meeting | None: ...
    def update_meeting(self, meeting_id: str, **fields) -> None: ...
    def save_meeting_result(self, result: MeetingResult) -> None: ...
    def meeting_notes(self, meeting_id: str) -> MeetingResult | None: ...
    def meeting_notes_many(self, meeting_ids: list[str]) -> dict[str, MeetingResult]: ...  # one read; ids without notes are left out
    def add_kt_links(self, project_id: str, links: list[KTLink]) -> None: ...
    # Everything a KT call's recording produced: transcript, screen moments, action items, notes and KT links; the call keeps
    # its row (title, time, attendees) as 'ended' with no recording. Its index chunks go through Index.delete_meeting.
    def erase_meeting_content(self, meeting_id: str) -> None: ...
    def kt_links(self, project_id: str, path: str | None = None, chunk_ids: list[str] | None = None) -> list[KTLink]: ...
    def mark_kt_code_changed(self, project_id: str, paths: list[str]) -> None: ...
    # tasks, approvals, reviews
    def create_tasks(self, onboarding_id: str, tasks: list[ProposedTask], start: date) -> list[TaskRow]: ...
    def get_task(self, task_id: str) -> TaskRow | None: ...
    def tasks(self, onboarding_id: str | None = None, project_id: str | None = None) -> list[TaskRow]: ...
    def update_task(self, task_id: str, **fields) -> None: ...
    def create_approval(self, project_id: str, kind: str, thread_id: str, payload: dict) -> Approval: ...
    def get_approval(self, approval_id: str) -> Approval | None: ...
    def approvals(self, project_id: str, status: str | None = "pending") -> list[Approval]: ...
    def decide_approval(self, approval_id: str, status: str, user_id: str) -> None: ...
    def pending_approval_for_thread(self, thread_id: str, kind: str) -> Approval | None: ...
    def discard_orphan_proposals(self, onboarding_id: str, grace_s: int = 0) -> int: ...
    def supersede_review_approvals(self, task_id: str) -> list[Approval]: ...  # all but the newest PR state's; returns the closed ones
    def close_review_approvals(self, task_id: str) -> list[Approval]: ...  # every pending one (the PR was merged); returns them
    def review_approvals(self, task_id: str) -> list[Approval]: ...  # every review approval of the task, any status, oldest first
    def save_review(self, task_id: str, pr_number: int, pr_url: str, head_sha: str, result: ReviewResult) -> ReviewRow: ...
    def get_review(self, review_id: str) -> ReviewRow | None: ...
    def decide_review(self, review_id: str, decision: str, user_id: str) -> None: ...
    # chat and experts
    def save_chat(self, project_id: str, user_id: str, question: str, result: QAResult) -> str: ...
    def mark_answers_stale(self, chunk_ids: list[str]) -> int: ...
    # draft: the Guide's AI draft answer, shown to the person in their inbox (an escalated question carries it)
    def create_expert_request(self, project_id: str, question: str, expert_id: str, asked_by: str, message_id: str,
                              draft: str | None = None) -> str: ...
    # cross-cutting
    def notify(self, user_id: str, kind: str, text: str, link: str = "") -> None: ...
    # Mark every unread notification whose link names this target (link_matches) read, for everyone: the approval was
    # decided, the question answered, the call cancelled. Returns how many.
    def close_notifications(self, link_prefix: str) -> int: ...
    # Who was ever sent a notification of this kind about this target (read or not), oldest first.
    def notification_recipients(self, link_prefix: str, kind: str) -> list[str]: ...
    # run_id: the id the run is stored under (a client-chosen uuid the asker streams its steps by); None: a new one. An id
    # that is taken already gets a new one too, never overwrites.
    def record_run(self, project_id: str, agent: str, trigger: str, status: str, detail: dict, run_id: str | None = None) -> str: ...
    def get_run(self, run_id: str) -> dict | None: ...  # {id, project_id, agent, trigger, status, detail, at, trace_id}; this org's only
    # idempotency: claim before handling, heartbeat while handling, finish on success, release on failure
    def claim_event(self, dedupe_key: str, lease_s: int) -> EventClaim: ...
    def touch_event(self, dedupe_key: str) -> None: ...
    def finish_event(self, dedupe_key: str) -> None: ...
    def release_event(self, dedupe_key: str) -> None: ...
    def event_status(self, dedupe_key: str) -> EventStatus: ...
    def reset_event(self, dedupe_key: str) -> bool: ...  # forget a finished (or abandoned) event so it can run again; False if busy


class AppStore(Store, Protocol):
    """Extra reads and writes used by the API (not by agents)."""

    def members(self, project_id: str) -> list[User]: ...
    def create_repo(self, project_id: str, full_name: str, installation_id: str | None, default_branch: str) -> Repository | None: ...
    def create_document(self, project_id: str, title: str, uri: str, mime: str, uploaded_by: str) -> str: ...
    def create_onboarding(self, project_id: str, new_hire_id: str, manager_id: str, start: date, role_title: str) -> Onboarding: ...
    def list_meetings(self, project_id: str) -> list[Meeting]: ...
    def chat_history(self, project_id: str, user_id: str, limit: int = 50) -> list[dict]: ...
    def notifications_for(self, user_id: str) -> list[dict]: ...
    def expert_requests_for(self, expert_id: str) -> list[dict]: ...
    def expert_requests_by(self, project_id: str, asked_by: str) -> list[dict]: ...  # every status, oldest first, with expert_name
    def answer_expert_request(self, request_id: str, answer: str) -> dict: ...
    def latest_reviews(self, task_ids: list[str]) -> dict[str, str]: ...  # task id -> its newest review's id (tasks with one)
    def get_document(self, document_id: str) -> dict | None: ...  # with its storage uri
    def end_meeting(self, meeting_id: str) -> bool: ...  # live -> ended (held where nothing records it); False otherwise
    def list_runs(self, project_id: str, limit: int = 50) -> list[dict]: ...
    # accounts (the definer-function lookups run before the tenant is known)
    def user_by_firebase(self, uid: str) -> User | None: ...
    def pending_invitations(self, email: str) -> list[dict]: ...
    def accept_invitations(self, uid: str, email: str, name: str, org_id: str) -> User | None: ...
    def create_organization(self, uid: str, email: str, name: str, org_name: str, project_name: str) -> User: ...
    def org_name(self, org_id: str) -> str: ...
    # projects, membership and invitations
    def projects_for(self, user: User) -> list[Project]: ...
    def create_project(self, org_id: str, name: str, description: str, creator_id: str) -> Project: ...
    def is_member(self, project_id: str, user_id: str) -> bool: ...
    def add_member(self, project_id: str, user_id: str) -> None: ...
    def user_by_email(self, org_id: str, email: str) -> User | None: ...  # a member: someone who left needs a new invitation
    def create_invitation(self, org_id: str, project_id: str | None, email: str, role: str, name: str | None,
                          github_login: str | None, invited_by: str) -> dict: ...
    def invitations(self, project_id: str) -> list[dict]: ...
    def revoke_invitation(self, invitation_id: str, project_id: str) -> bool: ...
    def leave_organization(self, user_id: str) -> bool: ...  # False if they are its last active admin and others still use it
    # members administration (app/api/members.py). A member is a user with a login bound (not someone who left, nor someone
    # enrolled who has not joined yet). Each row: {id, name, email, role, status, projects: [{id, name}]}, by name.
    def org_members(self, org_id: str) -> list[dict]: ...
    def org_member(self, org_id: str, user_id: str) -> dict | None: ...
    # Both return the updated user, or None if they are not a member of this organisation. Both raise LastAdmin, changing
    # nothing, when the change would leave the organisation without an active admin.
    def set_member_role(self, org_id: str, user_id: str, role: str) -> User | None: ...
    def set_member_status(self, org_id: str, user_id: str, status: str) -> User | None: ...
    # a person's GitHub username (None clears it): how their pull requests find their tasks. None if no such user in the org
    def set_github_login(self, org_id: str, user_id: str, login: str | None) -> User | None: ...
    # erasure (POST /api/org/people/erase): find anyone of the organisation by email (members, people who left, people
    # enrolled who never joined), then anonymise them: name, email and GitHub username replaced, login unbound, memberships,
    # chat history, questions they sent to a person, notifications, scheduling links and invitations removed. Ids stay, so
    # records that point at them (an onboarding, a review, the audit log) still hold together. Returns counts by kind.
    def find_person(self, org_id: str, email: str) -> User | None: ...
    def erase_person(self, org_id: str, user_id: str) -> dict[str, int]: ...
    # documents
    def documents(self, project_id: str) -> list[dict]: ...
    def update_document(self, document_id: str, **fields) -> None: ...
    def delete_document(self, document_id: str) -> None: ...
    # approvals, experts, notifications, chat feedback
    def claim_approval(self, approval_id: str, decision: dict | None = None) -> bool: ...
    def release_approval(self, approval_id: str) -> None: ...
    def get_expert_request(self, request_id: str) -> dict | None: ...
    def mark_notifications_read(self, user_id: str, ids: list[str] | None) -> int: ...
    def chat_feedback(self, message_id: str, user_id: str, value: int) -> bool: ...
    def audit(self, org_id: str, actor_id: str | None, action: str, target: str, detail: dict | None = None) -> None: ...
    # legal acceptances (app/legal.py, 019_legal.sql): append-only. One row per person, document and version ('terms',
    # 'privacy', 'recording'), and one per organisation and version for 'dpa' (user_id is the admin who accepted it).
    # Returns the new row {id, org_id, user_id, document, version, method, accepted_at, ip_prefix, user_agent}, or None
    # when that acceptance was recorded before (nothing is written twice). `at`: the demo seed's past; otherwise now.
    def record_legal_acceptance(self, org_id: str, user_id: str, document: str, version: str, method: str,
                                ip_prefix: str | None = None, user_agent: str | None = None,
                                at: datetime | None = None) -> dict | None: ...
    # The organisation's acceptances, newest first; narrowed to one person and/or one document.
    def legal_acceptances(self, org_id: str, user_id: str | None = None, document: str | None = None) -> list[dict]: ...
    # GitHub installations and pre-tenant webhook lookups
    def installation_org(self, installation_id: str) -> str | None: ...
    def org_installations(self) -> list[dict]: ...
    def record_installation_sender(self, installation_id: str, sender_id: int, sender_login: str, account_login: str) -> None: ...
    def installation_sender(self, installation_id: str) -> dict | None: ...
    def unbind_installation(self, installation_id: str) -> None: ...
    def bind_installation(self, installation_id: str, org_id: str, account: str, user_id: str) -> bool: ...
    def repos_by_full_name(self, full_name: str) -> list[dict]: ...
    def org_of_project(self, project_id: str) -> str | None: ...
    def claim_meeting_recording(self, meeting_id: str) -> bool: ...
    # transactional outbox: an event is recorded before it is published, so a failed publish is retried, not lost
    def outbox_add(self, topic: str, event: dict) -> str: ...
    def outbox_due(self, limit: int = 50, skip: tuple[str, ...] = ()) -> list[dict]: ...
    def outbox_attempts(self, outbox_id: str) -> int: ...
    def watched_repositories(self) -> list[dict]: ...  # self-hosted: every repository connected by URL, across organisations
    def outbox_sent(self, outbox_id: str) -> None: ...
    def outbox_failed(self, outbox_id: str, error: str) -> None: ...
    def ping(self) -> bool: ...
    # batched reads for the insight views, and one connection and transaction for all of a page's reads
    def chat_histories(self, project_id: str, user_ids: list[str], limit: int = 50) -> dict[str, list[dict]]: ...  # chat_history per user
    def open_expert_requests(self, project_id: str) -> list[dict]: ...
    def reading(self) -> AbstractContextManager[None]: ...
    # A booking from the KT planner (kt_key set) returns None when a scheduled, live or processing call already holds that key;
    # so does a calendar event (provider, calendar_event_id) already recorded (a redelivered Calendly webhook).
    def create_meeting(self, project_id: str, title: str, created_by: str | None, kt_key: str | None = None, *,
                       provider: str = "manual", join_url: str | None = None, calendar_event_id: str | None = None,
                       starts_at: datetime | None = None, ends_at: datetime | None = None,
                       attendees: list[Attendee] | None = None) -> Meeting | None: ...
    def cancel_meeting(self, meeting_id: str) -> bool: ...  # a scheduled call only; False otherwise
    # scheduling (docs/specs/scheduling.md)
    def meeting_by_event(self, provider: str, calendar_event_id: str) -> Meeting | None: ...  # in the current organisation
    # scheduled|ended|failed -> processing with its recording; False if it has one already, is live, or was cancelled
    def claim_meeting_import(self, meeting_id: str, recording_uri: str, duration_s: float) -> bool: ...
    def expire_meeting(self, meeting_id: str) -> bool: ...  # scheduled -> ended (held, no recording came); False otherwise
    def discard_meeting(self, meeting_id: str) -> bool: ...  # a scheduled session whose calendar event could not be made
    # Across organisations (the recording poller): scheduled sessions that ended at least grace_s ago and are Google sessions
    # (their recording can be imported), or that ended more than wait_s ago (to be expired). Oldest end first.
    def meetings_awaiting_recording(self, grace_s: int, wait_s: int, limit: int = 20) -> list[dict]: ...
    def calendar_connection(self, org_id: str, provider: str = "google") -> dict | None: ...  # with refresh_token_enc
    def save_calendar_connection(self, org_id: str, provider: str, account_email: str, refresh_token_enc: str, scopes: str,
                                 connected_by: str) -> None: ...
    def delete_calendar_connection(self, org_id: str, provider: str = "google") -> bool: ...
    def scheduling_link(self, user_id: str, provider: str = "calendly") -> str | None: ...
    def set_scheduling_link(self, org_id: str, user_id: str, provider: str, url: str | None) -> None: ...  # None removes it
    def scheduling_links(self, project_id: str, provider: str = "calendly") -> dict[str, str]: ...  # member id -> url


class RepoSource(Protocol):
    """Read-only access to a repository: GitHub App in production, a local folder in demo mode."""

    def files(self, full_name: str, ref: str, installation_id: str | None) -> dict[str, str]: ...
    def file(self, full_name: str, path: str, ref: str, installation_id: str | None) -> str | None: ...
    def pr_files(self, full_name: str, number: int, installation_id: str | None) -> list[dict]: ...
    def check_runs(self, full_name: str, sha: str, installation_id: str | None) -> list[dict]: ...
    def good_first_issues(self, full_name: str, installation_id: str | None) -> list[dict]: ...
    def repo_info(self, full_name: str, installation_id: str | None) -> dict | None: ...
    def head_sha(self, full_name: str, ref: str, installation_id: str | None) -> str | None: ...


class CalendarEvent(BaseModel):
    """A KT session as a calendar sees it. `meeting_id` makes the request idempotent (the Meet conference request id)."""
    meeting_id: str
    title: str
    description: str = ""
    starts_at: datetime
    ends_at: datetime
    attendees: list[str] = []  # email addresses; the calendar sends each an invitation


class CalendarBooking(BaseModel):
    event_id: str
    join_url: str | None = None  # the Google Meet link


class RecordingFile(BaseModel):
    """A session's recording where the calendar keeps it (a Google Drive file Meet attached to the event)."""
    file_id: str
    name: str = ""
    mime: str = "video/mp4"
    duration_s: float | None = None
    size: int | None = None


TRANSCRIPT_SUFFIX = ".transcript.txt"  # Google's transcript is stored beside its recording, at <recording file> + this


class RecordingNotShared(Exception):
    """The calendar event names a recording that this account cannot open (Drive says it does not exist for it): the
    organiser has not shared it with the connected Google account."""


class TranscriptFile(BaseModel):
    """The transcript Google Meet saved beside a session's recording (a Google Doc attached to the calendar event)."""
    file_id: str
    name: str = ""


class Scheduler(Protocol):
    """Puts KT sessions on a calendar and finds their recordings afterwards. MemoryScheduler (demo, tests) and
    GoogleCalendarScheduler (Calendar API v3 + Drive) implement it. Calendly bookings arrive by webhook instead, and a
    session held anywhere else ('manual') has no scheduler: its recording is uploaded."""

    provider: str  # the Meeting.provider of the sessions it books

    def create_event(self, event: CalendarEvent) -> CalendarBooking: ...
    def cancel_event(self, event_id: str) -> None: ...  # an event already gone is not an error
    def find_recording(self, event_id: str) -> RecordingFile | None: ...  # None: no recording (yet)
    # Stream the file into `sink`; returns the bytes written. Raises ValueError past max_bytes.
    def download(self, file: RecordingFile, sink: BinaryIO, max_bytes: int | None = None) -> int: ...
    # Optional (a scheduler without them has no transcripts): Google's own transcript of the session, as plain text.
    # find_transcript: None when Meet saved none. fetch_transcript: the text, cut at max_bytes; raises on failure.
    def find_transcript(self, event_id: str) -> TranscriptFile | None: ...
    def fetch_transcript(self, file: TranscriptFile, max_bytes: int) -> str: ...


class BlobStorage(Protocol):
    def get(self, uri: str) -> bytes: ...
    def put(self, uri: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def delete(self, uri: str) -> bool: ...  # True if a stored file was removed; a file that is already gone is not an error


class Mailer(Protocol):
    """Sends one email. `enabled` is True only when a message can really leave the server (SMTP is configured): callers send
    nothing, and the web app never says "emailed", while it is False. `send` raises when the message could not be handed on, so
    the caller (an outbox event, the daily tick) can retry or move on; a message body or link is never logged."""

    enabled: bool

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> None: ...


class EventBus(Protocol):
    def publish(self, topic: str, event: dict) -> None: ...


# ======================================================================================== onboarding lifecycle
# Programs, plans, pre-boarding, progress, blockers, feedback, check-ins, surveys, handovers and reporting (app/lifecycle).
# Tables: db/migrations/010_lifecycle.sql. `LifecycleStore` is implemented by adapters/lifecycle_memory.py (demo, tests) and
# adapters/lifecycle_pg.py (Postgres, row-level security per organisation). A side table per onboarding keeps `Onboarding`,
# which the agents use, unchanged.

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrgSettings(BaseModel):
    org_id: str
    setup: dict = {}  # {"skipped": [step], "completed": [step]}: what an admin decided on the setup checklist
    minutes_per_interruption: int = 15  # the senior-time-saved estimate's assumption, editable by admins
    demo_day_offset: int = 0  # MODE=demo only: days the demo clock is ahead of the real one (POST /api/demo/advance)
    agent_modes: dict = {}  # {agent key: "ai" | "rules"} (PATCH /api/agents/{key}); an agent not named here is "ai"


class Team(BaseModel):
    id: str
    org_id: str
    project_id: str
    name: str


class ProgramTemplate(BaseModel):
    """A role track and level's program. Days are days of a 90-day program; a template of another length scales them."""
    id: str
    org_id: str
    key: str  # 'backend-mid': one key, many versions; a change makes a new version and the old one inactive
    version: int = 1
    title: str
    role_track: str  # Backend | Frontend | SRE | Data
    level: str  # Junior | Mid | Senior | Staff
    duration_days: int = 90
    milestones: list[dict] = []  # [{key, title, due_day, description}]
    acknowledgements: list[dict] = []  # [{doc_key, title, summary}]
    access_items: list[dict] = []  # [{system, owner: manager | mentor | admin}]
    plan_items: list[dict] = []  # [{title, description, kind, due_day, acceptance_criteria}]: the plan when no model is set up
    # the board's milestones (app/lifecycle/board.py): [{key, title, due_day}] in days of this template's length, not scaled;
    # empty: the defaults (templates.DEFAULT_PHASES: days 15, 30, 45, 60 and 90 of a 90-day program, scaled to its length)
    phases: list[dict] = []
    active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)


class OnboardingProgram(BaseModel):
    onboarding_id: str
    org_id: str
    project_id: str
    template_id: str
    template_version: int
    duration_days: int = 90
    mentor_id: str | None = None
    team_id: str | None = None
    focus_area: str | None = None  # a module ('engine'), the area the first task should come from
    plan_approved_at: datetime | None = None
    preboarding_expires_at: datetime | None = None  # the link's token itself is stored hashed and never read back
    access_granted_at: datetime | None = None
    first_task: dict | None = None  # the selected first task's brief
    survey_days: list[int] = []  # check-in days the hire has answered the survey for (the answers carry no person id)
    completed_at: datetime | None = None
    completion: dict | None = None
    completion_draft: dict | None = None  # the Planner's drafted sign-off (specialists/planner), until a manager signs off
    created_at: datetime = Field(default_factory=_utcnow)


class Milestone(BaseModel):
    onboarding_id: str
    key: str
    title: str
    due_day: int
    status: Literal["pending", "done", "skipped"] = "pending"
    done_at: datetime | None = None
    evidence: dict = {}


# The milestone board (db/migrations/018_board.sql, app/lifecycle/board.py). A board milestone is called a phase in the code,
# so it never mixes with the checkpoints above (Milestone); people see it as "Day 30 · First change shipped".
class Phase(BaseModel):
    onboarding_id: str
    key: str
    title: str
    due_day: int  # the program day it is due by
    position: int = 0  # its place on the board, left to right
    created_at: datetime = Field(default_factory=_utcnow)


class TaskPlacement(BaseModel):
    """Where a person put a task: its milestone (None: the one its due date falls in) and its order in its column."""
    task_id: str
    onboarding_id: str
    phase_key: str | None = None
    position: int | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class TaskMove(BaseModel):
    """A task moved from one milestone to another: the board's "Moved" state and its history."""
    id: str = ""
    onboarding_id: str
    task_id: str
    from_phase: str | None = None
    to_phase: str
    from_title: str | None = None  # the milestones' titles at the time of the move
    to_title: str
    from_due: date | None = None  # the due date the move replaced, when it changed it
    to_due: date | None = None
    actor_id: str | None = None
    at: datetime = Field(default_factory=_utcnow)


class AccessItem(BaseModel):
    id: str
    onboarding_id: str
    system: str
    owner_id: str | None = None
    status: Literal["requested", "granted", "not_needed"] = "requested"
    requested_at: datetime = Field(default_factory=_utcnow)
    granted_at: datetime | None = None


class Acknowledgement(BaseModel):
    onboarding_id: str
    doc_key: str
    title: str
    acknowledged_at: datetime | None = None


class EnvironmentCheck(BaseModel):
    id: str
    onboarding_id: str
    ran_at: datetime
    ok: bool
    results: list[dict] = []  # [{name, ok, detail}]


class AreaProgress(BaseModel):
    """What the hire recorded about an area. The level itself is computed from evidence (app/lifecycle/progress.py)."""
    onboarding_id: str
    area: str
    evidence: dict = {}  # {"watched": [{meeting_id, at_s, at}]}
    self_level: Literal["not_started", "exploring", "working", "contributed"] | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class BlockerAlert(BaseModel):
    id: str
    onboarding_id: str
    rule: Literal["repeated_topic", "stalled_task", "failing_ci", "environment", "unresolved_review"]
    subject: str  # what the rule fired on (a module, a task id, 'env'): one open alert per rule and subject
    evidence: dict = {}
    stage: Literal["hire", "mentor", "manager"] = "hire"
    notified_stage: str | None = None
    opened_at: datetime = Field(default_factory=_utcnow)
    resolved_at: datetime | None = None
    resolved_by: str | None = None  # a user id, or 'system' when the condition cleared


class DocFeedback(BaseModel):
    id: str
    project_id: str
    target_kind: Literal["doc", "code", "meeting", "answer"]
    target_ref: str
    kind: Literal["unclear", "outdated"]
    note: str = ""
    owner_id: str | None = None
    status: Literal["open", "resolved", "dismissed"] = "open"
    created_by: str
    created_at: datetime = Field(default_factory=_utcnow)


class VerifiedAnswer(BaseModel):
    id: str
    project_id: str
    question: str
    answer: str
    citations: list[dict] = []
    verified_by: str
    source_request_id: str | None = None
    reuse_count: int = 0
    created_at: datetime = Field(default_factory=_utcnow)


class CheckinReview(BaseModel):
    id: str
    onboarding_id: str
    day: int  # 30, 60 or 90 (scaled with the program's length)
    author_role: Literal["hire", "manager"]
    author_id: str
    answers: dict = {}
    created_at: datetime = Field(default_factory=_utcnow)


class SurveyResponse(BaseModel):
    """No person id, by design: reported only in aggregate, and only for groups of five or more."""
    id: str
    org_id: str
    team_id: str | None = None
    cohort: str  # the quarter the hire started in, e.g. '2026-Q4'
    day: int
    answers: dict = {}


class ProgressSummary(BaseModel):
    id: str
    onboarding_id: str
    week_start: date
    body: dict = {}  # {"lines": [{text, citations: [{kind, ref, label}]}], ...}
    created_at: datetime = Field(default_factory=_utcnow)


class Handover(BaseModel):
    id: str
    org_id: str
    project_id: str
    user_id: str
    last_day: date
    plan: dict = {}
    status: Literal["draft", "in_progress", "done"] = "draft"
    created_by: str
    created_at: datetime = Field(default_factory=_utcnow)


class ExpertPreferences(BaseModel):
    user_id: str
    max_interruptions_per_day: int = 3
    office_hours: dict = {}  # {"days": ["Tue", "Thu"], "start": "15:00", "end": "16:00"}
    digest: bool = True  # questions that are not urgent wait for the daily digest once the day's interruptions are used


class DocEdit(BaseModel):
    """An approved doc_edit proposal (the Keeper's drafted wording, as a manager approved it). For an uploaded document it is
    an accepted edit shown with the document; for code, a KT call or a verified answer it is a suggestion for the owner, shown
    next to the file: Welcome Wave never writes to a repository."""
    id: str
    org_id: str
    project_id: str
    approval_id: str | None = None
    target_kind: Literal["doc", "code", "meeting", "answer"]
    target_ref: str
    title: str
    proposed_text: str
    current_excerpt: str = ""
    feedback_ids: list[str] = []
    status: Literal["accepted", "suggested"] = "suggested"
    owner_id: str | None = None
    accepted_by: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class MetricEvent(BaseModel):
    id: str
    org_id: str
    project_id: str | None = None
    kind: str
    subject_id: str | None = None
    at: datetime = Field(default_factory=_utcnow)
    props: dict = {}


class LifecycleStore(Protocol):
    """Everything app/lifecycle reads and writes beyond `AppStore`. `org_id` arguments scope the in-memory adapter;
    Postgres scopes every query to the request's organisation with row-level security as well."""

    # organisation settings (setup checklist, estimate assumptions, the demo clock) and teams
    def org_settings(self, org_id: str) -> OrgSettings: ...
    def update_org_settings(self, org_id: str, **fields) -> OrgSettings: ...
    def teams(self, org_id: str, project_id: str | None = None) -> list[Team]: ...
    def get_team(self, team_id: str) -> Team | None: ...
    def create_team(self, org_id: str, project_id: str, name: str) -> Team: ...
    def update_team(self, team_id: str, **fields) -> Team | None: ...
    # program templates: the active version of each key; a change makes a new version
    def templates(self, org_id: str, include_inactive: bool = False) -> list[ProgramTemplate]: ...
    def get_template(self, template_id: str) -> ProgramTemplate | None: ...
    def create_template(self, template: ProgramTemplate) -> ProgramTemplate: ...
    def new_template_version(self, template_id: str, **changes) -> ProgramTemplate | None: ...  # None: not the active version
    # programs (one per onboarding) and the people behind them
    def create_program(self, program: OnboardingProgram, milestones: list[Milestone], access: list[AccessItem],
                       acknowledgements: list[Acknowledgement]) -> OnboardingProgram: ...
    def get_program(self, onboarding_id: str) -> OnboardingProgram | None: ...
    def programs(self, org_id: str, project_id: str | None = None) -> list[OnboardingProgram]: ...
    def update_program(self, onboarding_id: str, **fields) -> None: ...
    def set_preboarding(self, onboarding_id: str, token_hash: str, expires_at: datetime) -> None: ...
    # before the tenant is known: (onboarding id, org id, expiry) for a pre-boarding link's token hash
    def program_by_token(self, token_hash: str) -> tuple[str, str, datetime] | None: ...
    def ensure_user(self, org_id: str, email: str, name: str, github_login: str | None) -> User: ...  # unbound until they accept
    def set_onboarding_status(self, onboarding_id: str, status: str) -> None: ...
    # the timeline
    def milestones(self, onboarding_id: str) -> list[Milestone]: ...
    # the approved plan's milestones replace the program's (enrolment: none until a manager approves the 30/60/90 plan)
    def set_milestones(self, onboarding_id: str, milestones: list[Milestone]) -> None: ...
    def update_milestone(self, onboarding_id: str, key: str, **fields) -> Milestone | None: ...
    # the milestone board (app/lifecycle/board.py): a hire's board milestones (phases), where people put tasks, and moves
    def phases(self, onboarding_id: str) -> list[Phase]: ...  # by position, then due day
    # the program's first board milestones: written only when it has none yet (two first reads at once write them once)
    def ensure_phases(self, onboarding_id: str, phases: list[Phase]) -> list[Phase]: ...
    def add_phase(self, phase: Phase) -> Phase | None: ...  # None: its key is taken
    def update_phase(self, onboarding_id: str, key: str, **fields) -> Phase | None: ...
    # deletes it; tasks a person put there move to `move_to` (their placement only: the moves are recorded by the caller)
    def delete_phase(self, onboarding_id: str, key: str, move_to: str | None) -> bool: ...
    def placements(self, onboarding_id: str) -> list[TaskPlacement]: ...
    def save_placements(self, onboarding_id: str, rows: list[TaskPlacement]) -> None: ...  # insert or replace, one per task
    def add_task_move(self, move: TaskMove) -> TaskMove: ...
    def task_moves(self, onboarding_id: str) -> list[TaskMove]: ...  # oldest first
    def access_items(self, onboarding_id: str) -> list[AccessItem]: ...
    def get_access_item(self, item_id: str) -> AccessItem | None: ...
    def add_access_item(self, item: AccessItem) -> AccessItem: ...
    def update_access_item(self, item_id: str, **fields) -> AccessItem | None: ...
    def acknowledgements(self, onboarding_id: str) -> list[Acknowledgement]: ...
    def acknowledge(self, onboarding_id: str, doc_key: str, ip_hash: str, at: datetime) -> Acknowledgement | None: ...
    def environment_checks(self, onboarding_id: str) -> list[EnvironmentCheck]: ...  # oldest first
    def add_environment_check(self, check: EnvironmentCheck) -> EnvironmentCheck: ...
    # progress, blockers, summaries, check-ins
    def area_progress(self, onboarding_id: str) -> list[AreaProgress]: ...
    def save_area_progress(self, row: AreaProgress) -> AreaProgress: ...
    def blockers(self, onboarding_id: str, open_only: bool = False) -> list[BlockerAlert]: ...
    def get_blocker(self, blocker_id: str) -> BlockerAlert | None: ...
    def open_blocker(self, alert: BlockerAlert) -> BlockerAlert: ...  # the open alert for its rule and subject, made if none
    def update_blocker(self, blocker_id: str, **fields) -> BlockerAlert | None: ...
    def summaries(self, onboarding_id: str) -> list[ProgressSummary]: ...  # newest first
    def save_summary(self, summary: ProgressSummary) -> ProgressSummary: ...  # one per week: saving again replaces it
    def checkins(self, onboarding_id: str) -> list[CheckinReview]: ...
    def save_checkin(self, checkin: CheckinReview) -> CheckinReview: ...  # one per day and author role: saving again replaces it
    # documentation feedback, verified answers, expert preferences
    def add_feedback(self, feedback: DocFeedback) -> DocFeedback: ...
    def get_feedback(self, feedback_id: str) -> DocFeedback | None: ...
    def feedback(self, project_id: str) -> list[DocFeedback]: ...  # newest first
    def update_feedback(self, feedback_id: str, **fields) -> DocFeedback | None: ...
    def add_verified_answer(self, answer: VerifiedAnswer) -> VerifiedAnswer: ...
    def verified_answers(self, project_id: str) -> list[VerifiedAnswer]: ...
    def bump_reuse(self, answer_id: str) -> None: ...
    def answered_requests(self, project_id: str) -> list[dict]: ...  # answered expert requests: {id, question, answer, answered_at}
    def expert_preferences(self, user_id: str) -> ExpertPreferences: ...  # the defaults when none were saved
    def save_expert_preferences(self, org_id: str, prefs: ExpertPreferences) -> ExpertPreferences: ...
    # surveys (no person id), handovers, metrics
    # Records that the onboarding answered the survey of this day: True once, False when it already had (atomic, so two
    # simultaneous submissions cannot both count)
    def claim_survey_day(self, onboarding_id: str, day: int) -> bool: ...
    def add_survey_response(self, response: SurveyResponse) -> None: ...
    def survey_responses(self, org_id: str) -> list[SurveyResponse]: ...
    def handovers(self, org_id: str) -> list[Handover]: ...
    def get_handover(self, handover_id: str) -> Handover | None: ...
    def add_handover(self, handover: Handover) -> Handover: ...
    def update_handover(self, handover_id: str, **fields) -> Handover | None: ...
    # a handover's transfer (app/lifecycle/handover.py): the hires a leaver manages, and questions routed to them
    def set_onboarding_manager(self, onboarding_id: str, manager_id: str) -> None: ...
    def reassign_expert_request(self, request_id: str, expert_id: str) -> bool: ...  # an open request only; False otherwise
    def record_metric(self, org_id: str, project_id: str | None, kind: str, subject_id: str | None = None, props: dict | None = None,
                      at: datetime | None = None) -> None: ...
    def metrics(self, org_id: str, kinds: list[str], since: datetime | None = None) -> list[MetricEvent]: ...
    # approved doc_edit proposals (the Keeper): accepted edits of documents, suggestions for owners of code
    def add_doc_edit(self, edit: DocEdit) -> DocEdit: ...
    def doc_edits(self, project_id: str, target_kind: str | None = None, target_ref: str | None = None) -> list[DocEdit]: ...  # newest first
    # across organisations (the daily tick): every organisation with a program that is not completed. Postgres: the definer
    # function lifecycle_orgs() (014_agents.sql)
    def lifecycle_orgs(self) -> list[str]: ...
    # reads of agent-owned tables the Store protocol does not offer
    def task_activity(self, task_ids: list[str]) -> dict[str, datetime]: ...  # the last change to each task
    def reviews_for_tasks(self, task_ids: list[str]) -> list[dict]: ...  # {id, task_id, head_sha, recommendation, findings, decision, created_at}
    # the organisation's data: a JSON export of every table, and deletion of all of it (its rows, its outbox events and, in the
    # demo, its calendar connection and Calendly links). What lives outside the database is found BEFORE the delete: the stored
    # files by uri, and the LangGraph threads of its approvals (app/lifecycle/orgdata.py removes them after the rows).
    def export_org(self, org_id: str) -> dict[str, list[dict]]: ...
    def delete_org(self, org_id: str) -> dict[str, int]: ...
    def org_blob_uris(self, org_id: str) -> list[str]: ...  # documents and call recordings
    def org_thread_ids(self, org_id: str) -> list[str]: ...  # LangGraph thread ids of the organisation's approvals
