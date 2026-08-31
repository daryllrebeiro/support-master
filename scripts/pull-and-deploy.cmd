@echo off
rem SupportMaster — Pull Latest Code & Deploy Script (Windows CMD)
rem Automatically stashes uncommitted work, pulls latest main, pops stash, and runs deploy.

setlocal enabledelayedexpansion

echo ==========================================================
echo  SupportMaster: Pull ^& Deploy Pipeline
echo ==========================================================

rem 1. Check for local changes and stash
git status --porcelain > "%TEMP%\sm_git_status.tmp" 2>&1
for %%A in ("%TEMP%\sm_git_status.tmp") do set SIZE=%%~zA
del "%TEMP%\sm_git_status.tmp" >nul 2>&1

set STASHED=0
if %SIZE% GTR 0 (
    echo [1/4] Stashing local changes...
    git stash push -m "pull-and-deploy-auto-stash"
    set STASHED=1
) else (
    echo [1/4] Working tree clean (no local changes to stash).
)

rem 2. Pull from origin main
echo [2/4] Pulling latest changes from origin/main...
git pull origin main
if %ERRORLEVEL% NEQ 0 (
    echo Error: git pull failed.
    if !STASHED! EQU 1 (
        echo Restoring stashed changes...
        git stash pop
    )
    exit /b %ERRORLEVEL%
)

rem 3. Restore stash if needed
if !STASHED! EQU 1 (
    echo [3/4] Restoring stashed local changes...
    git stash pop
) else (
    echo [3/4] No stash pop needed.
)

rem 4. Run deploy script
echo [4/4] Launching SupportMaster deployment script...
if exist ".\scripts\deploy.ps1" (
    powershell -ExecutionPolicy Bypass -File ".\scripts\deploy.ps1"
) else (
    if exist ".\scripts\deploy.sh" (
        bash ".\scripts\deploy.sh"
    ) else (
        echo Error: Deployment script not found in ./scripts/
        exit /b 1
    )
)
