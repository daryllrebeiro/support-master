# =============================================================================
# SupportMaster: Terraform-Provisioned, Env-Driven Single-Command Deployment (PowerShell)
# =============================================================================
#
# Usage:
#   $env:GOOGLE_CLOUD_PROJECT = "my-project-id"
#   $env:GOOGLE_CLOUD_REGION  = "us-central1"
#   $env:GOOGLE_API_KEY       = "AIzaSy..."
#   .\scripts\deploy.ps1
#
# Or pass parameters:
#   .\scripts\deploy.ps1 -ProjectId "my-project-id" -Region "us-central1"
# =============================================================================

param(
    [string]$ProjectId = $env:GOOGLE_CLOUD_PROJECT,
    [string]$Region = $(if ($env:GOOGLE_CLOUD_REGION) { $env:GOOGLE_CLOUD_REGION } else { "us-central1" }),
    [string]$ApiKey = $env:GOOGLE_API_KEY
)

$ErrorActionPreference = "Stop"

Write-Host "==> [1/7] Validating required environment variables..." -ForegroundColor Cyan

if (-not $ProjectId) {
    throw "GOOGLE_CLOUD_PROJECT is not set. Export it via `$env:GOOGLE_CLOUD_PROJECT = 'my-project' or pass -ProjectId."
}
if (-not $ApiKey) {
    throw "GOOGLE_API_KEY is not set. Export your Gemini API key via `$env:GOOGLE_API_KEY = 'AIza...' or pass -ApiKey."
}

Write-Host "    Project: $ProjectId"
Write-Host "    Region:  $Region"

# -----------------------------------------------------------------------------
# 2. Authentication & Prerequisites Pre-Flight
# -----------------------------------------------------------------------------
Write-Host "`n==> [2/7] Checking CLI tools and Google Cloud authentication..." -ForegroundColor Cyan

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud CLI not found. Install it from https://cloud.google.com/sdk/docs/install"
}
if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
    throw "terraform CLI not found. Install it from https://developer.hashicorp.com/terraform/install"
}

gcloud config set project $ProjectId --quiet 2>$null | Out-Null
$account = gcloud config get-value account 2>$null
if (-not $account) {
    throw "No authenticated Google Cloud account found. Run 'gcloud auth application-default login' first."
}
Write-Host "    Authenticated as: $account"

$repoRoot = Split-Path -Parent $PSScriptRoot
$tfDir = Join-Path $repoRoot "infra\terraform"

# -----------------------------------------------------------------------------
# 3. Google Cloud API Enablement
# -----------------------------------------------------------------------------
Write-Host "`n==> [3/8] Enabling required Google Cloud APIs..." -ForegroundColor Cyan
gcloud services enable `
    run.googleapis.com `
    cloudbuild.googleapis.com `
    artifactregistry.googleapis.com `
    secretmanager.googleapis.com `
    cloudtrace.googleapis.com `
    --quiet
if ($LASTEXITCODE -ne 0) { throw "gcloud services enable failed." }

# -----------------------------------------------------------------------------
# 4. Ensure Artifact Registry Repository Exists
# -----------------------------------------------------------------------------
Write-Host "`n==> [4/8] Ensuring Artifact Registry repository exists..." -ForegroundColor Cyan
$repoExists = gcloud artifacts repositories describe supportmaster --location=$Region 2>$null
if (-not $repoExists) {
    Write-Host "    Creating Docker repository 'supportmaster' in ${Region}..."
    gcloud artifacts repositories create supportmaster `
        --repository-format=docker `
        --location=$Region `
        --description="SupportMaster container images" `
        --quiet | Out-Null
    Write-Host "    Artifact Registry repository created successfully."
} else {
    Write-Host "    Artifact Registry repository 'supportmaster' is ready."
}

# -----------------------------------------------------------------------------
# 5. Ensure Secret Manager Container & Inject Secret Payload Out-of-Band
# -----------------------------------------------------------------------------
# INLINE SECURITY ARCHITECTURE NOTE:
# The secret container is ensured and populated here via gcloud CLI directly from
# memory ($ApiKey). Terraform binds to it via data source, guaranteeing
# that raw API keys NEVER touch disk files, git history, or Terraform state.
Write-Host "`n==> [5/9] Ensuring Secret Manager container and injecting GOOGLE_API_KEY..." -ForegroundColor Cyan
$secretExists = gcloud secrets describe "google-api-key" --project=$ProjectId 2>$null
if (-not $secretExists) {
    Write-Host "    Creating Secret Manager container 'google-api-key'..."
    gcloud secrets create "google-api-key" --replication-policy="automatic" --project=$ProjectId --quiet | Out-Null
    Write-Host "    Secret container created successfully."
} else {
    Write-Host "    Secret container 'google-api-key' is ready."
}
Write-Host "    Injecting GOOGLE_API_KEY version..."
$ApiKey | gcloud secrets versions add "google-api-key" --data-file=- --project=$ProjectId --quiet | Out-Null

# -----------------------------------------------------------------------------
# 6. Remote State GCS Bucket Bootstrap
# -----------------------------------------------------------------------------
$stateBucket = "${ProjectId}-tfstate"
Write-Host "`n==> [6/9] Ensuring remote Terraform state bucket gs://${stateBucket} exists..." -ForegroundColor Cyan

$bucketExists = gcloud storage buckets describe "gs://${stateBucket}" 2>$null
if (-not $bucketExists) {
    Write-Host "    Creating GCS bucket gs://${stateBucket} in ${Region}..."
    gcloud storage buckets create "gs://${stateBucket}" --location=$Region --uniform-bucket-level-access --quiet | Out-Null
    Write-Host "    Bucket created successfully."
} else {
    Write-Host "    State bucket gs://${stateBucket} is ready."
}

# -----------------------------------------------------------------------------
# 7. Build and Push Container Image via Cloud Build
# -----------------------------------------------------------------------------
Write-Host "`n==> [7/9] Building and pushing container image via Cloud Build..." -ForegroundColor Cyan

$commitSha = git rev-parse --short HEAD 2>$null
if (-not $commitSha) { $commitSha = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
$imageTag = "${Region}-docker.pkg.dev/${ProjectId}/supportmaster/supportmaster:${commitSha}"

Write-Host "    Submitting build for tag: ${imageTag}..."
Push-Location $repoRoot
try {
    gcloud builds submit --tag $imageTag --quiet $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed." }
} finally {
    Pop-Location
}

# -----------------------------------------------------------------------------
# 8. Full Terraform Apply
# -----------------------------------------------------------------------------
Write-Host "`n==> [8/9] Applying Terraform infrastructure (Cloud Run Service + Worker Job)..." -ForegroundColor Cyan

Push-Location $tfDir
try {
    terraform init `
        -backend-config="bucket=${stateBucket}" `
        -backend-config="prefix=supportmaster/state" `
        -reconfigure `
        -input=false
    if ($LASTEXITCODE -ne 0) { throw "terraform init failed." }

    terraform apply `
        -var="project_id=$ProjectId" `
        -var="region=$Region" `
        -var="image_tag=$imageTag" `
        -auto-approve `
        -input=false
    if ($LASTEXITCODE -ne 0) { throw "Full terraform apply failed." }

    $serviceUrl = (terraform output -raw service_url).Trim()
    $serviceAccount = (terraform output -raw service_account_email).Trim()
} finally {
    Pop-Location
}

# -----------------------------------------------------------------------------
# 9. Live Health Check Verification
# -----------------------------------------------------------------------------
Write-Host "`n==> [9/9] Verifying live Cloud Run deployment health..." -ForegroundColor Cyan
Write-Host "    Testing endpoint: ${serviceUrl}/health/live"

$healthOk = $false
for ($i = 1; $i -le 12; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri "${serviceUrl}/health/live" -TimeoutSec 5 -ErrorAction SilentlyContinue
        if ($resp -and $resp.status -eq "HEALTHY") {
            $healthOk = $true
            break
        }
    } catch {
        # continue retrying
    }
    Write-Host "    Waiting for service warm-up (attempt $i/12)..."
    Start-Sleep -Seconds 5
}

if (-not $healthOk) {
    throw "ERROR: /health/live failed to respond healthy after 12 attempts."
}

Write-Host ""
Write-Host "=========================================================================" -ForegroundColor Green
Write-Host " SupportMaster Deployment Complete & Verified!"
Write-Host "=========================================================================" -ForegroundColor Green
Write-Host " Live Hosted URL:       $serviceUrl"
Write-Host " Operator Workspace:    $serviceUrl/workspace"
Write-Host " Liveness Health Probe: $serviceUrl/health/live"
Write-Host " Readiness Probe:       $serviceUrl/health/ready"
Write-Host " Service Account:       $serviceAccount"
Write-Host " Container Image:       $imageTag"
Write-Host "=========================================================================" -ForegroundColor Green
