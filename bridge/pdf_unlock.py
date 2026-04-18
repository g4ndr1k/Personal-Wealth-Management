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
import sys
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
    Unlock a password-protected PDF using macOS Quartz API via direct Python subprocess.
    Bypasses AppleScript entirely to avoid shell injection risks.
    """
    # Write password to a temp file — never pass as arg or interpolate into strings.
    # Restrict permissions immediately so other processes can't read it before use.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        os.chmod(f.name, 0o600)
        f.write(password)
        pwd_file = f.name

    # Python script that uses Quartz to unlock the PDF
    py_script = '''
import Quartz, sys
src = sys.argv[1]
pwdf = sys.argv[2]
dst = sys.argv[3]
url = Quartz.NSURL.fileURLWithPath_(src)
pdf = Quartz.PDFDocument.alloc().initWithURL_(url)
if pdf is None:
    sys.exit(1)
ok = pdf.unlockWithPassword_(open(pwdf).read().strip())
if not ok:
    sys.exit(2)
pdf.writeToFile_(dst)
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", py_script, src_path, pwd_file, dest_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"PDF unlock failed (rc={result.returncode}): {result.stderr.strip()}")
        return dest_path
    finally:
        try:
            # Overwrite before deletion so password doesn't linger on disk
            with open(pwd_file, "w") as _f:
                _f.write("\0" * len(password))
            os.unlink(pwd_file)
        except OSError:
            pass
