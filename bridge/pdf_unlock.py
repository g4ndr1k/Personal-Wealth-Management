"""
PDF unlock: remove password protection before parsing.

Strategy:
  1. pikepdf  — fast, pure Python, handles AES-128/AES-256/RC4
  2. AppleScript via osascript — fallback for edge cases pikepdf can't handle
     (opens PDF in Preview, prints to PDF, saves unlocked copy)

Both strategies write the unlocked file to a deterministic path:
  {unlocked_dir}/{original_stem}_unlocked.pdf

The caller passes the password. Passwords are never logged.
"""
import os
import re
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class UnlockError(Exception):
    pass


def unlock_pdf(src_path: str, password: str, unlocked_dir: str) -> str:
    """
    Unlock a password-protected PDF. Returns path to the unlocked copy.
    Raises UnlockError if both strategies fail.
    """
    os.makedirs(unlocked_dir, exist_ok=True)
    stem = Path(src_path).stem
    # Strip any existing _unlocked suffix to avoid _unlocked_unlocked
    stem = re.sub(r"_unlocked$", "", stem)
    dest_path = os.path.join(unlocked_dir, f"{stem}_unlocked.pdf")

    # Already unlocked (idempotent)
    if os.path.exists(dest_path):
        try:
            import pikepdf
            with pikepdf.open(dest_path) as pdf:
                if not pdf.is_encrypted:
                    return dest_path
        except Exception:
            pass  # fall through and re-unlock

    # ── Strategy 1: pikepdf ───────────────────────────────────────────────
    try:
        import pikepdf
        with pikepdf.open(src_path, password=password) as pdf:
            pdf.save(dest_path)
        log.info(f"Unlocked via pikepdf: {Path(src_path).name}")
        return dest_path
    except Exception as e:
        log.warning(f"pikepdf unlock failed ({e}), trying AppleScript fallback")

    # ── Strategy 2: AppleScript via Preview ──────────────────────────────
    try:
        dest_path = _unlock_via_applescript(src_path, password, dest_path)
        log.info(f"Unlocked via AppleScript: {Path(src_path).name}")
        return dest_path
    except Exception as e:
        raise UnlockError(
            f"Both unlock strategies failed for {Path(src_path).name}. "
            f"Last error: {e}"
        ) from e


def is_encrypted(pdf_path: str) -> bool:
    """Quick check whether a PDF is password-protected."""
    try:
        import pikepdf
        with pikepdf.open(pdf_path) as _:
            return False
    except Exception as e:
        return "password" in str(e).lower() or "encrypt" in str(e).lower()


def _escape_applescript_string(s: str) -> str:
    """Escape a string for safe inclusion in an AppleScript double-quoted string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _unlock_via_applescript(src_path: str, password: str, dest_path: str) -> str:
    """
    Open PDF in Preview with password, print to PDF (saves without password).
    Uses a temp file to avoid leaking password into AppleScript string.
    """
    # Write password to a temp file — never interpolate into script string
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(password)
        pwd_file = f.name

    script = f'''
tell application "Preview"
    set pwdText to (read POSIX file "{_escape_applescript_string(pwd_file)}" as text)
    set srcPDF to POSIX file "{_escape_applescript_string(src_path)}"
    open srcPDF
    -- Wait for document to load
    delay 1
    set frontDoc to front document
    -- Print to PDF (save as)
    set destPDF to POSIX file "{_escape_applescript_string(dest_path)}"
    print frontDoc
    delay 0.5
    close frontDoc saving no
end tell
'''
    # More reliable approach: use Quartz PDFDocument via Python subprocess
    # instead of Preview (Preview may prompt for password interactively)
    script2 = f'''
set pwdPath to "{_escape_applescript_string(pwd_file)}"
set srcPath to "{_escape_applescript_string(src_path)}"
set destPath to "{_escape_applescript_string(dest_path)}"

-- Use Quartz to unlock
do shell script "python3 -c \\"
import Quartz
url = Quartz.NSURL.fileURLWithPath_(srcPath)
pdf = Quartz.PDFDocument.alloc().initWithURL_(url)
if pdf is None:
    import sys; sys.exit(1)
ok = pdf.unlockWithPassword_(open(pwdPath).read().strip())
if not ok:
    import sys; sys.exit(2)
pdf.writeToFile_(destPath)
\\""
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script2],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return dest_path
    finally:
        try:
            os.unlink(pwd_file)
        except OSError:
            pass
