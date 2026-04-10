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
            CREATE TABLE IF NOT EXISTS pipeline_notifications (
                month TEXT PRIMARY KEY,
                notified_at TEXT NOT NULL,
                message TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                trigger TEXT NOT NULL,
                files_scanned INTEGER DEFAULT 0,
                files_new INTEGER DEFAULT 0,
                files_skipped INTEGER DEFAULT 0,
                files_ok INTEGER DEFAULT 0,
                files_failed INTEGER DEFAULT 0,
                import_new_tx INTEGER DEFAULT 0,
                import_review INTEGER DEFAULT 0,
                sync_performed INTEGER DEFAULT 0,
                result_json TEXT DEFAULT ''
            );
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

    def start_pipeline_run(self, started_at: str, trigger: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO pipeline_runs (started_at, trigger)
                VALUES (?, ?)
                """,
                (started_at, trigger),
            )
            conn.commit()
            return int(cur.lastrowid)

    def finish_pipeline_run(self, run_id: int, result: dict):
        import json

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET finished_at = ?,
                    files_scanned = ?,
                    files_new = ?,
                    files_skipped = ?,
                    files_ok = ?,
                    files_failed = ?,
                    import_new_tx = ?,
                    import_review = ?,
                    sync_performed = ?,
                    result_json = ?
                WHERE id = ?
                """,
                (
                    result.get("finished_at"),
                    result.get("files_scanned", 0),
                    result.get("files_new", 0),
                    result.get("files_skipped", 0),
                    result.get("files_ok", 0),
                    result.get("files_failed", 0),
                    result.get("import_new_tx", 0),
                    result.get("import_review", 0),
                    result.get("sync_performed", 0),
                    json.dumps(result, default=str),
                    run_id,
                ),
            )
            conn.commit()

    def has_pipeline_notification(self, month: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM pipeline_notifications WHERE month = ?",
                (month,),
            ).fetchone()
            return row is not None

    def record_pipeline_notification(self, month: str, message: str):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pipeline_notifications (month, notified_at, message)
                VALUES (?, ?, ?)
                """,
                (month, datetime.now(timezone.utc).isoformat(), message),
            )
            conn.commit()
