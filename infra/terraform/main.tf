provider "google" {
  project = var.project_id
  region  = var.region
}

# -----------------------------------------------------------------------------
# 1. Required Google Cloud API Enablement
# -----------------------------------------------------------------------------
locals {
  required_services = [
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudtrace.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.required_services)
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# -----------------------------------------------------------------------------
# 2. Artifact Registry Container Repository
# -----------------------------------------------------------------------------
data "google_artifact_registry_repository" "app_repo" {
  project       = var.project_id
  location      = var.region
  repository_id = "supportmaster"

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# 3. Least-Privilege Runtime Service Account
# -----------------------------------------------------------------------------
resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "supportmaster-runner"
  display_name = "SupportMaster Cloud Run Runtime Account"
  description  = "Dedicated least-privilege identity for SupportMaster web service and worker jobs"

  depends_on = [google_project_service.apis]
}

# -----------------------------------------------------------------------------
# 4. Secret Manager Container (Managed Out-of-Band for Zero State Leakage)
# -----------------------------------------------------------------------------
data "google_secret_manager_secret" "api_key" {
  project   = var.project_id
  secret_id = var.secret_name

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "runtime_access" {
  project   = var.project_id
  secret_id = data.google_secret_manager_secret.api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "cloud_trace_agent" {
  count   = var.enable_cloud_trace ? 1 : 0
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# -----------------------------------------------------------------------------
# 5. Cloud Run Web Service
# -----------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "web" {
  project  = var.project_id
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.image_tag

      ports {
        container_port = 8001
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "PORT"
        value = "8001"
      }

      env {
        name  = "SUPPORTMASTER_AUTH_MODE"
        value = var.auth_mode
      }

      env {
        name  = "SUPPORTMASTER_MODEL"
        value = var.model_name
      }

      env {
        name  = "SUPPORTMASTER_RUN_DB"
        value = "/app/data/runs.db"
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      env {
        name = "GOOGLE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.api_key.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.runtime_access
  ]
}

# Public invoker permission for hackathon judging / demo access
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.web.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# 6. Cloud Run Durable Worker Job
# -----------------------------------------------------------------------------
resource "google_cloud_run_v2_job" "worker" {
  project  = var.project_id
  name     = var.job_name
  location = var.region

  template {
    template {
      service_account = google_service_account.runtime.email
      max_retries     = 1
      timeout         = "600s"

      containers {
        image   = var.image_tag
        command = ["python", "-m", "supportmaster.worker"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "SUPPORTMASTER_MODEL"
          value = var.model_name
        }

        env {
          name  = "SUPPORTMASTER_RUN_DB"
          value = "/app/data/runs.db"
        }

        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }

        env {
          name = "GOOGLE_API_KEY"
          value_source {
            secret_key_ref {
              secret  = data.google_secret_manager_secret.api_key.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.runtime_access
  ]
}
