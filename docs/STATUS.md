# Status after enterprise round 1

Checked against the working tree on 2026-11-14. Where this page and the code disagree, the code wins. The plan is in
[PLAN.md](PLAN.md); the starting point in [CURRENT_STATE.md](CURRENT_STATE.md).

## At a glance

| Check | Result | Baseline (start of round) |
|---|---|---|
| `cd backend && .venv/bin/python -m pytest -q` | **488 passed, 53 skipped** (541 collected, about 45 s). The 53 skipped need Postgres (`TEST_PG_ADMIN_URL`). | 341 passed, 43 skipped |
| `MODE=demo .venv/bin/python -m eval.run_eval` | recall@5 **1.00** (target 0.80) on 17 questions; answer metrics need a real model | the same 17 questions |
| `cd frontend && npx tsc --noEmit -p .` | passes | passes |
| Frontend tests | none (tooling awaits approval) | none |
| HTTP routes (demo mode, `/api/openapi.json`) | 130, of which 5 are demo-only | `routes.py` held 50 |
| Migrations | 001–013 (`010_lifecycle`, `011_scheduling`, `012_audit_retention`, `013_members` are new) | 001–009 |

`docs/PLAN.md` names Phase 0 and "Phases 1 to 5" without listing them one by one. This page maps them to the round's tracks,
plus Phase 6 for documentation; correct the mapping if the master prompt numbers them differently. Test counts are the test
files that cover each phase (many files were changed this round, so these are not "new tests" counts).

## What landed, by phase

### Phase 0: plan and baseline

- [PLAN.md](PLAN.md): decisions taken by default, track ownership, data model, API contract, the ranking and detection rules.
- [CURRENT_STATE.md](CURRENT_STATE.md): the baseline assessment.

### Phase 1: foundations (Track F)

| Landed | Where |
|---|---|
| Repository rules for agents and people | `CLAUDE.md` |
| Packaging from git-known files only, refusing secrets | `scripts/package.sh` |
| CI on GitHub Actions: in-memory tests plus the retrieval eval and a check that tests left nothing in `backend/`; Postgres tests (fail if skipped); frontend type-check, build and a CSP check of the bundle; dependency audit (report only); both Docker images | `.github/workflows/ci.yml` |
| Content-Security-Policy and security headers for the web app and the API | `frontend/nginx.conf.template`, `app/main.py` |
| Rate limits per person and per address, five groups | `app/ratelimit.py` |
| Audit-log API, CSV export, retention purge (30-day floor in the database) | `app/api/audit.py`, `adapters/audit_store.py`, `012_audit_retention.sql`, Terraform job `ww-audit-purge` |
| Prompt-injection fixtures (README, PR description, diff comment, KT transcript) | `tests/fixtures/injection/` |
| Tests write into a temp folder, never `backend/` | `tests/conftest.py` |
| LangSmith tracing opt-in only | `app/container.py configure_tracing`, `cloudbuild.yaml` |
| Demo mode ignores `backend/.env` for the main settings | `app/config.py` (two exceptions under "Known issues") |
| Security overview | `docs/SECURITY.md` |

Tests: `test_ratelimit` 19, `test_injection` 13, `test_audit` 9 (1 needs Postgres), `test_config` 4.

### Phase 2: landing page (Track A)

- A 3D office scroll story in six chapters (`frontend/src/site/Story.tsx`, `site/office/*`), with three.js loaded only after the
  hero has painted and never in the app's bundle; without WebGL a drawn still with the same words.
- Less text; a "Ready for your security review" grid. The landing page no longer claims SSO or SCIM: both are deferred (below).

Tests: none beyond the type-check and CI's CSP check of the build. Lighthouse has not been measured.

### Phase 3: scheduling (Track B)

- LiveKit is gone (code, dependencies, infrastructure). KT sessions are booked on Google Calendar with a Meet link, through a
  person's Calendly link, or held anywhere (`app/services/scheduling.py`, `adapters/google_calendar.py`, `adapters/calendly.py`,
  `app/api/integrations.py`, `011_scheduling.sql`).
- Recordings come back from Google Drive (button and poller; `POST /internal/recordings/poll`, Terraform job
  `ww-recordings-poll`) or by upload. The demo uses a built-in calendar that always "finds" a recording.
- Google refresh tokens are stored encrypted (Fernet, `TOKEN_ENCRYPTION_KEY`); the OAuth state is signed and expires in 10
  minutes. Calendly webhooks are signature-checked.
- Settings > Integrations; booking from the KT planner and KT sessions pages.

Tests: `test_scheduling` 31.

### Phase 4: lifecycle API (Track C)

- Programs and versioned templates (16 defaults), teams, one-step enrolment with a drafted plan through Approvals, milestones
  that scale with the program's length, access checklists, pre-boarding links (hashed tokens, 14 days) with acknowledgements
  and an environment-check script.
- Tiered answer routing (cited answer, verified answers, mentor, domain owner) with interruption budgets, digests and
  office hours; verified answers that later answers reuse.
- The first-task shortlist with scored reasons and exclusion of single-owner critical areas; private pre-submit reviews.
- Progress by area, blocker rules with escalation (hire, mentor, manager), weekly summaries counted from records,
  documentation feedback routed to owners, 30/60/90 check-ins, an anonymous survey with a floor of five, completion review,
  offboarding handovers.
- The visibility page and its registry (managers and mentors see exactly what the hire sees), role homes, setup, the
  leadership report with a "how" for every figure and "not enough data" floors.
- The `executive` role (read-only by construction), members administration (role changes, deactivation;
  `013_members.sql`, `app/api/members.py`), organisation export and deletion, the demo clock, and feature flags.
- `docs/PERMISSIONS.md` (every route × role) and `docs/specs/` (one spec per capability).

Tests: `test_lifecycle_api` 41, `test_lifecycle_rules` 11, `test_lifecycle_postgres` 6 (need Postgres), `test_visibility` 5,
`test_members` 4, `test_permissions_matrix` 3 (one of them calls every route of the app as each demo user).

### Phase 5: lifecycle UI (Track D)

- Role homes (`#/home`) for all five roles; every role now lands there.
- Setup, Enrol, Team, the per-hire Program page (timeline plus accordion sections), the pre-boarding portal (`/welcome/<link>`,
  its own chunk), What your manager sees, Reports with CSV export, Templates, Handovers, People, Audit log, Feedback, expert
  Preferences, the demo clock control.
- Rail per role with `g` shortcuts; ⌘K actions (enrol, pre-boarding link, export report, demo clock, settings).

Tests: none; `tsc` passes.

### Phase 6: documentation and this checkpoint

- New: [USER_GUIDE.md](USER_GUIDE.md), [ADMIN_GUIDE.md](ADMIN_GUIDE.md), [API.md](API.md), [DEMO_SCRIPT.md](DEMO_SCRIPT.md) (verified
  against the demo API, with its gaps), this page; README updated.
- Landing this round, not verified here: the multi-agent restructure (one Supervisor and eight named specialists, an Agents
  page, `GET /api/agents`, approval kinds `rerecord`, `doc_edit`, `template_change`), documented in [AGENTS.md](AGENTS.md) by its
  own track.

## Behind flags and settings

| Switch | Default | Covers |
|---|---|---|
| `FEATURES` | `all` | `preboarding`, `routing`, `first_task`, `presubmit`, `blockers`, `areas`, `summaries`, `feedback`, `checkins`, `surveys`, `completion`, `handover`, `reports`, `demo_clock`. Off: the routes answer 404. Not flags: enrolment, programs, homes, visibility. |
| `MODE=demo` | `demo` | `/api/demo/*`, `/api/docs` and `/api/openapi.json` exist only in demo mode |
| `LANGSMITH_TRACING` | off | Agent traces to LangSmith |
| `GOOGLE_OAUTH_CLIENT_ID` + `_SECRET` | empty (off) | Google Calendar, Meet links, Drive imports |
| `CALENDLY_WEBHOOK_SIGNING_KEY` | empty (off) | Calendly bookings coming back |
| `GITHUB_APP_*` / `GITHUB_TOKEN` | empty | GitHub App (Google Cloud) / private repositories by URL (self-hosted) |
| `GEMINI_API_KEY` | empty | Self-hosted AI; without it the app runs and says the AI is off |
| `SIGNUP_ENABLED`, `REQUIRE_VERIFIED_EMAIL` | on | Self-serve sign-up; verified email to join |
| `RATE_LIMIT_ENABLED` | on | All rate limits |

## Deferred, and why

| Item | State in the code | Why |
|---|---|---|
| SSO (SAML, OIDC with a customer's identity provider) and SCIM provisioning | Not built; sign-in is Identity Platform email or Google. The landing page no longer claims either. | Deferred by the founders this round; enterprise identity needs a customer to integrate against. |
| Public API and scoped API tokens | `api_tokens` table only (migration 010); no route issues or accepts a token | Second in PLAN §6's deferral order |
| Slack and Teams | Not built; notifications are in-app only | PLAN §1 decision 4: integrations beyond the calendar deferred |
| Jira and Linear | Not built; tasks come from GitHub issues and agents | Same decision |
| HRIS | Not built; people are invited or enrolled by email | Same decision |
| Email delivery | No `Mailer` port yet; invitations and notifications are in-app, and the enrolment page's "Invitation sent" sends nothing | PLAN §1 decision 1 chose the design (SMTP, an in-app outbox in demo); not built this round |
| Frontend tests: Playwright, Vitest, axe | Not added | New dependencies need the founders' approval (`CLAUDE.md`); pending |
| Speaker identification in recordings | Speakers are matched to members by name or GitHub login only; labels like "Speaker 1" stay unmapped | Needs a voice or calendar-attendee approach; not started |
| Redaction of screen frames | The redactor covers transcript text only (emails, phone numbers, key formats); frames are not redacted | Not started |
| Lighthouse measurement of the landing page | Not measured | Needs a browser run in CI or by hand |
| Files in storage on organisation deletion; configurable retention beyond the audit log; enforced MFA | Not built | Listed as planned in SECURITY.md |

## Known issues found while writing the docs

Code was not changed in this pass; these are for the owning tracks.

| Issue | Where |
|---|---|
| A first-PR task made from an issue has no file paths, so its merged change counts towards no area: the completion criterion "A merged change in at least one area" and the *area_contributed* milestone stay unmet after the first change merges. The selected brief knows the files (`engine/validate.py`). | `app/lifecycle/progress.py` (`_in(area, t.related_paths)`), the Task agent's first-PR task |
| Approving a review ticks "First change merged" (task done with a PR), before or without GitHub's merge event. | `progress.py` `first_pr_merged`, `supervisor.await_review_decision` |
| The AI draft attached on escalation to the domain owner is stored only in a metric event; no API or page shows it to the owner. | `app/lifecycle/routing.py` `hand_to`, `GET /api/experts/requests` |
| After the domain owner answers an escalated question, the mentor's copy stays open in the mentor's inbox. | routing escalation creates a second expert request |
| Answering a request indexes it (`verified-<request>`) and saving it as a verified answer indexes it again (`va-<id>`), so later answers cite the same answer twice. | `api/routes.py answer_request`, verified answers |
| Creating a pre-boarding link for Arjun in the demo replaces the fixed demo link `/welcome/demo-welcome-arjun` until restart. | `seed.py`, `preboarding-link` |
| The Onboarding health dashboard counts days from `date.today()` plus one (Vijay is day 13 there, day 12 in his program) and ignores the demo clock. | `app/services/dashboard.py` |
| `RateLimitSettings` and `AuditSettings` read `backend/.env` in demo mode, against the "demo reads no env file" rule. | `app/ratelimit.py`, `app/api/audit.py` |
| On Google Cloud, `FEATURES`, `RATE_LIMIT_*` and `AUDIT_*` are not passed by `cloudbuild.yaml`, and `--set-env-vars` replaces the service's variables on every deploy, so they cannot be changed there. | `cloudbuild.yaml` |
| The invitation form cannot invite an executive (API only); teams can only be created through the API; export and deletion have no page. | `pages/settings/People.tsx`, Setup's "Add teams" links to `#/team` |
| The landing page's "Secret redaction: Keys and tokens are removed before anything is indexed" overstates it: only transcripts are redacted; code and documents are indexed as provided (SECURITY.md §5). It also spells "organization". | `frontend/src/site/Site.tsx` `SECURITY` |

## Docs that are out of date

- `docs/SECURITY.md` still marks as in progress what has landed: pre-boarding links, organisation export and deletion, the
  Calendly signature check, Google Calendar in §13; and says the audit purge job is planned in Terraform, while
  `infra/terraform/events.tf` creates `ww-audit-purge`.
- `docs/PRD_Vijayabaskar_Aman_Nautiyal.pdf` (built by `docs/build_docs.py`) still describes LiveKit.
- `docs/PERMISSIONS.md` will need rows for `/api/agents*` when they land (the matrix test fails until it has them).
- `docs/CURRENT_STATE.md` is the round's baseline by design; this page supersedes it.

## Update — 2026-11-14 (late): agentic model, live-action landing, real-mode verification

- **Agentic RAG multi-agent model.** Every specialist (Guide, Scout, Planner, Coach, Keeper, Reviewer, plus the
  Supervisor's free-form router) runs a LangGraph think → tool → observe loop on Gemini function calling over read-only
  tools (`app/agents/runtime.py`, `app/agents/tools/`). Rules remain only as guardrails and as the no-AI fallback.
  Steps stream live (`agent:<run_id>` over SSE) and are kept privacy-filtered in run records. See docs/AGENTS.md.
- **Live-action landing** (docs/landing/CINEMATIC_BLUEPRINT.md): six scenes of real footage (stock until a custom shoot —
  docs/landing/PRODUCTION.md), scroll scrubbing, real React product scenes, adaptive dark → light → dark. three.js removed.
- **Real mode verified end to end** (self-hosted, real Firebase, Gemini free tier, GitHub token, public repo
  VijayabaskarR-06/bhume-boundary-takehome): sign-up, indexing (paced under the free tier's 100 embeddings/min), agentic
  answers with live steps, the citation guard, the off-topic refusal, organisation deletion. Fixed on the way:
  Firebase token verification without Google Cloud credentials (`app/firebase_tokens.py`), embedding pacing and 429 waits,
  a citation check calibrated on real answers (+ one repair turn), install/build files indexed (requirements*.txt, *.toml,
  Dockerfile, Makefile), a fallback-model chain (`MODEL_FALLBACK` comma-separated) with one retry on a 503.
- **Tests:** 677 passed, 60 skipped (the 60 need Postgres; earlier Postgres runs passed). Frontend type-check and build pass.
- **Known limits:** on the Gemini free tier answers take ~10–50 s and Pro models are unavailable (billing fixes both);
  footage is licensed stock (Pexels, cottonbro studio) until a custom shoot; the landing is checked in Chrome only
  (Safari/Firefox/iOS and Lighthouse not yet measured); public/media is 54 MB (serve from Cloud Storage/CDN in production);
  live steps stream in-process (enable session affinity on Cloud Run with several instances).

## Update — 2026-11-14 (night): brand and film
- **New identity** (docs/brand/BRAND.md): the W of four identical capsules with hairline seams ("explain once, reused
  everywhere"), Geist wordmark, lockups, favicon/app icons; the old spiral is removed everywhere (incl. the hero's full stop,
  now one teal capsule). New interactive intro ("Crowd"): strokes draw, a ripple runs through a stroke field that leans toward
  the pointer; skippable; reduced-motion still.
- **Landing as one continuous film**: smoothed scroll progress drives every shot (all clips scrubbed), motivated transitions
  (push-in match cuts, moving dissolves), layered captions; scenes 4–6 are a black CSS-3D product world with teal threads routed
  under the panels; 60 fps measured in Chrome.
- Checks: 677 backend tests pass; frontend type-check and build pass; a 36-page, 5-role browser sweep shows no console errors.
