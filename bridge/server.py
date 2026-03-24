import json
import logging
import signal
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bridge.config import load_settings, get_token_path, validate_settings
from bridge.auth import load_token, is_authorized
from bridge.state import BridgeState
from bridge.rate_limit import RateLimiter
from bridge.mail_source import MailSource
from bridge.messages_source import MessagesSource

SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.toml"
DATA_DB = PROJECT_ROOT / "data" / "bridge.db"
LOG_FILE = PROJECT_ROOT / "logs" / "bridge.log"
MAX_REQUEST_BODY = 65536

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s: %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_FILE, maxBytes=10_000_000, backupCount=5),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("bridge")


class AppContext:
    def __init__(self):
        self.settings = load_settings(str(SETTINGS_PATH))
        validate_settings(self.settings)
        self.token = load_token(get_token_path(self.settings))
        self.state = BridgeState(DATA_DB)
        self.rate = RateLimiter(DATA_DB)
        self.mail = MailSource(self.settings)
        self.messages = MessagesSource(self.settings)

        # Verify mail access and schema — fail fast if broken
        if not self.mail.can_access():
            raise RuntimeError(
                "Cannot access Mail database. "
                "Check Full Disk Access permissions.")

        schema = self.mail.verify_schema()
        if not schema["valid"]:
            raise RuntimeError(
                f"Incompatible Mail schema: {schema['errors']}. "
                "This may indicate an unsupported macOS version.")

        logger.info("Mail database accessible, schema valid")

        if not self.messages.can_access():
            logger.warning(
                "Cannot access Messages database — "
                "iMessage commands disabled")
        else:
            logger.info("Messages database accessible")


class Handler(BaseHTTPRequestHandler):
    ctx: AppContext = None

    def _json(self, code: int, payload: dict):
        raw = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BODY:
            self._json(413, {"error": "Payload too large"})
            return None
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON"})
            return None

    def _validate_ack_token(self, data: dict) -> str | None:
        ack = str(data.get("ack_token", "0")).strip()
        if not ack.isdigit():
            self._json(400, {
                "error": "Invalid ack_token: must be numeric"})
            return None
        return ack

    def _auth(self) -> bool:
        ok = is_authorized(
            self.headers.get("Authorization", ""), self.ctx.token)
        if not ok:
            self.ctx.state.log_request(
                self.path, "auth_fail", False)
            self._json(401, {"error": "Unauthorized"})
        return ok

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Unauthenticated liveness probe
        if path == "/healthz":
            self._json(200, {"status": "ok"})
            return

        if not self._auth():
            return

        try:
            if path == "/health":
                self._json(200, {
                    "status": "ok",
                    "service": "bridge",
                    "mail_available": self.ctx.mail.can_access(),
                    "messages_available": (
                        self.ctx.messages.can_access()),
                    "timestamp": (
                        datetime.now(timezone.utc).isoformat()),
                })
                return

            if path == "/mail/schema":
                self._json(200, self.ctx.mail.debug_schema())
                return

            if path == "/mail/pending":
                limit = int(params.get("limit", ["25"])[0])
                ack = self.ctx.state.get_ack("mail", "0")
                items, next_ack = (
                    self.ctx.mail.get_pending_messages(
                        ack, limit=limit))
                self.ctx.state.log_request(
                    path, "mail_pending", True)
                self._json(200, {
                    "count": len(items),
                    "items": items,
                    "next_ack_token": next_ack,
                })
                return

            if path == "/commands/pending":
                if not self.ctx.messages.can_access():
                    self._json(200, {
                        "count": 0, "items": [],
                        "next_ack_token": "0"})
                    return
                # No rate limit on polling — only on command
                # execution/replies (handled via /alerts/send)
                limit = int(params.get("limit", ["20"])[0])
                ack = int(self.ctx.state.get_ack(
                    "commands", "0"))
                items, next_ack = (
                    self.ctx.messages.get_pending_commands(
                        ack, limit=limit))
                self.ctx.state.log_request(
                    path, "commands_pending", True)
                self._json(200, {
                    "count": len(items),
                    "items": items,
                    "next_ack_token": next_ack,
                })
                return

            self._json(404, {"error": "Not found"})

        except Exception as e:
            logger.exception("GET error on %s", path)
            self.ctx.state.log_request(path, "error", False)
            self._json(500, {"error": str(e)})

    def do_POST(self):
        if not self._auth():
            return
        path = urlparse(self.path).path

        try:
            data = self._read_json()
            if data is None:
                return

            if path == "/mail/ack":
                ack = self._validate_ack_token(data)
                if ack is None:
                    return
                self.ctx.state.set_ack("mail", ack)
                self.ctx.state.log_request(
                    path, "mail_ack", True)
                self._json(200, {
                    "success": True, "acked_through": ack})
                return

            if path == "/commands/ack":
                ack = self._validate_ack_token(data)
                if ack is None:
                    return
                self.ctx.state.set_ack("commands", ack)
                self.ctx.state.log_request(
                    path, "commands_ack", True)
                self._json(200, {"success": True})
                return

            if path == "/alerts/send":
                limit = self.ctx.settings[
                    "imessage"]["max_alerts_per_hour"]
                if not self.ctx.rate.allow(
                        "/alerts/send", limit, minutes=60):
                    self.ctx.state.log_request(
                        path, "rate_limited", False)
                    self._json(429, {
                        "error": "Rate limit exceeded"})
                    return
                text = (data.get("text") or "").strip()
                if not text:
                    self._json(400, {"error": "Missing text"})
                    return
                result = self.ctx.messages.send_alert(text)
                success = result.get("success", False)
                self.ctx.state.log_request(
                    path, "alerts_send", success)
                self._json(200 if success else 500, result)
                return

            self._json(404, {"error": "Not found"})

        except ValueError as e:
            self._json(400, {"error": str(e)})
        except Exception as e:
            logger.exception("POST error on %s", path)
            self.ctx.state.log_request(path, "error", False)
            self._json(500, {"error": str(e)})

    def log_message(self, format, *args):
        return


def main():
    ctx = AppContext()
    Handler.ctx = ctx
    host = ctx.settings["bridge"]["host"]
    port = int(ctx.settings["bridge"]["port"])
    logger.info("Bridge starting on %s:%s", host, port)

    server = ThreadingHTTPServer((host, port), Handler)

    def shutdown_handler(signum, frame):
        logger.info("Bridge signal %s, shutting down", signum)
        if ctx.settings["imessage"].get(
                "shutdown_notifications", False):
            try:
                ctx.messages.send_alert(
                    "🔴 Bridge shutting down")
            except Exception:
                pass
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Bridge stopped")


if __name__ == "__main__":
    main()
