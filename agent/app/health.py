"""
health.py — Agent HTTP health + mail-API server (port 8080).

Endpoints
---------
GET  /                      Stats snapshot (always public, internal only)
POST /trigger[?force=1]     Queue an immediate scan cycle
GET  /api/mail/summary      KPIs + classification + action counts  [auth]
GET  /api/mail/recent       Last N processed messages              [auth]
GET  /api/mail/accounts     Per-IMAP-account health                [auth]
GET  /api/mail/credentials  Per-account credential presence        [auth]
POST /api/mail/run          Alias for /trigger                     [auth]

[auth] = requires X-Api-Key header matching FINANCE_API_KEY env var.
If FINANCE_API_KEY is not set the endpoints are open (development mode).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("agent.health")


# ── Stats view ────────────────────────────────────────────────────────────────

class StatsView:
    def __init__(self, initial: dict):
        self._data = dict(initial)
        self._lock = Lock()

    def update(self, **kwargs):
        with self._lock:
            self._data.update(kwargs)

    def incr(self, key: str, value: int = 1):
        with self._lock:
            self._data[key] = self._data.get(key, 0) + value

    def snapshot(self):
        with self._lock:
            return dict(self._data)


# ── Auth helper ───────────────────────────────────────────────────────────────

def _api_key() -> str | None:
    """Return the expected API key, or None if not configured."""
    return os.environ.get("FINANCE_API_KEY") or None


def _check_auth(handler: "BaseHTTPRequestHandler") -> bool:
    """
    Validate X-Api-Key header using constant-time comparison.
    Returns True if the request is authorised (or no key is configured).
    Sends a 401 response and returns False if auth fails.
    """
    expected = _api_key()
    if not expected:
        # Dev mode: no key configured → open
        return True

    provided = handler.headers.get("X-Api-Key", "")
    # Length pre-check before constant-time compare to prevent timing oracle
    if len(provided) != len(expected):
        _send_json(handler, 401, {"error": "Unauthorized"})
        return False
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        _send_json(handler, 401, {"error": "Unauthorized"})
        return False
    return True


# ── Response helper ───────────────────────────────────────────────────────────

def _send_json(handler: "BaseHTTPRequestHandler",
               status: int, body: object) -> None:
    payload = json.dumps(body, default=str).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    # Permissive CORS for local dashboard access
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "X-Api-Key, Content-Type")
    handler.end_headers()
    handler.wfile.write(payload)


# ── Server factory ────────────────────────────────────────────────────────────

def start_health_server(
    stats: StatsView,
    host: str = "127.0.0.1",
    port: int = 8080,
    trigger_callback=None,
):
    """
    Start the health + mail-API HTTP server in a daemon thread.

    trigger_callback: callable(force: bool) → dict
        Called on POST /trigger and POST /api/mail/run.
        Returns a dict to merge into the response body.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "X-Api-Key, Content-Type")
            self.end_headers()

        # ── GET ────────────────────────────────────────────────────────────
        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path in ("/", ""):
                # Legacy stats snapshot — always accessible (internal network)
                _send_json(self, 200, stats.snapshot())

            elif path == "/api/mail/summary":
                if not _check_auth(self):
                    return
                try:
                    from app.api_mail import get_summary
                    data = get_summary()
                    _send_json(self, 200, data)
                except Exception as exc:
                    logger.error("api_mail.get_summary error: %s", exc)
                    _send_json(self, 500, {"error": str(exc)})

            elif path == "/api/mail/recent":
                if not _check_auth(self):
                    return
                try:
                    raw_limit = qs.get("limit", ["20"])[0]
                    limit = max(1, min(int(raw_limit), 200))
                except (ValueError, IndexError):
                    limit = 20
                try:
                    from app.api_mail import get_recent
                    data = get_recent(limit=limit)
                    _send_json(self, 200, data)
                except Exception as exc:
                    logger.error("api_mail.get_recent error: %s", exc)
                    _send_json(self, 500, {"error": str(exc)})

            elif path == "/api/mail/accounts":
                if not _check_auth(self):
                    return
                try:
                    from app.api_mail import get_accounts
                    data = get_accounts()
                    _send_json(self, 200, data)
                except Exception as exc:
                    logger.error("api_mail.get_accounts error: %s", exc)
                    _send_json(self, 500, {"error": str(exc)})

            elif path == "/api/mail/credentials":
                if not _check_auth(self):
                    return
                try:
                    from app.config import load_settings
                    from app.imap_source import credential_debug_statuses
                    data = credential_debug_statuses(load_settings())
                    _send_json(self, 200, data)
                except Exception as exc:
                    logger.error("credential debug error: %s", exc)
                    _send_json(self, 500, {"error": str(exc)})

            else:
                _send_json(self, 404, {"error": "not found"})

        # ── POST ───────────────────────────────────────────────────────────
        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path == "/trigger":
                # Legacy trigger — accessible without auth (internal only)
                if trigger_callback:
                    force = qs.get("force", ["0"])[0] == "1"
                    try:
                        result = trigger_callback(force=force)
                        _send_json(self, 200, result)
                    except Exception as exc:
                        logger.error("Trigger callback error: %s", exc)
                        _send_json(self, 500, {"error": str(exc)})
                else:
                    _send_json(self, 404,
                               {"error": "no trigger configured"})

            elif path == "/api/mail/run":
                if not _check_auth(self):
                    return
                if trigger_callback:
                    force = qs.get("force", ["0"])[0] == "1"
                    try:
                        result = trigger_callback(force=force)
                        _send_json(self, 200,
                                   {"queued": True, **result})
                    except Exception as exc:
                        logger.error("/api/mail/run error: %s", exc)
                        _send_json(self, 500, {"error": str(exc)})
                else:
                    _send_json(self, 503,
                               {"error": "trigger not configured"})

            else:
                _send_json(self, 404, {"error": "not found"})

        def log_message(self, format, *args):  # noqa: A002
            pass  # suppress per-request access logs

    server = ThreadingHTTPServer((host, port), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
