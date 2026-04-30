import json
import sqlite3
import hashlib
import uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager


def apply_sqlite_pragmas(conn: sqlite3.Connection) -> None:
    """Apply the mail-agent SQLite connection contract."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def _bool_or_none(value) -> int | None:
    if value is None:
        return None
    return int(bool(value))


def _nullable_bool(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


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
                bridge_id TEXT,
                folder TEXT,
                imap_uid INTEGER,
                uidvalidity INTEGER,
                body_hash TEXT NOT NULL,
                sender TEXT,
                subject TEXT,
                received_at TEXT,
                body_text TEXT,
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
                trigger_id TEXT,
                name TEXT,
                priority INTEGER NOT NULL DEFAULT 100,
                conditions_json TEXT,
                actions_json TEXT,
                cooldown_seconds INTEGER NOT NULL DEFAULT 3600,
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

            -- ── Phase 4D.1 operator approval queue ──────────────────
            CREATE TABLE IF NOT EXISTS mail_action_approvals (
                approval_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id TEXT,
                message_key TEXT,
                account_id TEXT,
                folder TEXT,
                uidvalidity TEXT,
                imap_uid INTEGER,
                subject TEXT,
                sender TEXT,
                received_at TEXT,
                proposed_action_type TEXT NOT NULL,
                proposed_target TEXT,
                proposed_value_json TEXT,
                reason TEXT,
                ai_category TEXT,
                ai_urgency_score INTEGER,
                ai_confidence REAL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
	                decided_at TEXT,
	                decided_by TEXT,
	                decision_note TEXT,
	                execution_started_at TEXT,
	                executed_at TEXT,
	                execution_status TEXT,
                execution_result_json TEXT,
                archived_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(status IN (
                    'pending','approved','rejected','expired',
                    'executed','failed','blocked')),
                CHECK(source_type IN ('ai_trigger','manual','rule_preview'))
            );

            CREATE INDEX IF NOT EXISTS idx_mail_action_approvals_status_requested
                ON mail_action_approvals(status, requested_at);
            CREATE INDEX IF NOT EXISTS idx_mail_action_approvals_message
                ON mail_action_approvals(message_key);
            CREATE INDEX IF NOT EXISTS idx_mail_action_approvals_source
                ON mail_action_approvals(source_type, source_id);
            DROP INDEX IF EXISTS idx_mail_action_approval_dedupe_pending;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_action_approval_dedupe_pending
                ON mail_action_approvals(
                    source_type,
                    COALESCE(source_id, ''),
                    COALESCE(message_key, ''),
                    proposed_action_type,
                    COALESCE(proposed_target, ''),
                    COALESCE(proposed_value_json, '')
                )
                WHERE status = 'pending';

            CREATE TABLE IF NOT EXISTS imap_capability_cache (
                account_id TEXT NOT NULL,
                folder TEXT NOT NULL,
                uidvalidity TEXT,
                capabilities_json TEXT,
                supports_store_flags INTEGER,
                supports_move INTEGER,
                supports_create_folder INTEGER,
                supports_gmail_labels INTEGER,
                checked_at TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT,
                error TEXT,
                PRIMARY KEY (account_id, folder)
            );

            -- ── Phase 4E.1 future execution chassis ─────────────────
            CREATE TABLE IF NOT EXISTS mail_action_executions (
                execution_id TEXT PRIMARY KEY,
                approval_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                folder TEXT NOT NULL,
                uidvalidity TEXT NOT NULL,
                imap_uid TEXT NOT NULL,
                operation TEXT NOT NULL,
                target TEXT,
                plan_hash TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                before_state_json TEXT,
                after_state_json TEXT,
                rollback_plan_json TEXT,
                rollback_status TEXT,
                requested_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(status IN (
                    'blocked','ready','mock_executed','failed',
                    'rolled_back','rollback_failed'))
            );

            CREATE TABLE IF NOT EXISTS mail_action_execution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_mail_action_exec_approval
                ON mail_action_executions(approval_id);
            CREATE INDEX IF NOT EXISTS idx_mail_action_exec_status
                ON mail_action_executions(status);
            CREATE INDEX IF NOT EXISTS idx_mail_action_exec_idempotency
                ON mail_action_executions(idempotency_key);
            CREATE INDEX IF NOT EXISTS idx_mail_action_exec_created
                ON mail_action_executions(created_at);
            CREATE INDEX IF NOT EXISTS idx_mail_action_exec_events_execution
                ON mail_action_execution_events(execution_id);
            CREATE INDEX IF NOT EXISTS idx_mail_action_exec_events_created
                ON mail_action_execution_events(created_at);
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
        self._add_column_if_missing("mail_ai_queue", "bridge_id", "TEXT")
        self._add_column_if_missing("mail_ai_queue", "sender", "TEXT")
        self._add_column_if_missing("mail_ai_queue", "subject", "TEXT")
        self._add_column_if_missing("mail_ai_queue", "received_at", "TEXT")
        self._add_column_if_missing("mail_ai_queue", "body_text", "TEXT")
        self._add_column_if_missing("mail_ai_trigger_rules", "trigger_id", "TEXT")
        self._add_column_if_missing("mail_ai_trigger_rules", "name", "TEXT")
        self._add_column_if_missing(
            "mail_ai_trigger_rules", "priority", "INTEGER NOT NULL DEFAULT 100")
        self._add_column_if_missing(
            "mail_ai_trigger_rules", "conditions_json", "TEXT")
        self._add_column_if_missing(
            "mail_ai_trigger_rules", "actions_json", "TEXT")
        self._add_column_if_missing(
            "mail_ai_trigger_rules", "cooldown_seconds",
            "INTEGER NOT NULL DEFAULT 3600")
        self._add_column_if_missing(
            "mail_action_approvals", "execution_started_at", "TEXT")
        self._add_column_if_missing(
            "mail_action_approvals", "archived_at", "TEXT")
        with self._connect() as conn:
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_ai_triggers_trigger_id
                    ON mail_ai_trigger_rules(trigger_id)
                    WHERE trigger_id IS NOT NULL
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mail_ai_triggers_priority
                    ON mail_ai_trigger_rules(enabled, priority)
            """)
            conn.commit()

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

    # ── Phase 4B AI queue ──────────────────────────────────────────────────

    def _mail_message_audit_id(self, message: dict) -> str:
        if message.get("message_key"):
            return f"mkey:{message.get('message_key')}"
        if message.get("fallback_message_key"):
            return f"fkey:{message.get('fallback_message_key')}"
        return (
            message.get("message_id")
            or message.get("bridge_id")
            or self._body_hash(message.get("body_text") or "")
        )

    def _body_hash(self, body: str) -> str:
        return hashlib.sha256(str(body or "").encode("utf-8")).hexdigest()

    def enqueue_ai_work(
            self, message: dict, max_body_chars: int = 12000,
            manual_nonce: str | None = None) -> int | None:
        """Create or return a queue row for read-only AI enrichment."""
        now = self._now()
        body_text = str(message.get("body_text") or message.get("snippet") or "")
        body_text = body_text[:max_body_chars]
        body_hash = self._body_hash(body_text)
        account_id = message.get("imap_account") or message.get("account_id")
        folder = message.get("imap_folder") or message.get("folder")
        imap_uid = message.get("imap_uid")
        uidvalidity = message.get("imap_uidvalidity") or message.get("uidvalidity")
        message_id = self._mail_message_audit_id(message)
        bridge_id = message.get("bridge_id")
        sender = message.get("sender_email") or message.get("sender")
        subject = message.get("subject")
        received_at = message.get("date_received") or message.get("received_at")

        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO mail_ai_queue
                    (account_id, message_id, bridge_id, folder, imap_uid,
                     uidvalidity, body_hash, sender, subject, received_at,
                     body_text, status, attempts, next_attempt_at,
                     last_error, created_at, updated_at, manual_nonce)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                        0, NULL, NULL, ?, ?, ?)
            """, (
                account_id, message_id, bridge_id, folder, imap_uid,
                uidvalidity, body_hash, sender, subject, received_at,
                body_text, now, now, manual_nonce,
            ))
            row = conn.execute("""
                SELECT id FROM mail_ai_queue
                WHERE account_id IS ?
                  AND folder IS ?
                  AND uidvalidity IS ?
                  AND imap_uid IS ?
                  AND body_hash = ?
                  AND COALESCE(manual_nonce, '') = COALESCE(?, '')
                ORDER BY id DESC LIMIT 1
            """, (
                account_id, folder, uidvalidity, imap_uid,
                body_hash, manual_nonce,
            )).fetchone()
            conn.commit()
            return int(row[0]) if row else None

    def enqueue_manual_ai_reprocess(
            self, source_row: dict, max_body_chars: int = 12000) -> int:
        message = {
            "message_id": source_row.get("message_id"),
            "bridge_id": source_row.get("bridge_id"),
            "imap_account": source_row.get("account_id"),
            "imap_folder": source_row.get("folder"),
            "imap_uid": source_row.get("imap_uid"),
            "imap_uidvalidity": source_row.get("uidvalidity"),
            "sender_email": source_row.get("sender"),
            "subject": source_row.get("subject"),
            "date_received": source_row.get("received_at"),
            "body_text": source_row.get("body_text") or "",
        }
        queue_id = self.enqueue_ai_work(
            message, max_body_chars=max_body_chars,
            manual_nonce=str(uuid.uuid4()))
        if queue_id is None:
            raise RuntimeError("Failed to create manual AI reprocess item")
        return queue_id

    def claim_next_ai_item(self, max_attempts: int = 3) -> dict | None:
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT id, account_id, message_id, bridge_id, folder,
                       imap_uid, uidvalidity, body_hash, sender, subject,
                       received_at, body_text, status, attempts,
                       manual_nonce
                FROM mail_ai_queue
                WHERE status = 'pending'
                  AND attempts < ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY id ASC
                LIMIT 1
            """, (max_attempts, now)).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute(
                "UPDATE mail_ai_queue SET status = 'running', "
                "updated_at = ?, last_error = NULL WHERE id = ?",
                (now, row[0]),
            )
            conn.commit()
            keys = [
                "id", "account_id", "message_id", "bridge_id", "folder",
                "imap_uid", "uidvalidity", "body_hash", "sender", "subject",
                "received_at", "body_text", "status", "attempts",
                "manual_nonce",
            ]
            return dict(zip(keys, row))

    def complete_ai_item(self, queue_id: int, classification: dict) -> None:
        now = self._now()
        raw_json = json.dumps(classification, sort_keys=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                INSERT INTO mail_ai_classifications
                    (queue_id, category, urgency_score, confidence,
                     summary, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                queue_id,
                classification["category"],
                int(classification["urgency_score"]),
                float(classification["confidence"]),
                classification.get("summary", ""),
                raw_json,
                now,
            ))
            conn.execute(
                "UPDATE mail_ai_queue SET status = 'completed', "
                "updated_at = ?, last_error = NULL WHERE id = ?",
                (now, queue_id),
            )
            conn.commit()
        try:
            self.evaluate_ai_triggers_for_queue(queue_id, classification)
        except Exception as exc:
            self.write_event(
                "ai_trigger_evaluation_failed",
                {"queue_id": queue_id, "error": str(exc)[:500]},
            )

    # ── Phase 4C.3A AI trigger rules ───────────────────────────────────────

    def create_ai_trigger(self, payload: dict) -> dict:
        from .ai_triggers import (
            validate_trigger_actions,
            validate_trigger_conditions,
        )

        now = self._now()
        trigger_id = payload.get("trigger_id") or str(uuid.uuid4())
        conditions = validate_trigger_conditions(payload["conditions_json"])
        actions = validate_trigger_actions(payload["actions_json"])
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO mail_ai_trigger_rules
                    (trigger_id, name, enabled, priority, conditions_json,
                     actions_json, cooldown_seconds, action_type, threshold,
                     value_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'preview_only', 0, NULL, ?, ?)
            """, (
                trigger_id,
                str(payload["name"]).strip(),
                int(bool(payload.get("enabled", True))),
                int(payload.get("priority", 100)),
                json.dumps(conditions, sort_keys=True),
                json.dumps(actions, sort_keys=True),
                int(payload.get("cooldown_seconds", 3600)),
                now,
                now,
            ))
            conn.commit()
        return self.get_ai_trigger(trigger_id)

    def update_ai_trigger(self, trigger_id: str, patch: dict) -> dict | None:
        from .ai_triggers import (
            validate_trigger_actions,
            validate_trigger_conditions,
        )

        existing = self.get_ai_trigger(trigger_id)
        if not existing:
            return None
        updates = dict(patch)
        if "conditions_json" in updates:
            updates["conditions_json"] = json.dumps(
                validate_trigger_conditions(updates["conditions_json"]),
                sort_keys=True,
            )
        if "actions_json" in updates:
            updates["actions_json"] = json.dumps(
                validate_trigger_actions(updates["actions_json"]),
                sort_keys=True,
            )
        if "enabled" in updates:
            updates["enabled"] = int(bool(updates["enabled"]))
        if "priority" in updates:
            updates["priority"] = int(updates["priority"])
        if "cooldown_seconds" in updates:
            updates["cooldown_seconds"] = int(updates["cooldown_seconds"])
        if "name" in updates:
            updates["name"] = str(updates["name"]).strip()
        updates["updated_at"] = self._now()
        allowed = {
            "name", "enabled", "priority", "conditions_json",
            "actions_json", "cooldown_seconds", "updated_at",
        }
        updates = {k: v for k, v in updates.items() if k in allowed}
        with self._connect() as conn:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE mail_ai_trigger_rules SET {set_clause} "
                "WHERE trigger_id = ?",
                [*updates.values(), trigger_id],
            )
            conn.commit()
        return self.get_ai_trigger(trigger_id)

    def delete_ai_trigger(self, trigger_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM mail_ai_trigger_rules WHERE trigger_id = ?",
                (trigger_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_ai_trigger(self, trigger_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT trigger_id, name, enabled, priority, conditions_json,
                       actions_json, cooldown_seconds, created_at, updated_at
                FROM mail_ai_trigger_rules
                WHERE trigger_id = ?
            """, (trigger_id,)).fetchone()
        return self._ai_trigger_from_row(row) if row else None

    def list_ai_triggers(self, enabled_only: bool = False) -> list[dict]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT trigger_id, name, enabled, priority, conditions_json,
                       actions_json, cooldown_seconds, created_at, updated_at
                FROM mail_ai_trigger_rules
                {where}
                  AND trigger_id IS NOT NULL
                  AND conditions_json IS NOT NULL
                ORDER BY priority ASC, trigger_id ASC
            """ if enabled_only else """
                SELECT trigger_id, name, enabled, priority, conditions_json,
                       actions_json, cooldown_seconds, created_at, updated_at
                FROM mail_ai_trigger_rules
                WHERE trigger_id IS NOT NULL
                  AND conditions_json IS NOT NULL
                ORDER BY priority ASC, trigger_id ASC
            """).fetchall()
        return [self._ai_trigger_from_row(row) for row in rows]

    def _ai_trigger_from_row(self, row) -> dict:
        keys = [
            "trigger_id", "name", "enabled", "priority", "conditions_json",
            "actions_json", "cooldown_seconds", "created_at", "updated_at",
        ]
        payload = dict(zip(keys, row))
        payload["enabled"] = bool(payload["enabled"])
        payload["conditions_json"] = json.loads(
            payload["conditions_json"] or '{"match_type":"ALL","conditions":[]}')
        payload["actions_json"] = json.loads(payload["actions_json"] or "[]")
        return payload

    def preview_ai_triggers(
            self, classification: dict,
            *, triggers: list[dict] | None = None) -> list[dict]:
        from .ai_triggers import evaluate_triggers

        return evaluate_triggers(
            triggers if triggers is not None else self.list_ai_triggers(enabled_only=True),
            classification,
        )

    def evaluate_ai_triggers_for_queue(
            self, queue_id: int, classification: dict) -> list[dict]:
        results = self.preview_ai_triggers(classification)
        matched = [r for r in results if r.get("matched")]
        if not matched:
            return results
        with self._connect() as conn:
            row = conn.execute("""
                SELECT message_id, account_id, bridge_id, folder,
                       uidvalidity, imap_uid, sender, subject, received_at
                FROM mail_ai_queue
                WHERE id = ?
            """, (queue_id,)).fetchone()
        message_id = row[0] if row else f"queue:{queue_id}"
        account_id = row[1] if row else None
        bridge_id = row[2] if row else None
        message_meta = {
            "message_key": message_id,
            "account_id": account_id,
            "bridge_id": bridge_id,
            "folder": row[3] if row else None,
            "uidvalidity": row[4] if row else None,
            "imap_uid": row[5] if row else None,
            "sender": row[6] if row else None,
            "subject": row[7] if row else None,
            "received_at": row[8] if row else None,
        }
        now = self._now()
        with self._connect() as conn:
            for result in matched:
                details = {
                    "trigger_id": result["trigger_id"],
                    "trigger_name": result["name"],
                    "message_id": message_id,
                    "queue_id": queue_id,
                    "category": classification.get("category"),
                    "urgency_score": classification.get("urgency_score"),
                    "confidence": classification.get("confidence"),
                    "planned_actions": result["planned_actions"],
                    "matched_conditions": result["matched_conditions"],
                    "dry_run": True,
                    "reason": result["reason"],
                }
                conn.execute("""
                    INSERT INTO mail_processing_events
                        (message_id, account_id, bridge_id, rule_id,
                         action_type, event_type, outcome, details_json,
                         created_at)
                    VALUES (?, ?, ?, NULL, 'ai_trigger',
                            'ai_trigger_matched', 'dry_run', ?, ?)
                """, (
                    message_id,
                    account_id,
                    bridge_id,
                    json.dumps(details, sort_keys=True),
                    now,
                ))
            conn.commit()
        for result in matched:
            for action in result.get("planned_actions", []):
                self.create_action_approval(
                    source_type="ai_trigger",
                    source_id=result["trigger_id"],
                    message=message_meta,
                    action=action,
                    reason=result.get("reason"),
                    classification=classification,
                )
        return results

    def ai_trigger_events_for_message(self, message_id: str) -> list[dict]:
        candidates = [message_id]
        if not message_id.startswith(("mkey:", "fkey:")):
            candidates.extend([f"mkey:{message_id}", f"fkey:{message_id}"])
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, message_id, account_id, bridge_id, action_type,
                       event_type, outcome, details_json, created_at
                FROM mail_processing_events
                WHERE event_type = 'ai_trigger_matched'
                  AND message_id IN ({})
                ORDER BY id ASC
            """.format(",".join("?" for _ in candidates)), candidates).fetchall()
        keys = [
            "id", "message_id", "account_id", "bridge_id", "action_type",
            "event_type", "outcome", "details_json", "created_at",
        ]
        return [
            {
                **dict(zip(keys, row)),
                "details_json": json.loads(row[7]) if row[7] else None,
            }
            for row in rows
        ]

    def fail_ai_item(
            self, queue_id: int, error: str, *,
            retryable: bool = True, max_attempts: int = 3,
            delay_seconds: int = 60) -> None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM mail_ai_queue WHERE id = ?",
                (queue_id,),
            ).fetchone()
            attempts = int(row[0] if row else 0) + 1
            status = (
                "pending"
                if retryable and attempts < max_attempts
                else "failed"
            )
            next_attempt_at = (
                (now_dt + timedelta(seconds=delay_seconds)).isoformat()
                if status == "pending" else None
            )
            conn.execute("""
                UPDATE mail_ai_queue
                SET status = ?, attempts = ?, next_attempt_at = ?,
                    last_error = ?, updated_at = ?
                WHERE id = ?
            """, (
                status, attempts, next_attempt_at,
                str(error)[:1000], now, queue_id,
            ))
            conn.commit()

    # ── Phase 4D.1 operator approvals ─────────────────────────────────────

    def _approval_column_sql(self) -> str:
        return """
            approval_id, source_type, source_id, message_key,
            account_id, folder, uidvalidity, imap_uid, subject,
            sender, received_at, proposed_action_type,
            proposed_target, proposed_value_json, reason,
            ai_category, ai_urgency_score, ai_confidence, status,
            requested_at, decided_at, decided_by, decision_note,
            execution_started_at, executed_at, execution_status,
            execution_result_json, archived_at, created_at, updated_at
        """

    def create_action_approval(
            self, *, source_type: str, source_id: str | None,
            message: dict, action: dict, reason: str | None = None,
            classification: dict | None = None) -> dict:
        now = self._now()
        approval_id = str(uuid.uuid4())
        message_key = (
            message.get("message_key")
            or message.get("message_id")
            or message.get("bridge_id")
        )
        action_type = str(action.get("action_type") or "")
        target = action.get("target")
        proposed_value = action.get("value")
        classification = classification or {}
        payload = {
            "approval_id": approval_id,
            "source_type": source_type,
            "source_id": source_id,
            "message_key": message_key,
            "account_id": message.get("account_id") or message.get("imap_account"),
            "folder": message.get("folder") or message.get("imap_folder"),
            "uidvalidity": (
                message.get("uidvalidity") or message.get("imap_uidvalidity")),
            "imap_uid": message.get("imap_uid"),
            "subject": message.get("subject"),
            "sender": message.get("sender") or message.get("sender_email"),
            "received_at": (
                message.get("received_at") or message.get("date_received")),
            "proposed_action_type": action_type,
            "proposed_target": target,
            "proposed_value_json": (
                json.dumps(proposed_value, sort_keys=True)
                if proposed_value is not None else None),
            "reason": reason or action.get("reason"),
            "ai_category": classification.get("category"),
            "ai_urgency_score": classification.get("urgency_score"),
            "ai_confidence": classification.get("confidence"),
            "status": "pending",
            "requested_at": now,
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO mail_action_approvals
                    (approval_id, source_type, source_id, message_key,
                     account_id, folder, uidvalidity, imap_uid, subject,
                     sender, received_at, proposed_action_type,
                     proposed_target, proposed_value_json, reason,
                     ai_category, ai_urgency_score, ai_confidence, status,
                     requested_at, created_at, updated_at)
                VALUES (:approval_id, :source_type, :source_id, :message_key,
                        :account_id, :folder, :uidvalidity, :imap_uid,
                        :subject, :sender, :received_at,
                        :proposed_action_type, :proposed_target,
                        :proposed_value_json, :reason, :ai_category,
                        :ai_urgency_score, :ai_confidence, :status,
                        :requested_at, :created_at, :updated_at)
            """, payload)
            inserted = conn.execute("SELECT changes()").fetchone()[0] == 1
            row = conn.execute(f"""
                SELECT {self._approval_column_sql()} FROM mail_action_approvals
                WHERE source_type = ?
                  AND COALESCE(source_id, '') = COALESCE(?, '')
                  AND COALESCE(message_key, '') = COALESCE(?, '')
                  AND proposed_action_type = ?
                  AND COALESCE(proposed_target, '') = COALESCE(?, '')
                  AND COALESCE(proposed_value_json, '') = COALESCE(?, '')
                  AND status = 'pending'
                ORDER BY requested_at DESC LIMIT 1
            """, (
                source_type, source_id, message_key, action_type, target,
                payload["proposed_value_json"],
            )).fetchone()
            conn.commit()
        result = self._approval_from_row(row)
        if inserted and result:
            self.write_approval_event(
                result,
                "approval_created",
                "pending",
                {"reason": payload["reason"], "dry_run_preview": True},
            )
        return result

    def expire_pending_approvals(self, expiry_hours: int = 72) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=expiry_hours)
        ).isoformat()
        now = self._now()
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT {self._approval_column_sql()} FROM mail_action_approvals
                WHERE status = 'pending' AND requested_at < ?
            """, (cutoff,)).fetchall()
            for row in rows:
                approval = self._approval_from_row(row)
                conn.execute("""
                    UPDATE mail_action_approvals
                    SET status = 'expired', updated_at = ?
                    WHERE approval_id = ? AND status = 'pending'
                """, (now, approval["approval_id"]))
            conn.commit()
        for row in rows:
            approval = self._approval_from_row(row)
            approval["status"] = "expired"
            self.write_approval_event(
                approval, "approval_expired", "expired",
                {"reason": "approval_expiry_hours elapsed"},
            )
        return len(rows)

    def list_action_approvals(
            self, *, status: str | None = "pending",
            source_type: str | None = None, limit: int = 50,
            offset: int = 0, include_archived: bool = False,
            expiry_hours: int = 72) -> list[dict]:
        self.expire_pending_approvals(expiry_hours)
        clauses = []
        params = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        if not include_archived:
            clauses.append("archived_at IS NULL")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT {self._approval_column_sql()} FROM mail_action_approvals
                {where}
                ORDER BY requested_at DESC
                LIMIT ? OFFSET ?
            """, (*params, int(limit), int(offset))).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def get_action_approval(self, approval_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(f"""
                SELECT {self._approval_column_sql()} FROM mail_action_approvals
                WHERE approval_id = ?
            """, (approval_id,)).fetchone()
        return self._approval_from_row(row) if row else None

    def approve_action_approval(
            self, approval_id: str, *, decided_by: str = "operator",
            decision_note: str | None = None) -> dict:
        return self._decide_action_approval(
            approval_id, "approved", "approval_approved",
            decided_by=decided_by, decision_note=decision_note)

    def reject_action_approval(
            self, approval_id: str, *, decided_by: str = "operator",
            decision_note: str | None = None) -> dict:
        return self._decide_action_approval(
            approval_id, "rejected", "approval_rejected",
            decided_by=decided_by, decision_note=decision_note)

    def expire_action_approval(self, approval_id: str) -> dict:
        approval = self.get_action_approval(approval_id)
        if not approval:
            raise KeyError("Approval not found")
        if approval["status"] != "pending":
            raise ValueError("Only pending approvals can be expired")
        now = self._now()
        with self._connect() as conn:
            conn.execute("""
                UPDATE mail_action_approvals
                SET status = 'expired', updated_at = ?
                WHERE approval_id = ? AND status = 'pending'
            """, (now, approval_id))
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        if changed != 1:
            raise ValueError("Only pending approvals can be expired")
        updated = self.get_action_approval(approval_id)
        self.write_approval_event(
            updated, "approval_expired", "expired", {})
        return updated

    def mark_approval_execution_started(self, approval_id: str) -> dict:
        now = self._now()
        with self._connect() as conn:
            conn.execute("""
                UPDATE mail_action_approvals
                SET execution_status = 'started',
                    execution_started_at = ?,
                    updated_at = ?
                WHERE approval_id = ?
                  AND status = 'approved'
                  AND execution_status IS NULL
                  AND executed_at IS NULL
            """, (now, now, approval_id))
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        approval = self.get_action_approval(approval_id)
        if not approval:
            raise KeyError("Approval not found")
        if changed != 1:
            raise ValueError("Only approved approvals can execute")
        self.write_approval_event(
            approval, "approval_execution_started", "approved", {})
        return approval

    def finish_action_approval_execution(
            self, approval_id: str, *, status: str,
            execution_status: str, result: dict | None = None) -> dict:
        if status not in {"executed", "blocked", "failed"}:
            raise ValueError(f"Invalid execution terminal status: {status}")
        now = self._now()
        with self._connect() as conn:
            conn.execute("""
                UPDATE mail_action_approvals
                SET status = ?, executed_at = ?, execution_status = ?,
                    execution_result_json = ?, updated_at = ?
                WHERE approval_id = ?
                  AND status = 'approved'
                  AND execution_status = 'started'
            """, (
                status,
                now,
                execution_status,
                json.dumps(result or {}, sort_keys=True),
                now,
                approval_id,
            ))
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        if changed != 1:
            raise ValueError("Approval execution is not in progress")
        updated = self.get_action_approval(approval_id)
        event = {
            "executed": "approval_executed",
            "blocked": "approval_blocked",
            "failed": "approval_failed",
        }[status]
        self.write_approval_event(
            updated, event, execution_status, result or {})
        return updated

    def mark_stale_started_approval_failed(
            self, approval_id: str, *, stale_after_minutes: int = 30,
            decided_by: str = "operator",
            reason: str = "Execution started but did not finish") -> dict:
        approval = self.get_action_approval(approval_id)
        if not approval:
            raise KeyError("Approval not found")
        if not self.approval_is_stale_started(
                approval, stale_after_minutes=stale_after_minutes):
            raise ValueError("Only stale started approvals can be marked failed")
        now = self._now()
        result = {
            "error": reason,
            "marked_failed_by": decided_by,
            "stale_after_minutes": stale_after_minutes,
            "execution_started_at": approval.get("execution_started_at"),
            "no_retry_attempted": True,
        }
        with self._connect() as conn:
            conn.execute("""
                UPDATE mail_action_approvals
                SET status = 'failed',
                    executed_at = ?,
                    execution_status = 'failed',
                    execution_result_json = ?,
                    updated_at = ?
                WHERE approval_id = ?
                  AND status = 'approved'
                  AND execution_status = 'started'
            """, (
                now,
                json.dumps(result, sort_keys=True),
                now,
                approval_id,
            ))
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        if changed != 1:
            raise ValueError("Only stale started approvals can be marked failed")
        updated = self.get_action_approval(approval_id)
        self.write_approval_event(
            updated,
            "approval_marked_failed",
            "failed",
            result,
        )
        return updated

    def approval_is_terminal(self, approval: dict) -> bool:
        return approval.get("status") in {
            "executed", "blocked", "failed", "rejected", "expired",
        }

    def archive_action_approval(
            self, approval_id: str, *, decided_by: str = "operator") -> dict:
        approval = self.get_action_approval(approval_id)
        if not approval:
            raise KeyError("Approval not found")
        if not self.approval_is_terminal(approval):
            raise ValueError("Only terminal approvals can be archived")
        if approval.get("execution_status") == "started":
            raise ValueError("Started approvals cannot be archived")
        now = self._now()
        with self._connect() as conn:
            conn.execute("""
                UPDATE mail_action_approvals
                SET archived_at = COALESCE(archived_at, ?), updated_at = ?
                WHERE approval_id = ?
                  AND status IN ('executed','blocked','failed','rejected','expired')
                  AND COALESCE(execution_status, '') != 'started'
            """, (now, now, approval_id))
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        if changed != 1:
            raise ValueError("Only terminal approvals can be archived")
        updated = self.get_action_approval(approval_id)
        self.write_approval_event(
            updated, "approval_archived", "archived",
            {"archived_by": decided_by})
        return updated

    def unarchive_action_approval(
            self, approval_id: str, *, decided_by: str = "operator") -> dict:
        approval = self.get_action_approval(approval_id)
        if not approval:
            raise KeyError("Approval not found")
        if not approval.get("archived_at"):
            return approval
        now = self._now()
        with self._connect() as conn:
            conn.execute("""
                UPDATE mail_action_approvals
                SET archived_at = NULL, updated_at = ?
                WHERE approval_id = ?
            """, (now, approval_id))
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        if changed != 1:
            raise ValueError("Approval could not be unarchived")
        updated = self.get_action_approval(approval_id)
        self.write_approval_event(
            updated, "approval_unarchived", "active",
            {"unarchived_by": decided_by})
        return updated

    def approval_cleanup_candidates(
            self, *, expire_after_hours: int,
            archive_after_days: int, retain_audit_days: int,
            limit_examples: int = 5) -> dict:
        now = datetime.now(timezone.utc)
        expire_cutoff = (now - timedelta(hours=expire_after_hours)).isoformat()
        archive_cutoff = (now - timedelta(days=archive_after_days)).isoformat()
        retain_cutoff = (now - timedelta(days=retain_audit_days)).isoformat()
        terminal = "'executed','blocked','failed','rejected','expired'"
        with self._connect() as conn:
            expire_rows = conn.execute(f"""
                SELECT {self._approval_column_sql()} FROM mail_action_approvals
                WHERE status = 'pending'
                  AND requested_at < ?
                  AND execution_status IS NULL
                ORDER BY requested_at ASC
            """, (expire_cutoff,)).fetchall()
            archive_rows = conn.execute(f"""
                SELECT {self._approval_column_sql()} FROM mail_action_approvals
                WHERE status IN ({terminal})
                  AND archived_at IS NULL
                  AND COALESCE(executed_at, decided_at, requested_at) < ?
                  AND COALESCE(execution_status, '') != 'started'
                ORDER BY COALESCE(executed_at, decided_at, requested_at) ASC
            """, (archive_cutoff,)).fetchall()
            hard_delete_rows = conn.execute(f"""
                SELECT {self._approval_column_sql()} FROM mail_action_approvals
                WHERE status IN ({terminal})
                  AND archived_at IS NOT NULL
                  AND archived_at < ?
                  AND COALESCE(execution_status, '') != 'started'
                ORDER BY archived_at ASC
            """, (retain_cutoff,)).fetchall()
            stuck_count = conn.execute("""
                SELECT COUNT(*) FROM mail_action_approvals
                WHERE status = 'approved' AND execution_status = 'started'
            """).fetchone()[0]
        expire = [self._approval_from_row(row) for row in expire_rows]
        archive = [self._approval_from_row(row) for row in archive_rows]
        hard_delete = [self._approval_from_row(row) for row in hard_delete_rows]
        return {
            "expire_pending": expire,
            "archive_terminal": archive,
            "hard_delete": hard_delete,
            "stuck_or_started_count": int(stuck_count),
            "examples": {
                "expire_pending": expire[:limit_examples],
                "archive_terminal": archive[:limit_examples],
                "hard_delete": hard_delete[:limit_examples],
            },
        }

    def cleanup_action_approvals(
            self, *, expire_after_hours: int, archive_after_days: int,
            retain_audit_days: int, hard_delete: bool = False) -> dict:
        candidates = self.approval_cleanup_candidates(
            expire_after_hours=expire_after_hours,
            archive_after_days=archive_after_days,
            retain_audit_days=retain_audit_days,
        )
        now = self._now()
        expired_ids = [a["approval_id"] for a in candidates["expire_pending"]]
        archived_ids = [a["approval_id"] for a in candidates["archive_terminal"]]
        hard_deleted_ids = (
            [a["approval_id"] for a in candidates["hard_delete"]]
            if hard_delete else []
        )
        with self._connect() as conn:
            for approval_id in expired_ids:
                conn.execute("""
                    UPDATE mail_action_approvals
                    SET status = 'expired', updated_at = ?
                    WHERE approval_id = ?
                      AND status = 'pending'
                      AND execution_status IS NULL
                """, (now, approval_id))
            for approval_id in archived_ids:
                conn.execute("""
                    UPDATE mail_action_approvals
                    SET archived_at = COALESCE(archived_at, ?), updated_at = ?
                    WHERE approval_id = ?
                      AND status IN ('executed','blocked','failed','rejected','expired')
                      AND COALESCE(execution_status, '') != 'started'
                """, (now, now, approval_id))
            if hard_delete:
                for approval_id in hard_deleted_ids:
                    conn.execute("""
                        DELETE FROM mail_action_approvals
                        WHERE approval_id = ?
                          AND status IN ('executed','blocked','failed','rejected','expired')
                          AND archived_at IS NOT NULL
                          AND COALESCE(execution_status, '') != 'started'
                    """, (approval_id,))
            conn.commit()
        for approval in candidates["expire_pending"]:
            approval["status"] = "expired"
            self.write_approval_event(
                approval, "approval_cleanup_expired", "expired",
                {"cleanup": True, "auto_expire_pending_after_hours": expire_after_hours})
        for approval in candidates["archive_terminal"]:
            approval["archived_at"] = now
            self.write_approval_event(
                approval, "approval_cleanup_archived", "archived",
                {"cleanup": True, "archive_terminal_after_days": archive_after_days})
        return {
            "expired_ids": expired_ids,
            "archived_ids": archived_ids,
            "hard_deleted_ids": hard_deleted_ids,
            "stuck_or_started_count": candidates["stuck_or_started_count"],
        }

    def approval_is_stale_started(
            self, approval: dict, *, stale_after_minutes: int = 30) -> bool:
        if approval.get("status") != "approved":
            return False
        if approval.get("execution_status") != "started":
            return False
        started_at = approval.get("execution_started_at")
        if not started_at:
            return False
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return False
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - started
        return age >= timedelta(minutes=stale_after_minutes)

    def approval_events(self, approval: dict) -> list[dict]:
        approval_id = approval.get("approval_id")
        message_key = approval.get("message_key")
        source_id = approval.get("source_id")
        params = [
            f'%"approval_id": "{approval_id}"%',
            message_key or approval_id,
        ]
        clauses = [
            "details_json LIKE ?",
            "message_id = ?",
        ]
        if message_key and source_id:
            clauses.append(
                "(event_type = 'ai_trigger_matched' AND message_id = ? "
                "AND details_json LIKE ?)"
            )
            params.extend([message_key, f'%"trigger_id": "{source_id}"%'])
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT id, message_id, account_id, bridge_id, rule_id,
                       action_type, event_type, outcome, details_json,
                       created_at
                FROM mail_processing_events
                WHERE {" OR ".join(clauses)}
                ORDER BY id ASC
            """, params).fetchall()
        keys = [
            "id", "message_id", "account_id", "bridge_id", "rule_id",
            "action_type", "event_type", "outcome", "details_json",
            "created_at",
        ]
        events = []
        seen = set()
        for row in rows:
            event = dict(zip(keys, row))
            if event["id"] in seen:
                continue
            seen.add(event["id"])
            details = json.loads(event["details_json"] or "{}")
            event["details"] = details
            event.pop("details_json", None)
            events.append(event)
        return events

    def write_approval_event(
            self, approval: dict, event_type: str, outcome: str,
            details: dict | None = None) -> None:
        details = {
            **(details or {}),
            "approval_id": approval.get("approval_id"),
            "source_type": approval.get("source_type"),
            "source_id": approval.get("source_id"),
            "message_key": approval.get("message_key"),
            "proposed_action_type": approval.get("proposed_action_type"),
            "proposed_target": approval.get("proposed_target"),
            "status": approval.get("status"),
            "execution_status": approval.get("execution_status"),
            "operator": approval.get("decided_by"),
        }
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO mail_processing_events
                    (message_id, account_id, bridge_id, rule_id, action_type,
                     event_type, outcome, details_json, created_at)
                VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?)
            """, (
                approval.get("message_key") or approval.get("approval_id"),
                approval.get("account_id"),
                approval.get("proposed_action_type"),
                event_type,
                outcome,
                json.dumps(details, sort_keys=True),
                self._now(),
            ))
            conn.commit()

    # ── Phase 4E.1 mock execution audit rows ───────────────────────────────

    def get_action_execution_by_idempotency(
            self, idempotency_key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT execution_id, approval_id, account_id, folder,
                       uidvalidity, imap_uid, operation, target, plan_hash,
                       idempotency_key, status, before_state_json,
                       after_state_json, rollback_plan_json, rollback_status,
                       requested_at, started_at, finished_at, error_message,
                       created_at, updated_at
                FROM mail_action_executions
                WHERE idempotency_key = ?
            """, (idempotency_key,)).fetchone()
        return self._action_execution_from_row(row) if row else None

    def list_action_execution_events(
            self, execution_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, execution_id, event_type, event_json, created_at
                FROM mail_action_execution_events
                WHERE execution_id = ?
                ORDER BY id ASC
            """, (execution_id,)).fetchall()
        events = []
        for row in rows:
            event_json = json.loads(row[3] or "{}")
            events.append({
                "id": row[0],
                "execution_id": row[1],
                "event_type": row[2],
                "event_json": event_json,
                "created_at": row[4],
            })
        return events

    def insert_mock_action_execution(
            self, *, execution_id: str, approval_id: str, account_id: str,
            folder: str, uidvalidity: str, imap_uid: str, operation: str,
            target: str | None, plan_hash: str, idempotency_key: str,
            before_state: dict | None, after_state: dict | None,
            rollback_plan: dict | None, requested_at: str | None = None) -> dict:
        now = self._now()
        requested = requested_at or now
        before_json = (
            json.dumps(before_state, sort_keys=True)
            if before_state is not None else None)
        after_json = (
            json.dumps(after_state, sort_keys=True)
            if after_state is not None else None)
        rollback_json = (
            json.dumps(rollback_plan, sort_keys=True)
            if rollback_plan is not None else None)
        event_payload = {
            "approval_id": approval_id,
            "account_id": account_id,
            "folder": folder,
            "uidvalidity": uidvalidity,
            "imap_uid": imap_uid,
            "operation": operation,
            "target": target,
            "plan_hash": plan_hash,
            "idempotency_key": idempotency_key,
            "mailbox_mutation_occurred": False,
            "mock_only": True,
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                INSERT OR IGNORE INTO mail_action_executions
                    (execution_id, approval_id, account_id, folder,
                     uidvalidity, imap_uid, operation, target, plan_hash,
                     idempotency_key, status, before_state_json,
                     after_state_json, rollback_plan_json, rollback_status,
                     requested_at, started_at, finished_at, error_message,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'mock_executed',
                        ?, ?, ?, 'not_requested', ?, ?, ?, NULL, ?, ?)
            """, (
                execution_id, approval_id, account_id, folder,
                uidvalidity, imap_uid, operation, target, plan_hash,
                idempotency_key, before_json, after_json, rollback_json,
                requested, now, now, now, now,
            ))
            inserted = conn.execute("SELECT changes()").fetchone()[0] == 1
            if inserted:
                conn.execute("""
                    INSERT INTO mail_action_execution_events
                        (execution_id, event_type, event_json, created_at)
                    VALUES (?, 'mock_executed', ?, ?)
                """, (
                    execution_id,
                    json.dumps(event_payload, sort_keys=True),
                    now,
                ))
            row = conn.execute("""
                SELECT execution_id, approval_id, account_id, folder,
                       uidvalidity, imap_uid, operation, target, plan_hash,
                       idempotency_key, status, before_state_json,
                       after_state_json, rollback_plan_json, rollback_status,
                       requested_at, started_at, finished_at, error_message,
                       created_at, updated_at
                FROM mail_action_executions
                WHERE idempotency_key = ?
            """, (idempotency_key,)).fetchone()
            conn.commit()
        result = self._action_execution_from_row(row)
        result["inserted"] = inserted
        return result

    def insert_blocked_action_execution(
            self, *, execution_id: str, approval_id: str, account_id: str,
            folder: str, uidvalidity: str, imap_uid: str, operation: str,
            target: str | None, plan_hash: str, idempotency_key: str,
            error_message: str | None, event_type: str,
            event_payload: dict | None = None) -> dict:
        now = self._now()
        payload = event_payload or {}
        payload.update({
            "approval_id": approval_id,
            "account_id": account_id,
            "folder": folder,
            "uidvalidity": uidvalidity,
            "imap_uid": imap_uid,
            "operation": operation,
            "target": target,
            "plan_hash": plan_hash,
            "idempotency_key": idempotency_key,
            "mailbox_mutation_occurred": False,
            "mock_only": True,
        })
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                INSERT OR IGNORE INTO mail_action_executions
                    (execution_id, approval_id, account_id, folder,
                     uidvalidity, imap_uid, operation, target, plan_hash,
                     idempotency_key, status, before_state_json,
                     after_state_json, rollback_plan_json, rollback_status,
                     requested_at, started_at, finished_at, error_message,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'blocked',
                        NULL, NULL, NULL, NULL, ?, NULL, ?, ?, ?, ?)
            """, (
                execution_id, approval_id, account_id, folder,
                uidvalidity, imap_uid, operation, target, plan_hash,
                idempotency_key, now, now,
                error_message[:500] if error_message else None, now, now,
            ))
            inserted = conn.execute("SELECT changes()").fetchone()[0] == 1
            if inserted:
                conn.execute("""
                    INSERT INTO mail_action_execution_events
                        (execution_id, event_type, event_json, created_at)
                    VALUES (?, ?, ?, ?)
                """, (
                    execution_id,
                    event_type,
                    json.dumps(payload, sort_keys=True),
                    now,
                ))
            row = conn.execute("""
                SELECT execution_id, approval_id, account_id, folder,
                       uidvalidity, imap_uid, operation, target, plan_hash,
                       idempotency_key, status, before_state_json,
                       after_state_json, rollback_plan_json, rollback_status,
                       requested_at, started_at, finished_at, error_message,
                       created_at, updated_at
                FROM mail_action_executions
                WHERE idempotency_key = ?
            """, (idempotency_key,)).fetchone()
            conn.commit()
        result = self._action_execution_from_row(row)
        result["inserted"] = inserted
        return result

    def write_action_execution_event(
            self, execution_id: str, event_type: str,
            event_payload: dict | None = None) -> int:
        now = self._now()
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO mail_action_execution_events
                    (execution_id, event_type, event_json, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                execution_id,
                event_type,
                json.dumps(event_payload or {}, sort_keys=True),
                now,
            ))
            conn.commit()
            return int(cur.lastrowid)

    def _action_execution_from_row(self, row) -> dict:
        keys = [
            "execution_id", "approval_id", "account_id", "folder",
            "uidvalidity", "imap_uid", "operation", "target", "plan_hash",
            "idempotency_key", "status", "before_state_json",
            "after_state_json", "rollback_plan_json", "rollback_status",
            "requested_at", "started_at", "finished_at", "error_message",
            "created_at", "updated_at",
        ]
        payload = dict(zip(keys, row))
        for json_key, out_key in (
                ("before_state_json", "before_state"),
                ("after_state_json", "after_state"),
                ("rollback_plan_json", "rollback_plan")):
            payload[out_key] = (
                json.loads(payload[json_key])
                if payload.get(json_key) else None)
        return payload

    def _decide_action_approval(
            self, approval_id: str, status: str, event_type: str, *,
            decided_by: str, decision_note: str | None) -> dict:
        approval = self.get_action_approval(approval_id)
        if not approval:
            raise KeyError("Approval not found")
        if approval["status"] != "pending":
            raise ValueError("Only pending approvals can be decided")
        now = self._now()
        with self._connect() as conn:
            conn.execute("""
                UPDATE mail_action_approvals
                SET status = ?, decided_at = ?, decided_by = ?,
                    decision_note = ?, updated_at = ?
                WHERE approval_id = ? AND status = 'pending'
            """, (
                status, now, decided_by, decision_note, now, approval_id,
            ))
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
        if changed != 1:
            raise ValueError("Only pending approvals can be decided")
        updated = self.get_action_approval(approval_id)
        self.write_approval_event(
            updated, event_type, status,
            {"decision_note": decision_note})
        return updated

    def _approval_from_row(self, row) -> dict:
        keys = [
            "approval_id", "source_type", "source_id", "message_key",
            "account_id", "folder", "uidvalidity", "imap_uid", "subject",
            "sender", "received_at", "proposed_action_type",
            "proposed_target", "proposed_value_json", "reason",
            "ai_category", "ai_urgency_score", "ai_confidence", "status",
	            "requested_at", "decided_at", "decided_by", "decision_note",
	            "execution_started_at", "executed_at", "execution_status",
	            "execution_result_json", "archived_at", "created_at", "updated_at",
	        ]
        payload = dict(zip(keys, row))
        payload["proposed_value"] = (
            json.loads(payload["proposed_value_json"])
            if payload.get("proposed_value_json") else None)
        payload["execution_result"] = (
            json.loads(payload["execution_result_json"])
            if payload.get("execution_result_json") else None)
        return payload

    def find_ai_reprocess_source(self, message_id: str) -> dict | None:
        candidates = [message_id]
        if not message_id.startswith(("mkey:", "fkey:")):
            candidates.extend([f"mkey:{message_id}", f"fkey:{message_id}"])
        with self._connect() as conn:
            row = conn.execute("""
                SELECT q.account_id, q.message_id, q.bridge_id, q.folder,
                       q.imap_uid, q.uidvalidity, q.sender, q.subject,
                       q.received_at, q.body_text
                FROM mail_ai_queue q
                WHERE q.message_id IN ({})
                   OR q.bridge_id = ?
                ORDER BY q.id DESC LIMIT 1
            """.format(",".join("?" for _ in candidates)),
                (*candidates, message_id)).fetchone()
            if row:
                keys = [
                    "account_id", "message_id", "bridge_id", "folder",
                    "imap_uid", "uidvalidity", "sender", "subject",
                    "received_at", "body_text",
                ]
                return dict(zip(keys, row))

            row = conn.execute("""
                SELECT bridge_id, message_id, summary
                FROM processed_messages
                WHERE message_id IN ({})
                   OR bridge_id = ?
                ORDER BY processed_at DESC LIMIT 1
            """.format(",".join("?" for _ in candidates)),
                (*candidates, message_id)).fetchone()
            if row and row[2]:
                return {
                    "bridge_id": row[0],
                    "message_id": row[1],
                    "body_text": row[2],
                }
            return None

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

    def get_imap_capability_cache(
            self, account_id: str, folder: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT account_id, folder, uidvalidity, capabilities_json,
                       supports_store_flags, supports_move,
                       supports_create_folder, supports_gmail_labels,
                       checked_at, source, status, error
                FROM imap_capability_cache
                WHERE account_id = ? AND folder = ?
            """, (account_id, folder)).fetchone()
        if not row:
            return None
        try:
            capabilities = json.loads(row[3] or "[]")
        except json.JSONDecodeError:
            capabilities = []
        return {
            "account_id": row[0],
            "folder": row[1],
            "uidvalidity": row[2],
            "capabilities": capabilities,
            "supports_store_flags": _nullable_bool(row[4]),
            "supports_move": _nullable_bool(row[5]),
            "supports_create_folder": _nullable_bool(row[6]),
            "supports_gmail_labels": _nullable_bool(row[7]),
            "checked_at": row[8],
            "source": row[9],
            "status": row[10],
            "error": row[11],
        }

    def upsert_imap_capability_cache(
            self, *, account_id: str, folder: str,
            uidvalidity=None, capabilities=None,
            supports_store_flags=None, supports_move=None,
            supports_create_folder=None, supports_gmail_labels=None,
            source: str = "cached", status: str | None = "ok",
            error: str | None = None) -> dict:
        now = self._now()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO imap_capability_cache
                    (account_id, folder, uidvalidity, capabilities_json,
                     supports_store_flags, supports_move,
                     supports_create_folder, supports_gmail_labels,
                     checked_at, source, status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, folder) DO UPDATE
                    SET uidvalidity = excluded.uidvalidity,
                        capabilities_json = excluded.capabilities_json,
                        supports_store_flags = excluded.supports_store_flags,
                        supports_move = excluded.supports_move,
                        supports_create_folder = excluded.supports_create_folder,
                        supports_gmail_labels = excluded.supports_gmail_labels,
                        checked_at = excluded.checked_at,
                        source = excluded.source,
                        status = excluded.status,
                        error = excluded.error
            """, (
                account_id,
                folder,
                None if uidvalidity is None else str(uidvalidity),
                json.dumps(capabilities or [], sort_keys=True),
                _bool_or_none(supports_store_flags),
                _bool_or_none(supports_move),
                _bool_or_none(supports_create_folder),
                _bool_or_none(supports_gmail_labels),
                now,
                source,
                status,
                error[:500] if error else None,
            ))
            conn.commit()
        return self.get_imap_capability_cache(account_id, folder)

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
