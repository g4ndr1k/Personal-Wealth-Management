#!/bin/bash
# python_clean_install.sh
# Wipes existing Homebrew Python and installs a clean one.
# Run in Terminal: bash ~/agentic-ai/scripts/python_clean_install.sh

set -e
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Python Clean Install Script            ║"
echo "║   Removes old Homebrew python versions   ║"
echo "║   Installs: Homebrew python@3.14         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
read -p "Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── STEP 1: Unload bridge LaunchAgent (so python isn't in use) ──────────────
echo ""
echo "▶ Step 1 — Unloading bridge LaunchAgent..."
launchctl unload ~/Library/LaunchAgents/com.agentic.bridge.plist 2>/dev/null && echo "  ✅ Bridge unloaded" || echo "  — Bridge was not loaded (OK)"

# ── STEP 2: Remove Miniconda ─────────────────────────────────────────────────
echo ""
echo "▶ Step 2 — Removing Miniconda..."
brew uninstall --cask miniconda --force 2>/dev/null && echo "  ✅ Miniconda cask removed" || echo "  — brew uninstall failed, removing manually"
sudo rm -rf /opt/homebrew/Caskroom/miniconda 2>/dev/null && echo "  ✅ Miniconda files removed" || echo "  — already gone"

# ── STEP 3: Remove Homebrew python@3.14 ──────────────────────────────────────
echo ""
echo "▶ Step 3 — Removing Homebrew python@3.14..."
brew uninstall python@3.14 --force 2>/dev/null && echo "  ✅ python@3.14 removed" || echo "  — already gone"

# ── STEP 4: Remove python.org remnants ───────────────────────────────────────
echo ""
echo "▶ Step 4 — Removing python.org remnants..."
sudo rm -rf /Library/Frameworks/Python.framework 2>/dev/null && echo "  ✅ Python.framework removed" || echo "  — already gone"
sudo rm -rf /Library/Frameworks/PythonT.framework 2>/dev/null && echo "  ✅ PythonT.framework removed" || echo "  — already gone"
sudo rm -f /usr/local/bin/python3* /usr/local/bin/python3 /usr/local/bin/pydoc3* /usr/local/bin/idle3* /usr/local/bin/pip3* 2>/dev/null && echo "  ✅ /usr/local/bin symlinks removed" || true
sudo pkgutil --forget org.python.Python.PythonFramework-3.14 2>/dev/null && echo "  ✅ Forgot receipt: PythonFramework-3.14" || echo "  — receipt already gone"
sudo pkgutil --forget org.python.Python.PythonDocumentation-3.14 2>/dev/null && echo "  ✅ Forgot receipt: PythonDocumentation-3.14" || echo "  — receipt already gone"
sudo pkgutil --forget org.python.Python.PythonApplications-3.14 2>/dev/null && echo "  ✅ Forgot receipt: PythonApplications-3.14" || echo "  — receipt already gone"
sudo pkgutil --forget org.python.Python.PythonUnixTools-3.14 2>/dev/null && echo "  ✅ Forgot receipt: PythonUnixTools-3.14" || echo "  — receipt already gone"
sudo rm -rf "/Applications/Python 3.14" 2>/dev/null && echo "  ✅ Removed /Applications/Python 3.14" || echo "  — already gone"

# ── STEP 5: Clean Homebrew cache ─────────────────────────────────────────────
echo ""
echo "▶ Step 5 — Cleaning Homebrew cache..."
brew cleanup --prune=all 2>/dev/null
echo "  ✅ Done"

# ── STEP 6: Install python@3.14 ──────────────────────────────────────────────
echo ""
echo "▶ Step 6 — Installing Homebrew python@3.14..."
brew install python@3.14
echo "  ✅ python@3.14 installed"

# ── STEP 7: Verify ───────────────────────────────────────────────────────────
echo ""
echo "▶ Step 7 — Verifying..."
PYTHON_BIN="$(brew --prefix python@3.14)/bin/python3"
echo "  Binary:     $PYTHON_BIN"
echo "  Version:    $($PYTHON_BIN --version)"
echo "  Executable: $(realpath $PYTHON_BIN)"
$PYTHON_BIN -c "import tomllib, sqlite3; print('  tomllib:    ✅')"

# ── STEP 8: Update bridge LaunchAgent to python@3.14 ─────────────────────
echo ""
echo "▶ Step 8 — Updating bridge LaunchAgent to python@3.14..."
PLIST=~/Library/LaunchAgents/com.agentic.bridge.plist
NEW_PYTHON="$(brew --prefix python@3.14)/bin/python3"

# Replace the python path in the plist
/usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 $NEW_PYTHON" "$PLIST"
echo "  ✅ LaunchAgent updated to: $NEW_PYTHON"
echo "  Verifying plist:"
/usr/libexec/PlistBuddy -c "Print :ProgramArguments" "$PLIST"

# ── STEP 9: Reload bridge LaunchAgent ────────────────────────────────────────
echo ""
echo "▶ Step 9 — Reloading bridge LaunchAgent..."
launchctl load ~/Library/LaunchAgents/com.agentic.bridge.plist
echo "  ✅ Bridge reloaded"
sleep 2
launchctl list | grep agentic.bridge && echo "  ✅ Bridge is running" || echo "  ⚠️  Check logs: cat ~/agentic-ai/logs/bridge-launchd-err.log"

# ── DONE ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅ DONE                                                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Python is now: $(brew --prefix python@3.14)/bin/python3"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "NEXT: Add Python to Full Disk Access"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "The FDA picker won't select versioned binaries (e.g. python3.14)."
echo "Use drag-and-drop instead:"
echo ""
echo "  1. Open a NEW Finder window"
echo "  2. Press Cmd+Shift+G and go to:"
echo "     $(brew --prefix python@3.14)/bin/"
echo "  3. Keep System Settings > Privacy & Security > Full Disk Access open"
echo "  4. Drag 'python3' from Finder into the FDA list"
echo "     (NOT python3.14 — use the plain 'python3' file)"
echo ""
echo "After adding it, toggle it ON."
echo ""
echo "To verify the bridge is reading mail:"
echo "  TOKEN=\$(cat ~/agentic-ai/secrets/bridge.token)"
echo "  curl -s -H \"Authorization: Bearer \$TOKEN\" http://127.0.0.1:9100/health | python3 -m json.tool"
echo ""
