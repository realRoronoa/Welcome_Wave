# Optional custom domain: a global HTTPS load balancer with a Google-managed certificate in front of ww-web
# (which serves the app and proxies /api and /webhooks to ww-api), protected by Cloud Armor.
# Set var.domain, apply, then point an A record for the domain at the `lb_ip` output.
# The resources nothing else here references wait for the Compute API (time_sleep.apis); the rest wait through them.

locals { lb = var.domain != "" ? 1 : 0 }

resource "google_compute_global_address" "web" {
  count      = local.lb
  name       = "${local.prefix}-web-ip"
  depends_on = [time_sleep.apis]
}

resource "google_compute_region_network_endpoint_group" "web" {
  count                 = local.lb
  name                  = "${local.prefix}-web-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run { service = "ww-web" }
  depends_on = [time_sleep.apis]
}

resource "google_compute_security_policy" "web" {
  count = local.lb
  name  = "${local.prefix}-web-armor"
  rule { # keep any single client from flooding the API (the app adds a per-user chat limit on top)
    action   = "throttle"
    priority = 1000
    match {
      versioned_expr = "SRC_IPS_V1"
      config { src_ip_ranges = ["*"] }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 600
        interval_sec = 60
      }
    }
  }
  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config { src_ip_ranges = ["*"] }
    }
  }
  depends_on = [time_sleep.apis]
}

resource "google_compute_backend_service" "web" {
  count                 = local.lb
  name                  = "${local.prefix}-web-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.web[0].id
  backend { group = google_compute_region_network_endpoint_group.web[0].id }
  log_config {
    enable      = true
    sample_rate = 0.2
  }
}

resource "google_compute_url_map" "web" {
  count           = local.lb
  name            = "${local.prefix}-web"
  default_service = google_compute_backend_service.web[0].id
}

resource "google_compute_managed_ssl_certificate" "web" {
  count = local.lb
  name  = "${local.prefix}-web-cert"
  managed { domains = [var.domain] }
  depends_on = [time_sleep.apis]
}

resource "google_compute_target_https_proxy" "web" {
  count            = local.lb
  name             = "${local.prefix}-web-https"
  url_map          = google_compute_url_map.web[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.web[0].id]
}

resource "google_compute_global_forwarding_rule" "web" {
  count                 = local.lb
  name                  = "${local.prefix}-web-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_https_proxy.web[0].id
  ip_address            = google_compute_global_address.web[0].id
  port_range            = "443"
}

# http -> https
resource "google_compute_url_map" "redirect" {
  count = local.lb
  name  = "${local.prefix}-web-redirect"
  default_url_redirect {
    https_redirect = true
    strip_query    = false
  }
  depends_on = [time_sleep.apis]
}
resource "google_compute_target_http_proxy" "redirect" {
  count   = local.lb
  name    = "${local.prefix}-web-http"
  url_map = google_compute_url_map.redirect[0].id
}
resource "google_compute_global_forwarding_rule" "redirect" {
  count                 = local.lb
  name                  = "${local.prefix}-web-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_http_proxy.redirect[0].id
  ip_address            = google_compute_global_address.web[0].id
  port_range            = "80"
}
