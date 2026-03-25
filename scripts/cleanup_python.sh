#!/bin/bash
# cleanup_python.sh
# Removes the python.org Python 3.14 PKG installation.
# Keeps: Homebrew python@3.14 (used by bridge), Miniconda (for conda envs)
#
# Run this script in Terminal with:  bash ~/agentic-ai/scripts/cleanup_python.sh

set -e

echo ""
echo "=== Python Cleanup Script ==="
echo "This will remove the python.org Python 3.14 PKG installer files."
echo "Homebrew python@3.14 and Miniconda will NOT be touched."
echo ""
read -p "Continue? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

echo ""
echo "--- Removing /usr/local/bin python* symlinks (root-owned) ---"
sudo rm -f \
  /usr/local/bin/python3 \
  /usr/local/bin/python3-config \
  /usr/local/bin/python3-intel64 \
  /usr/local/bin/python3.14 \
  /usr/local/bin/python3.14-config \
  /usr/local/bin/python3.14-intel64 \
  /usr/local/bin/python3.14t \
  /usr/local/bin/python3.14t-config \
  /usr/local/bin/python3.14t-intel64 \
  /usr/local/bin/python3t \
  /usr/local/bin/python3t-config \
  /usr/local/bin/python3t-intel64
echo "✅ Done"

echo ""
echo "--- Removing /Library/Frameworks/Python.framework/Versions/3.14 ---"
sudo rm -rf /Library/Frameworks/Python.framework/Versions/3.14
sudo rm -rf /Library/Frameworks/PythonT.framework 2>/dev/null || true
# Clean up parent if empty
remaining=$(ls /Library/Frameworks/Python.framework/Versions/ 2>/dev/null | grep -v Current)
if [ -z "$remaining" ]; then
  sudo rm -rf /Library/Frameworks/Python.framework
  echo "✅ Removed entire Python.framework (was the only version)"
else
  echo "✅ Removed 3.14 (other versions still present: $remaining)"
fi

echo ""
echo "--- Forgetting PKG installer receipts ---"
sudo pkgutil --forget org.python.Python.PythonFramework-3.14    2>/dev/null && echo "✅ Forgot PythonFramework-3.14"    || echo "— Receipt not found"
sudo pkgutil --forget org.python.Python.PythonDocumentation-3.14 2>/dev/null && echo "✅ Forgot PythonDocumentation-3.14" || echo "— Receipt not found"
sudo pkgutil --forget org.python.Python.PythonApplications-3.14  2>/dev/null && echo "✅ Forgot PythonApplications-3.14"  || echo "— Receipt not found"
sudo pkgutil --forget org.python.Python.PythonUnixTools-3.14     2>/dev/null && echo "✅ Forgot PythonUnixTools-3.14"     || echo "— Receipt not found"

echo ""
echo "--- Removing /Applications/Python 3.14 (if present) ---"
sudo rm -rf "/Applications/Python 3.14" 2>/dev/null && echo "✅ Done" || echo "— Not found (OK)"

echo ""
echo "=== Verifying final state ==="

echo ""
echo "• /usr/local/bin python* remaining:"
ls /usr/local/bin/python* 2>/dev/null && echo "  (some remain — check above)" || echo "  (none — clean ✅)"

echo ""
echo "• /Library/Frameworks/Python.framework:"
ls /Library/Frameworks/Python.framework/Versions/ 2>/dev/null && echo "  (still present — check above)" || echo "  (gone ✅)"

echo ""
echo "• Homebrew python@3.14 (should still be here):"
/opt/homebrew/bin/python3 --version && echo "  ✅ Working" || echo "  ❌ Problem!"

echo ""
echo "• Active python3 in new shell (should be Homebrew 3.14 now):"
/usr/bin/env python3 --version

echo ""
echo "=== DONE ==="
echo ""
echo "Next steps:"
echo "1. Open a NEW Terminal tab/window and run:"
echo "     python3 --version"
echo "   It should say Python 3.14.x (Homebrew)"
echo ""
echo "2. Update Full Disk Access in System Settings:"
echo "   • REMOVE: the old 'python3.14' entry (it pointed to python.org)"
echo "   • ADD:    /opt/homebrew/Cellar/python@3.14/3.14.3_1/bin/python3.14"
echo "             (press Cmd+Shift+G in the file picker to type the path)"
echo ""
echo "3. Your bridge LaunchAgent already correctly points to:"
echo "     /opt/homebrew/bin/python3  →  Homebrew python@3.14"
echo "   No change needed there."
