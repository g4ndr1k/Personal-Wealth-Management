#!/bin/bash
# deploy_household.sh — Build PWA, rsync to NAS, rebuild Docker container
set -euo pipefail

NAS_HOST="g4ndr1k@192.168.1.44"
NAS_PORT="22"
NAS_KEY="$HOME/agentic-ai/secrets/nas_sync_key"
NAS_DIR="/volume1/docker/household-expense"
LOCAL_DIR="$HOME/agentic-ai/household-expense"

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
  "cd $NAS_DIR && echo 'REMOVED_NAS_SUDO_PASSWORD_ROTATED_2026_04_25' | sudo -S /volume1/@appstore/ContainerManager/usr/bin/docker compose up -d --build"

echo "=== Verifying health ==="
sleep 3
ssh -i "$NAS_KEY" -p "$NAS_PORT" "$NAS_HOST" \
  "curl -sf http://127.0.0.1:8088/api/household/health && echo '' || echo 'HEALTH CHECK FAILED'"

echo "=== Done ==="
