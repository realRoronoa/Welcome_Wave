# Integrations: where each key comes from

Every integration is optional and off until its key is set. This page gives the click path for each one, the setting it fills,
and how to check that it works. Self-hosted: put the values in `backend/selfhost.env` (never commit it, never paste it into a
chat or a ticket), then run `./scripts/selfhost.sh release` again: the running API keeps the settings it was started with
([ADMIN_GUIDE.md §15](ADMIN_GUIDE.md#15-self-hosted-release)). Google Cloud: they are Cloud Build substitutions or Secret Manager entries ([DEPLOY.md](DEPLOY.md)). The demo
reads no env file; pass a value in the command's own environment if you want to try one there.

| Integration | Settings it fills | What it is for |
|---|---|---|
| [Gemini](#gemini) | `GEMINI_API_KEY` | The AI: answers, plans, reviews, notes |
| [Firebase](#firebase) | `FIREBASE_PROJECT_ID`, `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN` | Sign-in |
| [GitHub](#github) | `GITHUB_TOKEN` | Reading private repositories and more API calls |
| [Google Calendar and Drive](#google-calendar-and-drive) | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | KT sessions with Meet links; recordings from Drive |
| [Calendly](#calendly) | `CALENDLY_WEBHOOK_SIGNING_KEY` | Bookings made on a presenter's Calendly page |
| [Email (SMTP)](#email-smtp) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Invitations, pre-boarding links, the expert digest, the weekly summary |
| [LangSmith](#langsmith) | `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` | Tracing agent runs (off by default) |

**This machine, 17 November 2026:** the local release runs at `http://localhost:8000`. Gemini, Firebase and GitHub credentials answer real checks. The `welcome-wave` Google Cloud project has Calendar and Drive APIs enabled, both required OAuth scopes saved, and a separate local web OAuth client whose secret is in the owner-only `backend/selfhost.env`. `/api/config` reports `google_calendar: true`. The **Welcome wave** organisation's admin completed Google consent; the app shows **Google Calendar connected**, and the database has an encrypted refresh token with both requested scopes. Read-only probes to the Calendar events and Drive files APIs both returned HTTP 200, without listing or changing personal data. `check` reports the OAuth client as `set` because that command checks server credentials, not an organisation's grant. Google marks `calendar.events` sensitive and `drive.readonly` restricted and unverified; the consent page still displays the default project name `project-605906640745`. Broad public use needs proper branding and Google's OAuth verification. SMTP email is configured, and a live STARTTLS/login check to Gmail passed without sending a message. Calendly webhook delivery is still off; Calendly cannot post to a localhost URL from its servers. A personal Calendly link can still be saved in the app.

## Check that it works

```
./scripts/selfhost.sh check
```

It tests each integration against its real service, printing one line each: with the settings the running API was started with
when the release is running, otherwise with `backend/selfhost.env`. No key, token or password is printed: a line is built from a
host, a count, an HTTP status or an error's type.

```
Integrations (checked now; no secret is printed)
  PASS  Gemini (the AI)              The key is accepted; 52 models listed.
  PASS  Firebase sign-in             The web API key is accepted by Identity Toolkit.
  FAIL  GitHub                       GitHub refused the token (HTTP 401): expired, revoked or mistyped.
  set   Google Calendar and Drive    The OAuth client is set (a sign-in is needed to test it). ... Authorised redirect URI: ...
  skip  Calendly                     No CALENDLY_WEBHOOK_SIGNING_KEY: bookings are not received.
  PASS  Email (SMTP)                 connected to smtp.gmail.com:587 and logged in; no message was sent.
  skip  LangSmith tracing            Tracing is off: nothing is sent to LangSmith.
```

| Mark | Meaning |
|---|---|
| `PASS` | A real call worked (Gemini lists models, Firebase answers a made-up address, GitHub answers `GET /user`, SMTP connects and logs in, LangSmith lists a project). |
| `FAIL` | The call was made and failed. The line says why in plain words. The exit code is 1. |
| `set` | Configured, but there is nothing to call without a person or a delivery: an OAuth client needs someone to sign in; a Calendly signing key needs a real booking. |
| `skip` | Not configured. |

It then calls each Gemini model once (`scripts/check_models.py`) when a Gemini key is set.

Admins see the same in the app: **Settings > Integrations** shows each integration as working, set up but not tested, failing or not
set up. It is `GET /api/integrations/status` (admins only): `{items: [{key, name, configured, ok, detail}]}` for `gemini`, `firebase`,
`github`, `google_calendar`, `calendly`, `smtp` and `langsmith`, with no secret in it. The live calls are cached for five minutes;
`?refresh=true` makes them again.

## Gemini

**Use a key from a project with billing turned on (Gemini's paid tier) for real data.** Welcome Wave sends Gemini your
organisation's source code, documents, redacted call transcripts, the KT recordings themselves (with people's faces and voices)
and new hires' questions. On the free tier, Google's terms for the Gemini API let Google use what is sent, and the answers, to
improve its products, and let people at Google read them; on the paid tier they do not. Read the current
[Gemini API terms](https://ai.google.dev/gemini-api/terms) before choosing, and record the choice with your customers'
processor list ([SECURITY.md §13](SECURITY.md#13-data-processing-overview)). A free key is fine for trying the product with
made-up data.

1. Open [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) and sign in with a Google account.
2. **Create API key** in a Google Cloud project with a billing account linked (AI Studio shows the project's tier next to the
   key; **Set up billing** moves it to the paid tier).
3. Copy the key. Keys start with `AIza` or `AQ.`; both work.
4. Put it in `GEMINI_API_KEY`.
5. Run `./scripts/selfhost.sh check`. `MODEL_FAST`, `MODEL_SMART` and `MODEL_FALLBACK` default to the models the project was built
   for; if the check says a model is not available to your key, set the ones it lists.

Without a key the AI is off and the app says so where it is needed. On Google Cloud no key is used: Vertex AI answers through the
service account.

On the free tier Gemini embeds 1,000 texts a day per model, and every text in a request counts. Reading a repository and
answering questions share that quota: a repository of more than about 900 search entries cannot be read in one day, and once
the day's quota is used, questions fail until it resets (midnight Pacific time). Billing removes the limit.

## Firebase

Firebase (free plan) signs people in.

1. Open [console.firebase.google.com](https://console.firebase.google.com) and **Add project**. Google Analytics is not needed.
2. **Build > Authentication > Get started > Sign-in method**. Turn on **Email/Password** and **Google** (Google asks for a support
   email).
3. **Authentication > Settings > Authorized domains**: add the host name of `PUBLIC_URL`, the address people open Welcome Wave
   on (`localhost` is there already). Sign-in fails on any other address.
4. **Project settings** (the gear) **> General**. Copy the **Project ID** into `FIREBASE_PROJECT_ID`.
5. On the same page, under **Your apps**, choose **Add app > Web (`</>`)**, register it (no hosting), and copy `apiKey` into
   `FIREBASE_API_KEY`. (Newer consoles also show it as **Web API key** at the top of the page.) It identifies the project and is
   public by design.
6. `FIREBASE_AUTH_DOMAIN` defaults to `<project id>.firebaseapp.com`; set it only if you use another.

The check sends one `createAuthUri` call for an address that cannot exist; it writes nothing.

## GitHub

A fine-grained, read-only token lets Welcome Wave read private repositories and raises the GitHub API limit from 60 to 5,000 calls
an hour. It can never write: the code has no write path.

1. GitHub, your profile picture **> Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new
   token**.
2. Name it (for example `welcome-wave-readonly`), set an expiry, and choose the **Resource owner** (your organisation, if the
   repositories belong to one; an owner may need to approve the token).
3. **Repository access**: select the repositories Welcome Wave should read.
4. **Repository permissions**, all **Read-only**: **Contents**, **Issues**, **Pull requests**, **Commit statuses**, **Actions**.
   (Metadata is read-only automatically. If CI results are missing in a review, add **Checks: Read-only** too.)
5. **Generate token**, copy it once, and put it in `GITHUB_TOKEN`.

On Google Cloud the GitHub App is used instead: see [DEPLOY.md](DEPLOY.md). Each new hire's GitHub username should be on their
profile: a pull request moves a task only when GitHub names that person as its author.

## Google Calendar and Drive

Sessions are booked on the organisation's Google Calendar with a Meet link, and recordings come back from Drive.

1. [console.cloud.google.com](https://console.cloud.google.com): pick or create a project.
2. **APIs & Services > Library**: enable the **Google Calendar API** and the **Google Drive API**.
3. **APIs & Services > OAuth consent screen** (in the newer console, **Google Auth Platform**). User type **Internal** for a Google
   Workspace organisation, or **External**; if the external app is in **Testing**, add yourself under **Test users**. Add the scopes
   `https://www.googleapis.com/auth/calendar.events` and `https://www.googleapis.com/auth/drive.readonly`.
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**. Application type **Web application**. Under
   **Authorized redirect URIs** add `PUBLIC_URL/api/integrations/google/callback` (for example
   `https://app.example.com/api/integrations/google/callback`, or `http://localhost:8000/api/integrations/google/callback` for
   the self-hosted release on your own machine; the Vite development server instead uses port 5173). `PUBLIC_URL` must be set,
   and the address must match exactly.
5. Copy the **Client ID** (it ends in `.apps.googleusercontent.com`) into `GOOGLE_OAUTH_CLIENT_ID` and the **Client secret** into
   `GOOGLE_OAUTH_CLIENT_SECRET`. Restart the server.
6. In Welcome Wave, a manager or admin opens **Settings > Integrations > Connect Google Calendar** and consents. One Google account
   per organisation.

**Recording needs a paid Google Workspace plan.** Google Meet can record a call (and save the file to the organiser's Drive, attached
to the calendar event) only on Workspace plans that include recording, such as Business Standard and above. With a free Google
account, sessions still get Meet links, but their recordings are uploaded by hand. `GOOGLE_OAUTH_REDIRECT_URI` overrides the default
redirect address if you need that. `TOKEN_ENCRYPTION_KEY` encrypts the stored refresh token (generated into the data folder when
self-hosted; required on Google Cloud).

The check can only confirm the client is set and well formed; the first real test is **Connect Google Calendar**.

## Calendly

Each person saves their own link in **Settings > Integrations > Your Calendly link** (no server setting needed). For bookings made
there to come back into Welcome Wave, create one webhook subscription for your Calendly organisation. [Calendly says](https://developer.calendly.com/docs/getting-started/frequently-asked-questions) webhooks are
available on all plans; an organisation-scoped subscription requires an organisation admin or owner. The callback must be reachable
from Calendly, so this localhost installation needs a public HTTPS address before this step (an HTTPS reverse proxy and
`PUBLIC_URL`: [ADMIN_GUIDE.md §15](ADMIN_GUIDE.md#https-a-reverse-proxy)).

1. [calendly.com](https://calendly.com) **> Integrations & apps > API and webhooks** (or
   [developer.calendly.com](https://developer.calendly.com)) **> Personal access tokens > Generate new token**. Copy it.
2. Find your organisation's address: `curl -H "Authorization: Bearer <token>" https://api.calendly.com/users/me`, and read
   `resource.current_organization`.
3. Choose a long random string as the signing key (for example `openssl rand -hex 32`) and create the subscription:

   ```
   curl -X POST https://api.calendly.com/webhook_subscriptions \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"url": "https://<your host>/webhooks/calendly",
          "events": ["invitee.created", "invitee.canceled"],
          "organization": "<current_organization>", "scope": "organization", "signing_key": "<the string you chose>"}'
   ```
4. Put the same string in `CALENDLY_WEBHOOK_SIGNING_KEY` and restart. Empty: the webhook answers 404.

The token is only used for this one call; Welcome Wave never stores it. Bookings made without `utm_source=welcome-wave` are ignored.
The check confirms the key is set; only a real booking tests the signature.

## Email (SMTP)

Without these settings nothing is emailed and the app shows each link for you to share (invitations say "Invitation created",
never "sent"). With them, Welcome Wave emails: the invitation after a plan is approved, a pre-boarding link when a manager chooses
to email it, the expert digest (a count and a link, never the questions), and the weekly summary to the hire and the manager.

| Setting | Value |
|---|---|
| `SMTP_HOST` | The mail server, for example `smtp.gmail.com` |
| `SMTP_PORT` | `587` (STARTTLS, the default) or `465` (SSL from the start) |
| `SMTP_USER`, `SMTP_PASSWORD` | The account and its password; leave both empty for a relay that needs none |
| `SMTP_FROM` | The From address; defaults to `SMTP_USER` |

**Gmail with an app password** (a normal Gmail password does not work over SMTP):

1. [myaccount.google.com](https://myaccount.google.com) **> Security > How you sign in to Google > 2-Step Verification**: turn it on.
2. Open [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (or search "App passwords" in the account
   page), name it `Welcome Wave`, **Create**, and copy the 16-character password.
3. `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=<you>@gmail.com`, `SMTP_PASSWORD=<the 16 characters, no spaces>`.
   Gmail sends every message as the account itself, whatever `SMTP_FROM` says.

The check connects and logs in; it sends no message. A message's subject, body and links are never logged.
`GET /api/config` reports `email: true` only when SMTP is set up; the web app says "Emailed" only then. On Google Cloud the
same settings are the `_SMTP_*` Cloud Build substitutions and the `ww-smtp-password` secret ([DEPLOY.md](DEPLOY.md)).

## LangSmith

Off unless you turn it on: a trace holds each agent run's prompts and outputs (code excerpts, redacted transcripts, new hires'
questions), sent to a third party. Read [SECURITY.md](SECURITY.md#13-data-processing-overview) first.

1. [smith.langchain.com](https://smith.langchain.com) **> Settings > API Keys > Create API Key**. Copy it.
2. `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY=<the key>`, `LANGSMITH_PROJECT=welcome-wave`. `LANGSMITH_TRACING` is the only
   switch: no other LangChain or LangSmith variable turns tracing on.
3. In the EU region, set `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com`.

When tracing is on, each run's record carries its trace id (`trace_id` in `GET /api/runs`).
