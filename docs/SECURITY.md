# Security and data protection

An overview of Welcome Wave's security controls, organised the way a SOC 2-style review asks about them. It describes what
the code and infrastructure in this repository do today. Each control is marked:

- **In place**: implemented and covered by tests or infrastructure code.
- **In progress**: being built in the current round (see `docs/PLAN.md`); not yet released.
- **Planned**: agreed, not started.

Welcome Wave runs in three modes. `gcp` (Google Cloud) and `self_hosted` (one machine) hold real data; `demo` holds made-up
data in memory and refuses to start with `ENV=prod`. Unless a section says otherwise, it applies to both real modes.

## Controls at a glance

| Area | Control | Status |
|---|---|---|
| Access | Identity Platform (Firebase Auth) sign-in, verified email, role checks on every route | In place |
| Tenancy | Organisation isolation by Postgres row-level security, enforced at the database | In place |
| Secrets | Secret Manager on Google Cloud; nothing secret in git, images or shared archives | In place |
| Code access | GitHub App with read-only permissions; agents have no write path | In place |
| Personal data | Transcripts redacted before storage and indexing | In place |
| Audit | Audit log with an admin API, CSV export and retention | In place |
| Retention | Audit-log purge; recordings deleted after 30 days on Google Cloud, or after `RECORDING_RETENTION_DAYS` self-hosted (off by default) | In place |
| Export and deletion | Organisation-wide export and deletion, including stored files, workflow checkpoints and queued events | In place |
| Abuse | Per-person and per-address rate limits; edge throttling on Google Cloud when the load balancer is set up (a custom domain) | In place |
| Browser | Strict Content-Security-Policy and security headers on the web app and the API | In place |
| AI safety | Prompt-injection defences, schema-validated output, human approval | In place |
| Supply chain | Exact dependency versions, base images pinned by digest, CI actions by commit; a vulnerability audit that fails CI | In place |
| Operations | Structured logs (links that are credentials redacted), error alerts, an uptime check when `api_host` is set, agent-failure escalation | In place; formal incident process planned |
| Privacy of onboarding data | Survey floor of five people per team and survey day; executives see counts, never a hire's words; a hire can read everything others can read about them | In place |
| Database safety check | The migrator refuses to finish unless every organisation table has forced row-level security and a policy | In place |

## 1. Access control and roles

- **Sign-in.** People sign in with Identity Platform (Firebase Auth): Google or email and password. The API verifies the ID
  token on every request (`app/api/auth.py`). Joining or creating an organisation requires a verified email address
  (`REQUIRE_VERIFIED_EMAIL`, on by default).
- **Joining an organisation.** A login never joins an organisation silently: an invited person sees who invited them and
  accepts explicitly. Without an invitation, sign-up creates a new organisation. Members can leave, which unbinds the login.
- **Roles.** `admin`, `manager`, `expert`, `new_hire` and `executive` (read-only organisation reports). Routes declare the
  roles they allow; project routes also require project membership, and a non-member gets "not found".
- **Admin-only functions** include the audit log (section 7), organisation setup and programme templates, members
  administration, and organisation export and deletion (section 8).
- **Members administration.** Admins list the organisation's members, change their roles, and deactivate or reactivate
  them (`/api/org/members`, `app/api/members.py`). A deactivated member cannot sign in or call the API (403
  `deactivated`, checked on every request); their data stays, no new questions, mentees or KT sessions are routed to
  them, and deactivating lists their open expert requests and mentees so an admin can reassign them. The organisation
  always keeps one active admin (409 `last_admin`). Every change is audited and the member is notified.
- **Service calls.** Scheduled jobs (`POST /internal/outbox/flush`, `POST /internal/audit/purge`) accept only a
  Google-signed OIDC token for the configured audience and the scheduler's own service account; they are off otherwise.
- **Unsafe configurations refuse to start** (`Settings.production_problems`): demo mode or the Auth emulator with
  `ENV=prod`, a development webhook secret while a GitHub App is set up, a missing scheduler identity, and missing required
  settings. Without a real webhook secret, or on a self-hosted server without a GitHub App, `/webhooks/github` is not served.
- **Pre-boarding links** for people who have not started yet: expiring, scoped links whose token (256 bits) is stored only as a
  hash, shown once to the manager who made it, replaced by the next link, and rate-limited by address. What the portal accepts
  is stored as data: acknowledgements are audited as the hire's own action (`via: preboarding`). In place.
- **Addresses that are credentials** (`/welcome/<token>`, `/api/preboarding/<token>`, signed file links `/api/blobs/<token>`,
  and the Google and GitHub OAuth callbacks' `code` and `state`) are kept out of the logs Welcome Wave writes: `app/logsafe.py`
  redacts them in the API's request log and the rate limiter's log, and `frontend/nginx.conf.template` in the web server's access
  log (which records no Referer either). Platform logs record every address in full, so: on Google Cloud, a log exclusion keeps
  Cloud Run's and the load balancer's request-log entries for those addresses out of Cloud Logging
  (`infra/terraform/observability.tf`); self-hosted, the operator's HTTPS reverse proxy must keep no access log of full
  addresses ([ADMIN_GUIDE.md §15](ADMIN_GUIDE.md#15-self-hosted-release)). A pre-boarding link still lives in the hire's
  browser history and, once they run the environment check, their shell history; it expires (14 days) and is replaced by the
  next one. In place.
- Not in place today: single sign-on (SAML/OIDC with a customer's identity provider), SCIM provisioning, and enforced
  multi-factor authentication.

## 2. Tenancy and row-level security

- Every table that carries an organisation's records has an `org_id` and **forced** row-level security: a policy admits only rows
  of the organisation set in `app.org_id` for the current transaction. The API sets it from the signed-in person; background
  workers set it from the event's project. A missing filter in a query cannot return another organisation's rows.
- The exceptions are listed, with the reason for each, in `GLOBAL_TABLES` (`app/migrate.py`), and are kept apart by the
  application, not by the database: LangGraph's checkpoint tables (the saved state of an agent run paused for approval,
  keyed by run id; it can hold a draft and what it was written from, and is deleted when the run finishes or the
  organisation is deleted), the event outbox and its dedupe keys (ids, file paths and, for a pull request, its description
  and author), and the senders allowed per GitHub installation.
- The application connects as `ww_app`: no superuser, no `BYPASSRLS`, data privileges only, no temporary tables. The
  migrator (`app/migrate.py`) refuses to finish if that role could bypass row-level security, if any table with `org_id`
  lacks forced row-level security, or if a `SECURITY DEFINER` function lacks a pinned `search_path`.
- The few operations that must cross organisations (looking up a login before the tenant is known, routing a webhook,
  the audit-log purge) are `SECURITY DEFINER` functions owned by a role that cannot log in, callable only by `ww_app`.
- Isolation is tested against a real Postgres with the same roles (`tests/test_postgres.py`, `tests/test_audit.py`).

## 3. Secrets

- **Google Cloud:** secrets live in Secret Manager (`infra/terraform/secrets.tf`), each readable only by the service
  accounts that need it. Cloud Run injects them at start; workers load them with Workload Identity (`app/secrets.py`). No
  secret is baked into an image or a Kubernetes Secret.
- **Self-hosted:** `backend/selfhost.env`, generated with fresh passwords by `scripts/selfhost.sh setup` and readable only
  by its owner from the moment it is created. `scripts/selfhost.sh` runs with `umask 077`, and so does the API it starts
  (release or development): documents, recordings, generated keys and backups are readable only by their owner. The release
  passes each setting to the API container by name, so no value appears on a command line; the API container gets the
  `ww_app` connection only, never the database owner's password. Database passwords reach `psql` on its input, not its
  arguments.
- **Never shared:** `.env` files, `selfhost.env` and `*.pem` keys are git-ignored and excluded from Docker build contexts.
  `scripts/package.sh` builds archives from git-known files only, leaves secrets and local data out, and refuses to finish
  if the archive contains any.
- Signed links to self-hosted recordings use a key from `BLOB_SIGNING_KEY` or one generated into the data folder (mode
  0600). Calendar refresh tokens are stored encrypted (`TOKEN_ENCRYPTION_KEY`). In place.
- The Firebase web API key is public by design: it identifies the project and grants nothing on its own.

## 4. Read-only access to source code

- Repositories are read through a GitHub App with read-only permissions (contents, metadata, pull requests, issues,
  checks), or, self-hosted, by URL with an optional token the operator provides (its scope is not checked, so it should be read-only). The code's `RepoSource` interface has no write
  methods: no agent can push, comment, approve or merge.
- Installing the App binds it to one organisation only after GitHub OAuth proves the installer can see the installation.
- Webhooks are accepted only with a valid signature (GitHub HMAC-SHA256; Calendly's webhook signature) and are processed
  idempotently. Google Calendar and Drive are connected by each organisation through Google's consent page (a signed, expiring
  state names who asked), with a refresh token kept encrypted. In place.

## 5. Personal data and redaction

- KT call transcripts pass through a redactor before they are stored or indexed: email addresses, phone numbers, common
  API key formats (Google, AWS, GitHub, OpenAI) and `password=`/`secret:` values are replaced with labels
  (`app/services/redaction.py`). Code and uploaded documents are stored as provided.
- Replacing the pattern redactor with Google Cloud Sensitive Data Protection behind the same interface is planned.
- **Recording consent.** A KT session's recording is uploaded or imported only after someone who may add it (a manager, an
  expert or whoever booked the session) has confirmed that everyone in the session was told it is recorded and agreed. The
  session keeps who confirmed, when, and the version of the recording notice in force; the confirmation is audited
  (`recording.consent_confirmed`). Without it the upload and import routes answer 422 `consent_required`, and the poller
  that imports Google Calendar sessions' recordings fetches nothing and asks whoever booked the session, once. Welcome
  Wave cannot see who was in a call: the confirmation is that person's statement, not proof that consent was given.
  Calendar invitations for a session say that it will be recorded, what to do if you do not agree, and link to the
  recording notice (`/recording`). Each member also acknowledges the notice once per version (`app/legal.py`). A
  confirmation is refused (409 `not_held_yet`) for a session that has not started, so nothing is on record before anyone
  knows who took part. Removing a recording clears the session's confirmation (the audit log keeps the first), so adding
  a recording again needs a new one. In place (`app/services/recordings.py`, `tests/test_recording_consent.py`).
- **Asking for a recording to be removed.** Anyone on the project can ask (`POST /api/meetings/{id}/recording/removal-requests`).
  The project's managers are notified; a manager removes the recording (section 8) or declines with a note, and whoever
  asked is told either way. The request removes nothing by itself, and its reason is shown to the managers only (and is in
  the organisation export, until the person is erased, which blanks it): it is
  never sent to a model and never written to the audit log (`recording.removal_requested`, `recording.removal_declined`
  record that it happened). Someone who was recorded but has no account asks the customer's own people. In place.
- Onboarding surveys are not tied to a name (a response holds a team, a quarter and a day, never a person id; the hire's program only notes which survey days were answered, so they are pseudonymous, not untraceable) and are reported only for a
  team and survey day in which **five or more distinct people** answered: one hire answers each survey day once, so the count is
  people, never answers across days; each question's average needs five answers too. The Planner reads the same floor. The
  organisation export holds floored aggregates only, no response rows and no record of who answered on which day. In place.
- New hires can read every data point that others can read about them (`GET /api/onboardings/{id}/visibility`), with who can read
  it: their questions to a person (read in full, with their name, by the person they went to), the audit trail of their actions
  (read by admins), reviews of their pull requests, their documentation-feedback notes, the records of what they accepted
  (document, version, time, network and browser: read by admins in the export) and the recording removal requests they made
  (read by the project's managers). Executives see counts of feedback, never a note or its author. In place.
- **A question passed on to a person** is read by that person in full, with the asker's name. Once answered, the question
  and the answer are indexed as a verified-answer source: the assistant can show it, without the asker's name, to any
  project member whose question retrieves it. The Privacy Policy says so (section 4).
- **Transcripts.** Only managers and admins open a session's full transcript. Its 60-second windows are search entries, so
  the assistant reads them to answer project members and an answer can repeat what someone said.
- A new hire's chat questions are not shown to their manager or mentor: the KT planner and knowledge risk count them towards
  the code they are about ("asked 3 times by new hires"), never with the hire's name or words. Managers can read the
  assistant's run records for their project (when, the outcome, the sources), never the words. The organisation export, an
  admin's action, holds conversations in full. The hire's visibility page says all of this. Experts see no
  new hire named in their KT queue, no plan that is still awaiting approval, and only the tasks and pull request reviews
  of hires they mentor (`docs/PERMISSIONS.md`).

## 6. Prompt-injection defences

Repository files, pull requests, documents, transcripts and calendar text are untrusted: anyone who can write to them can
try to instruct the model. The defences, tested in `tests/test_injection.py` with planted instructions in a README, a pull
request description, a diff comment and a KT transcript:

- **Fenced data.** Untrusted content reaches a model only inside one `<<DATA>> ... <<END DATA>>` block
  (`app/agents/core/llm.py`). Delimiter look-alikes inside the content are neutralised, so planted text cannot close the block
  early. Every system prompt tells the model that the block is data and that it cannot approve, grant or contact anyone.
- **No authority.** Models can call a fixed set of read-only tools (`app/agents/tools/`) that search and read the same
  project's code, documents, sessions and onboarding records, scoped to the run's project and checked against the asking
  person's access. No tool writes, sends or changes anything. Agents return structured drafts; code decides what happens.
  Plans and reviews are proposals that a manager approves; nothing a model writes changes roles, access, tasks or approvals.
- **Validated output.** Every model output must match its schema (`app/agents/core/schemas.py`) or the call fails and the work
  goes to a person. Then it is checked: citations must point at retrieved sources, review findings must quote the diff,
  task paths and issues must exist, and rule checks (tests present, CI status) override the model's recommendation.
- **Identity from the platform, not the text.** A pull request moves a task only if GitHub says its author is that task's
  new hire; text such as "Closes #12" in someone else's pull request counts for nothing.
- **No echo.** Errors about invalid model output name the fields, not the content, so planted text does not travel into
  logs or incident notices.

## 7. Audit log

- Security-relevant actions are recorded with who, what, target, details and time: sign-ups and joins, invitations,
  members leaving, role changes, deactivations and reactivations, projects, repository connections and GitHub
  installations, document uploads and deletions, onboarding starts, approval decisions, KT calls and recordings, task
  moves and expert answers.
- Admins read their organisation's log at `GET /api/audit` (filter by action, person, target and date range; paged) and
  download it at `GET /api/audit.csv`. Cells that a spreadsheet would run as formulas are neutralised.
- Reading and exporting the log are themselves recorded (`audit.viewed`, `audit.exported`), as is every purge
  (`audit.purged`). The API offers no way to edit or delete individual entries, and the application's database role can only add and read them; they go with the daily purge, or when the organisation is deleted.

## 8. Retention, export and deletion

- **Audit log:** entries older than `AUDIT_RETENTION_DAYS` (default 365) are deleted daily, by Cloud Scheduler on Google
  Cloud or by the server itself when self-hosted. The database function refuses to delete anything younger than 30 days,
  whatever it is asked. The job is scheduled in Terraform (`infra/terraform/events.tf`); self-hosted, the server runs it daily.
- **Recordings:** on Google Cloud, raw KT recordings are deleted after 30 days by a bucket lifecycle rule, with soft delete
  off; notes, transcripts and links to code remain. Self-hosted, they are kept until removed unless the operator sets
  `RECORDING_RETENTION_DAYS` (0 by default, which keeps them): the server then removes, once a day, each raw video file
  stored longer ago than that, and files under `recordings/` that no session names (an upload that was never completed).
  A session whose notes are still being written is left for the next round; notes, transcripts and links to code remain;
  each removal is audited (`recording.retention_removed`): the session is updated and the audit entry written first, then
  the file is deleted, so a failure in between leaves a file no session names, which the next round removes. The setting
  does nothing on Google Cloud, where the bucket rule applies. A manager can remove a session's recording, notes,
  transcript and its search entries at any time, after typing its title (audited); that also answers the session's open
  removal requests and clears its consent record (section 5).
- **Gemini's copy of a recording (self-hosted):** the file is uploaded to the Gemini Files API for the analysis and deleted
  there when the analysis finishes or fails (`release_media` in `app/agents/core/llm.py`); a copy that could not be
  deleted is removed by Google 48 hours after the upload. On Google Cloud, Vertex AI reads the file from the bucket and
  no copy is made.
- **Documents** can be deleted by a manager at any time, with their stored file (audited).
- **Erasing a person:** on their request, an admin replaces a person's name, email and GitHub username on their account,
  unbinds their login, deletes their chats, questions to a person, notifications, scheduling links and invitations, and
  blanks the reasons they wrote in recording removal requests (`POST /api/org/people/erase`, audited). Their onboarding
  records stay under the erased name. **Erasure does not reach copies made earlier**, and the route's answer and the
  Privacy Policy (section 9) list them: audit entries whose detail holds an address or username (an invitation, a changed
  GitHub username, a connected Google account) until they expire; session attendee lists; recordings and transcripts the
  person speaks in; verified answers made from a question they passed on; their documentation-feedback notes; drafts
  waiting for approval; notices already sent to colleagues; records of acceptance, by id; and backups until they expire.
  A recording or a document is removed from its own page. Their sign-in account at Identity Platform or Firebase stays,
  because one login can belong to other organisations; the operator removes it on request.
- **Records of acceptance:** who accepted which version of the terms, the privacy policy, the recording notice and (for the
  organisation, by an admin) the data-processing agreement, with the time, how (sign-up, invitation, the in-app prompt) and
  where from, kept small: the network only (IPv4 cut to /24, IPv6 to /48, never the full address) and the browser's
  description (200 characters). The table is append-only for the application, like the audit log: the app role can add and
  read rows, never change or remove one (`019_legal.sql`; the migrator refuses to finish otherwise). Rows name people by id
  only, so erasing a person leaves them standing under the erased identity; they are in the organisation's export and are
  deleted with the organisation. No retention period removes them earlier ([specs/legal-consent.md](specs/legal-consent.md)).
- **Backups (Google Cloud):** daily Cloud SQL backups, the last 30 kept in production (7 in other environments), and 7 days of point-in-time recovery.
  Deleted data remains in backups until they expire.
- **Backups (self-hosted):** `scripts/selfhost.sh backup` writes one owner-only file with the database dump (checked complete),
  the data folder (documents, recordings, the token and signing keys) and `selfhost.env`, with checksums; a failed backup leaves
  no file. `scripts/selfhost.sh restore` checks the checksums, creates the database roles before loading the dump (so the
  cross-organisation functions stay owned by `ww_definer`, the role that cannot log in, and not by the superuser that restores),
  loads it in one transaction that stops at the first error, applies newer migrations, and refuses to finish unless every
  `SECURITY DEFINER` function is owned by `ww_definer`. It refuses to overwrite existing data unless told to, and then backs it
  up first. Rehearsed in CI on every change (`.github/workflows/ci.yml`). The operator keeps the files, off the machine, as
  safely as the machine itself ([ADMIN_GUIDE.md §15](ADMIN_GUIDE.md#15-self-hosted-release)).
- **Organisation export and deletion:** an admin downloads every organisation-scoped table as JSON (credentials and
  derived search data left out; the survey as floored aggregates only) or deletes all of it after typing the organisation's
  name. Both are audited (`GET /api/org/export`, `DELETE /api/org`). Deletion removes the rows in one transaction, then the
  stored documents and recordings (found by the uris the rows hold, before they go), the LangGraph checkpoints of the runs
  its approvals paused, and the queued outbox events of its projects; a file that cannot be removed is reported, not hidden
  (`app/lifecycle/orgdata.py`). The organisation's Google grant is revoked at Google. In place. Deleted data remains in database backups until they expire.
- **LangSmith traces**, when tracing is on (section 13), are kept by LangSmith under its own retention. No deletion here
  (a recording, a person, an organisation) removes a trace there.
- Other data is kept for as long as the organisation exists. Configurable retention for other data is not in place.

## 9. Rate limits and abuse protection

- An in-process token bucket limits every route (`app/ratelimit.py`): 120 requests a minute per person (or per address
  before sign-in); 20 for questions to the model; 10 for sign-in and sign-up per address; 30 for pre-boarding links, 60
  for webhooks and 10 for the web app's crash reports (`POST /api/client-errors`, no sign-in, 16 KB at most, logged
  redacted and never stored) per address. Over the limit the API answers 429 with `Retry-After`. Limits are per server process.
- The chat route also limits questions per person (`CHAT_RATE_PER_MINUTE`), and demo sign-in attempts are limited per email.
- On Google Cloud with the load balancer, Cloud Armor throttles any single address above 600 requests a minute.
- Uploads are capped: 25 MB per document, 2 GB per recording.

## 10. Browser security: CSP and headers

- **Web app** (`frontend/nginx.conf.template`, in the web image that both Google Cloud and the self-hosted release run): a
  Content-Security-Policy that allows scripts only from the app itself and Google's sign-in and reCAPTCHA hosts, with no inline
  scripts and no `eval` (checked against every production build in CI); network calls only to the app's API, Firebase Auth and
  Cloud Storage (a recording's signed upload on Google Cloud);
  workers and fonts only from the app itself (no Google Fonts); no plugins, no `<base>`, forms only to the app, and `frame-ancestors 'none'`. Styles allow inline styles (the intro and sign-in libraries set them). Also HSTS,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and a
  `Permissions-Policy` that turns off camera, microphone, geolocation, payment, USB and similar features.
- **API**: `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `nosniff`, `X-Frame-Options: DENY`,
  HSTS, `Cache-Control: no-store`, and a request id on every response. Cross-origin requests are refused unless
  `CORS_ORIGINS` names the origin.
- **Host names** (self-hosted release): the web server answers only to the host name of `PUBLIC_URL`, `localhost` and any the
  operator lists (`WW_WEB_HOSTS`); other names get 421, so a page that rebinds its own name to the server's address cannot reach
  the API. It listens on `127.0.0.1` behind the operator's HTTPS reverse proxy, and the release refuses a plain-`http://`
  `PUBLIC_URL` for any host but `localhost` ([ADMIN_GUIDE.md §15](ADMIN_GUIDE.md#15-self-hosted-release)).
- Recordings are served through short-lived signed links (60 minutes).
- The web server runs as an unprivileged user. The Vite development server (`scripts/selfhost.sh start`) sends none of these
  headers and listens on localhost only; it is never used to serve people's work.

## 11. Dependencies and supply chain

- Images and CI install exact versions: `backend/requirements.lock` is complete and installed with `--no-deps`, so nothing is
  resolved at build time, and `pip check` fails the build if a dependency is missing; `frontend/package-lock.json` is installed
  with `npm ci` and fetches only from the public npm registry (`frontend/.npmrc`; CI fails on any other host). The API image
  carries no pip and runs as a non-root user; the web image's nginx runs as an unprivileged user.
- Base images are pinned by digest (`python:3.12-slim`, `node:22-slim`, `nginx:1.30-alpine`, a supported nginx and Alpine),
  CI actions by commit, and development tools by version (`backend/requirements-dev.txt`). Dependabot proposes updates to all of
  them weekly (`.github/dependabot.yml`), each tested by CI before a person merges it.
- CI fails on a known vulnerability (`.github/workflows/ci.yml`): any advisory in the API image's Python packages (`pip-audit`),
  and any high or critical advisory in the npm packages that ship to browsers. An advisory reviewed and judged unreachable is
  accepted by its id, with the reason next to it (today one: `@grpc/grpc-js`, which comes with Firebase's Node build of
  Firestore and is not in the bundle). Advisories in development tools only (the Vite dev server) are reported, not enforced.
- Not in place: hashes in `requirements.lock` (`--require-hashes`), and scanning the built images (Artifact Registry).
- Adding a dependency requires the founders' approval, with an alternative considered (`CLAUDE.md`).

## 12. Logging, monitoring and incident response

- The API writes one structured JSON log line per request (request id, user and organisation ids, status, latency),
  without request bodies, and with credential-bearing paths redacted (see section 1). Model calls log the model, latency and
  token counts, not the content. Email sending logs the kind of message and the recipient's domain, never a subject, body or link.
- On Google Cloud: alerts on server errors, dead-lettered events and events that fail to publish, and, when `api_host`
  is set in Terraform, an uptime check with an alert when the API is down, sent to the on-call email channel (`infra/terraform/observability.tf`).
- When an agent fails after its retries, the project's managers get an incident notice and the run is recorded.
- Containment tools that exist today: revoking the GitHub App installation, rotating secrets in Secret Manager, turning
  off sign-up (`SIGNUP_ENABLED`), and the audit log for investigation.
- A written incident response process (roles, severity levels, customer notification times) is planned.

## 13. Data processing overview

For customers' security and privacy reviews.

| Data | Source | Purpose | Stored in |
|---|---|---|---|
| Account data: name, email, role, GitHub username | The person, their admin | Sign-in, permissions, routing questions | Postgres; Identity Platform |
| Source code and pull requests | The customer's GitHub repositories (read-only) | Answers with citations, plans, reviews | Postgres (chunks, embeddings) |
| Documents | Uploaded by members | Answers with citations | Cloud Storage or local disk; Postgres (text, embeddings) |
| KT call recordings | Uploaded by members, or imported from the organisation's Google Drive | Notes, transcripts, links to code | Cloud Storage or local disk; raw recordings deleted after 30 days on Google Cloud. Self-hosted: raw recordings are kept until removed, or for `RECORDING_RETENTION_DAYS` when that is set. Self-hosted with a Gemini API key: each recording is also uploaded to the Gemini Files API for analysis; Welcome Wave deletes that copy when the analysis finishes or fails, and a copy left by a server that stopped mid-analysis (or whose deletion Google refused) is deleted by Google 48 hours after the upload |
| Transcripts and notes (redacted) | Derived from recordings | Answers, coverage, plans | Postgres |
| Questions, answers, plans, reviews, onboarding progress | Members and agents | The product itself | Postgres |
| Audit log and request logs | The service | Security, operations | Postgres; Cloud Logging |

- **Where it runs.** Google Cloud: Cloud Run, Cloud SQL, Cloud Storage and Secret Manager in the region set by
  `GCP_REGION` (default `asia-south1`). Pub/Sub, Cloud Logging and Monitoring are not pinned to that region by the
  Terraform, and Identity Platform runs on Google's global infrastructure. Self-hosted: the operator's machine, with
  Identity Platform for sign-in and, if a key is set, the Gemini API.
- **What a given server does.** `GET /api/config` reports, under `legal.deployment`, the facts the legal documents branch
  on: hosting, region, AI provider and location, email on or off, tracing on or off and where, the recording and audit
  periods, and whether a verified email is required (`app/legal.py`). The legal pages show them as "On this service", and
  `./scripts/selfhost.sh check` warns when a setting adds a sub-processor (`LANGSMITH_TRACING`, `SMTP_HOST`) or swaps
  Vertex AI for the Gemini API on Google Cloud (`GEMINI_API_KEY`).
- **AI processing.** Gemini models on Vertex AI (Google Cloud) or the Gemini API (self-hosted, with the operator's key; a
  `GEMINI_API_KEY` set on Google Cloud is used in place of Vertex AI). To build the search index, the whole indexed text
  of every connected repository and uploaded document is sent for embeddings; prompts carry passages, pull request diffs,
  redacted transcripts, questions and the onboarding records an agent needs.
  `VERTEX_LOCATION` defaults to `global`, which lets Google serve requests from any region; set a regional location where
  data residency matters. Welcome Wave does not train or fine-tune models on customer data; the provider's use of
  prompts is governed by its terms. Self-hosted, those terms depend on the key: on the Gemini API's free tier Google may use
  prompts and answers (source code, transcripts, recordings, hires' questions) to improve its products and people at Google may
  read them; on the paid tier (billing turned on) it does not. Real data needs a paid-tier key
  ([INTEGRATIONS.md](INTEGRATIONS.md#gemini)).
- **Other services.** GitHub (the customer's own App installation or read-only token). Google Calendar, Google Meet and Drive,
  and Calendly, for scheduling KT sessions and importing their recordings (each optional, each connected by the organisation).
  An SMTP server of the customer's choice, if one is configured, for invitations, pre-boarding links, the expert digest and the
  weekly summary (docs/INTEGRATIONS.md): the digest names a count and a link, never a question.
- **LangSmith tracing: off unless the organisation decides otherwise.** A trace holds an agent run's prompts and outputs:
  code excerpts, redacted transcripts, documents and new hires' questions. Sending them to LangSmith (a third party, in the
  US unless its EU endpoint is chosen) is a data-processing decision for the customer, so tracing is opt-in: it runs only
  when `LANGSMITH_TRACING=true` (`_LANGSMITH_TRACING` in the Cloud Build trigger, for the API and the workers alike), and
  no other LangChain or LangSmith variable can turn it on (`app/container.py configure_tracing`). Record the decision,
  the region and LangSmith's retention with the customer's processor list before turning it on: LangSmith is then a
  sub-processor, and the data-processing agreement promises admins 30 days' notice. Traces are not removed by removing a
  recording, erasing a person or deleting an organisation. When it is on, each agent
  run's record carries its trace id (`trace_id` in `GET /api/runs`), the link to that run's trace.
- **Encryption.** In transit: HTTPS (HSTS), and TLS-only connections to Cloud SQL. At rest on Google Cloud: Google's default
  encryption for Cloud SQL and Cloud Storage. Self-hosted: as provided by the operator's disk.
- **Who can see what.** Members see their projects; admins see their organisation; nobody outside the organisation sees its
  data (section 2). Recordings are visible to project members only.
- **Legal documents and acceptances.** The terms, privacy policy, recording notice, data-processing agreement and
  sub-processor list are in the product, as drafts awaiting a lawyer ([LEGAL.md](LEGAL.md)); every statement in them about
  data is backed by [legal/DATA_INVENTORY.md](legal/DATA_INVENTORY.md). Who accepted which version is recorded
  (section 8), and each recorded session carries its consent record (section 5).
