import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager


class RateLimiter:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def count_recent(self, endpoint: str, minutes: int = 60) -> int:
        with self._connect() as conn:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
            row = conn.execute("""
                SELECT COUNT(*)
                FROM request_log
                WHERE endpoint = ? AND created_at >= ? AND success = 1
            """, (endpoint, cutoff)).fetchone()
            return row[0] if row else 0

    def allow(self, endpoint: str, limit: int, minutes: int = 60) -> bool:
        return self.count_recent(endpoint, minutes) < limit
