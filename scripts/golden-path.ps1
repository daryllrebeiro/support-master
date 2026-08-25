# SupportMaster golden-path demo: one command, verified autonomous fix.
#
# Runs the authorization-aware engineering executor against demo-target/:
# grant check -> git preflight -> scoped patch -> real unittest run ->
# receipted commit on supportmaster/sup-golden. Offline-safe (no API keys).
#
# Usage:  .\scripts\golden-path.ps1

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    if (Test-Path ".demo-workspace") {
        Remove-Item -Recurse -Force ".demo-workspace"
    }
    & .\.venv\Scripts\python.exe -m supportmaster.execution.local_demo
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`nGOLDEN PATH FAILED" -ForegroundColor Red
        exit 1
    }
    Write-Host "`n--- Branch log (.demo-workspace) ---" -ForegroundColor Cyan
    git -C .demo-workspace log --oneline --all
    Write-Host "`nGOLDEN PATH OK: fix validated and committed on branch" -ForegroundColor Green
} finally {
    Pop-Location
}