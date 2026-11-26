# KT sessions: scheduling and recordings

KT sessions are held on Google Meet (or anywhere else), not inside Welcome Wave. Welcome Wave books them, keeps their
links, and turns their recordings into notes linked to the code. Code: `app/services/scheduling.py`,
`app/adapters/google_calendar.py`, `app/adapters/calendly.py`, `app/api/integrations.py`, the meeting routes in
`app/api/routes.py`, migration `db/migrations/011_scheduling.sql`.

## Where a session is held

| Provider | Booked by | Join link | Recording comes back by |
|---|---|---|---|
| `google` | Welcome Wave, on the organisation's connected Google Calendar | the Meet link Google creates | import from Google Drive (button, or the poller), or upload |
| `calendly` | the booker, on the presenter's Calendly page; the webhook creates the session | the Calendly event's location | upload |
| `manual` | Welcome Wave, held anywhere (an optional link) or with no time yet | the link given, if any | upload |

Which one a new session gets: what the form asks for; by default Google when the organisation has connected it and a
time is given, else `manual`. Demo mode uses the `MemoryScheduler`: Meet-style links, and every session "has" a recording,
so the whole flow plays without keys.

## Statuses (unchanged set)

| Status | Meaning |
|---|---|
| `scheduled` | Booked; its recording is not in yet. The page shows *Upcoming*, *Now* or *Awaiting recording* from the time. |
| `processing` | A recording is in (imported or uploaded); the Meeting agent is writing the notes. |
| `ready` | Notes written. |
| `ended` | Held, but no recording came within 48 hours of its end. A recording can still be added. |
| `failed` | The Meeting agent could not process the recording. A recording can be added again. |
| `cancelled` | Cancelled before it happened (here, or in Calendly). |
| `live` | Kept for calls made before scheduling; nothing sets it now. |

Only `scheduled` sessions can be cancelled. A recording can be added to `scheduled`, `ended` and `failed` sessions; the
first one wins (`claim_meeting_import` is one conditional update).

## Recordings

Every recording takes the same path: it is stored in blob storage, the meeting moves to `processing`, and
`meeting.recording_ready` is published. The Meeting agent writes the notes, the KT linker links moments to code, and the
managers' transcript review follows, exactly as before.

- **Upload** (`POST /api/meetings/{id}/recording`): any provider. The browser reads the video's length.
- **Import** (`POST /api/meetings/{id}/recording/import`): Google sessions. Meet attaches the recording (and the
  transcript) to the calendar event as `attachments` once someone records the meeting. The import reads the event
  (`events.get`), takes the video attachment, reads its length from Drive (`videoMediaMetadata`), streams it with
  `files.get?alt=media` into blob storage (spilling to disk, never whole in memory), and refuses files over
  `MAX_RECORDING_MB`. No recording yet: 409 `no_recording_yet`.
- **The poller** does the import by itself. Self-hosted: a thread every `RECORDING_POLL_S` (300 s). Google Cloud: Cloud
  Scheduler calls `POST /internal/recordings/poll` every 10 minutes with the outbox flush's OIDC token. Each run looks at
  up to 20 `scheduled` sessions, oldest end first:
  1. A Google session that ended at least 10 minutes ago (Meet needs a few minutes to save to Drive): import its
     recording if it is there. Skipped while the AI is off (self-hosted without a Gemini key).
  2. Any session that ended more than 48 hours ago without a recording: `ended`.
  3. Anything else waits for the next run.

## Calendly

- A person saves their Calendly link (Settings → Integrations, table `scheduling_links`; only `calendly.com` links).
- Booking with them opens the link with `name` and `email` prefilled and `utm_source=welcome-wave`,
  `utm_campaign=<project id>`, `utm_content=<KT planner session key>`.
- `POST /webhooks/calendly` checks `Calendly-Webhook-Signature` (`t=<unix time>,v1=<hex>`; HMAC-SHA256 of `"<t>.<raw body>"`
  with `CALENDLY_WEBHOOK_SIGNING_KEY`, refused if more than 3 minutes off), then:
  - ignores deliveries without `utm_source=welcome-wave` (bookings made outside Welcome Wave) and other events;
  - finds the project from `utm_campaign` and requires the host or the invitee to be a member of it;
  - `invitee.created`: creates the session (`calendly`, the event's join link, start and end, invitees, `kt_key` from
    `utm_content`, the planner session's title). One session per Calendly event: a redelivery changes nothing. If the
    planner session is held by another scheduled Calendly booking (a reschedule whose cancellation has not arrived), the
    newest booking takes it.
  - `invitee.canceled`: cancels that session.
- Cancelling a Calendly session in Welcome Wave does not cancel it in Calendly (that needs a Calendly token); the answer
  says so.

## Google Calendar connection

- One Google account per organisation, connected by a manager or admin (Settings → Integrations). Scopes:
  `calendar.events`, `drive.readonly`, plus `openid email` to show which account it is.
- `GET /api/integrations/google/start` returns Google's consent URL. Its `state` is signed (HMAC-SHA256 with a key
  derived from `TOKEN_ENCRYPTION_KEY`), names the organisation and the person, and expires after 10 minutes. The callback
  (`GET /api/integrations/google/callback`) arrives without the app's sign-in header, so the state is all it trusts; it
  also re-checks that the person is still a manager or admin of that organisation. Outcome:
  `/app#/settings?tab=integrations&google=connected|denied|expired|failed`.
- Only the refresh token is stored, encrypted with Fernet (`calendar_connections.refresh_token_enc`). Access tokens live
  in memory for their hour. `TOKEN_ENCRYPTION_KEY` comes from the environment (Terraform generates it on Google Cloud);
  self-hosted it is generated once into `DATA_DIR/.token-key`. A changed key makes the organisation connect again.
- `DELETE /api/integrations/google` revokes the grant at Google (best effort) and forgets it. Sessions already booked keep
  their Meet links; their recordings are uploaded from then on.
- Booking: the meeting row is written first (so a planner session booked twice is refused before any invitation goes
  out), then `events.insert` with `conferenceData.createRequest` (Meet), `conferenceDataVersion=1` and `sendUpdates=all`;
  the meeting id is the conference request id. If Google refuses, the row is removed again: nothing half-booked is left.
  Cancelling deletes the event with `sendUpdates=all`.

## API

| Route | Who | |
|---|---|---|
| `GET /api/meetings?project_id=` | members | the project's sessions |
| `POST /api/meetings` | members | schedule: `title`, `starts_at`, `duration_min`, `attendees` (member ids), `provider` (`google`/`manual`), `join_url` |
| `POST /api/meetings/{id}/schedule` | managers, the booker, invitees | give a session booked without a time its time and place |
| `POST /api/meetings/{id}/cancel` | managers, the booker | also cancels the calendar event; `warning` says what is left to do by hand |
| `POST /api/meetings/{id}/recording/import` | managers, experts, the booker | import from Google Drive |
| `POST /api/meetings/{id}/recording` | managers, experts, the booker | upload (not in demo mode) |
| `POST /api/kt-plan/schedule` | managers, experts | book a planner session; same `starts_at` … `join_url` fields; no time = unscheduled |
| `GET /api/integrations` | members | Google connected (by whom), the Calendly webhook, your Calendly link |
| `GET /api/integrations/google/start`, `DELETE /api/integrations/google` | managers | connect, disconnect |
| `GET /api/integrations/google/callback` | Google's redirect | signed state only |
| `PUT`/`DELETE /api/integrations/calendly/link` | members | your Calendly link |
| `GET /api/scheduling/links?project_id=` | members | members' Calendly links and what to prefill |
| `POST /webhooks/calendly` | Calendly | signed |
| `POST /internal/recordings/poll` | Cloud Scheduler | OIDC, as the outbox flush |

## Settings

`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` (both, or Google is off), `GOOGLE_OAUTH_REDIRECT_URI` (default
`PUBLIC_URL/api/integrations/google/callback`), `CALENDLY_WEBHOOK_SIGNING_KEY` (empty: the webhook answers 404),
`TOKEN_ENCRYPTION_KEY` (required on Google Cloud once Google is set up), `RECORDING_POLL_S` (self-hosted). The value
`unused` counts as empty for the three secrets.
