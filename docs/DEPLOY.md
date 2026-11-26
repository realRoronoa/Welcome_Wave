# Deploying Welcome Wave

From an empty Google Cloud project to a live, signed-in product, then how to run it. Do `dev` first; `prod` is the
same steps in a second project. Commands assume `PROJECT_ID`, `ENV` (`dev` or `prod`) and `REGION=asia-south1` are exported.

**The containers.** Everything runs from two images: `backend/Dockerfile` (the API, and with another command the workers,
the migrations and the scheduled calls) and `frontend/Dockerfile` (the web app behind nginx). This runbook puts the API and
the web app on Cloud Run and the workers on GKE. To run the whole product on GKE instead, or on any Kubernetes cluster,
follow steps 1-3 here, then [KUBERNETES.md](KUBERNETES.md). On one server, use `./scripts/selfhost.sh release`
([ADMIN_GUIDE.md](ADMIN_GUIDE.md)).

## 0. Accounts and tools

| You need | Why |
| --- | --- |
| Two GCP projects with billing (`welcome-wave-dev`, `welcome-wave-prod`) | One per environment |
| Owner on both projects (for the first apply only) | Terraform creates service accounts and IAM |
| Billing Account Costs Manager on the billing account | Only for the budget alert (`billing_account` in tfvars) |
| A GitHub organisation you control | Hosts the GitHub App and this code |
| Optional: Google Calendar API + Drive API, and a Calendly account | KT sessions with Meet links, recordings from Drive; Calendly bookings |
| Optional: a LangSmith account | Agent traces (off unless you turn them on, step 2) |
| Optional: an SMTP server (your mail provider's) | Invitation emails, the experts' digest, weekly summaries |
| Optional, recommended for production: a domain (e.g. `app.welcomewave.dev`) | Custom URL, managed TLS, Cloud Armor, and the run.app URLs closed to the internet |
| `gcloud`, `terraform` >= 1.6, `kubectl`, `helm` | Local tools |

## 1. Terraform (≈15 minutes, mostly Cloud SQL and GKE creation)

```bash
gcloud auth application-default login
# Terraform bills its own API calls to the project, so these two must be on before its first plan
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project $PROJECT_ID
gcloud storage buckets create gs://$PROJECT_ID-tfstate --location=$REGION --uniform-bucket-level-access --public-access-prevention
cd infra/terraform
cp dev.tfvars.example $ENV.tfvars          # fill in project_id, alert_email, billing_account, domain
terraform init -backend-config="bucket=$PROJECT_ID-tfstate" -backend-config="prefix=welcome-wave/$ENV"
terraform apply -var-file=$ENV.tfvars
```

This creates: least-privilege service accounts, Cloud SQL (PostgreSQL 16, backups + point-in-time recovery, regional
in prod), the database owner user and generated passwords (stored in Secret Manager), the recordings/documents bucket
(30-day deletion of raw recordings), Pub/Sub queues with dead-letter topics, GKE Autopilot, Artifact Registry,
Identity Platform, the browser API key, alerts, the budget, and four Cloud Scheduler jobs, all calling ww-api with the
same OIDC token and audience: `ww-outbox-flush` retries event publishing (see "Events the API could not publish" below),
`ww-recordings-poll` imports KT session recordings (see "KT session recordings" below), `ww-lifecycle-tick` runs the
agents' daily work (`POST /internal/lifecycle/tick`, hourly; each organisation's run happens once a day), and
`ww-audit-purge` deletes audit entries older than `AUDIT_RETENTION_DAYS` (`POST /internal/audit/purge`, daily). If `domain`
is set it also creates the HTTPS load balancer, certificate and Cloud Armor policy: point an `A` record for the domain at
`terraform output lb_ip` (the certificate becomes active 15–60 minutes after DNS resolves). The load balancer sends `/api/*`,
`/webhooks/*` and `/health/ready` to ww-api and everything else to ww-web, and the pipeline then deploys both services so
that their run.app URLs refuse callers from the internet (see "Traffic, streams and capacity" below).

## 2. Third-party setup

**Identity Platform → Google sign-in.** Console → Identity Platform → Providers → Add Google (creates the OAuth
client). Email/password is already enabled by Terraform, and Terraform owns the authorized domains (`domain` and
`web_host` in tfvars): add hosts there, not in the console, or the next apply removes them.

**GitHub App** (GitHub → Settings → Developer settings → GitHub Apps → New). Use the web URL
(`https://<domain>` or the `ww-web` run.app URL after the first deploy):

| Setting | Value |
| --- | --- |
| Callback URL | `https://<web>/github/callback` |
| Request user authorization (OAuth) during installation | **On** — the API uses it to prove the installer can see the installation |
| Webhook URL | `https://<web>/webhooks/github` (the web app proxies it to the API). Must be active before anyone installs the app: GitHub's `installation` event tells the API who installed it, and only that person can then connect it to an organisation (for an installation the API never heard about, an admin of the GitHub account it is on) |
| Webhook secret | a long random string (also goes into Secret Manager below) |
| Repository permissions | Contents, Metadata, Pull requests, Issues, Checks: **Read-only**. Nothing else. |
| Organization permissions | Members: **Read-only** — lets the API confirm that the person connecting an installation it has no `installation` event for is an admin of that GitHub organisation; without it they must reinstall the app |
| Subscribe to events | Push, Pull request |

Note the App ID, the app's URL slug, the Client ID; generate a Client secret and a private key (.pem).

**Google Calendar and Meet (optional).** KT sessions go on Google Calendar with a Meet link, and their recordings
come back from Google Drive ([specs/scheduling.md](specs/scheduling.md)). In the same project: APIs & Services → enable
the **Google Calendar API** and **Google Drive API**; OAuth consent screen (Internal for a Workspace organisation) with the
scopes `calendar.events` and `drive.readonly`; Credentials → OAuth client ID → Web application, authorized redirect URI
`https://<web>/api/integrations/google/callback`. Note the client ID (step 5) and secret (step 3). Each organisation then
connects its own Google account in Settings → Integrations; meetings are recorded from Meet as usual (or with the
Workspace auto-recording setting), and the recording is imported once Meet has attached it to the event.

**Calendly (optional).** People save their Calendly link in Settings → Integrations; booking a KT session with them opens
it prefilled. For the bookings to come back, create one webhook subscription for your Calendly organisation (API:
`POST https://api.calendly.com/webhook_subscriptions` with a personal access token) to `https://<web>/webhooks/calendly`,
events `invitee.created` and `invitee.canceled`, scope `organization`, and a `signing_key` you choose (step 3).

**LangSmith (optional, off by default).** Traces contain every agent run's prompts and outputs: code excerpts, redacted
transcripts and new hires' questions, sent to a third party. Turning tracing on is the organisation's data-processing
decision ([SECURITY.md](SECURITY.md#13-data-processing-overview)), so nothing is traced unless you set
`_LANGSMITH_TRACING = "true"` in step 5. If you do, create an API key, enter it in step 3, and choose the LangSmith region
that matches your data-residency promise (for the EU region set `_LANGSMITH_ENDPOINT = "https://eu.api.smith.langchain.com"`
in step 5); both settings reach the API and the workers, and each agent run then records its trace id (`GET /api/runs`).
Otherwise enter `unused` as the key in step 3.

## 3. Secrets

```bash
PROJECT_ID=$PROJECT_ID ./infra/scripts/set-secrets.sh
```

It asks for the GitHub private key path, webhook secret, client secret, the Google OAuth client secret, the Calendly
webhook signing key, the LangSmith key and the SMTP password, and writes each straight to Secret Manager (nothing touches
disk or shell history). **Every secret needs a version before the first deploy** — Cloud Run refuses to start with a
missing one. Enter `unused` for an optional feature you are not using yet: for the Google OAuth client secret and the
Calendly signing key, `unused` turns the feature off and is never used as a key; for the LangSmith key and the SMTP
password it is simply never used while the feature is off (`_LANGSMITH_TRACING`, `_SMTP_HOST`). **The GitHub webhook
secret is never `unused`**: enter the same long random string you gave the GitHub App, or the API accepts deliveries
signed with a guessable key. The SMTP password starts as `unused` (Terraform adds that first version, so upgrades deploy
without it); enter the real one when you set up email (step 5). Database credentials and `TOKEN_ENCRYPTION_KEY` (it
encrypts the stored Google refresh tokens and signs recording uploads) were generated by Terraform.

## 4. Cluster bootstrap (once per cluster)

```bash
PROJECT_ID=$PROJECT_ID ENV=$ENV ./infra/scripts/bootstrap-cluster.sh   # installs KEDA with Workload Identity
```

## 5. CI/CD

1. Console → Cloud Build → Repositories → **1st gen** tab → Connect repository → GitHub (Cloud Build GitHub App) →
   this repo (one-time OAuth; Terraform cannot do this, and its triggers use this 1st-gen connection).
2. Set `github_repo = "owner/name"` in `$ENV.tfvars` and `terraform apply` again: it creates the trigger
   (`main` → dev, tag `vX.Y.Z` → prod) running as `ww-build`.
3. Fill `build_substitutions` in `$ENV.tfvars` and apply (Terraform owns the trigger, so edit them there, not in the
   console). `_FIREBASE_API_KEY` is filled in automatically.

| Substitution | Value |
| --- | --- |
| `_GITHUB_APP_ID`, `_GITHUB_APP_SLUG`, `_GITHUB_CLIENT_ID` | from the GitHub App |
| `_GOOGLE_OAUTH_CLIENT_ID` | the Google OAuth client's ID (step 2); empty: KT sessions are held elsewhere and recordings uploaded |
| `_PUBLIC_URL` | `https://<domain>` (filled in by Terraform when `domain` is set) or the `ww-web` URL |
| `_MODEL_FAST`, `_MODEL_SMART`, `_MODEL_FALLBACK` | generally-available Gemini IDs — verify with step 6 first |
| `_LANGSMITH_TRACING`, `_LANGSMITH_ENDPOINT` | optional, default `"false"` (no traces): `"true"` only once the organisation has decided to send traces to LangSmith; the EU endpoint (see step 2) |
| `_SMTP_HOST`, `_SMTP_PORT`, `_SMTP_USER`, `_SMTP_FROM` | optional email: your mail server, port (587 STARTTLS, the default, or 465 SSL), user and From address; the password is the `ww-smtp-password` secret (step 3). Both ww-api and the review worker, which sends invitation emails, get the same settings, so the app says "Emailed" only when email really goes out. Set them here, not on the service: every deploy re-applies them |
| `_INGRESS` | leave unset. With `domain` set, Terraform makes it `internal-and-cloud-load-balancing` (only the load balancer reaches the services); set `"all"` only until the domain's certificate is active |

4. Push to `main`. The pipeline runs unit and Postgres integration tests, the evaluation gate, `terraform fmt -check` and
   `terraform validate` on `infra/terraform`, builds both images,
   runs migrations as the `ww-migrate` Cloud Run job, deploys `ww-api` and `ww-web`, applies the GKE workers, and
   smoke-tests the result (including that `/api/me` without a token is refused).
5. After the first deploy set `api_host` (the domain; ww-api's run.app host only when there is no domain), `web_host`
   (ww-web's run.app host) and `_PUBLIC_URL`, and `terraform apply` once more: this turns on the uptime check, lets
   sign-in work on the run.app URL, and lets browsers on the web app's addresses upload recordings to the bucket (its
   CORS rule lists the domain and `web_host`).

## 6. Check the models before relying on them

```bash
cd backend
MODE=gcp GCP_PROJECT=$PROJECT_ID MODEL_FAST=… MODEL_SMART=… MODEL_FALLBACK=… \
  python -m scripts.check_models gs://$PROJECT_ID-ww-data/recordings/<any>.mp4
```

It calls each model once (and video input, if you pass a recording) and fails in seconds if an ID is wrong,
withdrawn, not served in `VERTEX_LOCATION`, or out of quota. The embedding model must return 768 dimensions.

## 7. First sign-in

Open the web URL, sign in (Google or email), verify the email, and create your organisation — you become its admin.
Settings → People invites teammates by email; when they first sign in with that email they see the invitation and
choose to join (nobody is added to an organisation silently). Inviting someone from Settings → People sends no email:
tell them to sign in at the web URL. With email set up (`_SMTP_*`, step 5), approving a new hire's onboarding plan emails
them their invitation; without it the app shows the link to pass on. Settings → Repository installs the GitHub App and connects the project's repository (one per
project); the person who installs the app on GitHub must be the one who connects it (or, for an installation made
before the webhook was live, an admin of the GitHub account it is on). Settings → Onboarding starts a
new hire's plan; give new hires their GitHub username at invite time so their pull requests are matched to them.
To run invitation-only, set `_SIGNUP_ENABLED = "false"` in `build_substitutions` and apply (not on the service:
every deploy re-applies the pipeline's settings).

---

## Operating it

**Release to prod.** `git tag v1.2.0 && git push --tags` runs the same pipeline in the prod project.

**Roll back.** Every deploy is a Cloud Run revision:
`gcloud run services update-traffic ww-api --to-revisions=<previous>=100 --region $REGION` (same for `ww-web`).
Workers: `kubectl -n welcome-wave rollout undo deploy/<name>`. Migrations are forward-only; write a new one to undo.
On the Kubernetes layout, run `infra/k8s/deploy.sh` again with the previous images ([KUBERNETES.md](KUBERNETES.md#rolling-back)).
Upgrading a database created before `003_review_fixes`: a project holding several repositories keeps one, and each
other repository moves to a new project `<project> - <owner/repo>` with the same members (the migration log has a
NOTICE per move). Both projects' code is dropped and their repositories show "Indexing failed": press Re-index in
Settings → Repository for each.
The next pipeline run sends traffic back to the newest revision (`--to-latest`), so merge the fix before it runs.
Two pipeline runs at once (two merges a minute apart): each labels ww-api with the time it started (`ww_build`), and a run
that finds a newer label stops before migrating or deploying, so an older image never goes live last.

**Dead letters.** An alert fires when a message lands in `ww-<queue>-dead-sub`. Read it:
`gcloud pubsub subscriptions pull ww-meetings-dead-sub --limit 5`. Fix the cause, then republish the message body
to `ww-<queue>`; handlers are idempotent, so a replay of something already done is skipped.

**Events the API could not publish.** The API stores every event in its outbox table before publishing it to
Pub/Sub, so a failed publish is retried rather than lost. The Cloud Scheduler job `ww-outbox-flush` (Terraform,
`events.tf`) calls `POST /internal/outbox/flush` on ww-api every 2 minutes with a Google-signed OIDC token for the
`ww-scheduler` service account; the pipeline sets `OUTBOX_FLUSH_AUDIENCE` (ww-api's deterministic URL,
`https://ww-api-<project number>.$REGION.run.app`) and `OUTBOX_FLUSH_INVOKER` (that account's email), and the API
refuses any other caller. The job's calls fail until the first deploy. After that, the flush answers 503 whenever an
event could not be published (it stays in the outbox), so a failing job means events are waiting:
`gcloud scheduler jobs describe ww-outbox-flush --location $REGION` shows the last status, and
`gcloud scheduler jobs run ww-outbox-flush --location $REGION` flushes now. The alert "events not published" fires
when sends keep failing for 10 minutes; the API's `event not sent; the outbox will retry` log lines say which events.
Check that `ww-api` still has `roles/pubsub.publisher` and that the `ww-<queue>` topics exist.

**KT session recordings.** A recording never passes through Cloud Run, which refuses request bodies over 32 MiB and
keeps an instance's `/tmp` in its memory:

- *Uploaded* (any session): the API checks who is asking and for which call, then hands the browser a V4 signed URL for
  `gs://<project>-ww-data/recordings/<call>/<random>.<ext>` that accepts one `PUT` of exactly that file's size and type,
  creates the object only if it does not exist, and must be started within 15 minutes. The browser uploads straight to
  the bucket, then `POST /api/meetings/<id>/recording/complete` checks the stored file (size, type, that it starts like a
  video, that the same person started the upload for this call) and starts the notes as before. The bucket's CORS rule
  (Terraform, `data.tf`) admits the domain and `web_host`; the web app's Content-Security-Policy allows
  `connect-src https://storage.googleapis.com` (`frontend/nginx.conf.template`). A file uploaded but never completed is deleted with the other raw
  recordings after 30 days. Signing uses ww-api's own identity (IAM signBlob) and `TOKEN_ENCRYPTION_KEY` signs the upload id,
  so every instance can complete an upload another one started.
- *Imported from Google Drive*: the Cloud Scheduler job `ww-recordings-poll` (Terraform, `events.tf`) calls
  `POST /internal/recordings/poll` every 10 minutes, with the same OIDC token and audience as the outbox flush. Each run
  imports the Drive recording of Google Calendar sessions that ended at least 10 minutes ago, and marks sessions whose
  recording never came 48 hours after their end as ended (their recording can still be uploaded). An import streams the
  video from Drive into Cloud Storage as a resumable upload, 8 MB at a time, so nothing is kept on the instance. One import
  per session runs at a time, across instances: a lease in the database, renewed while the file streams, makes a second
  press of **Import from Google Drive** answer "being imported" instead of starting another download, and lets the next
  poll retry an import whose instance stopped. ww-api's timeout (60 minutes) covers a long import.

If an organisation's Google connection stops working (the grant was revoked, or `TOKEN_ENCRYPTION_KEY` changed), its admin
connects Google again in Settings → Integrations.

**Traffic, streams and capacity.** Every signed-in tab keeps one Server-Sent Events stream open (`GET /api/live`; the API
ends it every 15 minutes and the browser reconnects), plus one while an agent's steps are shown. An open stream holds a
request slot on each Cloud Run service it passes through, so the pipeline deploys:

| Service | Settings | Why |
| --- | --- | --- |
| ww-api | `--concurrency 80 --max-instances 10 --timeout 3600 --session-affinity` | room for about 800 streams and requests; a timeout longer than a stream and than a long Drive import; a person's stream and their requests tend to reach the same instance, so the Guide's live steps reach them |
| ww-web | `--concurrency 400 --max-instances 10 --timeout 3600 --memory 512Mi` | nginx proxies `/api` when there is no load balancer (two connections per stream, 1,024 per nginx worker) |

With a domain, the load balancer sends `/api/*`, `/webhooks/*` and `/health/ready` straight to ww-api (Cloud Armor in front of
both services), so streams hold no slot on ww-web, and both services run with ingress `internal-and-cloud-load-balancing`:
their run.app URLs refuse callers from the internet, so nobody can go around Cloud Armor. Cloud Scheduler's calls to ww-api's
run.app URL count as internal. The smoke test then checks the app through `_PUBLIC_URL` and that the run.app URLs are
closed. Per-address rate limits read the client's address from the right of `X-Forwarded-For`, never the entries a client
wrote: `RATE_LIMIT_TRUSTED_HOPS=2` (`_RATE_LIMIT_TRUSTED_HOPS` in `cloudbuild.yaml`) fits both routes, since through ww-web
nginx forwards the one address Google's front end appended and ww-api's front end appends ww-web's, and through the load
balancer the chain ends "client, load balancer". To check after a deploy: from one machine, 11 quick `POST /api/signup`
without a token get a 429 on the 11th, the same with a different made-up `X-Forwarded-For` on each, while a second
network is still answered 401 in that minute ([ADMIN_GUIDE.md](ADMIN_GUIDE.md)). Each ww-api instance keeps up to 15 database
connections and each worker pod up to 7; Cloud SQL allows 300 (dev) and 400 (prod). Raise `--max-instances` only together
with `max_connections` (`data.tf`). Session affinity is best effort: when a stream and the chat request land on different
instances, the answer still arrives with its steps; only the live view of them is missed.

**A worker died mid-job.** Nothing to do: the event's lease lapses after `EVENT_LEASE_S` (5 min) and Pub/Sub's
redelivery runs it again.

**Restore the database.** In place, from a nightly backup (same instance name, so nothing else changes):
`gcloud sql backups list --instance ww-db-$ENV`, then `gcloud sql backups restore <BACKUP_ID> --restore-instance ww-db-$ENV`.
To a point in time (creates a new instance, outside Terraform):
`gcloud sql instances clone ww-db-$ENV ww-db-$ENV-pitr --point-in-time <RFC3339>`. The clone already has the
`ww_owner` and `ww_app` users with their passwords. Point the Cloud Run URLs at its socket (the workers' Auth Proxy
URL has no instance name in it and stays as it is):

```bash
for s in database-url database-url-owner; do
  url=$(gcloud secrets versions access latest --secret ww-$s --project $PROJECT_ID)
  printf '%s' "${url/%:ww-db-$ENV/:ww-db-$ENV-pitr}" | gcloud secrets versions add ww-$s --project $PROJECT_ID --data-file=-
done
```

then set `_DB_INSTANCE = "ww-db-$ENV-pitr"` in `build_substitutions`, apply, and re-run the pipeline: it moves the
migration job, the API and the workers' Auth Proxy to the clone. Rehearse one of them before launch.

**Rotate a secret.** Add a new version (`set-secrets.sh github-app-private-key=~/Downloads/app.pem`, or `name=value`
for the others; the script refuses a key path that is not a file), then start new instances that read `:latest`:
`gcloud run services update ww-api --region $REGION --update-labels rotated=$(date +%s)` and, for workers,
`kubectl -n welcome-wave rollout restart deploy`.

**Watch it.** Cloud Monitoring alerts on: API down, dead letters, events waiting too long in a queue (reviews 30 minutes,
ingestion 1 hour, meetings 3 hours: a worker that crashes before it subscribes dead-letters nothing, and Pub/Sub drops a
message after 7 days), worker errors and container restarts, scheduled jobs failing, API 5xx, events not published, and
budget 50/90/100%. Logs are JSON, uvicorn's own lines included, with `request_id`, `user_id` and `org_id`, and each line of
a request carries its trace (`logging.googleapis.com/trace`), so Logs Explorer shows a Cloud Run request's app lines
beside it; `llm call` lines carry model, latency and token counts. Request log entries whose URL is a credential
(pre-boarding links, signed file links, OAuth callbacks) are excluded from Cloud Logging (`credential_urls` in
`observability.tf`); the app's own line for those requests, with the token redacted, is kept. With LangSmith tracing turned
on, every agent run has a trace, and its record in `GET /api/runs` carries the trace id.

**Workers.** The pipeline waits until each worker deployment is ready (subscribed to its queue; `readinessProbe`). The
deployments name no replica count: KEDA owns it, so a deploy no longer stops a meeting mid-analysis. The Cloud SQL Auth
Proxy sidecar is pinned by digest (`workers.yaml`, and the same digest in `infra/k8s/components/gke`); to update it,
take the new release's digest from `gcr.io/cloud-sql-connectors/cloud-sql-proxy`.

**Upgrading an installation made before this release.** `terraform apply` first: it adds the `ww-smtp-password` secret (with
an `unused` first version), the bucket's CORS rule, the load balancer's route to ww-api, the new alerts and the log
exclusion, and (with a domain) sets `_INGRESS` and `_PUBLIC_URL` on the trigger. With a domain, the run.app URLs close at the
next deploy: before it, make sure `api_host` is the domain, and that the GitHub App's webhook and callback URLs, the Google
OAuth redirect URI and the Calendly webhook subscription use the domain, not a run.app URL. If you had set `SMTP_*` on ww-api by hand,
move them to `build_substitutions` and the password to the secret, and remove the plain variable first
(`gcloud run services update ww-api --remove-env-vars SMTP_PASSWORD`), or the deploy refuses to turn it into a secret. The
first deploy after this one starts the meeting and ingestion workers once (their old `replicas: 0` is dropped); KEDA scales
them back to zero after its cooldown.

**Cost knobs.** `_API_MIN_INSTANCES=0` (scale to zero; set 1 on demo day; while anyone has the app open, their stream keeps an
instance running anyway), KEDA scales the meeting and ingestion workers to zero; the review worker keeps one pod. The largest fixed costs are Cloud SQL and the GKE Autopilot pods
that are running; the largest variable cost is Gemini video analysis (about one call per 10 minutes of recording).
