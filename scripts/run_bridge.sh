#!/bin/bash
# run_bridge.sh — Launch AgenticAI Bridge via the .app bundle for stable TCC identity.
#
# The .app bundle at /Applications/AgenticAI.app provides a stable path
# for macOS Full Disk Access (FDA). The Python interpreter is resolved
# dynamically inside the bundle, so Homebrew upgrades don't break TCC.
#
# Usage:
#   ./scripts/run_bridge.sh          # normal launch
#   ./scripts/run_bridge.sh --check   # only run TCC pre-flight check
#
set -euo pipefail

APP_BUNDLE="/Applications/AgenticAI.app"
LAUNCHER="${APP_BUNDLE}/Contents/MacOS/launch_bridge"
FALLBACK_PYTHON="$(command -v python3.14 || command -v python3.13 || command -v python3)"
PROJECT_ROOT="/Users/g4ndr1k/agentic-ai"

# ── Mode: check only ───────────────────────────────────────────────────
if [ "${1:-}" = "--check" ]; then
    echo "Running TCC pre-flight check..."
    exec "$FALLBACK_PYTHON" -m bridge.tcc_check
fi

# ── Prefer .app bundle if installed ─────────────────────────────────────
if [ -x "$LAUNCHER" ]; then
    echo "Launching via .app bundle: $APP_BUNDLE"
    exec "$LAUNCHER"
fi

# ── Fallback: direct Python (TCC identity = Python binary path) ────────
echo "WARNING: AgenticAI.app not found at $APP_BUNDLE"
echo "         Running with direct Python — TCC identity will break on brew upgrade."
echo "         Run scripts/setup-app.sh to install the .app bundle."
echo ""

export HOME=/Users/g4ndr1k
export PYTHONPATH=/Users/g4ndr1k/agentic-ai
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
export MallocStackLogging=0

cd "$PROJECT_ROOT"
exec "$FALLBACK_PYTHON" -m bridge.server
