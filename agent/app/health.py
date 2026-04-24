import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Lock
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("agent.health")


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


def start_health_server(stats: StatsView, host="127.0.0.1", port=8080,
                        trigger_callback=None):
    """Start a health HTTP server.

    trigger_callback: callable() called when POST /trigger is received.
                      Returns a dict to include in the response.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps(stats.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path == "/trigger" and trigger_callback:
                force = qs.get("force", ["0"])[0] == "1"
                try:
                    result = trigger_callback(force=force)
                    payload = json.dumps(result).encode()
                    self.send_response(200)
                except Exception as e:
                    logger.error("Trigger callback error: %s", e)
                    payload = json.dumps({"error": str(e)}).encode()
                    self.send_response(500)
            else:
                payload = json.dumps({"error": "not found"}).encode()
                self.send_response(404)

            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
