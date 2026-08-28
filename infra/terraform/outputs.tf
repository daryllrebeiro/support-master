output "service_url" {
  value       = google_cloud_run_v2_service.web.uri
  description = "The public HTTPS URL of the deployed SupportMaster web service."
}

output "service_name" {
  value       = google_cloud_run_v2_service.web.name
  description = "The name of the Cloud Run web service."
}

output "job_name" {
  value       = google_cloud_run_v2_job.worker.name
  description = "The name of the Cloud Run durable worker job."
}

output "service_account_email" {
  value       = google_service_account.runtime.email
  description = "The email address of the runtime service account."
}

output "artifact_registry_repo" {
  value       = google_artifact_registry_repository.app_repo.name
  description = "The name of the Artifact Registry repository."
}

output "secret_id" {
  value       = google_secret_manager_secret.api_key.secret_id
  description = "The ID of the Secret Manager secret container."
}
