#!/usr/bin/env python3
"""Export secrets from macOS Keychain to files for Docker container mounts.

Keychain is the source of truth. This script writes files into secrets/
so that docker-compose volume mounts work inside the Linux container
(which cannot access the macOS Keychain).

Usage:
    python3 scripts/export-secrets-for-docker.py
"""

import json
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bridge.secret_manager import get_from_keychain, _load_settings, _keychain_service

SECRETS_DIR = PROJECT_ROOT / "secrets"


def export_keychain_to_file(key: str, filename: str, subdir: str | None = None) -> bool:
    """Read a secret from Keychain and write it to a file."""
    settings = _load_settings()
    service = _keychain_service(settings)
    raw = get_from_keychain(service, key)
    if not raw:
        print(f"  {filename:40s} ✗ (not in Keychain)")
        return False

    out_dir = SECRETS_DIR / subdir if subdir else SECRETS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(raw)
    print(f"  {filename:40s} ✓")
    return True


def main():
    print(f"Exporting secrets from Keychain → {SECRETS_DIR}/\n")

    # Google service account
    export_keychain_to_file("google_service_account_json", "google_service_account.json")

    # Google OAuth credentials
    export_keychain_to_file("google_credentials_json", "google_credentials.json")

    # Google OAuth token (may not exist yet — created on first OAuth flow)
    export_keychain_to_file("google_token_json", "google_token.json")

    # Bridge token
    export_keychain_to_file("bridge_token", "bridge.token")

    # Bank passwords as banks.toml
    export_keychain_to_file("banks_toml", "banks.toml")

    print("\nDone.")


if __name__ == "__main__":
    main()
