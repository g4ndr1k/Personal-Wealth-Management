#!/usr/bin/env bash
# Deploy updated finance-api image to Synology NAS.
# Run this after any backend (finance/*.py) or frontend (pwa/) changes
# that need to be reflected on the NAS (iPhone/Tailscale access).
#
# Prerequisites: nas_sync_key in secrets/, ssh on port 68 to 192.168.1.44

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAS_HOST="192.168.1.44"
NAS_PORT=68
NAS_USER="g4ndr1k"
NAS_KEY="$REPO_ROOT/secrets/nas_sync_key"
DOCKER_NAS="/var/packages/ContainerManager/target/usr/bin/docker"
NAS_PASS="REMOVED_NAS_SUDO_PASSWORD_ROTATED_2026_04_25"
TMP_IMAGE="/tmp/finance-api-amd64.tar.gz"

run_nas() {
  ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
      -p "$NAS_PORT" -i "$NAS_KEY" \
      "${NAS_USER}@${NAS_HOST}" "$@"
}

echo "==> Building amd64 image (no-cache)..."
docker buildx build --no-cache --platform linux/amd64 \
  -t agentic-ai-finance-api:amd64 \
  -f "$REPO_ROOT/finance/Dockerfile" \
  --load "$REPO_ROOT/"

echo "==> Saving image..."
docker save agentic-ai-finance-api:amd64 | gzip > "$TMP_IMAGE"
echo "    Size: $(du -sh "$TMP_IMAGE" | cut -f1)"

echo "==> Uploading to NAS..."
run_nas "cat > $TMP_IMAGE" < "$TMP_IMAGE"

echo "==> Loading image on NAS..."
run_nas "echo '$NAS_PASS' | sudo -S $DOCKER_NAS load -i $TMP_IMAGE"

echo "==> Recreating container..."
run_nas "
  echo '$NAS_PASS' | sudo -S $DOCKER_NAS stop finance-api-nas 2>/dev/null || true
  echo '$NAS_PASS' | sudo -S $DOCKER_NAS rm finance-api-nas 2>/dev/null || true
  echo '$NAS_PASS' | sudo -S $DOCKER_NAS run -d \
    --name finance-api-nas \
    --restart unless-stopped \
    -p 8090:8090 \
    -e SETTINGS_FILE=/app/config/settings.toml \
    -e FINANCE_READ_ONLY=true \
    -e FINANCE_SQLITE_DB=/app/data/finance_readonly.db \
    -e 'FINANCE_API_KEY=REMOVED_FINANCE_API_KEY_ROTATED_2026_04_25' \
    -e OLLAMA_FINANCE_HOST= \
    -v /volume1/finance:/app/data \
    -v /volume1/finance/config/settings.toml:/app/config/settings.toml:ro \
    agentic-ai-finance-api:amd64
"

echo "==> Verifying..."
sleep 4
STATUS=$(run_nas "echo '$NAS_PASS' | sudo -S $DOCKER_NAS ps --filter name=finance-api-nas --format 'table {{.Names}}\t{{.Status}}'")
echo "$STATUS"

CACHE_HEADER=$(curl -s -I -X GET "http://$NAS_HOST:8090/sw.js" | grep -i cache-control || echo "MISSING")
echo "Cache-Control on sw.js: $CACHE_HEADER"

rm -f "$TMP_IMAGE"
echo "==> Done."
