# Admin guide

For the admins who set up and run a Welcome Wave organisation, and the engineers who deploy it. Deployment itself is in
[DEPLOY.md](DEPLOY.md), security controls in [SECURITY.md](SECURITY.md), who may call what in [PERMISSIONS.md](PERMISSIONS.md).
Every server setting is an environment variable; the main ones are in `backend/app/config.py`.

| Mode | Where settings go |
|---|---|
| `demo` | The command's own environment. Demo mode reads no env file. |
| `self_hosted` | `backend/selfhost.env`. `./scripts/selfhost.sh release` passes each setting in it to the API container by name; the development commands (`start`, `api`) load it (`set -a`) before they start the API. Section 15. |
| `gcp` | `build_substitutions` in `infra/terraform/<env>.tfvars`, applied by the Cloud Build trigger. Every deploy re-applies the variables named in `cloudbuild.yaml` (`--update-env-vars`); others set on the service, such as `FEATURES`, `RATE_LIMIT_*` and `AUDIT_*`, are kept. |

## 1. First sign-in and workspace setup

1. Open the web URL, sign in (Google or email), verify the email and create the organisation. You become its admin.
   Self-hosted, that first organisation closes sign-up: everyone else joins by invitation (`SIGNUP_ENABLED=true` lets anyone
   create another). On Google Cloud sign-up stays open unless `SIGNUP_ENABLED=false`.
2. Open **Setup** (`#/setup`, `g u`). Each step is checked from your data; you can mark any **Done**, **Skip** or **Reopen**.

| Step | Done when | Where |
|---|---|---|
| Connect a repository | A project has a repository | Settings > Repository |
| Add teams | The organisation has a team | **Teams**, on this page below the steps (also Settings > Onboarding) |
| Review the program templates | Templates exist (the 16 defaults are created on first read) | Templates |
| Invite managers and experts | A project has a second member or an invitation | Settings > People |
| Enrol the first hire | A program exists | Enrol |
| Check the senior-time estimate | You marked it done | Setup, **Minutes per avoided interruption** (default 15), **Save** |

The estimate's minutes feed "Senior time saved" in Reports, which shows the assumption next to the figure.

## 2. Connect GitHub

Welcome Wave reads code; it never writes. The repository interface (`RepoSource`) has no write methods, and the GitHub App
asks only for read permissions. One repository per project.

| Mode | How |
|---|---|
| Google Cloud | Create the GitHub App ([DEPLOY.md §2](DEPLOY.md#2-third-party-setup)): Contents, Metadata, Pull requests, Issues, Checks read-only; Members read-only at organisation level; events Push and Pull request; webhook `https://<web>/webhooks/github`. Then **Settings > Repository**: install the App, come back through the callback, pick the repository. Only the GitHub user who installed the App (or an admin of that GitHub account) can bind it to your organisation. |
| Self-hosted | **Settings > Repository**: paste the GitHub URL. A private repository needs a read-only `GITHUB_TOKEN` on the server (it also raises GitHub's limit to 5,000 calls an hour). The server checks for new commits and pull requests every `REPO_POLL_S` seconds (default 120). |
| Demo | The sample repository in `backend/sample_repo` is already connected. |

Give each new hire their GitHub username when you invite or enrol them: a pull request moves a task only if GitHub says its
author is that task's hire.

## 3. Google Calendar (with Google Meet)

KT sessions are booked on the organisation's Google Calendar with a Meet link, and their recordings come back from Google
Drive. Full design: [specs/scheduling.md](specs/scheduling.md).

Server settings (once per deployment):

| Setting | Value |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | A Web-application OAuth client with the Calendar and Drive APIs enabled; scopes `calendar.events` and `drive.readonly`. Both empty (or `unused`): Google is off. |
| `GOOGLE_OAUTH_REDIRECT_URI` | Default `PUBLIC_URL/api/integrations/google/callback`; register it on the OAuth client. |
| `TOKEN_ENCRYPTION_KEY` | Encrypts the stored refresh token. Required on Google Cloud; self-hosted it is generated into `DATA_DIR/.token-key` when empty. Changing it means connecting again. |
| `RECORDING_POLL_S` | Self-hosted: how often ended sessions are checked for their recording (default 300; 0 = never). Google Cloud uses the `ww-recordings-poll` Scheduler job every 10 minutes. |

Connect (a manager or admin): **Settings > Integrations > Connect Google Calendar**, consent at Google, and you come back to
`#/settings?tab=integrations&google=connected` (or `denied`, `expired`, `failed`). One Google account per organisation.
**Disconnect** revokes the grant; sessions already booked keep their Meet links.

After a session, its recording is imported by the poller (10 minutes after the end) or by **Import from Google Drive** on the
session. A session with no recording 48 hours after its end becomes *ended*; a recording can still be uploaded.

**Recording consent.** No recording is uploaded or imported until a manager, an expert or whoever booked the session has
confirmed that everyone in it was told it is recorded and agreed. The upload and import buttons ask; the answer is kept with
the session (who, when, which version of the recording notice) and in the audit log. The poller imports nothing for a session
nobody confirmed: it sends whoever booked it one notification, and imports on its next round once they confirm. Calendar
invitations say the session will be recorded and link to the recording notice (`/recording`; set `PUBLIC_URL` so the link
is a full address). Anyone on the project can ask for a recording to be removed, from the session's page; the project's
managers are notified and remove it or decline with a note.

**Accepting the data-processing agreement.** The organisation's agreement with the operator is accepted once per version by an
admin: **Settings** > **Organisation** > **Legal** shows the current version, who accepted it and when, and a text file of the
record. Until an admin accepts it, a quiet note on every workspace page says so; nothing is blocked. A new version needs a new
acceptance (the earlier ones stay listed). The terms, privacy policy and recording notice are accepted by each person for
themselves, at sign-up or on the consent screen. `LEGAL_ENFORCE` (section 14) makes the API refuse workspace routes until they
have; `RECORDING_RETENTION_DAYS` (self-hosted) is described under retention. The texts are drafts until a lawyer has read them
([LEGAL.md](LEGAL.md)).

## 4. Calendly

1. Each person saves their link: **Settings > Integrations > Your Calendly link** (`calendly.com` links only).
2. For bookings to come back, create one webhook subscription for your Calendly organisation to `https://<web>/webhooks/calendly`
   with events `invitee.created` and `invitee.canceled`, scope `organization`, and a signing key you choose
   ([DEPLOY.md §2](DEPLOY.md#2-third-party-setup)).
3. Set `CALENDLY_WEBHOOK_SIGNING_KEY` to that key. Empty: the webhook answers 404 and Calendly is offered without the
   automatic booking.

Bookings made outside Welcome Wave (without `utm_source=welcome-wave`) are ignored. Cancelling a Calendly session in Welcome
Wave does not cancel it in Calendly; the answer says so.

## 5. People and roles

| Role | Can |
|---|---|
| `admin` | Everything, in every project; the admin pages (Setup, People, Templates (edit), Audit log, export and deletion) |
| `manager` | Projects, repositories, documents, invitations, enrolment, approvals, programs, reports for their projects |
| `expert` | Answer routed questions, verify answers, deliver and book their KT sessions, mentor, own feedback, upload documents |
| `new_hire` | Their own program, tasks, questions, private reviews, check-ins and survey |
| `executive` | Read-only: organisation reports, survey aggregates, their Home |

| Task | Steps | Rules |
|---|---|---|
| Invite | **Settings > People**: email, role, name, GitHub username, **Invite**. They join when they first sign in with that email and accept. | Only admins invite admins. An invitation made here is not emailed: send them the sign-in link the page shows (`PUBLIC_URL/login`). A new hire's invitation is emailed when their plan is approved, if email is set up ([INTEGRATIONS.md](INTEGRATIONS.md#email-smtp)). Only admins invite executives. |
| Change a role | **People** (`#/people`, `g e`) > the role list next to the person. | 409 `last_admin` if no active admin would be left. Audited; the person is told. |
| Deactivate | **People** > **Deactivate**. | They are refused on every request (403 `deactivated`); their data stays. Nothing new is routed to them. The page lists their mentees and open questions to reassign. You cannot deactivate yourself (409 `self`). |
| Reactivate | **People** > **Reactivate**. | |
| Remove a pending invitation | **Settings > People** > **Revoke**. | |

A person can also leave the organisation themselves (**Settings > Account > Leave the organisation**, production only); their
user row is kept so a new invitation can bring them back.

## 6. Program templates, milestones and checkpoints

Two words, two things, everywhere in the app:

- **Milestones** are the board's columns of time: Day 15 · Settled in, Day 30 · First change shipped, Day 45 · Working on
  your own, Day 60 · Owning an area, Day 90 · Fully on board by default. Every task sits in one.
- **Checkpoints** are what a program checks along the way: pre-boarding done, environment passing, access settled, meet the
  team, first PR opened and merged, check-ins at days 30, 60 and 90, an area at *working*, a merged change in the focus area,
  plus one per track and level (pairing for Juniors, a design review for Seniors and Staff, a written technical direction for
  Staff). The board shows each checkpoint inside the milestone whose days it falls in.

Templates:

- 16 defaults: Backend, Frontend, SRE and Data, each at Junior, Mid, Senior and Staff. Each has board milestones,
  checkpoints, access items, documents to acknowledge and a plan used when no AI model is available.
- Checkpoint days are days of a 90-day program; another length (14 to 365 days) scales them, so a 120-day program has
  check-ins on days 40, 80 and 120.
- **Templates** (`#/templates`, `g l`) > **Edit** makes a new version: **Board milestones** (up to 12, one per day; **+ Board
  milestone**) and **Checkpoints** (**+ Checkpoint**). Programs already running keep the version they started with, and a
  board keeps the milestones it was given. Only the active version can be changed.
- Automatic checkpoints tick themselves from evidence: documents acknowledged, environment check passing, access settled,
  first PR opened and merged, both check-in halves, an area at *working* or *contributed*.

### The board, per hire

**Board** (`#/board`, `g b`; managers pick a hire, a hire sees their own):

- **Move a card**: drag it, or focus it and press `Alt` + `←` `→` (columns), `Alt` + `↑` `↓` (its place), `Alt` + `Shift` +
  `←` `→` (managers: the milestone before or after). Every card also has a `⋯` menu.
- **The hire** moves their own cards. Their **Done** means "waiting for sign-off"; their manager signs it off (**Sign off**)
  or sends it back (**Ask for changes**). A task with a pull request is decided by its review in Approvals.
- **Managers and admins** also add (**+ Task**), edit and remove tasks, move tasks between milestones (the card keeps "Moved
  from Day 15", and the milestone it left lists it under "Moved out"), and edit the milestones themselves (**Milestones**:
  rename, re-date, reorder, add, remove with "move its tasks to"). The hire is told about each change that concerns them.
- A hire whose own role is manager or admin is still the hire on their own board: they cannot edit it or re-date its tasks.
- Every change is in the audit log (`board.*`), and the managers' Home shows it under Recent activity.

## 7. Handovers

When someone leaves, everything they hold goes to a successor, so nobody starts from scratch. **Handovers** (`#/handovers`,
`g v`; admins and managers of the project):

1. **New handover**: who is leaving and their last day. The Keeper drafts the plan and suggests a successor (someone who
   co-owns their code, else a manager); nothing moves yet.
2. Open it. The **transfer set** is read live and lists, in nine sections, what they hold: the code CODEOWNERS gives them
   (with how much a recorded KT session explains and a GitHub link), documents, KT recordings and explanations, KT sessions
   to record before they leave, mentees and hires they manage, questions routed to them, approvals that wait on them, access
   items and documentation feedback. **Gaps** are what would leave the successor starting cold (no successor, code no
   session explains, files only they hold, CODEOWNERS still naming them, no document for their code).
3. **Attach a document** the plan did not find: a file of the repository (by path, with its GitHub link), a document
   uploaded to the project, or any https link, with who owns it next.
4. **Choose who takes over**, then **Move N to <name>**: mentees, hires they manage, open questions, access items and
   feedback move now. Tick what a person did outside Welcome Wave (a session recorded, CODEOWNERS changed). The successor
   must be an active expert, manager or admin of the project; if the person chosen earlier has left or was deactivated, the
   move is refused and nothing changes.
5. **Complete handover**: type the leaver's name to confirm. Open gaps must be accepted, and are recorded. An admin may also
   **deactivate** the leaver's account in the same step; that is refused while they still manage or mentor an active hire,
   or hold open questions, access items or feedback, in another project: start a handover there first.

One open handover per person and project. Every step is in the audit log (`handover.*`).

## 8. Audit log

**Audit log** (`#/audit`, `g d`; admins only). Newest entries are first.

- **Sort**: select a column title (When, Person, Action, Target) to sort by it; select it again to reverse. Every column
  shows its sort arrow.
- **Time range**: Today, 7 days, 30 days or All time, or choose From and To days. Both days are included and use your own
  time zone. A From day after the To day is shown as an error and the table keeps the last range that worked.
- **Narrow down** with the Action and Person menus (each shows how many entries it has) or an exact Target id or email,
  then press Enter.
- **Views of the log**: each time an admin opens or pages through the log, an entry is recorded. They are hidden unless you
  tick **Show views of this log**.
- **Rows per page**: 25, 30 or 50; your choice is remembered. The footer says "Showing 1–25 of N" for the entries that
  match, with first, previous, page numbers, next and last. Pages hold still while you page through them: entries written
  meanwhile (your own page views included) wait until you refresh or change a filter.
- **Details**: select a row (or its arrow) to see everything the entry recorded, in plain words (a board move reads "From
  To do, To In progress"; a move between milestones "From Day 15 · Settled in"), with buttons to show only that action,
  person or target.
- **Export CSV** downloads exactly what matches, in the order shown (cells a spreadsheet would run as formulas are
  neutralised).
- The address bar keeps your filters and order, so you can bookmark or share a view.
- Recorded: sign-ups and joins, invitations, role changes, deactivations, projects, repositories, documents, enrolments,
  approval decisions, KT sessions and recordings, board moves and edits, task moves, routed questions, verified answers,
  pre-boarding actions, check-ins, completions, handovers, setup changes, exports, the demo clock. Reading and exporting the
  log are recorded too (`audit.viewed`, `audit.exported`), and so is every purge. Nothing in the API edits or deletes a
  single entry.

| Setting | Default | |
|---|---|---|
| `AUDIT_RETENTION_DAYS` | 365 | Entries older than this are purged. The database function always keeps 30 days, whatever this says. |
| `AUDIT_EXPORT_MAX_ROWS` | 100,000 | Rows in one CSV export |
| `AUDIT_PURGE_EVERY_S` | 86,400 | Self-hosted: how often the server purges (0 = never). Google Cloud: the `ww-audit-purge` Scheduler job calls `POST /internal/audit/purge` daily. Demo: no purge. |

## 9. Retention

| Data | Kept |
|---|---|
| Audit log | `AUDIT_RETENTION_DAYS` (365 by default, never under 30) |
| Raw KT recordings | Google Cloud: deleted after 30 days by a bucket rule; notes, transcripts and links to code remain. Self-hosted: until a manager removes them, or `RECORDING_RETENTION_DAYS` days after they were stored when that is set (0, the default, keeps them; the server removes the video files once a day and keeps the notes; audited). A manager can remove a session's recording, notes and transcript at any time: **KT sessions** > the session > **Remove recording**, then type its title (audited). |
| Documents | Until a manager deletes them (audited) |
| Backups | Google Cloud: daily, kept 30 days in production, plus 7 days of point-in-time recovery. Self-hosted: `./scripts/selfhost.sh backup` (the database, the files, the keys and the settings in one file), kept by the operator (section 15). |
| Everything else | As long as the organisation exists. Configurable retention for other data is not built. |

## 10. Export and deletion

**Settings > Organisation** (admins), or the API ([API.md](API.md#organisation-data)).

| Action | Call | Result |
|---|---|---|
| Export | `GET /api/org/export` | A JSON download: every organisation-scoped table plus the organisation row. Left out: search data (embeddings) and anything that is or protects a credential. Audited (`organization.exported`). |
| Delete | `DELETE /api/org` with `{"confirm": "<organisation name>"}` | Every organisation table emptied in one transaction (the organisation row and the queued outbox events of its projects included); then the stored documents and recordings named by those rows (Cloud Storage, or the data folder when self-hosted), and the saved state of agent runs its approvals had paused (`app/lifecycle/orgdata.py`). A file that cannot be removed is reported in the answer, not hidden. A wrong name answers 422. A final audit entry records who did it. The organisation's Google Calendar grant is revoked at Google. Left behind: the saved state of agent runs that finished or failed, and database backups until they expire. |
| Erase a person | `POST /api/org/people/erase` with `{"email", "confirm"}` | On their request: their name, email and GitHub username are replaced, their login is unbound, and their chats, questions to a person, notifications, scheduling links and invitations are deleted. Kept: their onboarding records under the erased name, the audit log, backups until they expire, and their sign-in account at Identity Platform or Firebase (one login can belong to other organisations; the operator removes it on request). You cannot erase yourself, or the last admin. |

## 11. Feature flags

`FEATURES` turns lifecycle features on and off: `all` (the default), or names separated by commas; `-name` turns one off
(`all,-surveys`). The value is read from the environment; restart the API after a change.

| Name | Turns off |
|---|---|
| `preboarding` | Pre-boarding links and the portal |
| `routing` | `POST /api/ask/route` (chat then asks the plain way) |
| `first_task` | The first-task shortlist and selection |
| `presubmit` | Private reviews |
| `blockers` | Blocker alerts |
| `areas` | Progress by area |
| `summaries` | Weekly summaries |
| `feedback` | Documentation feedback |
| `checkins` | Check-ins |
| `surveys` | The onboarding survey |
| `completion` | The completion review |
| `handover` | Handovers |
| `reports` | Reports and their CSV |
| `demo_clock` | The demo clock (demo mode only) |

A route of a feature that is off answers 404 "This feature (name) is turned off on this server." Enrolment, programs, homes and
the visibility page are not flags. `GET /api/lifecycle/config` (no sign-in) returns the flags and the reporting floors.

## 12. Rate limits

An in-process token bucket in front of every route (`app/ratelimit.py`). Over the limit: 429 with `Retry-After`.

| Group | Routes | Charged to | Per minute | Variable |
|---|---|---|---|---|
| auth | sign-in, sign-up, joining | address | 10 | `RATE_LIMIT_AUTH_PER_MINUTE` |
| chat | `POST /api/chat`, `/api/ask/...`, private reviews | person | 20 | `RATE_LIMIT_CHAT_PER_MINUTE` |
| preboarding | `/api/preboarding/*` | address | 30 | `RATE_LIMIT_PREBOARDING_PER_MINUTE` |
| webhooks | `/webhooks/*` | address | 60 | `RATE_LIMIT_WEBHOOKS_PER_MINUTE` |
| client_errors | `POST /api/client-errors` (crash reports) | address | 10 | `RATE_LIMIT_CLIENT_ERRORS_PER_MINUTE` |
| default | everything else | person | 120 | `RATE_LIMIT_DEFAULT_PER_MINUTE` |

- `RATE_LIMIT_ENABLED=false` turns them off (the tests do). `RATE_LIMIT_TRUSTED_HOPS`: the number of proxies that append to
  `X-Forwarded-For` (0 uses the connection's address, with `X-Forwarded-For` trusted only from `FORWARDED_ALLOW_IPS`;
  Google Cloud runs with 2, set by the pipeline: [DEPLOY.md](DEPLOY.md)). `RATE_LIMIT_MAX_KEYS`: buckets kept (100,000).
- Questions also have a per-person limit, `CHAT_RATE_PER_MINUTE` (12), shared by chat and routing. Demo sign-in allows 10
  tries a minute per email and address.
- Limits are per API process: with several Cloud Run instances each enforces its own. Cloud Armor (with a domain) throttles
  any address above 600 requests a minute.
- Health checks and CORS preflights are never limited.

## 13. LangSmith tracing (opt-in)

A trace holds an agent run's prompts and outputs: code excerpts, redacted transcripts, documents and new hires' questions.
Sending them to LangSmith (a third party, in the US unless its EU endpoint is chosen) is the customer's data-processing
decision, so tracing is off unless `LANGSMITH_TRACING=true`. No other LangChain or LangSmith variable can turn it on.

1. Record the decision, the region and LangSmith's retention on the customer's processor list.
2. Google Cloud: set `_LANGSMITH_TRACING = "true"` (and `_LANGSMITH_ENDPOINT` for the EU region) in `build_substitutions`, and
   the key in Secret Manager ([DEPLOY.md §2 and §3](DEPLOY.md#2-third-party-setup)). Self-hosted: `LANGSMITH_TRACING=true`
   and the LangSmith key in `selfhost.env`.
3. Each agent run's record then carries its trace id (`trace_id` in `GET /api/runs`).

## 14. Other settings worth knowing

| Setting | Default | |
|---|---|---|
| `PUBLIC_URL` | empty | The address people open, such as `https://onboarding.example.com`. Used in links (invitations, notifications, the GitHub callback, pre-boarding links, the environment-check script) and the Google sign-in return address. With `ENV=prod` (always, in a self-hosted release) it must be `https://`, or `http://localhost` |
| `SIGNUP_ENABLED` | unset | Unset: self-hosted, only the first organisation can be created, then invitation only; Google Cloud, open. `false`: invitation only. `true`: anyone can create an organisation |
| `REQUIRE_VERIFIED_EMAIL` | true | Joining or creating an organisation needs a verified email |
| `MAX_UPLOAD_MB` / `MAX_RECORDING_MB` | 25 / 2048 | Document and recording size limits |
| `CORS_ORIGINS` | empty | Same origin only (the web app proxies `/api`) |
| `GEMINI_API_KEY` | empty | Self-hosted: without it the AI is off, and the app says so where it matters |
| `LEGAL_OPERATOR_NAME`, `LEGAL_OPERATOR_ADDRESS`, `LEGAL_CONTACT_EMAIL`, `LEGAL_PRIVACY_EMAIL`, `LEGAL_GOVERNING_LAW`, `LEGAL_COURTS` | empty | Who operates this server, as the terms of service, the privacy policy and the data-processing agreement name them. None is needed to start: an empty one (or a left-over placeholder) shows as "the company that operates this service". `./scripts/selfhost.sh check` lists the ones missing. Self-hosted: `selfhost.env` (quote values with spaces). Google Cloud: set them once on the `ww-api` service (`gcloud run services update ww-api --update-env-vars ...`); deploys keep them. Governing law and courts are your lawyer's decision ([specs/legal-consent.md](specs/legal-consent.md)) |
| `LEGAL_ENFORCE` | false | `false`: the web app asks each person to accept the current terms and privacy policy at sign-in. `true`: the API also answers 403 `legal_required` on workspace routes until they have. Turn it on only when the deployed web app shows the prompt |

## 15. Self-hosted release

`./scripts/selfhost.sh release` runs Welcome Wave for people to use on one machine of yours, with the images Google Cloud runs:

| Container | Image | What it is |
|---|---|---|
| `ww-web` | `frontend/Dockerfile`: the production build, served by nginx | The web app, with the Content-Security-Policy and security headers of `frontend/nginx.conf.template`, compressed; forwards `/api` and `/webhooks` to the API. Listens on `127.0.0.1:8000` for your HTTPS reverse proxy, and answers only to the host name of `PUBLIC_URL` (and `localhost`). |
| `ww-api` | `backend/Dockerfile` | The API and its background work (indexing, call notes, reviews, plans, the scheduled checks), with `ENV=prod`. Reachable only from `ww-web`. Runs as the owner of the data folder, never as root. |
| `ww-db` | `pgvector/pgvector:pg16` | Postgres, its data in the Docker volume `ww-db-data`; `127.0.0.1:5433` on this machine only. |

Docker starts each one again if it stops, and after a reboot (`--restart unless-stopped`); their logs are capped at 50 MB each.
Uploaded documents, recordings and the keys the server generates (`.token-key`, `.signing-key`) stay in `DATA_DIR`
(`backend/.data` unless set), the same folder the development commands use.

Needs: Linux with Docker (or macOS with Docker Desktop or colima; the data folder must be one Docker can mount), `python3`,
`curl`, and the repository. As a starting point, 2 CPUs and 4 GB of memory, and disk for the recordings.

### First time

1. `./scripts/selfhost.sh setup` writes `backend/selfhost.env` (owner-only, with fresh database passwords) and starts Postgres.
2. In `backend/selfhost.env`, set at least:
   - `PUBLIC_URL=https://onboarding.example.com`, the address people will open. Invitation links, pre-boarding links, the
     Google sign-in return address and the webhook addresses are built from it. The release refuses plain `http://` for any
     host but `localhost`.
   - `FIREBASE_PROJECT_ID` and `FIREBASE_API_KEY` ([INTEGRATIONS.md](INTEGRATIONS.md#firebase)), and `GEMINI_API_KEY` from a
     project with billing turned on: on the free tier Google may use what is sent to improve its products
     ([INTEGRATIONS.md](INTEGRATIONS.md#gemini)). Leave `FIREBASE_AUTH_EMULATOR_HOST` unset: it is for development only.
   - Optional: email (SMTP), GitHub token, Google Calendar, Calendly ([INTEGRATIONS.md](INTEGRATIONS.md)).
3. Firebase console > **Authentication > Settings > Authorized domains**: add the host name of `PUBLIC_URL`. Without it,
   sign-in fails on that address.
4. `./scripts/selfhost.sh release`. It builds both images, applies the migrations, starts the API and the web app, and waits
   until the API answers through the web app with its database. Then `./scripts/selfhost.sh check` tests each integration.
5. Put the HTTPS reverse proxy in front (below). Open `PUBLIC_URL` and sign in: the first account creates the organisation,
   and everyone else joins by invitation.

To update: get the new code (`git pull`, or unpack the new archive), then `./scripts/selfhost.sh release` again. The new API
starts after the migrations; requests in flight get 20 seconds to finish.

### HTTPS: a reverse proxy

The web app listens on `127.0.0.1:8000`, so only this machine reaches it. A reverse proxy on the same machine serves
`PUBLIC_URL` over HTTPS and forwards to it. It must: pass the `Host` and add the client's address to `X-Forwarded-For`; allow
request bodies up to 2.1 GB (recording uploads); keep streams open for an hour without buffering (`/api/live`); and keep no
access log of full addresses, because pre-boarding links (`/welcome/<token>`) and OAuth callbacks (`?code=`) carry credentials
(the app and `ww-web` redact them in their own logs).

[Caddy](https://caddyserver.com) gets and renews the certificate itself, streams as it goes, and keeps no access log unless
asked. `/etc/caddy/Caddyfile`:

```
onboarding.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

nginx on the host, with a certificate from certbot or your own:

```
server {
    listen 443 ssl;
    http2 on;
    server_name onboarding.example.com;
    ssl_certificate     /etc/letsencrypt/live/onboarding.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/onboarding.example.com/privkey.pem;
    access_log off;                      # or a format without $request_uri: some addresses are credentials
    client_max_body_size 2100m;          # recordings
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;             # live updates
        proxy_request_buffering off;     # large uploads stream through
        proxy_read_timeout 1h;
        proxy_send_timeout 600s;
    }
}
server { listen 80; server_name onboarding.example.com; return 301 https://$host$request_uri; }
```

**Host names.** The web app answers only to the host name of `PUBLIC_URL`, `localhost` and `127.0.0.1`; any other name gets
`421 Misdirected Request`, so a web page cannot reach your installation under a name of its own (DNS rebinding). The proxy
must pass the `Host` it received (both examples do). To allow more names, such as an internal alias, set
`WW_WEB_HOSTS=wave.lan,wave.corp.example` in `selfhost.env` and run the release again.

**Where it listens.** `WW_WEB_BIND` (default `127.0.0.1`) and `WW_WEB_PORT` (default `8000`). Keep the default with the proxy on
this machine. With the proxy on another machine, set `WW_WEB_BIND` to this machine's private address (or `0.0.0.0`) and let the
firewall admit only the proxy to that port: the web app speaks plain HTTP, so it must never face the network by itself.

**A server only your network reaches** (no public DNS name, so no public certificate): give it an internal name and let Caddy
issue the certificate from its own authority, then install Caddy's root certificate (`caddy trust`, or its `root.crt`) on the
team's computers:

```
wave.corp.example {
    tls internal
    reverse_proxy 127.0.0.1:8000
}
```

Plain `http://` to another computer is refused on purpose (sign-in tokens would cross the network unencrypted), so a test
on this machine alone uses `PUBLIC_URL=http://localhost:8000`, and anything other people open goes through HTTPS.

Open only 443 (and 80 for the redirect and certificate renewal) in the firewall. The API takes each person's address from the
proxy's `X-Forwarded-For` (it trusts that header only from the containers' own network), so per-address rate limits work with
`RATE_LIMIT_TRUSTED_HOPS` left at 0. Register the addresses the integrations call back on: Google OAuth
`PUBLIC_URL/api/integrations/google/callback`, Calendly `PUBLIC_URL/webhooks/calendly`.

### Backups and restore

`./scripts/selfhost.sh backup` writes `backups/welcomewave-<time>.tar`, readable only by you, holding:

- the database (`pg_dump`, one consistent snapshot; checked complete before the file is kept);
- the data folder: documents, recordings, `.token-key` (it decrypts each organisation's stored Google connection) and
  `.signing-key`;
- `selfhost.env`: the database passwords, `TOKEN_ENCRYPTION_KEY` and `BLOB_SIGNING_KEY` if you set them, and the integrations'
  keys;
- checksums of all three.

A failed backup leaves no file behind. The app keeps running during a backup; a file uploaded in that minute may be missing
from it. The file holds every organisation's data and every key: keep copies off this machine, as safely as the machine itself.
Run it daily, for example with cron (`crontab -e`), and remove old ones:

```
30 2 * * * cd /srv/welcome-wave && ./scripts/selfhost.sh backup >> backups/backup.log 2>&1 && find backups -name 'welcomewave-*.tar' -mtime +30 -delete
```

`./scripts/selfhost.sh restore backups/welcomewave-<time>.tar` puts one back:

- **On a new machine** (the old one is gone): get the code, then run the restore. It puts the settings back if
  `backend/selfhost.env` is missing, creates Postgres, creates the database roles the dump refers to (`ww_definer`, which owns
  the functions that look across organisations, and `ww_app`), loads the dump in one transaction (stopping at the first
  error), puts the files and keys back, applies newer migrations, and refuses to finish if any `SECURITY DEFINER` function is
  not owned by `ww_definer`. Then `./scripts/selfhost.sh release`. Add the new host to Firebase's authorized domains if it
  changed.
- **Over the current data** (going back to an earlier state): the restore refuses unless you add `--replace`. With it, it
  backs up the current data first, stops the API, replaces the database and the files (the current files are moved to
  `<data folder>.before-restore-<time>`), and starts the API again.

If `TOKEN_ENCRYPTION_KEY` in the current settings differs from the backup's, the restore says so: stored Google Calendar
connections would not decrypt. Try a restore on a spare machine now and then: a backup that was never restored is a hope.

### Day to day

| Command | Does |
|---|---|
| `./scripts/selfhost.sh status` | The three containers, and whether the API answers through the web app with its database |
| `./scripts/selfhost.sh logs` | Follows the API's log (one JSON object a line); `logs web`, `logs db` for the others |
| `./scripts/selfhost.sh check` | Tests each integration with the settings the API runs with |
| `./scripts/selfhost.sh stop` | Stops the web app, the API and Postgres; they stay stopped after a reboot until the next `release` |

The API keeps the settings it was started with: after changing `selfhost.env`, run `./scripts/selfhost.sh release` again.

Names and ports can be changed in the environment or in `selfhost.env`: `WW_WEB_PORT` (8000), `WW_WEB_BIND` (127.0.0.1),
`WW_WEB_HOSTS` (none: host names to answer to besides `PUBLIC_URL`'s and `localhost`),
`WW_BACKUP_DIR` (`backups/`), `WW_DB_PORT` (5433), `WW_DB_CONTAINER` (`ww-db`), `WW_DB_VOLUME` (`ww-db-data`),
`WW_API_CONTAINER` (`ww-api`), `WW_WEB_CONTAINER` (`ww-web`), `WW_NETWORK` (`ww-net`), `WW_IMAGE_TAG` (`selfhost`), and
`WW_ENV_FILE` for a settings file kept elsewhere.

### Development on the same machine

`./scripts/selfhost.sh start` runs the API from `backend/.venv` (created from `backend/requirements.lock` if missing) and the
Vite development server on `localhost:5173`, for working on the code. It serves an unminified development build without the
web app's security headers: never make it reachable from other machines, and never use it for people's work.

## Known gaps

- **Time zone.** Program days and business days follow the server's local date; there is no time zone per organisation.
- **Self-hosted recordings** are kept until a manager removes them; there is no automatic retention yet.
- **Invitations** made on **Settings > People** are not emailed; only a new hire's invitation is, when their plan is approved and
  email is set up. Send the others the sign-in link the page shows.
