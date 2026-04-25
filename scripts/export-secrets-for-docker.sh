#!/bin/bash
# export-secrets-for-docker.sh — Regenerate secret files from macOS Keychain
# for Docker container mounts.
#
# Keychain is the source of truth. This script exports secrets into the
# secrets/ directory so that docker-compose volume mounts work.
#
# Run this after `python3 -m bridge.secret_manager init` or whenever
# secrets change:
#   ./scripts/export-secrets-for-docker.sh
#
set -euo pipefail

SECRETS_DIR="$(cd "$(dirname "$0")/.." && pwd)/secrets"
PYTHON="$(command -v python3.14 || command -v python3.13 || command -v python3)"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Export for use inside Python via os.environ
export SECRETS_DIR PROJECT_ROOT

echo "Exporting secrets from Keychain → $SECRETS_DIR/"

# Helper: run a Python snippet with the project root on sys.path.
# Paths are passed via environment variables (SECRETS_DIR, PROJECT_ROOT).
run_py() {
  "$PYTHON" -c "
import sys, os
sys.path.insert(0, os.environ['PROJECT_ROOT'])
$(cat)
" 2>/dev/null
}

# ── Google service account JSON ─────────────────────────────────────────
echo -n "  google_service_account.json ... "
run_py <<'PYEOF'
import os
from bridge.secret_manager import get_from_keychain, _load_settings, _keychain_service
settings = _load_settings()
service = _keychain_service(settings)
raw = get_from_keychain(service, 'google_service_account_json')
if raw:
    with open(os.path.join(os.environ['SECRETS_DIR'], 'google_service_account.json'), 'w') as f:
        f.write(raw)
    print('✓')
else:
    print('✗ (not in Keychain)')
PYEOF

# ── Google OAuth credentials JSON ───────────────────────────────────────
echo -n "  google_credentials.json ... "
run_py <<'PYEOF'
import os
from bridge.secret_manager import get_from_keychain, _load_settings, _keychain_service
settings = _load_settings()
service = _keychain_service(settings)
raw = get_from_keychain(service, 'google_credentials_json')
if raw:
    with open(os.path.join(os.environ['SECRETS_DIR'], 'google_credentials.json'), 'w') as f:
        f.write(raw)
    print('✓')
else:
    print('✗ (not in Keychain)')
PYEOF

# ── Google OAuth token JSON ────────────────────────────────────────────
echo -n "  google_token.json ... "
run_py <<'PYEOF'
import os
from bridge.secret_manager import get_from_keychain, _load_settings, _keychain_service
settings = _load_settings()
service = _keychain_service(settings)
raw = get_from_keychain(service, 'google_token_json')
if raw:
    with open(os.path.join(os.environ['SECRETS_DIR'], 'google_token.json'), 'w') as f:
        f.write(raw)
    print('✓')
else:
    print('✗ (not in Keychain — will be created on first OAuth flow)')
PYEOF

# ── Bridge token ────────────────────────────────────────────────────────
echo -n "  bridge.token ... "
run_py <<'PYEOF'
import os
from bridge.secret_manager import get_from_keychain, _load_settings, _keychain_service
settings = _load_settings()
service = _keychain_service(settings)
raw = get_from_keychain(service, 'bridge_token')
if raw:
    target = os.path.join(os.environ['SECRETS_DIR'], 'bridge.token')
    # Guard: if a broken prior install left a directory, remove it
    if os.path.isdir(target):
        import shutil
        print(f'WARNING: removing stale directory {target}')
        shutil.rmtree(target)
    with open(target, 'w') as f:
        f.write(raw)
    os.chmod(target, 0o600)
    print('✓')
else:
    print('✗ (not in Keychain)')
PYEOF

# ── Bank passwords (as banks.toml for Docker) ──────────────────────────
echo -n "  banks.toml ... "
run_py <<'PYEOF'
import os
from bridge.secret_manager import get_from_keychain, _load_settings, _keychain_service
settings = _load_settings()
service = _keychain_service(settings)
raw = get_from_keychain(service, 'banks_toml')
if raw:
    with open(os.path.join(os.environ['SECRETS_DIR'], 'banks.toml'), 'w') as f:
        f.write(raw)
    print('✓')
else:
    print('✗ (not in Keychain)')
PYEOF

echo ""
echo "Done. Files in $SECRETS_DIR/:"
ls -la "$SECRETS_DIR/" | grep -v DS_Store | grep -v README | grep -v total | grep -v '^d'
