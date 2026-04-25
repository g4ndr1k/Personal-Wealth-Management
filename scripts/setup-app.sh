#!/bin/bash
# setup-app.sh — One-time setup for AgenticAI.app bundle and TCC identity.
#
# This script:
#   1. Installs AgenticAI.app to /Applications (stable TCC identity)
#   2. Registers the LaunchAgent
#   3. Prints instructions for granting Full Disk Access
#
# Run once after initial setup or when migrating to the .app bundle:
#   ./scripts/setup-app.sh
#
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_ROOT="/Users/g4ndr1k/agentic-ai"
BUNDLE_SRC="${PROJECT_ROOT}/app-bundle/AgenticAI.app"
BUNDLE_DST="/Applications/AgenticAI.app"
PLIST_SRC="${PROJECT_ROOT}/launchd/com.agentic.bridge.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.agentic.bridge.plist"
LAUNCHER="${BUNDLE_DST}/Contents/MacOS/launch_bridge"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  AgenticAI — .app Bundle Setup & TCC Identity"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ── 1. Install .app bundle ──────────────────────────────────────────────
if [ -d "$BUNDLE_DST" ]; then
    echo -e "${YELLOW}→ Replacing existing ${BUNDLE_DST}${NC}"
    rm -rf "$BUNDLE_DST"
fi

echo -e "${GREEN}→ Installing ${BUNDLE_DST}${NC}"
cp -R "$BUNDLE_SRC" "$BUNDLE_DST"
chmod +x "$LAUNCHER"

# ── 2. Verify bundle ────────────────────────────────────────────────────
echo -e "${GREEN}→ Verifying bundle${NC}"
if [ -x "$LAUNCHER" ]; then
    echo "  ✓ launch_bridge executable"
else
    echo -e "${RED}  ✗ launch_bridge NOT executable!${NC}"
    exit 1
fi

# Check Info.plist
if plutil -lint "${BUNDLE_DST}/Contents/Info.plist" &>/dev/null; then
    echo "  ✓ Info.plist valid"
else
    echo -e "${RED}  ✗ Info.plist invalid!${NC}"
    exit 1
fi

# ── 3. Register LaunchAgent ─────────────────────────────────────────────
echo -e "${GREEN}→ Registering LaunchAgent${NC}"
# Unload old version if running
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Copy updated plist
mkdir -p "$(dirname "$PLIST_DST")"
cp "$PLIST_SRC" "$PLIST_DST" 2>/dev/null || true
# If no separate plist file, the one we just wrote is already there

launchctl load "$PLIST_DST"
echo "  ✓ com.agentic.bridge registered"

# ── 4. TCC / Full Disk Access ────────────────────────────────────────────
echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  ACTION REQUIRED: Grant Full Disk Access${NC}"
echo -e "${YELLOW}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo "  The bridge needs Full Disk Access to read Mail database."
echo ""
echo "  1. Open: System Settings → Privacy & Security → Full Disk Access"
echo "  2. Click '+' and add:"
echo ""
echo -e "     ${GREEN}/Applications/AgenticAI.app${NC}"
echo ""
echo "  3. Restart the bridge:"
echo "     launchctl kickstart -k gui/\$(id -u)/com.agentic.bridge"
echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════════════${NC}"

# ── 5. Verify TCC (will fail until FDA is granted manually) ─────────────
echo ""
echo -e "→ Checking current TCC status..."
sleep 1
PY="$(command -v python3.14 || command -v python3.13 || command -v python3)"
if [ -x "$PY" ] && "$PY" -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from bridge.tcc_check import check_fda
if check_fda():
    print('  ✓ Full Disk Access: GRANTED')
else:
    print('  ✗ Full Disk Access: NOT YET GRANTED (expected — follow steps above)')
" 2>/dev/null; then
    :
else
    echo "  (TCC check skipped — Python not found at expected path)"
fi

echo ""
echo "Setup complete. After granting FDA, the bridge will start automatically."
echo "Verify:  python3 -m bridge.tcc_check"
echo "Logs:   tail -f ~/agentic-ai/logs/bridge.log"
