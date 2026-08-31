#!/usr/bin/env bash
# SupportMaster — Pull Latest Code & Deploy Script (Bash)
# Automatically stashes uncommitted work, pulls latest main, pops stash, and runs deploy.

set -euo pipefail

echo "=========================================================="
echo " SupportMaster: Pull & Deploy Pipeline"
echo "=========================================================="

# 1. Check if there are local uncommitted changes
HAS_CHANGES=0
if [[ -n $(git status --porcelain) ]]; then
    echo "[1/4] Stashing local changes..."
    git stash push -m "pull-and-deploy-auto-stash-$(date +%Y%m%d%H%M%S)"
    HAS_CHANGES=1
else
    echo "[1/4] Working tree clean (no local changes to stash)."
fi

# 2. Pull latest main
echo "[2/4] Pulling latest changes from origin/main..."
if ! git pull origin main; then
    echo "Error: git pull failed." >&2
    if [[ $HAS_CHANGES -eq 1 ]]; then
        echo "Attempting to restore stashed changes..." >&2
        git stash pop || true
    fi
    exit 1
fi

# 3. Restore stash if needed
if [[ $HAS_CHANGES -eq 1 ]]; then
    echo "[3/4] Restoring stashed local changes..."
    git stash pop || echo "Warning: Check git status for stash pop results."
else
    echo "[3/4] No stash pop needed."
fi

# 4. Run deploy script
echo "[4/4] Launching SupportMaster deployment script..."
if [[ -f "./scripts/deploy.sh" ]]; then
    chmod +x "./scripts/deploy.sh"
    ./scripts/deploy.sh
else
    echo "Error: ./scripts/deploy.sh not found." >&2
    exit 1
fi
