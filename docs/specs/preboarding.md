# Pre-boarding

**Problem.** Before day one a hire has no account, but documents, access and a working machine should be done by then.

**Users.** The hire (no sign-in, through a link); managers create the link.

**Data.** `onboarding_programs.preboarding_token_hash` and `preboarding_expires_at`, `acknowledgements` (with a keyed hash of
the address), `environment_checks`. `preboarding_lookup(hash)`: the one definer function that finds a link before the
tenant is known.

**API.**
- `POST /api/onboardings/{id}/preboarding-link` → 201 `{token, url, expires_at}` (managers). The token is in this
  response only; a new link replaces the old one.
- `GET /api/preboarding/{token}` → `{hire: {first_name}, project, team, role_title, start_date, days_until_start, manager,
  mentor, acknowledgements: [{doc_key, title, acknowledged_at}], access: [{system, status}], environment: {tools, latest},
  expires_at}`.
- `POST /api/preboarding/{token}/acknowledge {doc_key}` → `{doc_key, title, acknowledged_at}`.
- `GET /api/preboarding/{token}/env-check.sh` → a shell script; `POST /api/preboarding/{token}/env-check
  {results: [{name, ok, detail}]}` → `EnvironmentCheck`.

**Logic.** The token is 256 random bits; only its SHA-256 is stored; it expires after 14 days on the real clock (the demo
clock cannot extend or shorten it). The portal shows names, dates, documents and access *status*: no code, recordings,
chat, owners or other people. The script asks each tool of the template's track for its version and posts the results;
it changes nothing. Results are cleaned (names `[A-Za-z0-9._+-]{1,40}`, details 200 characters, 30 at most) and stored as
data. The acknowledgement's address is stored as an HMAC keyed with the token, which is never stored: comparable within
one link, readable by nobody.

**UI states.** Welcome page with start countdown; documents with Acknowledge; access list; environment card with the
download, the last result and the failing tools. Expired or replaced link: one message, "ask your manager for a new one".

**Edge cases.** Unknown, replaced, expired or over-long tokens all answer the same 404. A completed or paused onboarding's
link stops working. The route group has its own rate limit (`app/ratelimit.py`). Demo: Arjun's link is
`/welcome/demo-welcome-arjun`.

**Tests.** `test_lifecycle_api.py::test_preboarding_link_is_shown_once_scoped_and_expires`,
`::test_the_preboarding_token_is_stored_hashed`; Postgres: tenant isolation and the pre-tenant lookup.
