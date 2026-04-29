#!/usr/bin/env python3
"""
mailagent_status.py — CLI smoke harness for the mail-agent /api/mail/* API.

Usage (from repo root):
    python3 scripts/mailagent_status.py
    python3 scripts/mailagent_status.py --url http://127.0.0.1:8080 --api-key YOUR_KEY
    python3 scripts/mailagent_status.py --recent 5       # show only 5 recent
    python3 scripts/mailagent_status.py --no-run         # skip POST /api/mail/run

Exits 0 on success, 1 on any connectivity error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_URL     = "http://127.0.0.1:8080"
DEFAULT_API_KEY = os.environ.get("FINANCE_API_KEY", "")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _request(
    method: str,
    url: str,
    api_key: str,
    timeout: int = 10,
) -> tuple[int, Any]:
    """
    Send an HTTP request with X-Api-Key header.
    Returns (status_code, parsed_json_body).
    Raises SystemExit on connectivity failure.
    """
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key

    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw.decode(errors="replace")}
        return exc.code, body
    except (urllib.error.URLError, OSError) as exc:
        print(f"\n  ERROR: Cannot reach {url}")
        print(f"  {exc}\n")
        print("  Is the mail-agent health server running on port 8080?")
        sys.exit(1)


# ── Pretty-print helpers ──────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_DIM    = "\033[2m"


def _hdr(title: str) -> None:
    bar = "─" * (len(title) + 4)
    print(f"\n{_BOLD}{_CYAN}┌{bar}┐")
    print(f"│  {title}  │")
    print(f"└{bar}┘{_RESET}")


def _kv(label: str, value: Any, width: int = 26) -> None:
    label_str = f"{label}:".ljust(width)
    print(f"  {_DIM}{label_str}{_RESET} {_BOLD}{value}{_RESET}")


def _status_icon(ok: bool) -> str:
    return f"{_GREEN}✓{_RESET}" if ok else f"{_RED}✗{_RESET}"


# ── Section printers ──────────────────────────────────────────────────────────

def print_summary(base_url: str, api_key: str) -> bool:
    _hdr("GET /api/mail/summary — KPIs")
    status, body = _request("GET", f"{base_url}/api/mail/summary", api_key)

    if status != 200:
        print(f"  {_RED}HTTP {status}{_RESET}: {body}")
        return False

    kpis  = body.get("kpis", {})
    split = body.get("source_split", {})
    clsf  = body.get("classification_counts", {})
    acts  = body.get("action_counts", {})

    print(f"\n  {'KPI':<28} Value")
    print(f"  {'─'*40}")
    _kv("Total Processed",    kpis.get("total_processed", 0))
    _kv("Urgent / High",      kpis.get("urgent_count", 0))
    _kv("Drafts Created",     kpis.get("drafts_created", 0))
    _kv("Avg Priority Score", kpis.get("avg_priority", 0))

    print(f"\n  {'Source Split':<28} Count")
    print(f"  {'─'*40}")
    _kv("Gmail",   split.get("gmail", 0))
    _kv("Outlook", split.get("outlook", 0))

    if clsf:
        print(f"\n  {'Category':<28} Count")
        print(f"  {'─'*40}")
        for cat, cnt in sorted(clsf.items(), key=lambda x: -x[1]):
            _kv(cat, cnt)

    if acts:
        print(f"\n  {'Action':<28} Count")
        print(f"  {'─'*40}")
        for key, val in acts.items():
            _kv(key, val)

    return True


def print_recent(base_url: str, api_key: str, limit: int = 20) -> bool:
    _hdr(f"GET /api/mail/recent?limit={limit} — Recent Messages")
    status, body = _request(
        "GET", f"{base_url}/api/mail/recent?limit={limit}", api_key
    )

    if status != 200:
        print(f"  {_RED}HTTP {status}{_RESET}: {body}")
        return False

    items: list[dict] = body if isinstance(body, list) else []
    if not items:
        print(f"  {_DIM}(no messages yet){_RESET}")
        return True

    # Column widths
    W_AT  = 26
    W_CAT = 18
    W_URG = 10

    print(f"\n  {'processed_at':<{W_AT}} {'category':<{W_CAT}} "
          f"{'urgency':<{W_URG}} summary")
    print(f"  {'─'*(W_AT+W_CAT+W_URG+50)}")

    for msg in items:
        at  = (msg.get("processed_at") or "")[:19].replace("T", " ")
        cat = (msg.get("category") or "(none)")[:W_CAT]
        urg = (msg.get("urgency") or "")[:W_URG]
        summary = (msg.get("summary") or "")[:60]
        alert_icon = f"{_GREEN}🔔{_RESET}" if msg.get("alert_sent") else "  "
        print(f"  {at:<{W_AT}} {cat:<{W_CAT}} {urg:<{W_URG}} "
              f"{alert_icon} {summary}")

    print(f"\n  {_DIM}{len(items)} message(s) shown{_RESET}")
    return True


def print_accounts(base_url: str, api_key: str) -> bool:
    _hdr("GET /api/mail/accounts — IMAP Account Health")
    status, body = _request(
        "GET", f"{base_url}/api/mail/accounts", api_key
    )

    if status != 200:
        print(f"  {_RED}HTTP {status}{_RESET}: {body}")
        return False

    items: list[dict] = body if isinstance(body, list) else []
    if not items:
        print(f"  {_DIM}(imap_accounts table not yet populated — "
              f"IMAP layer not active){_RESET}")
        return True

    for acct in items:
        name = acct.get("account_name", "(unknown)")
        ok   = acct.get("last_success")
        err  = acct.get("last_error")
        print(f"\n  {_BOLD}{name}{_RESET}")
        _kv("last_success", ok or "(never)")
        _kv("last_error",   err or "(none)")

    return True


def trigger_run(base_url: str, api_key: str) -> bool:
    _hdr("POST /api/mail/run — Trigger Poll Cycle")
    status, body = _request("POST", f"{base_url}/api/mail/run", api_key)

    icon = _status_icon(status == 200)
    print(f"\n  {icon}  HTTP {status}")
    print(f"  Response: {json.dumps(body, indent=4, default=str)}")
    return status == 200


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the mail-agent /api/mail/* endpoints."
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"Base URL of the agent health server (default: {DEFAULT_URL})"
    )
    parser.add_argument(
        "--api-key", default=DEFAULT_API_KEY,
        dest="api_key",
        help="X-Api-Key value (default: $FINANCE_API_KEY)"
    )
    parser.add_argument(
        "--recent", type=int, default=20, metavar="N",
        help="Number of recent messages to fetch (default: 20)"
    )
    parser.add_argument(
        "--no-run", action="store_true",
        help="Skip POST /api/mail/run trigger"
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    api_key  = args.api_key

    print(f"{_BOLD}Mail-Agent Status Smoke Test{_RESET}")
    print(f"  Target : {base_url}")
    print(f"  Auth   : {'configured' if api_key else _YELLOW + 'NONE (open mode)' + _RESET}")

    errors: list[str] = []

    if not print_summary(base_url, api_key):
        errors.append("/api/mail/summary")

    if not print_recent(base_url, api_key, limit=args.recent):
        errors.append("/api/mail/recent")

    if not print_accounts(base_url, api_key):
        errors.append("/api/mail/accounts")

    if not args.no_run:
        if not trigger_run(base_url, api_key):
            errors.append("/api/mail/run")
    else:
        print(f"\n  {_DIM}(POST /api/mail/run skipped via --no-run){_RESET}")

    if errors:
        print(f"\n{_RED}{_BOLD}FAILED endpoints: {', '.join(errors)}{_RESET}\n")
        sys.exit(1)
    else:
        print(f"\n{_GREEN}{_BOLD}All checks passed.{_RESET}\n")


if __name__ == "__main__":
    main()
