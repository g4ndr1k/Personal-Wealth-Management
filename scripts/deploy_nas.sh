#!/usr/bin/env bash
# Deploy updated finance-api image to Synology NAS.
# Run this after any backend (finance/*.py) or frontend (pwa/) changes
# that need to be reflected on the NAS.
#
# Prerequisites:
#   - nas_sync_key in secrets/
#   - NAS_SUDO_PASSWORD env var or secrets/nas_sudo_password
#   - FINANCE_API_KEY env var or secrets/finance_api.key
#   - ssh on port 22 to the Synology NAS (default: 192.168.1.44)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAS_HOST="${NAS_HOST:-192.168.1.44}"
NAS_PORT="${NAS_PORT:-22}"
NAS_USER="${NAS_USER:-chfun}"
NAS_KEY="$REPO_ROOT/secrets/nas_sync_key"
DOCKER_NAS="/var/packages/ContainerManager/target/usr/bin/docker"
NAS_PASS_FILE="$REPO_ROOT/secrets/nas_sudo_password"
FINANCE_API_KEY_FILE="${FINANCE_API_KEY_FILE:-$REPO_ROOT/secrets/finance_api.key}"
LOCAL_TMP_IMAGE="/tmp/finance-api-amd64.tar.gz"
REMOTE_TMP_IMAGE="/volume1/homes/${NAS_USER}/finance-api-amd64.tar.gz"

NAS_PASS="${NAS_SUDO_PASSWORD:-}"
if [[ -z "$NAS_PASS" && -f "$NAS_PASS_FILE" ]]; then
  NAS_PASS="$(<"$NAS_PASS_FILE")"
fi
if [[ -z "$NAS_PASS" ]]; then
  echo "ERROR: set NAS_SUDO_PASSWORD or create $NAS_PASS_FILE" >&2
  exit 1
fi

NAS_FINANCE_API_KEY="${FINANCE_API_KEY:-}"
if [[ -z "$NAS_FINANCE_API_KEY" && -f "$FINANCE_API_KEY_FILE" ]]; then
  NAS_FINANCE_API_KEY="$(<"$FINANCE_API_KEY_FILE")"
fi
if [[ -z "$NAS_FINANCE_API_KEY" ]]; then
  echo "ERROR: set FINANCE_API_KEY or create $FINANCE_API_KEY_FILE" >&2
  exit 1
fi

NAS_PASS_Q="$(printf '%q' "$NAS_PASS")"
NAS_FINANCE_API_KEY_Q="$(printf '%q' "$NAS_FINANCE_API_KEY")"

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
docker save agentic-ai-finance-api:amd64 | gzip > "$LOCAL_TMP_IMAGE"
echo "    Size: $(du -sh "$LOCAL_TMP_IMAGE" | cut -f1)"

echo "==> Uploading to NAS..."
run_nas "mkdir -p $(dirname "$REMOTE_TMP_IMAGE") && cat > $REMOTE_TMP_IMAGE" < "$LOCAL_TMP_IMAGE"

echo "==> Loading image on NAS..."
run_nas "printf '%s\n' $NAS_PASS_Q | sudo -S $DOCKER_NAS load -i $REMOTE_TMP_IMAGE"

echo "==> Recreating container..."
run_nas "
  printf '%s\n' $NAS_PASS_Q | sudo -S $DOCKER_NAS stop finance-api-nas 2>/dev/null || true
  printf '%s\n' $NAS_PASS_Q | sudo -S $DOCKER_NAS rm finance-api-nas 2>/dev/null || true
  printf '%s\n' $NAS_PASS_Q | sudo -S $DOCKER_NAS run -d \
    --name finance-api-nas \
    --restart unless-stopped \
    -p 8090:8090 \
    -e SETTINGS_FILE=/app/config/settings.toml \
    -e FINANCE_READ_ONLY=true \
    -e FINANCE_SQLITE_DB=/app/data/finance_readonly.db \
    -e FINANCE_API_KEY=$NAS_FINANCE_API_KEY_Q \
    -e OLLAMA_FINANCE_HOST= \
    -v /volume1/finance:/app/data \
    -v /volume1/finance/config/settings.toml:/app/config/settings.toml:ro \
    agentic-ai-finance-api:amd64
"

echo "==> Verifying..."
sleep 4
STATUS=$(run_nas "printf '%s\n' $NAS_PASS_Q | sudo -S $DOCKER_NAS ps --filter name=finance-api-nas --format 'table {{.Names}}\t{{.Status}}'")
echo "$STATUS"

CACHE_HEADER=$(curl -s -I -X GET "http://$NAS_HOST:8090/sw.js" | grep -i cache-control || echo "MISSING")
echo "Cache-Control on sw.js: $CACHE_HEADER"

run_nas "rm -f $REMOTE_TMP_IMAGE"
rm -f "$LOCAL_TMP_IMAGE"
echo "==> Done."
