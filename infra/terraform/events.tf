# One topic + subscription + dead-letter topic (with its own subscription, so failed messages are kept for
# inspection and replay instead of vanishing) per queue.

resource "google_pubsub_topic" "queue" {
  for_each                   = toset(local.queues)
  name                       = "${local.prefix}-${each.value}"
  message_retention_duration = "604800s"
  depends_on                 = [time_sleep.apis]
}
resource "google_pubsub_topic" "dead" {
  for_each   = toset(local.queues)
  name       = "${local.prefix}-${each.value}-dead"
  depends_on = [time_sleep.apis]
}

resource "google_pubsub_subscription" "queue" {
  for_each                   = toset(local.queues)
  name                       = "${local.prefix}-${each.value}-sub"
  topic                      = google_pubsub_topic.queue[each.value].id
  ack_deadline_seconds       = each.value == "meetings" ? 600 : 120 # the client library extends leases while a handler runs
  message_retention_duration = "604800s"
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead[each.value].id
    max_delivery_attempts = each.value == "meetings" ? 10 : 5
  }
  expiration_policy { ttl = "" }
}

resource "google_pubsub_subscription" "dead" {
  for_each                   = toset(local.queues)
  name                       = "${local.prefix}-${each.value}-dead-sub"
  topic                      = google_pubsub_topic.dead[each.value].id
  message_retention_duration = "604800s"
  expiration_policy { ttl = "" }
}

# Dead-lettering is done by Pub/Sub's own service agent: it must publish to the dead topic and ack the original.
locals {
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
resource "google_pubsub_topic_iam_member" "dead_publisher" {
  for_each = toset(local.queues)
  topic    = google_pubsub_topic.dead[each.value].id
  role     = "roles/pubsub.publisher"
  member   = local.pubsub_agent
}
resource "google_pubsub_subscription_iam_member" "dead_subscriber" {
  for_each     = toset(local.queues)
  subscription = google_pubsub_subscription.queue[each.value].id
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

# The API writes every event to its outbox table before publishing it; an event whose publish failed is retried by
# POST /internal/outbox/flush, which this job calls every 2 minutes. The API accepts only a Google-signed OIDC token
# for ww-scheduler with this audience (OUTBOX_FLUSH_AUDIENCE / OUTBOX_FLUSH_INVOKER, set by cloudbuild.yaml).
locals {
  api_url = "https://ww-api-${data.google_project.this.number}.${var.region}.run.app" # Cloud Run's deterministic URL
}
# KT sessions on Google Calendar: once a session has ended, POST /internal/recordings/poll imports the recording Meet
# attached to its event (from Google Drive) and marks sessions whose recording never came as ended
# (docs/specs/scheduling.md). Same token, same audience as the outbox flush. An import streams the video from Drive into
# Cloud Storage in chunks within the request (nothing is spooled on the instance); one import per session runs at a time.
resource "google_cloud_scheduler_job" "recordings_poll" {
  name             = "${local.prefix}-recordings-poll"
  region           = var.region
  schedule         = "*/10 * * * *"
  attempt_deadline = "1800s" # the longest Cloud Scheduler allows; ww-api's own timeout is 60 minutes

  retry_config { retry_count = 0 } # the next run is ten minutes away
  http_target {
    http_method = "POST"
    uri         = "${local.api_url}/internal/recordings/poll"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.api_url
    }
  }
  depends_on = [time_sleep.apis]
}

# Audit log retention: POST /internal/audit/purge deletes entries older than AUDIT_RETENTION_DAYS in every organisation,
# once a day. Same token, same audience as the outbox flush.
resource "google_cloud_scheduler_job" "audit_purge" {
  name             = "${local.prefix}-audit-purge"
  region           = var.region
  schedule         = "17 3 * * *" # daily, 03:17 in the job's time zone (UTC)
  attempt_deadline = "300s"
  retry_config { retry_count = 1 }
  http_target {
    http_method = "POST"
    uri         = "${local.api_url}/internal/audit/purge"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.api_url
    }
  }
  depends_on = [time_sleep.apis]
}

# The daily tick of the agents (docs/AGENTS.md): POST /internal/lifecycle/tick runs `lifecycle.tick` for every organisation with
# a program in progress: the Coach's blocker rules and nudges, the weekly summaries, the Planner's completion drafts, the
# Guide's digest. Each organisation's tick runs once per day (its dedupe key), so the hourly schedule only makes a missed run
# catch up. Same token, same audience as the outbox flush.
resource "google_cloud_scheduler_job" "lifecycle_tick" {
  name             = "${local.prefix}-lifecycle-tick"
  region           = var.region
  schedule         = "23 * * * *" # hourly; the first run after midnight (UTC) does the day's work
  attempt_deadline = "600s"
  retry_config { retry_count = 1 }
  http_target {
    http_method = "POST"
    uri         = "${local.api_url}/internal/lifecycle/tick"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.api_url
    }
  }
  depends_on = [time_sleep.apis]
}

resource "google_cloud_scheduler_job" "outbox_flush" {
  name             = "${local.prefix}-outbox-flush"
  region           = var.region
  schedule         = "*/2 * * * *"
  attempt_deadline = "60s"
  retry_config { retry_count = 0 } # the next run is two minutes away
  http_target {
    http_method = "POST"
    uri         = "${local.api_url}/internal/outbox/flush"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = local.api_url
    }
  }
  depends_on = [time_sleep.apis]
}
