# Running Welcome Wave on Kubernetes

The same two images that run on Cloud Run and in the self-hosted release, on a Kubernetes cluster: `backend/Dockerfile`
(the API; the same image runs the workers, the migrations and the scheduled calls) and `frontend/Dockerfile` (the web app,
served by nginx). The manifests are a Kustomize base with one overlay per environment, in [`infra/k8s`](../infra/k8s).

## Pick a layout

| Layout | What runs on the cluster | Use it when |
| --- | --- | --- |
| **GKE**: `overlays/staging`, `overlays/production` | Everything: API, web app, the three queue workers, the scheduled calls, migrations. Cloud SQL, Pub/Sub, Vertex AI and Cloud Storage from the project Terraform made | You want one platform for the whole product on Google Cloud |
| **Any cluster**: `overlays/self-hosted` | The API (with its background work inside), the web app, migrations. Your Postgres, a Gemini API key, a volume | EKS, AKS, on-premises: a customer that runs its own cluster, no Google Cloud |
| **Cloud Run + GKE workers** | Only the three workers (`infra/k8s/workers.yaml`); the API and web app on Cloud Run | Today's pipeline, `cloudbuild.yaml`: [DEPLOY.md](DEPLOY.md) |

Use one layout per cluster. The GKE overlays and `workers.yaml` both create the workers in the `welcome-wave` namespace;
moving a cluster from one to the other is in [Switching from Cloud Run](#switching-from-cloud-run).

## What runs

| Pod | Command | Port | Ready when | Scales |
| --- | --- | --- | --- | --- |
| `api` | `uvicorn app.main:app` (the image's default) | 8080 | `/health/ready`: the database answers | CPU: staging 1-3, production 3-10; self-hosted 1 |
| `web` | nginx with the app's CSP and headers | 8080 | `/health` | CPU: staging 1-2, production 2-6 |
| `meeting-worker` | `python -m workers.worker` (`WORKER_QUEUE=meetings`) | 8081 | `/healthz`: subscribed to its queue | KEDA on the queue, 0-5 |
| `ingestion-worker` | the same, `ingestion` | 8081 | `/healthz` | KEDA, 0-4 |
| `review-worker` | the same, `reviews` | 8081 | `/healthz` | KEDA, 1-4 |
| `migrate` (Job) | `python -m app.migrate` | | finishes | once per release |
| 4 CronJobs | `python /scripts/call.py /internal/...` | | finishes | on schedule |

The Ingress sends `/api`, `/webhooks` and `/health/ready` to the API and everything else to the web app, like the Google
Cloud load balancer. `/internal/*` is not routed: the scheduled calls reach the API inside the cluster only.

Every pod runs as a non-root user with a read-only root filesystem, no capabilities, the runtime's seccomp profile and no
Kubernetes token; the namespace enforces the `restricted` Pod Security Standard, so the API server refuses anything else.
Network policies deny every connection that is not needed (the list is at the top of
[`base/networkpolicies.yaml`](../infra/k8s/base/networkpolicies.yaml)).

## The files

```
infra/k8s/
├── deploy.sh                   releases an overlay: migrations first, then the new code (below)
├── base/                       portable objects only
│   ├── config.env              the non-secret settings every pod reads (ConfigMap ww-config)
│   ├── secrets.example.yaml    the three Secrets' keys, no values (never rendered)
│   ├── api.yaml, web.yaml, workers.yaml, migrate.yaml, scheduler.yaml (+ scheduler/call.py)
│   ├── ingress.yaml, scaling.yaml (autoscalers, disruption budgets), networkpolicies.yaml
│   └── namespace.yaml, serviceaccounts.yaml
├── components/
│   ├── gke/                    Workload Identity, Cloud SQL Auth Proxy, KEDA, Google's load balancer
│   ├── gcp-secret-manager/     the Secrets, synced from Secret Manager (External Secrets Operator)
│   ├── self-hosted/            MODE=self_hosted: one API pod with a volume; no workers, no scheduled calls
│   └── public-url/             PUBLIC_URL's host becomes the Ingress host and the certificate's name
├── overlays/
│   ├── staging/, production/, self-hosted/
│   │   └── settings/config.env    THE file to edit per environment
└── workers.yaml                the Cloud Run layout's workers (cloudbuild.yaml applies it)
```

See what an overlay produces, without a cluster: `kubectl kustomize infra/k8s/overlays/production`. CI renders every
overlay on each push and checks it (`backend/tests/test_release_k8s.py`).

## GKE: first time

From a project where [DEPLOY.md](DEPLOY.md) steps 1-3 are done (Terraform, third-party setup, secrets). Terraform made the
Autopilot cluster `ww-workers-$ENV`, the Google service accounts, Cloud SQL and every secret. Commands assume
`PROJECT_ID`, `ENV` and `REGION=asia-south1` are exported, and `gcloud container clusters get-credentials ww-workers-$ENV
--region $REGION` has been run.

1. **Workload Identity.** Each Kubernetes service account acts as its Google one. Terraform binds the workers'; bind the
   API's, the migration's and the scheduler's once:

   ```bash
   for w in api migrate scheduler; do
     gcloud iam service-accounts add-iam-policy-binding ww-$w@$PROJECT_ID.iam.gserviceaccount.com --project $PROJECT_ID \
       --role roles/iam.workloadIdentityUser --member "serviceAccount:$PROJECT_ID.svc.id.goog[welcome-wave/$w]"
   done
   ```

2. **KEDA** (scales the workers on their queues): `PROJECT_ID=$PROJECT_ID ENV=$ENV ./infra/scripts/bootstrap-cluster.sh`.

3. **External Secrets Operator** (keeps the Secrets in step with Secret Manager). Pick a release that supports your
   cluster's version, and install it once:

   ```bash
   helm repo add external-secrets https://charts.external-secrets.io
   helm upgrade --install external-secrets external-secrets/external-secrets -n external-secrets --create-namespace \
     --version <release> --wait
   ```

   Each Secret is read with its own workload's identity, so Terraform's per-secret access still decides who reads what.

4. **The address.** Reserve a global IP, point the domain's `A` record at it, and let Google issue the certificate (15-60
   minutes after DNS resolves):

   ```bash
   gcloud compute addresses create ww-k8s-web-ip --global --project $PROJECT_ID
   gcloud compute addresses describe ww-k8s-web-ip --global --project $PROJECT_ID --format 'value(address)'
   ```

5. **Settings.** Edit `infra/k8s/overlays/<staging|production>/settings/config.env`: `PUBLIC_URL`, `GCP_PROJECT`,
   `GCS_BUCKET`, `CLOUD_SQL_INSTANCE`, `OUTBOX_FLUSH_INVOKER` (`ww-scheduler@<project>.iam.gserviceaccount.com`; its domain
   also names every workload's Google account), and the values from DEPLOY.md step 5 (`FIREBASE_API_KEY`, the GitHub App's
   ids, `GOOGLE_OAUTH_CLIENT_ID`, models, email). Commit them: nothing in that file is secret.

6. **Outside addresses.** Point the GitHub App's webhook (`https://<host>/webhooks/github`) and callback
   (`https://<host>/github/callback`), the Google OAuth redirect URI (`https://<host>/api/integrations/google/callback`)
   and the Calendly webhook at the new host. Set Terraform's `web_host` to it (and `api_host`, for the uptime check) and
   apply, so sign-in and the bucket's upload rule accept it. Not `domain`: that one builds the Cloud Run layout's load
   balancer.

7. **Release** (next section). Then check: `curl -fsS https://<host>/health/ready`, and `/api/me` without a token answers
   401.

## Releasing

Images come from the pipeline (`cloudbuild.yaml` pushes `backend:<commit>` and `web:<commit>` to Artifact Registry) or
from `docker build`. Release them by digest, so what runs is exactly what was tested:

```bash
REPO=$REGION-docker.pkg.dev/$PROJECT_ID/ww-images
infra/k8s/deploy.sh production \
  --api-image "$REPO/backend@$(gcloud artifacts docker images describe $REPO/backend:<commit> --format 'value(image_summary.digest)')" \
  --web-image "$REPO/web@$(gcloud artifacts docker images describe $REPO/web:<commit> --format 'value(image_summary.digest)')"
```

It prints the cluster it is about to change and asks first (`--yes` in CI; `--context` picks another cluster;
`--render-only` prints the manifests and changes nothing). Then, in order:

1. applies what runs no new code: settings, service accounts, Secret sources, Services, the Ingress, autoscalers, policies;
2. waits until the Secrets exist (synced, with the External Secrets Operator);
3. runs the migration Job with the new image and waits for it. **If it fails, it stops**: the running release keeps
   serving, and the Job's log is printed;
4. rolls out the API, the web app, the workers and the scheduled jobs, and waits until each is ready.

Do not `kubectl apply -k` an overlay on its own: the new API could start before its tables exist. With GitOps, Argo CD
runs the migration as a `PreSync` hook (annotations on the Job) for the same order; with Flux, put the Job in its own
Kustomization and make the app's depend on it.

## Migrations

`python -m app.migrate` runs as the database owner, from the Secret `ww-migrate`, which no other pod mounts. It applies the
numbered files in `backend/db/migrations` that have not run, keeps the `ww_app` role the services use (no row-level-security
bypass), creates the agents' checkpoint tables, and refuses to finish if a table could leak between organisations. One
migrator runs at a time (a database lock), and a table lock it cannot get within 10 seconds fails the run instead of
stalling the site.

A failed run: `kubectl -n welcome-wave logs job/migrate`, fix the cause, run `deploy.sh` again. The Job and its log stay
until the next release (a week at most). Migrations are forward-only: to undo one, write a new one.

## Secrets

| Secret | Read by | Keys |
| --- | --- | --- |
| `ww-api` | the API | `DATABASE_URL`, `GITHUB_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_CLIENT_SECRET`, `GOOGLE_OAUTH_CLIENT_SECRET`, `CALENDLY_WEBHOOK_SIGNING_KEY`, `TOKEN_ENCRYPTION_KEY`, `LANGSMITH_API_KEY`, `SMTP_PASSWORD`; self-hosted adds `GEMINI_API_KEY`, `BLOB_SIGNING_KEY`, `GITHUB_TOKEN` |
| `ww-worker` | the three workers | `DATABASE_URL`, `GITHUB_PRIVATE_KEY`, `LANGSMITH_API_KEY`, `SMTP_PASSWORD` |
| `ww-migrate` | the migration Job | `DATABASE_URL_OWNER`, `APP_DB_PASSWORD` |

Every key and what it is for: [`base/secrets.example.yaml`](../infra/k8s/base/secrets.example.yaml). Two ways to make them:

- **From Secret Manager** (the GKE overlays): `components/gcp-secret-manager` maps each key to Terraform's secret
  (`ww-database-url`, `ww-github-app-private-key`, ...). Values are refreshed every hour. A store that cannot be read keeps
  the last values.
- **By hand**, from files kept outside the repository (one `KEY=value` per line; the PEM as its own file):

  ```bash
  kubectl -n welcome-wave create secret generic ww-api --from-env-file=ww-api.env --from-file=GITHUB_PRIVATE_KEY=app.pem
  kubectl -n welcome-wave create secret generic ww-migrate --from-env-file=ww-migrate.env
  ```

Pods read Secrets when they start. After a rotation: `kubectl -n welcome-wave rollout restart deploy`.

## Scaling

| | Staging | Production | Self-hosted |
| --- | --- | --- | --- |
| API pods | 1-3 | 3-10, spread over zones | 1 |
| Web pods | 1-2 | 2-6 | 2-6 |
| Workers | KEDA: meetings 0-5, ingestion 0-4, reviews 1-4 | same | none (inside the API) |

The API scales on CPU and scales down slowly (one pod a minute, after five quiet minutes), because each pod holds live-update
streams. **The database caps it**: each API pod keeps up to 15 connections and each worker pod up to 7, and Cloud SQL allows
300 (dev) and 400 (prod). Raise an autoscaler's maximum only with `max_connections` (`infra/terraform/data.tf`).
Change the bounds in the overlay's `kustomization.yaml`.

## Rolling back

Run `deploy.sh` again with the previous images. The migration step finds nothing to apply (migrations only add), and the
previous code runs on the newer schema. `kubectl -n welcome-wave rollout undo deploy/api` also works for a quick undo: older
settings stay in the cluster under their own names.

## Any cluster

`overlays/self-hosted` runs the product without Google Cloud, as `scripts/selfhost.sh` does on one machine.

**You bring:**

- **Postgres 16 with pgvector** that the cluster can reach: a managed one (Amazon RDS, Azure Flexible Server, Cloud SQL) or
  an operator such as CloudNativePG. Create the database `welcomewave` and an owner role; the migration creates `ww_app`.
- **An Ingress controller and a certificate**: the Secret `ww-tls` (cert-manager can fill it: add its issuer annotation in
  the overlay). Set the controller's limits for this app: request bodies up to 2.1 GB on `/api` (KT recordings; 26 MB is
  enough without uploads), and a read timeout of an hour on `/api` (each tab holds a live-update stream the API ends every
  15 minutes). Its access log must not keep full URLs of `/welcome/`, `/api/preboarding/`, `/api/blobs/` and the OAuth
  callbacks: they carry sign-in links.
- **A default storage class** for the 50 GB data volume (documents, recordings, generated keys).
- **metrics-server**, which the web app's autoscaler reads (most managed clusters have it), and a network plugin that
  enforces network policies (Calico, Cilium) if you want them enforced.
- **A Firebase project** for sign-in and a **Gemini API key** ([INTEGRATIONS.md](INTEGRATIONS.md)).

**Then:** edit `overlays/self-hosted/settings/config.env`, create `ww-api` (at least `DATABASE_URL` for `ww_app` and
`GEMINI_API_KEY`) and `ww-migrate`, and release with `deploy.sh self-hosted`. The API runs as one pod, replaced on a release
(a few seconds without it), because its files are on one ReadWriteOnce volume.

**Backups:** the database (your provider's backups, or `pg_dump`) and the volume (a VolumeSnapshot). The volume holds
`.token-key` and `.signing-key`: without the first every organisation must connect Google again; without the second,
recording links signed before the restore stop working. Restore the database first, then the volume.

## Switching from Cloud Run

On a project that runs the Cloud Run layout, before the first GKE release:

```bash
kubectl -n welcome-wave delete deployment meeting-worker ingestion-worker review-worker
kubectl -n welcome-wave delete scaledobject --all; kubectl -n welcome-wave delete configmap worker-config
for j in outbox-flush recordings-poll lifecycle-tick audit-purge; do
  gcloud scheduler jobs pause ww-$j --location $REGION --project $PROJECT_ID   # the CronJobs do this work now
done
```

Then stop the pipeline's deploy steps for this project (they would put the Cloud Run services back), release with
`deploy.sh`, and move DNS to the new address. To go back, release Cloud Run with the pipeline and resume the scheduler jobs.

## When something is wrong

| You see | Look at |
| --- | --- |
| A pod is `Pending` | `kubectl -n welcome-wave describe pod <pod>`: the quota, or a node pool too small for the requests |
| `CreateContainerConfigError` | A Secret or key is missing: `kubectl -n welcome-wave get secret`; `describe externalsecret` |
| The API never becomes ready | `kubectl -n welcome-wave logs deploy/api`: it refuses to start with unsafe settings and says which; `/health/ready` fails while the database is unreachable (`logs deploy/api -c cloud-sql-proxy` on GKE) |
| 502 from the load balancer | `kubectl -n welcome-wave describe ingress welcome-wave`; on GKE the backends become healthy a few minutes after the first release |
| The certificate stays `Provisioning` | DNS does not point at `ww-k8s-web-ip` yet: `kubectl -n welcome-wave describe managedcertificate ww-web` |
| A scheduled job fails | `kubectl -n welcome-wave get jobs`, then `logs job/<name>`. 401/403: the scheduler's Workload Identity binding, or `OUTBOX_FLUSH_INVOKER`; 503 from the flush: events are waiting to be published |
| Workers never start | KEDA: `kubectl -n keda logs deploy/keda-operator`; the workers stay at zero until a message waits |
| Live updates stop every minute | The Ingress controller's read timeout on `/api` is too short |
