# SupportMaster — Pull Latest Code & Deploy Script (PowerShell)
# Automatically stashes uncommitted work, pulls latest main, pops stash, and runs deploy.

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " SupportMaster: Pull & Deploy Pipeline" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check if there are local uncommitted changes to stash
$status = git status --porcelain
$hasChanges = [bool]($status -and $status.Trim().Length -gt 0)

if ($hasChanges) {
    Write-Host "[1/4] Stashing local changes..." -ForegroundColor Yellow
    git stash push -m "pull-and-deploy-auto-stash-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
} else {
    Write-Host "[1/4] Working tree clean (no local changes to stash)." -ForegroundColor Green
}

# 2. Pull latest main
Write-Host "[2/4] Pulling latest changes from origin/main..." -ForegroundColor Cyan
git pull origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: git pull failed." -ForegroundColor Red
    if ($hasChanges) {
        Write-Host "Attempting to restore stashed changes..." -ForegroundColor Yellow
        git stash pop
    }
    exit 1
}

# 3. Restore stashed changes if any
if ($hasChanges) {
    Write-Host "[3/4] Restoring stashed local changes..." -ForegroundColor Yellow
    git stash pop
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Warning: git stash pop reported conflicts or warnings. Please inspect git status." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "[3/4] No stash pop needed." -ForegroundColor Green
}

# 4. Run the deploy script
Write-Host "[4/4] Launching SupportMaster deployment script..." -ForegroundColor Cyan
if (Test-Path ".\scripts\deploy.ps1") {
    & ".\scripts\deploy.ps1"
} elseif (Test-Path ".\scripts\deploy.sh") {
    bash ".\scripts\deploy.sh"
} else {
    Write-Host "Error: Deployment script not found in ./scripts/" -ForegroundColor Red
    exit 1
}
