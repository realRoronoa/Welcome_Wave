# Least privilege: one service account per workload. Secret access is granted per secret (secrets.tf), so the
# API can never read the migrator's owner credentials.

resource "google_service_account" "api" {
  account_id   = "${local.prefix}-api"
  display_name = "Welcome Wave API (Cloud Run)"
  depends_on   = [time_sleep.apis]
}
resource "google_service_account" "worker" {
  account_id   = "${local.prefix}-worker"
  display_name = "Welcome Wave workers (GKE, Workload Identity)"
  depends_on   = [time_sleep.apis]
}
resource "google_service_account" "migrate" {
  account_id   = "${local.prefix}-migrate"
  display_name = "Welcome Wave database migrations (Cloud Run job)"
  depends_on   = [time_sleep.apis]
}
resource "google_service_account" "web" {
  account_id   = "${local.prefix}-web"
  display_name = "Welcome Wave web (Cloud Run, static + proxy; no Google API access)"
  depends_on   = [time_sleep.apis]
}
resource "google_service_account" "keda" {
  account_id   = "${local.prefix}-keda"
  display_name = "KEDA reads Pub/Sub backlog metrics"
  depends_on   = [time_sleep.apis]
}
resource "google_service_account" "scheduler" {
  account_id   = "${local.prefix}-scheduler"
  display_name = "Cloud Scheduler: calls the API's outbox flush, recording poll, audit purge (OIDC, no roles)"
  depends_on   = [time_sleep.apis]
}
resource "google_service_account" "build" {
  account_id   = "${local.prefix}-build"
  display_name = "Cloud Build: test, build, deploy"
  depends_on   = [time_sleep.apis]
}

locals {
  api_roles     = ["roles/cloudsql.client", "roles/pubsub.publisher", "roles/aiplatform.user", "roles/logging.logWriter", "roles/cloudtrace.agent", "roles/monitoring.metricWriter"]
  worker_roles  = ["roles/cloudsql.client", "roles/pubsub.subscriber", "roles/pubsub.publisher", "roles/aiplatform.user", "roles/dlp.user", "roles/logging.logWriter", "roles/monitoring.metricWriter"]
  migrate_roles = ["roles/cloudsql.client", "roles/logging.logWriter"]
  build_roles   = ["roles/run.admin", "roles/artifactregistry.writer", "roles/container.developer", "roles/logging.logWriter", "roles/cloudsql.client"]
}

resource "google_project_iam_member" "api" {
  for_each = toset(local.api_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.api.email}"
}
resource "google_project_iam_member" "worker" {
  for_each = toset(local.worker_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_project_iam_member" "migrate" {
  for_each = toset(local.migrate_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.migrate.email}"
}
resource "google_project_iam_member" "build" {
  for_each = toset(local.build_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.build.email}"
}
resource "google_project_iam_member" "keda" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.keda.email}"
}

# The API signs short-lived recording URLs with its own identity (IAM signBlob, no key file).
resource "google_service_account_iam_member" "api_sign_self" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.api.email}"
}

# Cloud Build deploys services that run as these accounts.
resource "google_service_account_iam_member" "build_act_as" {
  for_each           = { api = google_service_account.api.name, web = google_service_account.web.name, migrate = google_service_account.migrate.name, worker = google_service_account.worker.name }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.build.email}"
}

# Workload Identity: Kubernetes service accounts act as Google service accounts, no key files.
resource "google_service_account_iam_member" "worker_wi" {
  service_account_id = google_service_account.worker.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[welcome-wave/worker]"
  depends_on         = [google_container_cluster.workers]
}
resource "google_service_account_iam_member" "keda_wi" {
  service_account_id = google_service_account.keda.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[keda/keda-operator]"
  depends_on         = [google_container_cluster.workers]
}
