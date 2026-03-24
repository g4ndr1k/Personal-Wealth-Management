import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread, Lock


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


def start_health_server(stats: StatsView, host="0.0.0.0", port=8080):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = json.dumps(stats.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer((host, port), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
