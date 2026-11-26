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
| Retention | Audit-log purge; recordings deleted after 30 days on Google Cloud | In place |
| Export and deletion | Organisation-wide export and deletion, including stored files, workflow checkpoints and queued events | In place |
| Abuse | Per-person and per-address rate limits; edge throttling on Google Cloud | In place |
| Browser | Strict Content-Security-Policy and security headers on the web app and the API | In place |
| AI safety | Prompt-injection defences, schema-validated output, human approval | In place |
| Supply chain | Pinned dependencies; vulnerability audit in CI (report only) | In place; enforcing it is planned |
| Operations | Structured logs (links that are credentials redacted), uptime and error alerts, agent-failure escalation | In place; formal incident process planned |
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
  `ENV=prod`, a development webhook secret, a missing scheduler identity, and missing required settings.
- **Pre-boarding links** for people who have not started yet: expiring, scoped links whose token (256 bits) is stored only as a
  hash, shown once to the manager who made it, replaced by the next link, rate-limited by address, and never written to a log
  (`app/logsafe.py` redacts `/welcome/<token>`, `/api/preboarding/<token>`, signed file links and OAuth `code` and `state` from
  the request log, the rate limiter's log and the server's access log). What the portal accepts is stored as data: acknowledgements
  are audited as the hire's own action (`via: preboarding`). In place.
- Not in place today: single sign-on (SAML/OIDC with a customer's identity provider), SCIM provisioning, and enforced
  multi-factor authentication.

## 2. Tenancy and row-level security

- Every table that holds customer data carries an `org_id` and has **forced** row-level security: a policy admits only rows
  of the organisation set in `app.org_id` for the current transaction. The API sets it from the signed-in person; background
  workers set it from the event's project. A missing filter in a query cannot return another organisation's rows.
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
  by its owner.
- **Never shared:** `.env` files, `selfhost.env` and `*.pem` keys are git-ignored and excluded from Docker build contexts.
  `scripts/package.sh` builds archives from git-known files only, leaves secrets and local data out, and refuses to finish
  if the archive contains any.
- Signed links to self-hosted recordings use a key from `BLOB_SIGNING_KEY` or one generated into the data folder (mode
  0600). Calendar refresh tokens are stored encrypted (`TOKEN_ENCRYPTION_KEY`). In place.
- The Firebase web API key is public by design: it identifies the project and grants nothing on its own.

## 4. Read-only access to source code

- Repositories are read through a GitHub App with read-only permissions (contents, metadata, pull requests, issues,
  checks), or, self-hosted, by URL with an optional read-only token. The code's `RepoSource` interface has no write
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
- Onboarding surveys are anonymous (a response holds a team, a quarter and a day, never a person) and are reported only for a
  team and survey day in which **five or more distinct people** answered: one hire answers each survey day once, so the count is
  people, never answers across days; each question's average needs five answers too. The Planner reads the same floor. The
  organisation export holds floored aggregates only, no response rows and no record of who answered on which day. In place.
- New hires can read every data point that others can read about them (`GET /api/onboardings/{id}/visibility`), with who can read
  it: their questions to a person (read in full, with their name, by the person they went to), the audit trail of their actions
  (read by admins), reviews of their pull requests, and their documentation-feedback notes. Executives see counts of feedback, never
  a note or its author. In place.
- A new hire's chat questions are theirs: the KT planner and knowledge risk count them towards the code they are about
  ("asked 3 times by new hires"), never with the hire's name or words, for managers and experts alike. Experts see no
  new hire named in their KT queue, no plan that is still awaiting approval, and only the tasks and pull request reviews
  of hires they mentor (`docs/PERMISSIONS.md`).

## 6. Prompt-injection defences

Repository files, pull requests, documents, transcripts and calendar text are untrusted: anyone who can write to them can
try to instruct the model. The defences, tested in `tests/test_injection.py` with planted instructions in a README, a pull
request description, a diff comment and a KT transcript:

- **Fenced data.** Untrusted content reaches a model only inside one `<<DATA>> ... <<END DATA>>` block
  (`app/agents/llm.py`). Delimiter look-alikes inside the content are neutralised, so planted text cannot close the block
  early. Every system prompt tells the model that the block is data and that it cannot approve, grant or contact anyone.
- **No authority.** Models have no tools. Agents return structured drafts; code decides what happens. Plans and reviews are
  proposals that a manager approves; nothing a model writes changes roles, access, tasks or approvals.
- **Validated output.** Every model output must match its schema (`app/agents/schemas.py`) or the call fails and the work
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
  (`audit.purged`). The API offers no way to edit or delete individual entries.

## 8. Retention, export and deletion

- **Audit log:** entries older than `AUDIT_RETENTION_DAYS` (default 365) are deleted daily, by Cloud Scheduler on Google
  Cloud or by the server itself when self-hosted. The database function refuses to delete anything younger than 30 days,
  whatever it is asked. The job is scheduled in Terraform (`infra/terraform/events.tf`); self-hosted, the server runs it daily.
- **Recordings (Google Cloud):** raw KT recordings are deleted after 30 days by a bucket lifecycle rule, with soft delete
  off; notes, transcripts and links to code remain.
- **Documents** can be deleted by a manager at any time (audited).
- **Backups (Google Cloud):** daily Cloud SQL backups, kept 30 days in production, and 7 days of point-in-time recovery.
  Deleted data remains in backups until they expire. Self-hosted: `scripts/selfhost.sh backup`, kept by the operator.
- **Organisation export and deletion:** an admin downloads every organisation-scoped table as JSON (credentials and
  derived search data left out; the survey as floored aggregates only) or deletes all of it after typing the organisation's
  name. Both are audited (`GET /api/org/export`, `DELETE /api/org`). Deletion removes the rows in one transaction, then the
  stored documents and recordings (found by the uris the rows hold, before they go), the LangGraph checkpoints of the runs
  its approvals paused, and the queued outbox events of its projects; a file that cannot be removed is reported, not hidden
  (`app/lifecycle/orgdata.py`). In place. Deleted data remains in database backups until they expire.
- Other data is kept for as long as the organisation exists. Configurable retention for other data is not in place.

## 9. Rate limits and abuse protection

- An in-process token bucket limits every route (`app/ratelimit.py`): 120 requests a minute per person (or per address
  before sign-in); 20 for questions to the model; 10 for sign-in and sign-up per address; 30 for pre-boarding links and 60
  for webhooks per address. Over the limit the API answers 429 with `Retry-After`. Limits are per server process.
- The chat route also limits questions per person (`CHAT_RATE_PER_MINUTE`), and demo sign-in attempts are limited per email.
- On Google Cloud with the load balancer, Cloud Armor throttles any single address above 600 requests a minute.
- Uploads are capped: 25 MB per document, 2 GB per recording.

## 10. Browser security: CSP and headers

- **Web app** (`frontend/nginx.conf.template`): a Content-Security-Policy that allows scripts only from the app itself and
  Google's sign-in and reCAPTCHA hosts, with no inline scripts and no `eval` (checked against every production build in CI);
  network calls only to the app's API and Firebase Auth; no plugins, no `<base>`, forms only to the app, and
  `frame-ancestors 'none'`. Styles allow inline styles (the intro and sign-in libraries set them). Also HSTS,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and a
  `Permissions-Policy` that turns off camera, microphone, geolocation, payment, USB and similar features.
- **API**: `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `nosniff`, `X-Frame-Options: DENY`,
  HSTS, `Cache-Control: no-store`, and a request id on every response. Cross-origin requests are refused unless
  `CORS_ORIGINS` names the origin.
- Recordings are served through short-lived signed links (60 minutes).

## 11. Dependencies and supply chain

- Images install exact versions (`backend/requirements.lock`, `frontend/package-lock.json`). The API image runs as a
  non-root user.
- CI audits Python and npm dependencies for known vulnerabilities on every push (`pip-audit`, `npm audit` at high
  severity). Today the audit reports without failing the build; making it fail on high-severity findings is planned.
- Adding a dependency requires the founders' approval, with an alternative considered (`CLAUDE.md`).

## 12. Logging, monitoring and incident response

- The API writes one structured JSON log line per request (request id, user and organisation ids, status, latency),
  without request bodies, and with credential-bearing paths redacted (see section 1). Model calls log the model, latency and
  token counts, not the content. Email sending logs the kind of message and the recipient's domain, never a subject, body or link.
- On Google Cloud: an uptime check with an alert when the API is down, and alerts on server errors, dead-lettered events
  and events that fail to publish, sent to the on-call email channel (`infra/terraform/observability.tf`).
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
| KT call recordings | Uploaded by members, or imported from the organisation's Google Drive | Notes, transcripts, links to code | Cloud Storage or local disk; raw recordings deleted after 30 days on Google Cloud |
| Transcripts and notes (redacted) | Derived from recordings | Answers, coverage, plans | Postgres |
| Questions, answers, plans, reviews, onboarding progress | Members and agents | The product itself | Postgres |
| Audit log and request logs | The service | Security, operations | Postgres; Cloud Logging |

- **Where it runs.** Google Cloud in the region set by `GCP_REGION` (default `asia-south1`): Cloud Run, Cloud SQL,
  Cloud Storage, Pub/Sub, Identity Platform, Secret Manager and Cloud Logging. Self-hosted: the operator's machine, with
  Identity Platform for sign-in and, if a key is set, the Gemini API.
- **AI processing.** Gemini models on Vertex AI (Google Cloud) or the Gemini API (self-hosted, with the operator's key).
  `VERTEX_LOCATION` defaults to `global`, which lets Google serve requests from any region; set a regional location where
  data residency matters. Welcome Wave does not train or fine-tune models on customer data; the provider's use of
  prompts is governed by its terms.
- **Other services.** GitHub (the customer's own App installation or read-only token). Google Calendar, Google Meet and Drive,
  and Calendly, for scheduling KT sessions and importing their recordings (each optional, each connected by the organisation).
  An SMTP server of the customer's choice, if one is configured, for invitations, pre-boarding links, the expert digest and the
  weekly summary (docs/INTEGRATIONS.md): the digest names a count and a link, never a question.
- **LangSmith tracing: off unless the organisation decides otherwise.** A trace holds an agent run's prompts and outputs:
  code excerpts, redacted transcripts, documents and new hires' questions. Sending them to LangSmith (a third party, in the
  US unless its EU endpoint is chosen) is a data-processing decision for the customer, so tracing is opt-in: it runs only
  when `LANGSMITH_TRACING=true` (`_LANGSMITH_TRACING` in the Cloud Build trigger, for the API and the workers alike), and
  no other LangChain or LangSmith variable can turn it on (`app/container.py configure_tracing`). Record the decision,
  the region and LangSmith's retention with the customer's processor list before turning it on. When it is on, each agent
  run's record carries its trace id (`trace_id` in `GET /api/runs`), the link to that run's trace.
- **Encryption.** In transit: HTTPS (HSTS), and TLS-only connections to Cloud SQL. At rest on Google Cloud: Google's default
  encryption for Cloud SQL and Cloud Storage. Self-hosted: as provided by the operator's disk.
- **Who can see what.** Members see their projects; admins see their organisation; nobody outside the organisation sees its
  data (section 2). Recordings are visible to project members only.
