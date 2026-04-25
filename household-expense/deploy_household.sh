#!/bin/bash
# deploy_household.sh — Build PWA, rsync to NAS, rebuild Docker container
set -euo pipefail

NAS_USER="${NAS_USER:-chfun}"
NAS_ADDR="${NAS_ADDR:-192.168.1.44}"
NAS_HOST="${NAS_USER}@${NAS_ADDR}"
NAS_PORT="${NAS_PORT:-22}"
NAS_KEY="$HOME/agentic-ai/secrets/nas_sync_key"
NAS_PASS_FILE="$HOME/agentic-ai/secrets/nas_sudo_password"
NAS_DIR="/volume1/docker/household-expense"
LOCAL_DIR="$HOME/agentic-ai/household-expense"

NAS_PASS="${NAS_SUDO_PASSWORD:-}"
if [[ -z "$NAS_PASS" && -f "$NAS_PASS_FILE" ]]; then
  NAS_PASS="$(<"$NAS_PASS_FILE")"
fi
if [[ -z "$NAS_PASS" ]]; then
  echo "ERROR: set NAS_SUDO_PASSWORD or create $NAS_PASS_FILE" >&2
  exit 1
fi
NAS_PASS_Q="$(printf '%q' "$NAS_PASS")"

echo "=== Building PWA ==="
cd "$LOCAL_DIR/pwa"
npm run build
# Copy icons to dist
cp public/icon-*.png ../dist/

echo "=== Syncing to NAS ==="
# rsync everything except node_modules, .git, and data
rsync -avz --delete \
  --exclude node_modules \
  --exclude .git \
  --exclude data/ \
  -e "ssh -i $NAS_KEY -p $NAS_PORT" \
  "$LOCAL_DIR/" "$NAS_HOST:$NAS_DIR/"

echo "=== Rebuilding Docker on NAS ==="
ssh -i "$NAS_KEY" -p "$NAS_PORT" "$NAS_HOST" \
  "cd $NAS_DIR && printf '%s\n' $NAS_PASS_Q | sudo -S /volume1/@appstore/ContainerManager/usr/bin/docker compose up -d --build"

echo "=== Verifying health ==="
sleep 3
ssh -i "$NAS_KEY" -p "$NAS_PORT" "$NAS_HOST" \
  "curl -sf http://127.0.0.1:8088/api/household/health && echo '' || echo 'HEALTH CHECK FAILED'"

echo "=== Done ==="
