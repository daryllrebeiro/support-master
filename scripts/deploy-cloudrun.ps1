# SupportMaster one-command Google Cloud Run deployment.
#
# Prerequisites:
#   1. gcloud CLI installed and authenticated (gcloud auth login)
#   2. A GCP project with billing enabled
#   3. GOOGLE_API_KEY set in your environment (Gemini API key from AI Studio)
#
# Usage:
#   .\scripts\deploy-cloudrun.ps1 -ProjectId my-project -Region us-central1

param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "us-central1",
    [string]$ServiceName = "supportmaster",
    [string]$SecretName = "google-api-key"
)

$ErrorActionPreference = "Stop"

Write-Host "== SupportMaster Cloud Run deployment ==" -ForegroundColor Cyan
Write-Host "Project: $ProjectId | Region: $Region | Service: $ServiceName"

# 0. Verify gcloud availability and active account.
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud CLI not found. Install it from https://cloud.google.com/sdk/docs/install"
}
gcloud config set project $ProjectId | Out-Null
$account = gcloud config get-value account 2>$null
if (-not $account) { throw "No authenticated gcloud account. Run 'gcloud auth login' first." }
Write-Host "Authenticated as: $account"

# 1. Enable required Google Cloud services.
Write-Host "`n[1/5] Enabling required APIs..." -ForegroundColor Yellow
gcloud services enable run.googleapis.com cloudbuild.googleapis.com `
    secretmanager.googleapis.com artifactregistry.googleapis.com --quiet

# 2. Store the Gemini API key in Secret Manager (never baked into the image).
Write-Host "`n[2/5] Storing GOOGLE_API_KEY in Secret Manager..." -ForegroundColor Yellow
if (-not $env:GOOGLE_API_KEY) {
    throw "GOOGLE_API_KEY environment variable is not set. Export your Gemini API key first."
}
$existing = gcloud secrets describe $SecretName --format="value(name)" 2>$null
if ($existing) {
    "GOOGLE_API_KEY" | gcloud secrets versions add $SecretName --data-file=- --quiet | Out-Null
} else {
    "GOOGLE_API_KEY" | gcloud secrets create $SecretName --data-file=- --replication-policy=automatic --quiet | Out-Null
}
Write-Host "Secret '$SecretName' ready."

# 3. Build the container image with Cloud Build.
Write-Host "`n[3/5] Building image with Cloud Build..." -ForegroundColor Yellow
$image = "$Region-docker.pkg.dev/$ProjectId/cloud-run-source-deploy/${ServiceName}:latest"
gcloud builds submit --tag $image --quiet
if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed." }

# 4. Grant the Cloud Run runtime service account access to the secret.
Write-Host "`n[4/5] Granting the runtime service account secret access..." -ForegroundColor Yellow
$projectNumber = gcloud projects describe $ProjectId --format="value(projectNumber)"
$runtimeSa = "service-$projectNumber@serverless-robot-prod.iam.gserviceaccount.com"
gcloud secrets add-iam-policy-binding $SecretName `
    --member="serviceAccount:$runtimeSa" --role="roles/secretmanager.secretAccessor" --quiet | Out-Null

# 5. Deploy to Cloud Run.
Write-Host "`n[5/5] Deploying to Cloud Run..." -ForegroundColor Yellow
gcloud run deploy $ServiceName `
    --image $image `
    --region $Region `
    --allow-unauthenticated `
    --set-secrets "GOOGLE_API_KEY=${SecretName}:latest" `
    --set-env-vars "SUPPORTMASTER_AUTH_MODE=OPTIONAL,SUPPORTMASTER_MODEL=gemini-3.5-flash,PORT=8001" `
    --port 8001 `
    --cpu 1 --memory 1Gi `
    --min-instances 0 --max-instances 2 `
    --quiet
if ($LASTEXITCODE -ne 0) { throw "Cloud Run deployment failed." }

$url = gcloud run services describe $ServiceName --region $Region --format="value(status.url)"
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Deployment complete!"
Write-Host " Hosted URL (use this on the Devpost submission form):"
Write-Host "   $url"
Write-Host " Operator workspace: $url/workspace"
Write-Host " Health probes:      $url/health/live | $url/health/ready"
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Video proof tip: show 'gcloud run services list' or the Cloud Console"
Write-Host "service page for ~5 seconds to prove the backend runs on Google Cloud."