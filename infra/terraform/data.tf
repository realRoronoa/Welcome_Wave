# ---------- Cloud SQL: PostgreSQL 16 + pgvector ----------
resource "google_sql_database_instance" "db" {
  name             = "${local.prefix}-db-${var.env}"
  database_version = "POSTGRES_16"
  region           = var.region
  settings {
    edition           = "ENTERPRISE" # PostgreSQL 16 defaults to Enterprise Plus, which does not offer these tiers
    tier              = var.env == "prod" ? "db-custom-2-7680" : "db-custom-1-3840"
    availability_type = var.env == "prod" ? "REGIONAL" : "ZONAL" # regional = automatic failover to another zone
    disk_autoresize   = true
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "20:30" # 02:00 IST
      transaction_log_retention_days = 7
      backup_retention_settings { retained_backups = var.env == "prod" ? 30 : 7 }
    }
    ip_configuration {
      ipv4_enabled = true # reached only through the Cloud SQL connector / Auth Proxy (IAM-authorised, TLS); no authorised networks
      ssl_mode     = "ENCRYPTED_ONLY"
    }
    database_flags { # worst case ~245: ww-api 10 x 15 (pools, checkpoints, LISTEN) + KEDA's 13 worker pods x 7 + migrations
      name  = "max_connections"
      value = var.env == "prod" ? "400" : "300"
    }
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    insights_config {
      query_insights_enabled  = true
      record_application_tags = true
    }
    maintenance_window {
      day  = 7 # Sunday
      hour = 21
    }
  }
  deletion_protection = var.env == "prod"
  depends_on          = [time_sleep.apis]
}

resource "google_sql_database" "app" {
  name     = "welcomewave"
  instance = google_sql_database_instance.db.name
}

resource "random_password" "db_owner" {
  length  = 32
  special = false
}
resource "random_password" "db_app" {
  length  = 32
  special = false
}

# The migrator owns the schema. The services connect as ww_app, which the migrator creates without BYPASSRLS.
resource "google_sql_user" "owner" {
  name     = "ww_owner"
  instance = google_sql_database_instance.db.name
  password = random_password.db_owner.result
}

locals {
  db_socket = "host=/cloudsql/${google_sql_database_instance.db.connection_name}"
  db_urls = {
    "database-url"       = "postgresql+psycopg://ww_app:${random_password.db_app.result}@/welcomewave?${local.db_socket}" # Cloud Run
    "database-url-proxy" = "postgresql+psycopg://ww_app:${random_password.db_app.result}@127.0.0.1:5432/welcomewave"      # GKE + Auth Proxy sidecar
    "database-url-owner" = "postgresql://ww_owner:${random_password.db_owner.result}@/welcomewave?${local.db_socket}"
    "app-db-password"    = random_password.db_app.result
  }
}

# ---------- Cloud Storage: recordings and uploaded documents ----------
# The browser uploads KT recordings straight into the bucket with a signed URL (backend/app/api/uploads.py), so the bucket
# accepts a PUT from the web app's origins with the headers that URL binds. The origins are the domain and ww-web's run.app
# host (`domain`, `web_host`): set `web_host` after the first deploy when there is no domain, or uploads are refused.
locals {
  web_origins = compact([var.domain != "" ? "https://${var.domain}" : "", var.web_host != "" ? "https://${var.web_host}" : ""])
}

resource "google_storage_bucket" "data" {
  name                        = "${var.project_id}-${local.prefix}-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning { enabled = false } # recordings must really disappear after 30 days
  lifecycle_rule {               # raw recordings are deleted after 30 days; notes, transcripts and KT links stay in Postgres
    condition {
      age            = 30
      matches_prefix = ["recordings/"]
    }
    action { type = "Delete" }
  }
  dynamic "cors" {
    for_each = length(local.web_origins) > 0 ? [1] : []
    content {
      origin          = local.web_origins
      method          = ["PUT"]
      response_header = ["Content-Type", "x-goog-content-length-range", "x-goog-if-generation-match"]
      max_age_seconds = 3600
    }
  }
  soft_delete_policy { retention_duration_seconds = 0 } # deleted recordings must really be gone (30-day promise)
  depends_on = [time_sleep.apis]
}

resource "google_storage_bucket_iam_member" "api_rw" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.api.email}"
}
resource "google_storage_bucket_iam_member" "worker_read" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}

# Gemini reads recordings straight from the bucket (gs:// URIs) as the Vertex AI service agent.
resource "google_project_service_identity" "vertex" {
  provider   = google-beta
  service    = "aiplatform.googleapis.com"
  depends_on = [time_sleep.apis]
}
resource "google_storage_bucket_iam_member" "vertex_read" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_project_service_identity.vertex.email}"
}
