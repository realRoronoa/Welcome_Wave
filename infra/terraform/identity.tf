# Identity Platform: email/password and email-link sign-in. Google sign-in additionally needs an OAuth client
# (Console > Identity Platform > Providers > Google), which cannot be created by Terraform.

resource "google_identity_platform_config" "auth" {
  project                    = var.project_id
  autodelete_anonymous_users = true
  sign_in {
    allow_duplicate_emails = false
    email {
      enabled           = true
      password_required = true
    }
  }
  authorized_domains = compact(["localhost", "${var.project_id}.firebaseapp.com", "${var.project_id}.web.app", var.domain, var.web_host])
  depends_on         = [time_sleep.apis]
}

# The browser API key only identifies the project to Identity Platform; it is public by design and restricted to it.
resource "google_apikeys_key" "web" {
  name         = "${local.prefix}-web-${var.env}"
  display_name = "Welcome Wave web sign-in"
  restrictions {
    api_targets { service = "identitytoolkit.googleapis.com" }
    api_targets { service = "securetoken.googleapis.com" }
    dynamic "browser_key_restrictions" {
      for_each = var.domain != "" ? [1] : []
      content {
        # the sign-in popup and email-action pages run on the auth domain, so it must be allowed too
        allowed_referrers = compact(["https://${var.domain}/*", "https://${var.project_id}.firebaseapp.com/*", "https://${var.project_id}.web.app/*",
        var.web_host != "" ? "https://${var.web_host}/*" : "", "http://localhost:5173/*"])
      }
    }
  }
  depends_on = [time_sleep.apis]
}
