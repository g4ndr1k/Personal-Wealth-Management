import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager


class AgentState:
    def __init__(self, db_path: str = "/app/data/agent.db"):
        self.db_path = Path(db_path)
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

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                bridge_id TEXT PRIMARY KEY,
                message_id TEXT,
                processed_at TEXT,
                category TEXT,
                urgency TEXT,
                provider TEXT,
                alert_sent INTEGER,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS processed_commands (
                command_id TEXT PRIMARY KEY,
                processed_at TEXT,
                command_text TEXT,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bridge_id TEXT,
                sent_at TEXT,
                category TEXT,
                recipient TEXT,
                alert_text TEXT,
                success INTEGER
            );
            CREATE TABLE IF NOT EXISTS agent_flags (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            -- Lookup index for message_id dedup queries
            CREATE INDEX IF NOT EXISTS idx_processed_message_id
                ON processed_messages(message_id);

            CREATE TABLE IF NOT EXISTS command_log (
                command_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );

            -- Uniqueness constraint: prevent true duplicates
            -- by Message-ID header (excludes synthetic rowid- IDs)
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_processed_real_message_id
                ON processed_messages(message_id)
                WHERE message_id IS NOT NULL
                  AND message_id != ''
                  AND message_id NOT LIKE 'rowid-%';

            CREATE INDEX IF NOT EXISTS idx_alerts_sent_at
                ON alerts(sent_at);
            CREATE INDEX IF NOT EXISTS idx_processed_commands_processed_at
                ON processed_commands(processed_at);
            """)
            conn.commit()

    def message_processed(self, bridge_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_messages "
                "WHERE bridge_id = ?",
                (bridge_id,)
            ).fetchone() is not None

    def message_id_processed(self, message_id: str) -> bool:
        """Check if we already processed an email with this
        Message-ID header. Skips synthetic IDs."""
        if (not message_id
                or message_id.startswith("rowid-")):
            return False
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_messages "
                "WHERE message_id = ? LIMIT 1",
                (message_id,)
            ).fetchone() is not None

    def save_message_result(
        self, bridge_id, message_id, category,
        urgency, provider, alert_sent, summary
    ):
        with self._connect() as conn:
            try:
                conn.execute("""
                INSERT OR REPLACE INTO processed_messages
                (bridge_id, message_id, processed_at, category,
                 urgency, provider, alert_sent, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (bridge_id, message_id, self._now(),
                      category, urgency, provider,
                      int(alert_sent), summary))
                conn.commit()
            except sqlite3.IntegrityError:
                # Unique message_id constraint hit —
                # already processed under different bridge_id
                pass

    def save_alert(self, bridge_id, category, recipient,
                   alert_text, success):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO alerts
            (bridge_id, sent_at, category, recipient,
             alert_text, success)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (bridge_id, self._now(), category,
                  recipient, alert_text, int(success)))
            conn.commit()

    def recent_alerts(self, limit: int = 5):
        with self._connect() as conn:
            return conn.execute(
                "SELECT sent_at, category, alert_text, success "
                "FROM alerts ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()

    def command_processed(self, command_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_commands "
                "WHERE command_id = ?",
                (command_id,)
            ).fetchone() is not None

    def save_command_result(self, command_id, command_text,
                            result):
        with self._connect() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO processed_commands
            (command_id, processed_at, command_text, result)
            VALUES (?, ?, ?, ?)
            """, (command_id, self._now(),
                  command_text, result))
            conn.commit()

    def get_bool_flag(self, key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM agent_flags WHERE key = ?",
                (key,)
            ).fetchone()
            return ((row[0] if row else "false")
                    .lower() == "true")

    def set_bool_flag(self, key: str, value: bool):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO agent_flags (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE
                SET value = excluded.value
            """, (key, "true" if value else "false"))
            conn.commit()


    def count_commands_last_hour(self) -> int:
        from datetime import timedelta
        with self._connect() as conn:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            row = conn.execute(
                "SELECT COUNT(*) FROM command_log WHERE created_at >= ?",
                (cutoff,)
            ).fetchone()
            return row[0] if row else 0

    def record_command_processed(self, command_id: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO command_log (command_id, created_at) VALUES (?, ?)",
                (command_id, self._now())
            )
            conn.commit()
