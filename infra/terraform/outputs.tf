output "bucket" { value = google_storage_bucket.data.name }
output "db_connection_name" { value = google_sql_database_instance.db.connection_name }
output "cluster" { value = google_container_cluster.workers.name }
output "firebase_api_key" {
  value       = google_apikeys_key.web.key_string
  sensitive   = true
  description = "Public by design (identifies the project to Identity Platform). terraform output -raw firebase_api_key"
}
output "lb_ip" { value = var.domain != "" ? google_compute_global_address.web[0].address : null }
output "service_accounts" {
  value = {
    api = google_service_account.api.email, worker = google_service_account.worker.email, migrate = google_service_account.migrate.email,
    web = google_service_account.web.email, build = google_service_account.build.email, keda = google_service_account.keda.email
  }
}
