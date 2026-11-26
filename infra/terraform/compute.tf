# Cloud Run services (ww-api, ww-web) and the migration job (ww-migrate) are deployed by Cloud Build on every
# merge (cloudbuild.yaml), so a deploy is always an immutable, rollback-able revision of a tested image.

resource "google_artifact_registry_repository" "images" {
  repository_id = "${local.prefix}-images"
  format        = "DOCKER"
  location      = var.region
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions { keep_count = 30 }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition { older_than = "2592000s" }
  }
  depends_on = [time_sleep.apis]
}

# Nodes run as a minimal account (new organisations disable the default compute account's broad roles).
resource "google_service_account" "nodes" {
  account_id   = "${local.prefix}-gke-nodes"
  display_name = "Welcome Wave GKE nodes"
  depends_on   = [time_sleep.apis]
}
resource "google_project_iam_member" "nodes" {
  for_each = toset(["roles/container.defaultNodeServiceAccount", "roles/artifactregistry.reader"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "workers" {
  name                = "${local.prefix}-workers-${var.env}"
  location            = var.region # regional: pods spread across zones
  enable_autopilot    = true       # Google manages nodes; Workload Identity is on by default
  deletion_protection = var.env == "prod"
  release_channel { channel = "REGULAR" }
  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }
  depends_on = [time_sleep.apis]
}

# Empty values are dropped so cloudbuild.yaml's defaults apply instead of being overridden with "".
locals { build_subs = { for k, v in var.build_substitutions : k => v if v != "" } }

# Optional: Cloud Build triggers. They need the GitHub repository connected to Cloud Build once, by hand
# (Console > Cloud Build > Repositories > Connect). Leave github_repo empty to create triggers in the console.
resource "google_cloudbuild_trigger" "main" {
  count           = var.github_repo != "" && var.env == "dev" ? 1 : 0
  name            = "${local.prefix}-main-to-dev"
  location        = "global" # triggers on the GitHub (Cloud Build GitHub App) connection are global
  service_account = google_service_account.build.id
  filename        = "cloudbuild.yaml"
  substitutions   = merge({ _ENV = "dev", _REGION = var.region, _DB_INSTANCE = google_sql_database_instance.db.name, _FIREBASE_API_KEY = google_apikeys_key.web.key_string }, local.build_subs)
  github {
    owner = split("/", var.github_repo)[0]
    name  = split("/", var.github_repo)[1]
    push { branch = "^main$" }
  }
}
resource "google_cloudbuild_trigger" "release" {
  count           = var.github_repo != "" && var.env == "prod" ? 1 : 0
  name            = "${local.prefix}-tag-to-prod"
  location        = "global" # triggers on the GitHub (Cloud Build GitHub App) connection are global
  service_account = google_service_account.build.id
  filename        = "cloudbuild.yaml"
  substitutions   = merge({ _ENV = "prod", _REGION = var.region, _DB_INSTANCE = google_sql_database_instance.db.name, _FIREBASE_API_KEY = google_apikeys_key.web.key_string }, local.build_subs)
  github {
    owner = split("/", var.github_repo)[0]
    name  = split("/", var.github_repo)[1]
    push { tag = "^v[0-9]+\\.[0-9]+\\.[0-9]+$" }
  }
}
