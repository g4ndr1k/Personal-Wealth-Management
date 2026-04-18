"""
Bridge authentication — supports both file-based and Keychain token sources.

When auth.token_source = "keychain" in settings.toml, reads the bridge token
from macOS Keychain instead of a file on disk. Falls back to file if Keychain
lookup fails.
"""
import hmac
import logging
import stat
from pathlib import Path

log = logging.getLogger(__name__)


def load_token(token_file: Path) -> str:
    """Load bridge token from a file, enforcing strict permissions."""
    mode = token_file.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError(
            f"Bridge token file must not grant group/other access: {token_file}. "
            f"Run: chmod 600 {token_file}"
        )
    token = token_file.read_text().strip()
    if not token:
        raise RuntimeError("Bridge token file is empty")
    return token


def resolve_token(settings: dict) -> str:
    """Resolve the bridge token from Keychain or file, based on settings.

    If auth.token_source == "keychain", reads from macOS Keychain.
    Otherwise (or on Keychain miss) falls back to the file at auth.token_file.
    """
    auth = settings.get("auth", {})
    source = auth.get("token_source", "file")

    if source == "keychain":
        try:
            from bridge.secret_manager import get_from_keychain, _keychain_service
            service = _keychain_service(settings)
            token = get_from_keychain(service, "bridge_token")
            if token:
                return token
        except ImportError:
            pass
        # Keychain miss — fall through to file
        log.warning(
            "Keychain lookup failed for token_source='keychain', "
            "falling back to file-based auth"
        )

    # File-based fallback
    token_file = Path(auth["token_file"]).expanduser()
    return load_token(token_file)


def is_authorized(header_value: str, token: str) -> bool:
    """Check if a request's Bearer token matches the expected token."""
    if not header_value or not header_value.startswith("Bearer "):
        return False
    supplied = header_value[7:].strip()
    # Length check before compare_digest avoids leaking token length via
    # timing differences on inputs of vastly different sizes.
    if len(supplied) != len(token):
        return False
    return hmac.compare_digest(supplied.encode(), token.encode())
