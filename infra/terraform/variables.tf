variable "project_id" {
  type        = string
  description = "The Google Cloud Project ID to deploy resources to. Must be supplied at runtime."
}

variable "region" {
  type        = string
  description = "The Google Cloud Region for Cloud Run, Artifact Registry, and Secret Manager."
}

variable "image_tag" {
  type        = string
  description = "The full container image URI (e.g. us-central1-docker.pkg.dev/PROJECT/supportmaster/supportmaster:COMMIT_SHA) to deploy."
}

variable "service_name" {
  type        = string
  default     = "supportmaster"
  description = "Name of the Cloud Run web service."
}

variable "job_name" {
  type        = string
  default     = "supportmaster-worker"
  description = "Name of the Cloud Run durable worker job."
}

variable "secret_name" {
  type        = string
  default     = "google-api-key"
  description = "Name of the Secret Manager secret container for the Gemini API key."
}

variable "auth_mode" {
  type        = string
  default     = "OPTIONAL"
  description = "Authentication mode for the web service (OPTIONAL for judging demo, REQUIRED for production)."
}

variable "model_name" {
  type        = string
  default     = "gemini-3.5-flash"
  description = "Default Gemini reasoning model for SupportMaster."
}

variable "enable_cloud_trace" {
  type        = bool
  default     = true
  description = "Whether to grant Cloud Trace Agent IAM role to the runtime service account."
}
