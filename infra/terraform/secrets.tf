# Secrets. Database credentials and the key that encrypts stored Google refresh tokens are generated here; the rest are
# third-party values a human adds once (scripts/set-secrets.sh). Access is granted per secret, per workload.

# Encrypts each organisation's Google Calendar refresh token before it is stored (TOKEN_ENCRYPTION_KEY). Changing it
# makes every organisation connect Google again.
resource "random_password" "token_encryption_key" {
  length  = 48
  special = false
}

locals {
  generated_secrets = merge(local.db_urls, { "token-encryption-key" = random_password.token_encryption_key.result })
  generated_names   = ["database-url", "database-url-proxy", "database-url-owner", "app-db-password", "token-encryption-key"]
  human_secrets = ["github-app-private-key", "github-webhook-secret", "github-client-secret", "google-oauth-client-secret",
  "calendly-webhook-signing-key", "langsmith-api-key"]

  # which workload may read which secret (keys are static names so the first plan works before accounts exist)
  readers = {
    "database-url"           = ["api"]
    "database-url-proxy"     = ["worker"]
    "database-url-owner"     = ["migrate"]
    "app-db-password"        = ["migrate"]
    "token-encryption-key"         = ["api"]
    "github-app-private-key"       = ["api", "worker"]
    "github-webhook-secret"        = ["api"]
    "github-client-secret"         = ["api"]
    "google-oauth-client-secret"   = ["api"]
    "calendly-webhook-signing-key" = ["api"]
    "langsmith-api-key"            = ["api", "worker"]
  }
  workload_emails = {
    api = google_service_account.api.email, worker = google_service_account.worker.email, migrate = google_service_account.migrate.email
  }
  secret_reader_pairs = { for pair in flatten([for s, who in local.readers : [for w in who : { key = "${s}|${w}", secret = s, workload = w }]]) : pair.key => pair }
}

resource "google_secret_manager_secret" "s" {
  for_each  = toset(concat(local.generated_names, local.human_secrets))
  secret_id = "${local.prefix}-${each.value}"
  replication {
    user_managed {
      replicas { location = var.region } # data residency: secrets stay in the app's region
    }
  }
  depends_on = [time_sleep.apis]
}

resource "google_secret_manager_secret_version" "generated" {
  for_each    = toset(local.generated_names)
  secret      = google_secret_manager_secret.s[each.value].id
  secret_data = local.generated_secrets[each.value]
}

resource "google_secret_manager_secret_iam_member" "readers" {
  for_each  = local.secret_reader_pairs
  secret_id = google_secret_manager_secret.s[each.value.secret].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.workload_emails[each.value.workload]}"
}
