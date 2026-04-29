import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager


def apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    """Apply the mail-agent SQLite connection contract."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")


class AgentState:
    def __init__(self, db_path: str = "/app/data/agent.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        apply_sqlite_pragmas(conn)
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

            -- ── IMAP intake state ─────────────────────────────────────
            CREATE TABLE IF NOT EXISTS imap_accounts (
                account_name TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                email TEXT NOT NULL,
                folders TEXT NOT NULL,
                last_uid INTEGER DEFAULT 0,
                uidvalidity INTEGER,
                last_success_at TEXT,
                last_error TEXT,
                status TEXT DEFAULT 'active',
                last_event_type TEXT,
                last_event_payload TEXT,
                last_event_at TEXT
            );

            CREATE TABLE IF NOT EXISTS imap_folder_state (
                account_name TEXT NOT NULL,
                folder TEXT NOT NULL,
                last_uid INTEGER DEFAULT 0,
                uidvalidity INTEGER,
                last_fetch_at TEXT,
                PRIMARY KEY (account_name, folder)
            );

            -- ── PDF attachment pipeline ───────────────────────────────
            -- status: pending|unlocked|renamed|routed|failed
            --         |pending_review|failed_retryable
            CREATE TABLE IF NOT EXISTS pdf_attachments (
                attachment_key TEXT PRIMARY KEY,
                message_key TEXT NOT NULL,
                fallback_message_key TEXT,
                account TEXT NOT NULL,
                folder TEXT NOT NULL,
                uid INTEGER NOT NULL,
                original_filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                sha256 TEXT,
                proposed_filename TEXT,
                routed_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error_reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_pdf_att_message_key
                ON pdf_attachments(message_key);
            CREATE INDEX IF NOT EXISTS idx_pdf_att_fallback_key
                ON pdf_attachments(fallback_message_key);

            -- ── Agent event log ───────────────────────────────────────
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_events_type
                ON agent_events(event_type);

            -- ── Phase 4A deterministic mail rules ───────────────────
            CREATE TABLE IF NOT EXISTS mail_rules (
                rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT,
                name TEXT NOT NULL,
                priority INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                match_type TEXT NOT NULL DEFAULT 'ALL',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(match_type IN ('ALL','ANY')),
                CHECK(enabled IN (0,1))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_mail_rules_account_priority
                ON mail_rules(COALESCE(account_id,'__global__'), priority);

            CREATE TABLE IF NOT EXISTS mail_rule_conditions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                operator TEXT NOT NULL,
                value TEXT,
                value_json TEXT,
                case_sensitive INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(rule_id)
                    REFERENCES mail_rules(rule_id) ON DELETE CASCADE,
                UNIQUE(rule_id, field, operator, value)
            );

            CREATE INDEX IF NOT EXISTS idx_mail_rule_conditions_rule
                ON mail_rule_conditions(rule_id);

            CREATE TABLE IF NOT EXISTS mail_rule_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                target TEXT,
                value_json TEXT,
                stop_processing INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(rule_id)
                    REFERENCES mail_rules(rule_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_mail_rule_actions_rule
                ON mail_rule_actions(rule_id);

            CREATE TABLE IF NOT EXISTS mail_needs_reply (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                account_id TEXT,
                bridge_id TEXT,
                sender_email TEXT,
                subject TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(message_id, account_id)
            );

            CREATE TABLE IF NOT EXISTS mail_ai_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT,
                message_id TEXT NOT NULL,
                folder TEXT,
                imap_uid INTEGER,
                uidvalidity INTEGER,
                body_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                manual_nonce TEXT,
                CHECK(status IN (
                    'pending','running','completed','failed','skipped')),
                CHECK(attempts >= 0)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_ai_queue_unique
                ON mail_ai_queue(
                    account_id, folder, uidvalidity, imap_uid,
                    body_hash, COALESCE(manual_nonce,''));

            CREATE TABLE IF NOT EXISTS mail_ai_classifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                urgency_score INTEGER NOT NULL,
                confidence REAL NOT NULL,
                summary TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(queue_id)
                    REFERENCES mail_ai_queue(id) ON DELETE CASCADE,
                CHECK(urgency_score BETWEEN 0 AND 10),
                CHECK(confidence BETWEEN 0 AND 1)
            );

            CREATE TABLE IF NOT EXISTS mail_ai_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(enabled IN (0,1))
            );

            CREATE TABLE IF NOT EXISTS mail_ai_trigger_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                action_type TEXT NOT NULL,
                threshold INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                value_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(category_id)
                    REFERENCES mail_ai_categories(id) ON DELETE CASCADE,
                CHECK(threshold BETWEEN 0 AND 10),
                CHECK(enabled IN (0,1))
            );

            CREATE TABLE IF NOT EXISTS mail_processing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                account_id TEXT,
                bridge_id TEXT,
                rule_id INTEGER,
                action_type TEXT,
                event_type TEXT NOT NULL,
                outcome TEXT NOT NULL,
                details_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(rule_id)
                    REFERENCES mail_rules(rule_id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_mail_processing_events_message
                ON mail_processing_events(message_id);
            CREATE INDEX IF NOT EXISTS idx_mail_processing_events_created
                ON mail_processing_events(created_at);
            """)
            conn.commit()

        # ── Non-destructive column migrations ─────────────────────────
        # Add status + source to processed_messages for existing DBs.
        self._add_column_if_missing(
            "processed_messages", "status", "TEXT DEFAULT 'processed'")
        self._add_column_if_missing(
            "processed_messages", "source", "TEXT DEFAULT 'bridge'")
        self._add_column_if_missing(
            "imap_accounts", "last_event_type", "TEXT")
        self._add_column_if_missing(
            "imap_accounts", "last_event_payload", "TEXT")
        self._add_column_if_missing(
            "imap_accounts", "last_event_at", "TEXT")

    def _add_column_if_missing(
            self, table: str, column: str, definition: str):
        """ALTER TABLE ADD COLUMN if the column does not exist yet."""
        with self._connect() as conn:
            rows = conn.execute(
                f"PRAGMA table_info({table})").fetchall()
            existing = {r[1] for r in rows}
            if column not in existing:
                conn.execute(
                    f"ALTER TABLE {table} "
                    f"ADD COLUMN {column} {definition}")
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


    # ── IMAP folder state ──────────────────────────────────────────────────

    def get_imap_folder_state(
            self, account_name: str, folder: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_uid, uidvalidity, last_fetch_at "
                "FROM imap_folder_state "
                "WHERE account_name = ? AND folder = ?",
                (account_name, folder)
            ).fetchone()
            if row:
                return {
                    "last_uid": row[0] or 0,
                    "uidvalidity": row[1],
                    "last_fetch_at": row[2],
                }
            return {"last_uid": 0, "uidvalidity": None,
                    "last_fetch_at": None}

    def set_imap_folder_state(
            self, account_name: str, folder: str,
            last_uid: int, uidvalidity: int):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO imap_folder_state
                (account_name, folder, last_uid, uidvalidity,
                 last_fetch_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_name, folder) DO UPDATE
                SET last_uid = excluded.last_uid,
                    uidvalidity = excluded.uidvalidity,
                    last_fetch_at = excluded.last_fetch_at
            """, (account_name, folder,
                  last_uid, uidvalidity, self._now()))
            conn.commit()

    def update_imap_account_success(self, account_name: str):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO imap_accounts
                (account_name, host, email, folders,
                 last_success_at, status)
            VALUES (?, '', '', '[]', ?, 'active')
            ON CONFLICT(account_name) DO UPDATE
                SET last_success_at = excluded.last_success_at,
                    status = 'active',
                    last_error = NULL
            """, (account_name, self._now()))
            conn.commit()

    def update_imap_account_error(
            self, account_name: str, error: str):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO imap_accounts
                (account_name, host, email, folders,
                 last_error, status)
            VALUES (?, '', '', '[]', ?, 'error')
            ON CONFLICT(account_name) DO UPDATE
                SET last_error = excluded.last_error,
                    status = 'error'
            """, (account_name, error[:500]))
            conn.commit()

    def update_imap_account_event(
            self, account_name: str, event_type: str,
            payload: dict | None = None):
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO imap_accounts
                (account_name, host, email, folders,
                 status, last_event_type, last_event_payload,
                 last_event_at)
            VALUES (?, '', '', '[]', 'active', ?, ?, ?)
            ON CONFLICT(account_name) DO UPDATE
                SET last_event_type = excluded.last_event_type,
                    last_event_payload = excluded.last_event_payload,
                    last_event_at = excluded.last_event_at
            """, (
                account_name, event_type,
                json.dumps(payload) if payload else None,
                self._now(),
            ))
            conn.commit()

    # ── PDF attachment state ───────────────────────────────────────────────

    def upsert_pdf_attachment(
            self, attachment_key: str, message_key: str,
            fallback_message_key: str | None,
            account: str, folder: str, uid: int,
            original_filename: str,
            status: str = "pending",
            sha256: str | None = None,
            proposed_filename: str | None = None,
            routed_path: str | None = None,
            error_reason: str | None = None):
        now = self._now()
        with self._connect() as conn:
            conn.execute("""
            INSERT INTO pdf_attachments
                (attachment_key, message_key,
                 fallback_message_key, account, folder, uid,
                 original_filename, status, sha256,
                 proposed_filename, routed_path,
                 created_at, updated_at, error_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(attachment_key) DO UPDATE
                SET status = excluded.status,
                    sha256 = COALESCE(excluded.sha256, sha256),
                    proposed_filename = COALESCE(
                        excluded.proposed_filename,
                        proposed_filename),
                    routed_path = COALESCE(
                        excluded.routed_path, routed_path),
                    updated_at = excluded.updated_at,
                    error_reason = excluded.error_reason
            """, (
                attachment_key, message_key,
                fallback_message_key,
                account, folder, uid,
                original_filename, status, sha256,
                proposed_filename, routed_path,
                now, now, error_reason,
            ))
            conn.commit()

    def get_pdf_attachment(
            self, attachment_key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attachment_key, message_key, "
                "fallback_message_key, account, folder, uid, "
                "original_filename, status, sha256, "
                "proposed_filename, routed_path, "
                "created_at, updated_at, error_reason "
                "FROM pdf_attachments "
                "WHERE attachment_key = ?",
                (attachment_key,)
            ).fetchone()
            if not row:
                return None
            keys = [
                "attachment_key", "message_key",
                "fallback_message_key", "account", "folder",
                "uid", "original_filename", "status",
                "sha256", "proposed_filename", "routed_path",
                "created_at", "updated_at", "error_reason",
            ]
            return dict(zip(keys, row))

    def all_attachments_settled(
            self, message_key: str) -> bool:
        """True when every PDF for this message is past 'pending'."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM pdf_attachments "
                "WHERE message_key = ? AND status = 'pending'",
                (message_key,)
            ).fetchone()
            return (row[0] == 0) if row else True

    # ── Event log ──────────────────────────────────────────────────────────

    def write_event(self, event_type: str,
                    payload: dict | None = None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_events "
                "(event_type, payload, created_at) "
                "VALUES (?, ?, ?)",
                (event_type,
                 json.dumps(payload) if payload else None,
                 self._now()))
            conn.commit()

        account = (payload or {}).get("account")
        if account:
            self.update_imap_account_event(account, event_type, payload)

    # ── IMAP-aware message dedup ───────────────────────────────────────────

    def message_key_processed(
            self, message_key: str) -> bool:
        """Check dedup by IMAP message_key (SHA-256 of account+folder+msgid)."""
        if not message_key:
            return False
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_messages "
                "WHERE message_id = ? LIMIT 1",
                (f"mkey:{message_key}",)
            ).fetchone() is not None

    def fallback_message_key_processed(
            self, fallback_message_key: str) -> bool:
        if not fallback_message_key:
            return False
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM processed_messages "
                "WHERE message_id = ? LIMIT 1",
                (f"fkey:{fallback_message_key}",)
            ).fetchone() is not None

    def save_message_result_imap(
            self, message: dict, category: str,
            urgency: str, provider: str,
            alert_sent: bool, summary: str):
        """Persist an IMAP-sourced message result."""
        bridge_id = message["bridge_id"]
        mkey = message.get("message_key")
        fkey = message.get("fallback_message_key")
        message_id = (
            f"mkey:{mkey}" if mkey
            else f"fkey:{fkey}" if fkey
            else message.get("message_id", "")
        )
        with self._connect() as conn:
            try:
                conn.execute("""
                INSERT OR IGNORE INTO processed_messages
                (bridge_id, message_id, processed_at,
                 category, urgency, provider, alert_sent,
                 summary, status, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    bridge_id, message_id, self._now(),
                    category, urgency, provider,
                    int(alert_sent), summary,
                    "processed", "imap",
                ))
                conn.commit()
            except Exception:
                pass

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
