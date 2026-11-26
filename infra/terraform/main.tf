# Welcome Wave - Google Cloud infrastructure. One GCP project per environment (dev, prod).
#
#   terraform init -backend-config="bucket=<state-bucket>" -backend-config="prefix=welcome-wave/dev"
#   terraform apply -var-file=dev.tfvars
#
# Files: main.tf (providers, variables, APIs) - iam.tf - data.tf (Cloud SQL, GCS) - events.tf (Pub/Sub)
#        compute.tf (GKE, Artifact Registry) - secrets.tf - identity.tf - observability.tf - lb.tf - outputs.tf

terraform {
  required_version = ">= 1.6"
  backend "gcs" {} # bucket and prefix are passed at init time; the state holds generated passwords, keep the bucket private
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 6.0" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 6.0" }
    random      = { source = "hashicorp/random", version = "~> 3.6" }
    time        = { source = "hashicorp/time", version = "~> 0.12" }
  }
}

variable "project_id" { type = string }
variable "env" {
  type = string
  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be dev or prod"
  }
}
variable "region" {
  type    = string
  default = "asia-south1"
}
variable "alert_email" {
  type        = string
  description = "Where uptime, dead-letter and error alerts go"
}
variable "billing_account" {
  type        = string
  default     = ""
  description = "Billing account id (XXXXXX-XXXXXX-XXXXXX). Empty = no budget alert"
}
variable "monthly_budget_inr" {
  type    = number
  default = 15000
}
variable "domain" {
  type        = string
  default     = ""
  description = "e.g. app.welcomewave.dev. Empty = use the run.app URLs (no load balancer, no Cloud Armor)"
}
variable "api_host" {
  type        = string
  default     = ""
  description = "Host the uptime check calls /health/ready on: the domain (the web app proxies it to the API) or ww-api's run.app host"
}
variable "web_host" {
  type        = string
  default     = ""
  description = "ww-web's run.app host (after the first deploy), so sign-in also works on that URL"
}
variable "github_repo" {
  type        = string
  default     = ""
  description = "owner/name of this code's GitHub repo, for the Cloud Build triggers. Empty = create triggers by hand"
}

variable "build_substitutions" {
  type        = map(string)
  default     = {}
  description = "Non-secret deploy configuration for cloudbuild.yaml: _GITHUB_APP_ID, _GITHUB_APP_SLUG, _GITHUB_CLIENT_ID, _GOOGLE_OAUTH_CLIENT_ID, _PUBLIC_URL, _MODEL_FAST, _MODEL_SMART, _MODEL_FALLBACK, _API_MIN_INSTANCES, _SIGNUP_ENABLED, _LANGSMITH_TRACING, _LANGSMITH_ENDPOINT, _DB_INSTANCE"
}

# billing_project + user_project_override: with user credentials (gcloud auth application-default login), APIs such as
# API Keys and Billing Budgets need a quota project, or the first apply fails. Every call is then billed to this project,
# so Cloud Resource Manager and Service Usage must be enabled before the first plan (docs/DEPLOY.md step 1 does it).
provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}
provider "google-beta" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

data "google_project" "this" {}

locals {
  prefix = "ww"
  queues = ["ingestion", "meetings", "reviews"]
  apis = [
    "run.googleapis.com", "container.googleapis.com", "sqladmin.googleapis.com", "pubsub.googleapis.com",
    "aiplatform.googleapis.com", "secretmanager.googleapis.com", "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com", "identitytoolkit.googleapis.com", "apikeys.googleapis.com", "dlp.googleapis.com",
    "monitoring.googleapis.com", "logging.googleapis.com", "iamcredentials.googleapis.com", "cloudresourcemanager.googleapis.com",
    "billingbudgets.googleapis.com", "compute.googleapis.com", "cloudtrace.googleapis.com", "iam.googleapis.com",
    "serviceusage.googleapis.com", "cloudscheduler.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# Enabling an API returns before every backend sees it; resources that use a fresh API wait a little.
resource "time_sleep" "apis" {
  depends_on      = [google_project_service.apis]
  create_duration = "60s"
}
