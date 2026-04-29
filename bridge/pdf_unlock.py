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


def unlock_pdf_bytes(
    pdf_bytes: bytes,
    passwords: list[str],
) -> dict:
    """Unlock a PDF from in-memory bytes, trying each password in order.

    No temp files on the host; all work is done in-memory via pikepdf.

    Returns:
        {
            "unlocked_bytes": bytes,
            "was_encrypted": bool,
            "password_used_index": int | None,
            "page_count": int,
        }

    Raises:
        UnlockError  if the PDF is encrypted and no password works.
        ImportError  if pikepdf is not installed.
    """
    import io
    try:
        import pikepdf
    except ImportError:
        raise ImportError(
            "pikepdf is required for in-memory PDF unlock")

    # ── Try opening without a password first ─────────────────────────────
    buf = io.BytesIO(pdf_bytes)
    try:
        with pikepdf.open(buf) as pdf:
            if not pdf.is_encrypted:
                out = io.BytesIO()
                pdf.save(out)
                return {
                    "unlocked_bytes": out.getvalue(),
                    "was_encrypted": False,
                    "password_used_index": None,
                    "page_count": len(pdf.pages),
                }
    except (pikepdf.PasswordError, pikepdf.EncryptionError):
        pass  # encrypted — fall through to password loop
    except Exception as e:
        raise UnlockError(f"Failed to open PDF: {e}") from e

    # ── Try each password ─────────────────────────────────────────────────
    for idx, password in enumerate(passwords):
        buf = io.BytesIO(pdf_bytes)
        try:
            with pikepdf.open(buf, password=password) as pdf:
                out = io.BytesIO()
                pdf.save(out)
                log.debug(
                    "PDF unlocked with password index %d", idx)
                return {
                    "unlocked_bytes": out.getvalue(),
                    "was_encrypted": True,
                    "password_used_index": idx,
                    "page_count": len(pdf.pages),
                }
        except (pikepdf.PasswordError, pikepdf.EncryptionError):
            continue
        except Exception as e:
            log.warning(
                "pikepdf error with password index %d: %s", idx, e)
            continue

    raise UnlockError(
        f"PDF is encrypted and none of the {len(passwords)} "
        "password(s) worked")


def is_encrypted(pdf_path: str) -> bool:
    """Quick check whether a PDF is password-protected."""
    try:
        import pikepdf
    except ImportError:
        return False

    try:
        with pikepdf.open(pdf_path) as _:
            return False
    except (pikepdf.PasswordError, pikepdf.EncryptionError):
        return True
    except Exception:
        return False


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
