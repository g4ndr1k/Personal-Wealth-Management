"""
net_guard.py — pre-flight network reachability checks for the mail agent.

Call network_ok() before attempting any network I/O in a scan cycle.
Only outbound and IMAP failures are fatal for mail scans. Bridge and NAS
probes are advisory so IMAP processing and deterministic rules can proceed.
Each probe records a structured event in the returned reasons list so
callers can log or surface them without duplicating logic.
"""
from __future__ import annotations

import logging
import os
import json
import socket
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger("agent.net_guard")

_DNS_PROBES = [
    ("1.1.1.1", 53),
    ("8.8.8.8", 53),
]
_IMAP_HOST = ("imap.gmail.com", 993)
_NAS_MOUNT = "/Volumes/Synology"
_TCP_TIMEOUT = 3  # seconds


def _tcp_connect(host: str, port: int, timeout: float = _TCP_TIMEOUT) -> bool:
    """Attempt a TCP connect; return True if successful."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _bridge_token() -> str:
    token_file = os.environ.get("BRIDGE_TOKEN_FILE", "")
    if token_file:
        try:
            return Path(token_file).read_text().strip()
        except OSError:
            return ""
    return ""


def network_ok(bridge_url: str | None = None,
               bridge_token: str | None = None) -> tuple[bool, list[str]]:
    """
    Run multi-step connectivity probes.

    Parameters
    ----------
    bridge_url:
        Base URL of the bridge HTTP server (e.g. ``http://host.docker.internal:9100``).
        Falls back to the ``BRIDGE_URL`` environment variable when *None*.

    Returns
    -------
    (ok, reasons)
        *ok* is True when fatal probes pass.
        *reasons* is a list of human-readable strings describing each
        result — always populated, not just on failure.
    """
    reasons: list[str] = []
    all_ok = True

    # ── 1. General outbound: either DNS resolver must be reachable ────────────
    dns_ok = any(_tcp_connect(h, p) for h, p in _DNS_PROBES)
    if dns_ok:
        reasons.append("outbound:ok — TCP to 1.1.1.1:53 or 8.8.8.8:53 succeeded")
    else:
        reasons.append("outbound:fail — cannot reach 1.1.1.1:53 or 8.8.8.8:53")
        all_ok = False

    # ── 2. Bridge reachability ────────────────────────────────────────────────
    url = bridge_url or os.environ.get("BRIDGE_URL", "")
    if url:
        health_url = url.rstrip("/") + "/health"
        try:
            token = bridge_token if bridge_token is not None else _bridge_token()
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            req = urllib.request.Request(health_url, headers=headers)
            with urllib.request.urlopen(req, timeout=_TCP_TIMEOUT) as resp:
                payload = json.loads(resp.read() or b"{}")
                overall = payload.get("overall", "fail")
                if resp.status == 200 and overall == "ok":
                    reasons.append(f"bridge:ok — {health_url} overall=ok")
                else:
                    reasons.append(
                        f"bridge:degraded — {health_url} returned HTTP {resp.status} overall={overall}; bridge actions disabled"
                    )
        except urllib.error.HTTPError as e:
            reasons.append(
                f"bridge:degraded — {health_url} HTTP error {e.code}: {e.reason}; bridge actions disabled"
            )
        except Exception as e:
            reasons.append(
                f"bridge:degraded — {health_url} unreachable: {type(e).__name__}: {e}; bridge actions disabled"
            )
    else:
        reasons.append("bridge:skip — BRIDGE_URL not set")

    # ── 3. IMAP reachability (at least one Gmail server) ─────────────────────
    imap_ok = _tcp_connect(*_IMAP_HOST)
    if imap_ok:
        reasons.append(f"imap:ok — TCP to {_IMAP_HOST[0]}:{_IMAP_HOST[1]} succeeded")
    else:
        reasons.append(
            f"imap:fail — cannot reach {_IMAP_HOST[0]}:{_IMAP_HOST[1]}"
        )
        all_ok = False

    # ── 4. NAS mount (non-blocking — failure queues PDF jobs as pending) ──────
    nas_present = os.path.ismount(_NAS_MOUNT)
    if nas_present:
        reasons.append(f"nas:ok — {_NAS_MOUNT} is mounted")
    else:
        # NAS absence is advisory: IMAP still proceeds, PDF jobs queued
        reasons.append(
            f"nas:degraded — {_NAS_MOUNT} not mounted; PDF jobs will queue as pending"
        )
        # NOTE: intentionally does NOT flip all_ok

    # ── Structured log ────────────────────────────────────────────────────────
    for r in reasons:
        level = (
            logging.WARNING
            if ":degraded" in r or ":fail" in r
            else logging.DEBUG
        )
        logger.log(level, "net_guard: %s", r)

    return all_ok, reasons
