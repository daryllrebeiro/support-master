# SupportMaster: Terraform-Provisioned Google Cloud Deployment

SupportMaster features an Infrastructure-as-Code (IaC) deployment layer combining **Terraform** for declarative cloud resources and an **environment-variable-driven wrapper script** for zero-leak secret injection and live verification.

---

## 1. Architecture Overview

```
 [ Local Dev / CI ] ---> Env Vars (GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION, GOOGLE_API_KEY)
                               |
               +---------------+---------------+
               |                               |
       [ 1. Terraform ]                [ 2. Cloud Build ]
  - Required APIs Enablement      - Builds container from repo
  - Artifact Registry Repo        - Tags with commit SHA
  - Least-Privilege IAM Account   - Pushes to Artifact Registry
  - Secret Manager Container                   |
  - Cloud Run Service (Web) <------------------+
  - Cloud Run Job (Worker)
  - Remote GCS State Storage
               |
  [ 3. Imperative Secret Injection ]
  - Injects GOOGLE_API_KEY directly via gcloud
  - Secret NEVER written to disk or tfstate
               |
  [ 4. Automated Health Verification ]
  - /health/live & /health/ready probes
```

### Infrastructure Provisioned (`infra/terraform/`)
| Resource | Terraform Resource Name | Purpose |
| :--- | :--- | :--- |
| **API Enablement** | `google_project_service.apis` | Enables `run`, `cloudbuild`, `artifactregistry`, `secretmanager`, `cloudtrace` |
| **Artifact Registry** | `google_artifact_registry_repository.app_repo` | Stores immutable Docker images tagged with git commit SHA |
| **Runtime Service Account** | `google_service_account.runtime` | `supportmaster-runner` (least-privilege runtime identity) |
| **IAM Secret Binding** | `google_secret_manager_secret_iam_member` | Grants `roles/secretmanager.secretAccessor` on the specific API key secret |
| **Secret Container** | `google_secret_manager_secret.api_key` | Container for `google-api-key` (values injected outside Terraform) |
| **Cloud Run Web Service** | `google_cloud_run_v2_service.web` | Hosts UI, workspace, SSE streams, model picker, and REST APIs on port 8001 |
| **Cloud Run Worker Job** | `google_cloud_run_v2_job.worker` | Executes asynchronous, durable multi-agent workflow tasks |
| **State Storage** | `backend "gcs"` | Remote state in `gs://${PROJECT_ID}-tfstate` |

---

## 2. Prerequisites (One-Time Setup)

1. A Google Cloud Project with billing enabled.
2. `gcloud` CLI installed and authenticated with Application Default Credentials:
   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```
3. `terraform` CLI (>= 1.5.0) installed.

---

## 3. Environment-Driven Deployment (One Command)

### Option A: Shell Exports (Bash / Zsh / Cloud Shell)
```bash
export GOOGLE_CLOUD_PROJECT="my-project-id"
export GOOGLE_CLOUD_REGION="us-central1"
export GOOGLE_API_KEY="AIzaSy..."

./scripts/deploy.sh
```

### Option B: PowerShell (Windows)
```powershell
$env:GOOGLE_CLOUD_PROJECT = "my-project-id"
$env:GOOGLE_CLOUD_REGION  = "us-central1"
$env:GOOGLE_API_KEY       = "AIzaSy..."

.\scripts\deploy.ps1
```

---

## 4. What the Deployment Script Executes

1. **Environment Validation**: Confirms `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_REGION`, and `GOOGLE_API_KEY` are populated.
2. **Pre-flight Checks**: Verifies `gcloud`, `terraform`, active ADC, and billing.
3. **State Bucket Bootstrap**: Ensures `gs://${PROJECT_ID}-tfstate` exists with uniform bucket-level access.
4. **Targeted Foundation Apply**: Runs `terraform apply` targeting APIs and Artifact Registry so the Docker repository exists prior to image push.
5. **Container Build**: Cloud Build compiles the Docker image and tags it with the current git commit SHA (`${REGION}-docker.pkg.dev/${PROJECT}/supportmaster/supportmaster:${COMMIT_SHA}`).
6. **Full Infrastructure Apply**: Deploys the Cloud Run Service and Cloud Run Job with auto-scaling bounds (0 to 2 instances) and CPU/Memory limits (1 CPU, 1Gi RAM).
7. **Imperative Secret Value Injection**: Injects `GOOGLE_API_KEY` directly from the environment into Secret Manager via `gcloud secrets versions add` — ensuring the raw key never enters `.tf` files or Terraform state.
8. **Live Health Verification**: Pings `${SERVICE_URL}/health/live` and verifies HTTP 200 before exiting.

---

## 5. Security & Isolation Guarantees

- **Zero Secret Ingestion in State**: Terraform manages the secret *resource container*, but the secret *payload* is injected out-of-band via gcloud. No API key ever appears in `terraform.tfstate` or plan outputs.
- **Least-Privilege Identity**: The runtime account `supportmaster-runner` only possesses `roles/secretmanager.secretAccessor` on the `google-api-key` secret and `roles/cloudtrace.agent` (if tracing is enabled). No Owner/Editor roles are granted.
- **Immutable Container Tagging**: Deployments use the git commit SHA rather than `:latest`, ensuring full reproducibility and audit traceability.
- **Non-Root Execution**: Container runs under unprivileged UID `8888` (`appuser`).