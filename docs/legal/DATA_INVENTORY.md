# Data inventory

What Welcome Wave holds about people and customers, where each piece goes, and the file that proves it. The legal
documents (`frontend/src/legal/documents.ts`) say nothing this page cannot back. Read from the code on 19 November 2026;
when the code changes, change this page and the documents together ([LEGAL.md](../LEGAL.md#publishing-a-new-version)).

Paths are relative to the repository root. "Google Cloud" is `MODE=gcp`; "self-hosted" is `MODE=self_hosted`, one
machine; the demo (`MODE=demo`) holds made-up data in memory and is not covered.

## Who is who

| Role | Who | For which data |
|---|---|---|
| Controller (GDPR), data fiduciary (India's DPDP Act), business (CCPA) | The customer organisation | Everything its people put into a workspace: sections 2 to 9 below |
| Processor, data processor, service provider | The operator (`LEGAL_OPERATOR_NAME`) | The same data, handled on the customer's instructions |
| Controller in its own right | The operator | Sign-in accounts, records of who accepted which document, security logs, and its dealings with customers (contacts, invoices: held outside the product) |
| Sub-processors | Google, and others only when turned on | [Section 12](#12-third-parties-each-with-what-it-receives) |

## 1. Account and identity

| | |
|---|---|
| **What** | Name, work email, role, GitHub username (optional), the sign-in provider's user id, when the account was made, whether it is deactivated. The password is held by Google's Identity Platform, never by Welcome Wave. |
| **Source** | The person at sign-up; the admin or manager who invites or enrols them |
| **Purpose** | Sign-in, permissions, sending questions to the right person, matching pull requests to their author |
| **Stored** | Postgres `users` (Cloud SQL, or the machine's own Postgres). The login itself (email, password hash, Google account link, email-verified flag) at Identity Platform / Firebase Authentication in both modes |
| **Who sees it** | Members of the same organisation see names, emails and roles of people on their projects; admins see every member |
| **Kept** | Until the person is erased or the organisation is deleted. No automatic expiry |
| **Deleted / exported** | Erase: an admin, `POST /api/org/people/erase` (name, email and GitHub username replaced on the `users` row, login unbound; [what erasure does not reach](#what-erasing-a-person-does-and-does-not-reach) is listed under section 7). Leaving: `POST /api/me/leave` unbinds the login and keeps the records. In the organisation export, without `firebase_uid`. The Identity Platform account is not removed by either: the operator deletes it by hand on request |
| **Proof** | `backend/db/migrations/001_init.sql` (`users`), `backend/app/api/auth.py` (`_verify`), `backend/app/api/routes.py` (`erase_person`, `ERASE_KEEPS`, `me/leave`), `backend/app/adapters/postgres.py` (`erase_person`), `backend/app/adapters/lifecycle_pg.py` (`_EXPORT_SKIP`), `frontend/src/auth/firebase.ts` |

## 2. Organisation, teams, projects, invitations

| | |
|---|---|
| **What** | Organisation name; teams; projects and who is on them; invitations (email, name, role, GitHub username, who invited, when it expires) |
| **Source** | Admins and managers |
| **Purpose** | Tenancy, permissions, joining by invitation |
| **Stored** | Postgres: `organizations`, `teams`, `projects`, `project_members`, `invitations`, `org_settings` |
| **Who sees it** | The organisation's members, by role |
| **Kept** | Until deleted. An invitation stops working after 14 days; its row stays until the person is erased or the organisation is deleted |
| **Deleted / exported** | Organisation deletion; erasure removes a person's invitations and project memberships. All in the export |
| **Proof** | `backend/db/migrations/002_production.sql` (`invitations`, `expires_at`), `backend/app/api/members.py`, `backend/app/lifecycle/orgdata.py` |

## 3. Records of acceptance (the legal documents)

| | |
|---|---|
| **What** | Who accepted which document, which version, when and how (sign-up, invitation, the in-app prompt, or for the organisation), the network the click came from (IPv4 cut to /24, IPv6 to /48: never the full address) and the browser's description (200 characters at most) |
| **Source** | The person's click |
| **Purpose** | Evidence that the terms, privacy policy, recording notice and data-processing agreement were accepted |
| **Stored** | Postgres `legal_acceptances` (append-only for the application) |
| **Who sees it** | The person (their own document, version and time, `GET /api/legal/status`; a new hire also sees that the network and browser are kept, on "What your manager sees"); admins (the organisation's agreement, `GET /api/org/legal`; every row, with network prefix and browser, in the organisation export) |
| **Kept** | Until the organisation is deleted. Erasing a person leaves the rows under the erased identity, with the network prefix and browser description |
| **Deleted / exported** | Organisation deletion (`delete_org_legal()`); in the export |
| **Proof** | `backend/app/legal.py` (`record`, `ip_prefix`, `user_agent`), `backend/db/migrations/019_legal.sql` |

## 4. Source code, pull requests, issues, CI results

| | |
|---|---|
| **What** | Files of the repositories the organisation connects, cut into search entries with embeddings; pull requests, their diffs, authors' GitHub usernames, issues, check results; CODEOWNERS. Code can contain names and email addresses in comments and commit data |
| **Source** | The organisation's GitHub, read-only: a GitHub App with read permissions (Google Cloud), or a repository URL with an optional read-only token (self-hosted) |
| **Purpose** | Answers with citations, first-task shortlists, plans, review drafts, knowledge risk |
| **Stored** | Postgres: `repositories`, `chunks` (text and embeddings), `tasks`, `reviews`, `github_installations`, `github_installation_senders`. Stored as provided: the redactor is not run on code |
| **Who sees it** | Project members |
| **Kept** | Until the repository is disconnected or the organisation deleted; an entry is replaced when its file changes |
| **Deleted / exported** | Organisation deletion. In the export without embeddings and search columns |
| **Third parties** | GitHub (the source). Gemini: every entry's text for its embedding (so the whole indexed text of each connected repository), code passages in prompts, and pull request diffs for reviews |
| **Proof** | `backend/app/adapters/github.py` ("There is deliberately no method that writes to GitHub"), `backend/app/ports.py` (`RepoSource`), `backend/app/services/embeddings.py`, `backend/app/services/chunking.py`, `docs/SECURITY.md` §4 |

## 5. Documents

| | |
|---|---|
| **What** | Files members upload (25 MB at most), their extracted text, embeddings, who uploaded them, proposed edits |
| **Source** | Members |
| **Purpose** | Answers with citations |
| **Stored** | The file: Cloud Storage `docs/…` (Google Cloud) or the data folder on the machine (self-hosted). Text and embeddings: Postgres `documents`, `chunks`, `doc_edits`. Stored as provided: not redacted |
| **Who sees it** | Project members |
| **Kept** | Until a manager deletes the document or the organisation is deleted. The 30-day Cloud Storage rule covers `recordings/` only, not documents |
| **Deleted / exported** | A manager, `DELETE /api/projects/{id}/documents/{id}` (row and file, audited). The export holds the rows, not the files |
| **Third parties** | Gemini: the whole extracted text for embeddings, and passages of it in prompts |
| **Proof** | `backend/app/api/routes.py` (documents routes), `infra/terraform/data.tf` (`matches_prefix = ["recordings/"]`), `backend/app/config.py` (`max_upload_mb`) |

## 6. KT sessions and recordings

Welcome Wave does not record a call. A call is recorded by the meeting tool (Google Meet, when the organisation's Google
Workspace plan includes recording); the file is then uploaded by a member, or imported from the organisation's Google
Drive for a session booked through its Google Calendar.

| | |
|---|---|
| **What** | The session: title, agenda, times, who booked it, attendees' email addresses, the Meet link, the calendar event id. The recording: a video file with voices, faces (cameras on) and shared screens, 2 GB and 4 hours at most. From the recording: a transcript with speaker labels, notes, decisions, action items, descriptions of what was on screen, links from clips to code, search entries with embeddings |
| **Consent record** | Per session: who confirmed that every participant was told and agreed, when, and the version of the recording notice. Upload and import are refused without it. It cannot be put on record ahead of a session that has not been held, and it is cleared when the recording is removed, so adding one again needs a new confirmation (the audit log keeps the first). Each member also acknowledges the recording notice once per version (section 3) |
| **Source** | A manager, expert or whoever booked the session uploads the file; or Google Drive (the poller imports it after a session ends, or a person presses import) |
| **Purpose** | Knowledge transfer: notes, a searchable transcript, clips beside the code they explain, cited answers, planning further sessions |
| **Redaction** | Every text kept from a recording passes the redactor first: email addresses, phone numbers, private keys, common API key and token formats, and values of things named like a password are replaced with labels. Names, voices, faces and anything else personal are not removed. The video file is stored as recorded |
| **Stored** | The file: Cloud Storage `recordings/…` (Google Cloud) or the data folder (self-hosted). The rest: Postgres `meetings`, `meeting_participants`, `transcript_segments`, `screen_moments`, `meeting_notes`, `action_items`, `kt_links`, `chunks`, `recording_removal_requests` |
| **Who sees it** | Project members watch the recording (a signed link valid 60 minutes) and read the notes. The transcript as a whole is opened by managers and admins only. Its 60-second windows are search entries: the assistant reads them to answer project members, so an answer can repeat what someone said, and cites the clip |
| **Kept** | The video file: deleted after 30 days on Google Cloud (bucket lifecycle rule, soft delete off, versioning off). Self-hosted: kept until removed, unless `RECORDING_RETENTION_DAYS` is set (see under this table). Notes, transcript and links: until removed or the organisation is deleted |
| **Deleted / exported** | A manager, `DELETE /api/meetings/{id}/recording`, after typing the session's title: the file, transcript, screen moments, action items, notes, KT links and search entries go; answers that cited them are marked out of date; audited. Anyone on the project can ask for removal; the project's managers decide and the person is told. The export holds the rows, not the video |
| **Third parties** | Gemini receives the recording itself (video and sound) and writes the transcript and notes. Google Cloud: Vertex AI reads the file from the bucket. Self-hosted: the file is uploaded to the Gemini Files API; Welcome Wave deletes that copy when the analysis finishes or fails, and Google deletes any copy left behind 48 hours after the upload. Google Calendar sends the invitations; Google Drive is where Meet saves the file |
| **Proof** | `backend/app/api/uploads.py`, `backend/app/services/scheduling.py` (`import_recording`), `backend/app/adapters/google_calendar.py`, `backend/app/agents/specialists/note_taker/agent.py` (`_redacted`), `backend/app/services/redaction.py` (`PATTERNS`), `backend/app/agents/core/llm.py` (`_media_uri`: Files API; `release_media` and `_delete_upload`: the copy is deleted when the analysis is over), `backend/app/api/routes.py` (`meeting_notes`, `recording_url`, `delete_recording`), `backend/app/services/kt_review.py` (`can_read_transcript`), `infra/terraform/data.tf` (lifecycle rule; `vertex_read`), `backend/db/migrations/020_recording_consent.sql`, `backend/app/api/recordings.py` (consent, removal requests), `backend/app/services/recordings.py` |

**Self-hosted retention.** `RECORDING_RETENTION_DAYS` (0 by default, which keeps every recording) removes each raw video
file that many days after it was stored, once a day, and audits each removal. Notes, transcripts and KT links stay
(`backend/app/services/recordings.py`). The documents therefore say "for the number of days the operator has set, or until
someone removes them when no period is set".

**What the consent record is.** Welcome Wave cannot see who was in a call. The record is the confirming member's own
statement, kept and audited (`recording.consent_confirmed`); it is not proof that consent was given. The Drive poller never
imports a session nobody confirmed. Calendar invitations Welcome Wave sends for a session say it will be recorded and link
to the recording notice (`backend/app/services/recordings.py`: `invitation_notice`, `confirm_consent`).

## 7. Questions and answers

| | |
|---|---|
| **What** | What a person asks the assistant, the answer, its sources and confidence, thumbs up or down; questions sent on to a person and that person's answer; verified answers; notes left on a document, file, call or answer |
| **Source** | Members; the assistant |
| **Purpose** | The product: cited answers, routing to an expert, improving documentation |
| **Stored** | Postgres: `chat_messages`, `expert_requests`, `verified_answers`, `documentation_feedback`, `expert_preferences` |
| **Who sees it** | The asker. A manager or mentor never sees a new hire's words to the assistant: they see counts on code ("asked 3 times by new hires") and that the assistant answered, when and from which sources. A question sent to a person is read by that person in full, with the asker's name. Once that person answers, the question and the answer are indexed as a verified-answer source (`verified-<request id>`): the assistant can show it, question included and without the asker's name, to any project member whose own question retrieves it. Managers read documentation feedback; the owner of the thing reads notes about it. Admins get conversations in full in the organisation export |
| **Kept** | Until the person is erased or the organisation is deleted |
| **Deleted / exported** | Erasure deletes the person's chats and the questions they sent to a person (the `expert_requests` rows), not the verified-answer source made from an answered one, and not their documentation feedback notes. In the export, in full |
| **Third parties** | Gemini: the question, with the retrieved excerpts, in the prompt; the question's embedding |
| **Proof** | `backend/app/lifecycle/visibility.py` (`FIELDS`, `visibility`), `backend/app/adapters/postgres.py` (`erase_person`), `backend/app/api/routes.py` (`answer_request`: the indexed chunk), `docs/SECURITY.md` §5 |

### What erasing a person does and does not reach

`erase_person` (both adapters) rewrites the person's `users` row (name, email, GitHub username, login), deletes their
`chat_messages`, `expert_requests` (as asker), own `notifications`, `scheduling_links`, `invitations` and
`project_members` rows, and blanks the `reason` of their `recording_removal_requests`. Nothing else is touched. Copies
made before the erasure therefore stay, and the route's answer lists them (`ERASE_KEEPS` in `backend/app/api/routes.py`):

- audit entries whose `detail` holds an address or username: `invitation.created` (the invited email),
  `member.github_changed` (from, to), `integration.google.connected` (the Google account's email), until they expire;
- `meetings.attendees` (emails), transcript speaker labels and the recordings themselves;
- verified-answer search entries made from a question the person passed on, and `verified_answers` rows;
- `documentation_feedback` notes; `approvals` payloads (a draft names the hire); other people's notifications that quote
  the person ("Vijay asked: …"); `metric_events` rows keyed to the person;
- `legal_acceptances`, by id, with network prefix and browser description;
- backups until they expire, and the Identity Platform account.

The Privacy Policy (section 9) says the same in plain words.

## 8. Onboarding records

| | |
|---|---|
| **What** | Start date, role title, team, manager, mentor, template; plan and tasks; milestones; access requests; documents acknowledged (the time; the network address only as a keyed hash); environment checks from the hire's machine (tool names, versions, pass or fail); progress by area; blockers; weekly summaries; check-in answers at day 30, 60 and 90; completion review; reviews of pull requests; handovers; board placements and moves |
| **Source** | Managers, the hire, GitHub, and agents' drafts that a person approved |
| **Purpose** | Running and reviewing an onboarding |
| **Stored** | Postgres: `onboardings`, `onboarding_programs`, `onboarding_phases`, `onboarding_milestones`, `program_templates`, `tasks`, `task_placements`, `task_moves`, `access_items`, `acknowledgements`, `environment_checks`, `area_progress`, `blocker_alerts`, `progress_summaries`, `checkin_reviews`, `reviews`, `approvals`, `handovers` |
| **Who sees it** | The hire, their mentor and the project's managers, field by field. The hire reads every field others can read about them, with who reads it, at `GET /api/onboardings/{id}/visibility`. What a private pre-submit review found is stored for nobody. Executives see counts only |
| **Kept** | Until the organisation is deleted. Erasing a person keeps these under "Erased person" |
| **Deleted / exported** | Organisation deletion; in the export |
| **Third parties** | Gemini: the records an agent needs to draft a plan, summary or review |
| **Proof** | `backend/app/lifecycle/visibility.py`, `backend/app/lifecycle/preboarding.py` (`ip_hash`), `backend/db/migrations/010_lifecycle.sql`, `backend/app/api/routes.py` (`ERASE_KEEPS`) |

Pre-boarding links (for someone who has not started): the token is 256 bits, stored only as a hash, expires after 14 days
and is replaced by the next link (`docs/SECURITY.md` §1).

## 9. Surveys

| | |
|---|---|
| **What** | Answers on a 1 to 5 scale, with a team, a quarter and a survey day. No person id on the response; the hire's `onboarding_programs.survey_days` records which days they answered, so a response can be matched to a person by anyone with database access |
| **Stored** | Postgres `survey_responses` |
| **Who sees it** | Averages only, and only for a team and day in which five or more different people answered |
| **Deleted / exported** | Organisation deletion. The export holds the floored averages, never the rows or who answered on which day |
| **Proof** | `backend/app/lifecycle/checkins.py` (`SURVEY_MIN_GROUP = 5`), `backend/app/lifecycle/orgdata.py` (`export`) |

## 10. Agent runs, queues and checkpoints

| | |
|---|---|
| **What** | A record per agent run (which agent, what started it, when, the outcome, the sources used, a cost estimate); the saved state of a run paused for an approval (LangGraph checkpoint, which can hold the draft and its inputs); queued events (ids, file paths, branch and commit names and, for a pull request, its description and its author's GitHub username); metric events (kinds and counts) |
| **Stored** | Postgres: `agent_runs`, `approvals`, the checkpoint tables, `outbox`, `processed_events`, `metric_events`. Google Cloud also passes the same events through Pub/Sub. The checkpoint tables, `outbox` and `processed_events` have no `org_id` and no row-level security: they are keyed by run or event id and kept apart by the application (`GLOBAL_TABLES` in `backend/app/migrate.py`) |
| **Who sees it** | Managers read a project's run list; a hire reads their own. Private reviews show that one ran, never what it found |
| **Kept** | Until the organisation is deleted. A checkpoint is deleted when its run finishes |
| **Deleted / exported** | Organisation deletion removes rows, checkpoints and queued events (`orgdata.delete`) |
| **Proof** | `backend/app/lifecycle/orgdata.py`, `backend/app/adapters/lifecycle_pg.py` (`delete_org`), `backend/app/api/agents.py`, `backend/app/container.py` (`_forget_if_finished`), `backend/app/adapters/github.py` (`events_from_webhook`) |

## 11. Notifications, email, scheduling connections, audit, logs

| Data | What and where | Kept | Proof |
|---|---|---|---|
| Notifications | In-app messages per person. Postgres `notifications` | Until erased or the organisation is deleted | `backend/app/adapters/postgres.py` (`erase_person`) |
| Email | Only when SMTP is set: the invitation, a pre-boarding link when a manager asks, the expert digest (a count and a link, never a question), the weekly summary to the hire and the manager. Welcome Wave keeps no copy of a message; its log records the kind and the recipient's domain | At the mail provider, under its own terms | `backend/app/adapters/mailer.py`, `backend/app/services/mailing.py` |
| Google connection | One per organisation: the Google account's email, the scopes (`calendar.events`, `drive.readonly`) and a refresh token encrypted with `TOKEN_ENCRYPTION_KEY`. Postgres `calendar_connections` | Until disconnected or the organisation is deleted, when the grant is also revoked at Google | `backend/db/migrations/011_scheduling.sql`, `backend/app/lifecycle/orgdata.py` (`_revoke_google`) |
| Calendly link | A person's own booking link. Postgres `scheduling_links`. Opening it passes the booker's name and email to Calendly; a booking comes back by signed webhook with the invitee's and hosts' names and emails | Until removed, erased or the organisation is deleted | `backend/app/adapters/calendly.py` (`booking_link`, `parse_webhook`) |
| Audit log | Who did what to what, when, with details: sign-ups, joins, invitations, role changes, uploads and deletions, approvals, recordings, exports, erasures, acceptances. Append-only for the application: no edit or delete of one entry; removed by the daily purge or with the organisation (`delete_org_audit`). Postgres `audit_log` | `AUDIT_RETENTION_DAYS`, 365 by default, purged daily; never younger than 30 days. Erasure keeps entries: most name people by id, and a few hold an address or username in `detail` (see section 7) | `backend/app/adapters/audit_store.py` (`MIN_RETENTION_DAYS`), `backend/db/migrations/012_audit_retention.sql`, `015_audit_append_only.sql`, `docs/SECURITY.md` §7 |
| Request log | One line per request: path (links that are credentials redacted), method, status, timing, request id, user id, organisation id. No request bodies. Model calls: model, timing, token counts, never content | The logging service's own period: Cloud Logging's default bucket keeps 30 days unless changed; self-hosted, the machine's container logs | `backend/app/main.py` (`"request"` line), `backend/app/logsafe.py`, `docs/SECURITY.md` §12 |
| Network address | Used in memory to rate-limit, never written to the database (pre-boarding stores a keyed hash; acceptances store the network prefix). Google Cloud's load balancer and Cloud Run logs record addresses in full | In memory until the process restarts; platform logs as above | `backend/app/ratelimit.py`, `infra/terraform/observability.tf` |
| Crash reports | The web app's error text, with query strings, emails, keys and tokens removed. One log line; never stored in the database; no address, no browser name | As the request log | `backend/app/api/client_errors.py` |
| Backups | Google Cloud: daily Cloud SQL backups, 30 kept in production (`env = "prod"`; 7 otherwise), and 7 days of point-in-time recovery. Self-hosted: only when the operator runs `scripts/selfhost.sh backup` | Deleted data stays in a backup until the backup expires | `infra/terraform/data.tf` (`backup_configuration`), `docs/SECURITY.md` §8 |
| Unused | `api_tokens` exists (a hash, a name, scopes) and no route issues one today | | `backend/db/migrations/010_lifecycle.sql` |

## 12. Third parties, each with what it receives

| Third party | When | What it receives | Where | Proof |
|---|---|---|---|---|
| **Google Cloud**: Cloud Run, Cloud SQL, Cloud Storage, Pub/Sub, Secret Manager, Cloud Logging and Monitoring, Cloud Scheduler, Kubernetes Engine (workers), load balancer with Cloud Armor (only with a custom domain: `lb.tf`), Artifact Registry and Cloud Build (code, no customer data) | `MODE=gcp` | Everything: it is the host | Cloud SQL, the bucket, Cloud Run and Secret Manager: the region in `GCP_REGION`, default `asia-south1` (Mumbai). Not pinned by the Terraform: Pub/Sub topics (no `message_storage_policy`), Cloud Logging's default bucket and Monitoring | `infra/terraform/*.tf`, `backend/app/config.py` (`gcp_region`) |
| **Google Identity Platform / Firebase Authentication** | Both real modes | Email, password hash, display name, the Google account link, sign-in times; the browser talks to it directly | Google's global infrastructure; not pinned to a region here | `frontend/src/auth/firebase.ts`, `backend/app/api/auth.py`, `infra/terraform/identity.tf`, `frontend/nginx.conf.template` (`connect-src`) |
| **Gemini on Vertex AI** | `MODE=gcp` without `GEMINI_API_KEY` | For embeddings: the whole indexed text of every connected repository and uploaded document. Prompts: code and document passages, pull request diffs, redacted transcripts, questions, onboarding records an agent needs. Recordings, read from the bucket | `VERTEX_LOCATION`, default `global` (Google may serve from any region); embeddings in `EMBEDDING_LOCATION`, default the region | `backend/app/agents/core/llm.py` (`GeminiLLM`), `backend/app/services/embeddings.py`, `backend/app/config.py` |
| **Gemini API** (Google AI Studio key) | `MODE=self_hosted` with `GEMINI_API_KEY` | The same. Each recording is uploaded to the Files API, deleted by Welcome Wave when the analysis is over, and by Google 48 hours after the upload at the latest. **Free tier:** Google's terms let it use what is sent to improve its products and let people at Google read it. **Paid tier:** they do not | Google's choice | `backend/app/agents/core/llm.py` (`_media_uri`, `release_media`), `docs/INTEGRATIONS.md#gemini` |
| **GitHub** | When a repository is connected | Welcome Wave reads; it sends API requests and, at installation, a one-time OAuth code. The code has no write method. The GitHub App is given read-only permissions; self-hosted, the token is whatever the operator supplies and its scope is not checked | GitHub | `backend/app/adapters/github.py` |
| **Google Calendar, Meet, Drive** | When an organisation connects its Google account | Events Welcome Wave creates: title, agenda, times, attendees' emails (Google emails the invitations). It reads the event's recording attachment from Drive. The scopes asked (`calendar.events`, `drive.readonly`) technically allow reading any file in the connecting account's Drive; the code opens only the file attached to a session's event (`recording_attachment`) | The organisation's Google Workspace | `backend/app/adapters/google_calendar.py` |
| **Calendly** | When a person saves a Calendly link, and when the webhook key is set | The booker's name and email in the booking link; Calendly sends bookings back | Calendly | `backend/app/adapters/calendly.py` |
| **SMTP server** of the operator's choice | Only when `SMTP_HOST` is set | Recipient address and the message: invitation link, pre-boarding link, digest count, weekly summary | The provider's | `backend/app/adapters/mailer.py`, `backend/app/config.py` (`email_ready`) |
| **LangSmith** (LangChain) | Only when `LANGSMITH_TRACING=true`. Off by default; no other variable turns it on | Each agent run's prompts and outputs: code excerpts, redacted transcripts, documents, new hires' questions. Kept under LangSmith's own retention: no deletion in Welcome Wave (a recording, a person, an organisation) removes a trace | United States, or the EU with `LANGSMITH_ENDPOINT` | `backend/app/container.py` (`configure_tracing`), `backend/app/config.py` (`langsmith_tracing`), `docs/SECURITY.md` §13 |

With `GEMINI_API_KEY` set, the key is used in either mode, in place of Vertex AI (`backend/app/agents/core/llm.py`: `client`).

**Which of these applies to a server.** `GET /api/config` reports it under `legal.deployment` (`backend/app/legal.py`:
`deployment`): hosting, region, AI provider and location, email on or off, tracing on or off and where, the recording and
audit periods, and whether a verified email is required. The legal pages show it as "On this service"
(`frontend/src/legal/deployment.ts`), and `./scripts/selfhost.sh check` warns when a setting adds a sub-processor
(`deployment_report`).

Not present: advertising, analytics or session-replay scripts; third-party fonts (Geist is served by the app); payment
processing (there is no billing in the product).

Welcome Wave does not train or fine-tune a model on customer data: there is no training code in the repository, and
models are called through `generate_content` and `embed_content` only (`backend/app/agents/core/llm.py`,
`backend/app/services/embeddings.py`).

## 13. What the browser stores

No advertising or analytics cookies. One cookie, set only when a manager connects Google Calendar. Everything else is
the browser's own storage, read by the web app only.

| Name | Kind | What for | Lasts | Proof |
|---|---|---|---|---|
| `ww_google_state` | Cookie: HTTP-only, SameSite=Lax, sent only to `/api/integrations/google/callback` | Ties the Google consent page to the browser that opened it | 10 minutes | `backend/app/api/integrations.py` (`STATE_COOKIE`) |
| Firebase sign-in state | IndexedDB (`firebaseLocalStorageDb`), kept by the Firebase Authentication library | Keeps the person signed in: their ID token and refresh token | Until sign-out | `frontend/src/auth/firebase.ts` (default persistence) |
| `ww-signed-in` | localStorage | A hint so the landing page offers "Open Welcome Wave" instead of "Log in" | Until sign-out | `frontend/src/surface.ts` |
| `ww-theme` | localStorage | Light or dark | Until changed | `frontend/src/theme.ts`, `frontend/public/boot-theme.js` |
| `ww-project:<user id>` | localStorage | The project last opened | Until cleared | `frontend/src/Workspace.tsx` |
| `ww-board:<project id>` | localStorage | The person last picked on the board | Until cleared | `frontend/src/lifecycle/board/Board.tsx` |
| `ww-tour:<project id>:<user id>` | localStorage | Which code-tour stops were visited | Until cleared | `frontend/src/pages/CodeTour.tsx` |
| `ww-palette-recent` | localStorage | Recent commands | Until cleared | `frontend/src/CommandPalette.tsx` |
| `ww-audit-rows` | localStorage | Rows per page in the audit log | Until cleared | `frontend/src/lifecycle/Audit.tsx` |
| `ww-demo-user`, `ww-demo-focus` | localStorage | Demo only: the demo account and the demo clock's focus | Until cleared | `frontend/src/api.ts`, `frontend/src/lifecycle/DemoClock.tsx` |
| `ww-scroll` | sessionStorage | Scroll positions, so Back returns to the same place | The tab | `frontend/src/scroll.ts` |
| `ww-routes:<project id>` | sessionStorage | How each answer in the open chat was routed | The tab; cleared on sign-out | `frontend/src/pages/Chat.tsx`, `frontend/src/App.tsx` |
| `ww-chunk-reload` | sessionStorage | One automatic reload after an update | The tab | `frontend/src/main.tsx` |
| `ww-boot-words` | sessionStorage | Which intro words were shown | The tab | `frontend/public/boot-words.js` |

Signing in with Google opens Google's own window, where Google sets its own cookies on its own domains. If the operator
turns on Identity Platform's reCAPTCHA protection, Google's reCAPTCHA script loads on the sign-in page
(`frontend/nginx.conf.template`, `script-src`).

## 14. Encryption and isolation

| Control | Google Cloud | Self-hosted | Proof |
|---|---|---|---|
| In transit | HTTPS with HSTS; TLS-only connections to Cloud SQL | HTTPS at the operator's reverse proxy; a plain-`http` public address is refused except for `localhost` | `infra/terraform/data.tf` (`ssl_mode`), `backend/app/config.py` (`_public_url_problems`) |
| At rest | Google's default encryption for Cloud SQL and Cloud Storage | Whatever the machine's disk provides | `docs/SECURITY.md` §13 |
| Application-level | Google refresh tokens encrypted with `TOKEN_ENCRYPTION_KEY`; link tokens stored as hashes | The same | `backend/db/migrations/011_scheduling.sql` |
| Between organisations | Forced row-level security on every table with an `org_id`; the application's database role cannot bypass it; the migrator refuses to finish otherwise. The checkpoint tables, `outbox` and `processed_events` are the listed exceptions (section 10) | The same | `backend/app/migrate.py`, `docs/SECURITY.md` §2 |
| Files | Bucket with public access prevented; recordings served by signed links valid 60 minutes | Signed links from the API, valid 60 minutes; files readable only by the server's user | `infra/terraform/data.tf`, `backend/app/adapters/gcp.py` (`signed_url`), `backend/app/adapters/memory.py` (`ServedLocalStorage`) |

## 15. Not in place (so the documents do not promise it)

- Single sign-on, SCIM, enforced multi-factor authentication (`docs/SECURITY.md` §1).
- A third-party security certification (SOC 2, ISO 27001).
- A written incident-response process (`docs/SECURITY.md` §12: planned).
- An uptime commitment.
- Automatic expiry for anything other than the audit log and recordings (`docs/SECURITY.md` §8).
- Self-service download or deletion for one person: an admin exports the organisation or erases a person.
- Removing a person's Identity Platform account automatically.
- Stored files (documents, videos) inside the organisation export: it holds the records.
- Billing: fees are invoiced outside the product.
