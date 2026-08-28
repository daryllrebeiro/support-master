#!/usr/bin/env bash
# =============================================================================
# SupportMaster: Terraform-Provisioned, Env-Driven Single-Command Deployment
# =============================================================================
#
# Usage:
#   export GOOGLE_CLOUD_PROJECT="my-project-id"
#   export GOOGLE_CLOUD_REGION="us-central1"
#   export GOOGLE_API_KEY="AIzaSy..."
#   ./scripts/deploy.sh
#
# Prerequisites:
#   1. Google Cloud CLI (gcloud) installed and authenticated:
#      gcloud auth application-default login
#   2. Terraform CLI (>= 1.5.0) installed.
#   3. A GCP project with billing enabled.
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# 1. Environment Variable Validation
# -----------------------------------------------------------------------------
echo "==> [1/7] Validating required environment variables..."

if [ -z "${GOOGLE_CLOUD_PROJECT:-}" ]; then
  echo "ERROR: GOOGLE_CLOUD_PROJECT is not set." >&2
  echo "Please export your Google Cloud Project ID (e.g. export GOOGLE_CLOUD_PROJECT=\"my-project\")." >&2
  exit 1
fi

GOOGLE_CLOUD_REGION="${GOOGLE_CLOUD_REGION:-us-central1}"

if [ -z "${GOOGLE_API_KEY:-}" ]; then
  echo "ERROR: GOOGLE_API_KEY is not set." >&2
  echo "Please export your Gemini API key from Google AI Studio (e.g. export GOOGLE_API_KEY=\"AIza...\")." >&2
  exit 1
fi

echo "    Project: ${GOOGLE_CLOUD_PROJECT}"
echo "    Region:  ${GOOGLE_CLOUD_REGION}"

# -----------------------------------------------------------------------------
# 2. Authentication & Prerequisites Pre-Flight
# -----------------------------------------------------------------------------
echo "==> [2/7] Checking CLI tools and Google Cloud authentication..."

if ! command -v gcloud &>/dev/null; then
  echo "ERROR: gcloud CLI not found. Please install the Google Cloud SDK." >&2
  exit 1
fi

if ! command -v terraform &>/dev/null; then
  echo "ERROR: terraform CLI not found. Please install Terraform (>= 1.5.0)." >&2
  exit 1
fi

# Confirm Application Default Credentials (ADC) or active account
if ! gcloud auth print-access-token &>/dev/null; then
  echo "ERROR: No active Google Cloud authentication found." >&2
  echo "Please run: gcloud auth application-default login" >&2
  exit 1
fi

gcloud config set project "${GOOGLE_CLOUD_PROJECT}" --quiet >/dev/null 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TF_DIR="${REPO_ROOT}/infra/terraform"

# -----------------------------------------------------------------------------
# 3. Google Cloud API Enablement
# -----------------------------------------------------------------------------
echo "==> [3/8] Enabling required Google Cloud APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudtrace.googleapis.com \
  --quiet

# -----------------------------------------------------------------------------
# 4. Remote State GCS Bucket Bootstrap
# -----------------------------------------------------------------------------
STATE_BUCKET="${GOOGLE_CLOUD_PROJECT}-tfstate"
echo "==> [4/8] Ensuring remote Terraform state bucket gs://${STATE_BUCKET} exists..."

if ! gcloud storage buckets describe "gs://${STATE_BUCKET}" &>/dev/null && ! gsutil ls "gs://${STATE_BUCKET}" &>/dev/null; then
  echo "    Creating GCS bucket gs://${STATE_BUCKET} in ${GOOGLE_CLOUD_REGION}..."
  if command -v gcloud &>/dev/null && gcloud storage buckets create "gs://${STATE_BUCKET}" --location="${GOOGLE_CLOUD_REGION}" --uniform-bucket-level-access --quiet 2>/dev/null; then
    echo "    Bucket created successfully."
  else
    gsutil mb -p "${GOOGLE_CLOUD_PROJECT}" -l "${GOOGLE_CLOUD_REGION}" -b on "gs://${STATE_BUCKET}"
    echo "    Bucket created via gsutil."
  fi
else
  echo "    State bucket gs://${STATE_BUCKET} is ready."
fi

# -----------------------------------------------------------------------------
# 5. Terraform Initialization & Targeted Pre-Apply
# -----------------------------------------------------------------------------
echo "==> [5/8] Initializing Terraform backend and enabling foundation services..."

cd "${TF_DIR}"
terraform init \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="prefix=supportmaster/state" \
  -reconfigure \
  -input=false

# Pre-apply APIs and Artifact Registry so the container repository exists before pushing
echo "    Applying foundation: Google Cloud APIs and Artifact Registry..."
terraform apply \
  -target=google_project_service.apis \
  -target=google_artifact_registry_repository.app_repo \
  -var="project_id=${GOOGLE_CLOUD_PROJECT}" \
  -var="region=${GOOGLE_CLOUD_REGION}" \
  -var="image_tag=placeholder" \
  -auto-approve \
  -input=false

# -----------------------------------------------------------------------------
# 6. Build and Push Container Image via Cloud Build
# -----------------------------------------------------------------------------
echo "==> [6/8] Building and pushing container image via Cloud Build..."

cd "${REPO_ROOT}"
COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
IMAGE_TAG="${GOOGLE_CLOUD_REGION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/supportmaster/supportmaster:${COMMIT_SHA}"

echo "    Submitting build for tag: ${IMAGE_TAG}..."
gcloud builds submit --tag "${IMAGE_TAG}" --quiet "${REPO_ROOT}"

# -----------------------------------------------------------------------------
# 7. Full Terraform Apply & Imperative Secret Value Injection
# -----------------------------------------------------------------------------
echo "==> [7/8] Applying full Terraform infrastructure (Cloud Run Service + Worker Job)..."

cd "${TF_DIR}"
terraform apply \
  -var="project_id=${GOOGLE_CLOUD_PROJECT}" \
  -var="region=${GOOGLE_CLOUD_REGION}" \
  -var="image_tag=${IMAGE_TAG}" \
  -auto-approve \
  -input=false

# INLINE SECURITY ARCHITECTURE NOTE:
# The Secret Manager secret *container* is created by Terraform above
# (google_secret_manager_secret.api_key), but the secret *payload* is deliberately
# injected below via gcloud CLI. This prevents sensitive API keys from ever being
# written to disk, stored in terraform.tfvars, or recorded in plain text within
# the Terraform state file.
echo "    Injecting GOOGLE_API_KEY into Secret Manager..."
printf "%s" "${GOOGLE_API_KEY}" | gcloud secrets versions add "google-api-key" --data-file=- --quiet

# -----------------------------------------------------------------------------
# 8. Live Health Check Verification
# -----------------------------------------------------------------------------
echo "==> [8/8] Verifying live Cloud Run deployment health..."

SERVICE_URL="$(terraform output -raw service_url)"
SERVICE_ACCOUNT="$(terraform output -raw service_account_email)"

echo "    Testing endpoint: ${SERVICE_URL}/health/live"

MAX_RETRIES=12
RETRY_DELAY=5
HEALTH_OK=false

for i in $(seq 1 ${MAX_RETRIES}); do
  HTTP_STATUS="$(curl -s -o /dev/null -w "%{http_code}" "${SERVICE_URL}/health/live" || echo "000")"
  if [ "${HTTP_STATUS}" = "200" ]; then
    HEALTH_OK=true
    break
  fi
  echo "    Waiting for service warm-up (attempt ${i}/${MAX_RETRIES}, status=${HTTP_STATUS})..."
  sleep "${RETRY_DELAY}"
done

if [ "${HEALTH_OK}" = "false" ]; then
  echo "ERROR: /health/live failed to respond with HTTP 200 after ${MAX_RETRIES} attempts." >&2
  exit 1
fi

READY_STATUS="$(curl -s -o /dev/null -w "%{http_code}" "${SERVICE_URL}/health/ready" || echo "000")"
if [ "${READY_STATUS}" != "200" ]; then
  echo "WARNING: /health/ready returned HTTP ${READY_STATUS} (may require initial DB hydration)."
fi

echo ""
echo "========================================================================="
echo " SupportMaster Deployment Complete & Verified!"
echo "========================================================================="
echo " Live Hosted URL:       ${SERVICE_URL}"
echo " Operator Workspace:    ${SERVICE_URL}/workspace"
echo " Liveness Health Probe: ${SERVICE_URL}/health/live"
echo " Readiness Probe:       ${SERVICE_URL}/health/ready"
echo " Service Account:       ${SERVICE_ACCOUNT}"
echo " Container Image:       ${IMAGE_TAG}"
echo "========================================================================="
