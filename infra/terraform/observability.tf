# Alerts go to one email channel: API down, messages dead-lettered, server errors, events not published, spend.

resource "google_monitoring_notification_channel" "email" {
  display_name = "${local.prefix}-${var.env} on-call"
  type         = "email"
  labels       = { email_address = var.alert_email }
  depends_on   = [time_sleep.apis]
}

resource "google_monitoring_uptime_check_config" "api" {
  count        = var.api_host != "" ? 1 : 0
  display_name = "${local.prefix}-${var.env}-api-ready"
  timeout      = "10s"
  period       = "60s"
  http_check {
    path    = "/health/ready"
    port    = 443
    use_ssl = true
  }
  monitored_resource {
    type   = "uptime_url"
    labels = { project_id = var.project_id, host = var.api_host }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  count        = var.api_host != "" ? 1 : 0
  display_name = "${local.prefix}-${var.env}: API is down"
  combiner     = "OR"
  conditions {
    display_name = "uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.api[0].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "120s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_alert_policy" "dead_letters" {
  display_name = "${local.prefix}-${var.env}: events dead-lettered"
  combiner     = "OR"
  conditions {
    display_name = "messages waiting in a dead-letter subscription"
    condition_threshold {
      filter          = "resource.type=\"pubsub_subscription\" AND metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\" AND resource.label.subscription_id=monitoring.regex.full_match(\"${local.prefix}-.*-dead-sub\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_logging_metric" "server_errors" {
  name   = "${local.prefix}_api_5xx"
  filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"ww-api\" AND jsonPayload.message=\"request\" AND jsonPayload.status>=500"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
  depends_on = [time_sleep.apis]
}

# Alert policies reject a log-based metric for a short while after it is created.
resource "time_sleep" "server_errors_metric" {
  depends_on      = [google_logging_metric.server_errors]
  create_duration = "60s"
}

resource "google_monitoring_alert_policy" "server_errors" {
  display_name = "${local.prefix}-${var.env}: API server errors"
  combiner     = "OR"
  conditions {
    display_name = "more than 5 server errors in 5 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.server_errors.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
  depends_on            = [time_sleep.server_errors_metric]
}

# Every event the outbox could not publish logs this line (a request's own send, and each 2-minute Scheduler flush).
resource "google_logging_metric" "outbox_unsent" {
  name   = "${local.prefix}_outbox_unsent"
  filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"ww-api\" AND jsonPayload.logger=\"outbox\" AND jsonPayload.message=\"event not sent; the outbox will retry\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
  depends_on = [time_sleep.apis]
}

resource "time_sleep" "outbox_unsent_metric" {
  depends_on      = [google_logging_metric.outbox_unsent]
  create_duration = "60s"
}

resource "google_monitoring_alert_policy" "outbox_unsent" {
  display_name = "${local.prefix}-${var.env}: events not published"
  combiner     = "OR"
  conditions {
    display_name = "publishing to Pub/Sub failing for 10 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.outbox_unsent.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "600s" # a blip is retried by the next flush; failures in every window for 10 minutes are not a blip
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
  depends_on            = [time_sleep.outbox_unsent_metric]
}

resource "google_billing_budget" "monthly" {
  count           = var.billing_account != "" ? 1 : 0
  billing_account = var.billing_account
  display_name    = "${local.prefix}-${var.env} monthly"
  budget_filter { projects = ["projects/${data.google_project.this.number}"] }
  amount {
    specified_amount {
      currency_code = "INR"
      units         = tostring(var.monthly_budget_inr)
    }
  }
  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.9 }
  threshold_rules { threshold_percent = 1.0 }
  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = false
  }
  depends_on = [time_sleep.apis]
}
