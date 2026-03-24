import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager


class BridgeState:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                stream TEXT PRIMARY KEY,
                ack_token TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                action TEXT NOT NULL,
                success INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_request_log_endpoint_created
                ON request_log(endpoint, created_at);
            """)
            conn.commit()

    def get_ack(self, stream: str, default: str = "0") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ack_token FROM checkpoints WHERE stream = ?",
                (stream,)
            ).fetchone()
            return row[0] if row else default

    def set_ack(self, stream: str, ack_token: str):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO checkpoints (stream, ack_token)
            VALUES (?, ?)
            ON CONFLICT(stream) DO UPDATE SET ack_token = excluded.ack_token
            """, (stream, ack_token))
            conn.commit()

    def log_request(self, endpoint: str, action: str, success: bool):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO request_log(endpoint, action, success, created_at)
            VALUES (?, ?, ?, ?)
            """, (endpoint, action, int(success), datetime.now(timezone.utc).isoformat()))
            conn.commit()

    def count_recent_actions(self, action: str, minutes: int = 60) -> int:
        from datetime import timedelta
        with self._connect() as conn:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
            row = conn.execute("""
                SELECT COUNT(*)
                FROM request_log
                WHERE action = ?
                  AND success = 1
                  AND created_at >= ?
            """, (action, cutoff)).fetchone()
            return row[0] if row else 0
