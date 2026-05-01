# Mail-Agent Preflight Report

**Generated:** 2026-05-01T08:45:19Z  
**Repo:** `/Users/g4ndr1k/agentic-ai`  
**Python:** 3.14.4  


## Docker — docker-compose.yml

**File:** `/Users/g4ndr1k/agentic-ai/docker-compose.yml`

**Services found:** finance-api, mail-agent

### Service: `finance-api`
- **restart:** `unless-stopped`
- **healthcheck:** interval=30s timeout=10s retries=3 start_period=20s
- **volumes (7):**
  - `./config/settings.toml:/app/config/settings.toml:rw`
  - `./data:/app/data`
  - `./agent:/app/agent`
  - `./output/xls:/app/output/xls:ro`
  - `./secrets/bridge.token:/run/secrets/bridge.token:ro`
  - `./household-expense/secrets/household_api.key:/run/secrets/household_api.key:ro`
  - `./secrets/nas_sync_key:/run/secrets/nas_sync_key:ro`

### Service: `mail-agent`
- **restart:** `unless-stopped`
- **healthcheck:** interval=30s timeout=10s retries=3 start_period=30s
- **volumes (4):**
  - `./config/settings.toml:/app/config/settings.toml:rw`
  - `./data:/app/data`
  - `./secrets:/app/secrets:ro`
  - `./secrets/bridge.token:/run/secrets/bridge.token:ro  # file, not directory`



## FastAPI — finance/api.py

- **Entrypoint module:** `finance.server` → `finance/server.py`
- **Bound host:port:** `0.0.0.0:8090`

**Routes found (112 total):**
  - `/api/accounts`
  - `/api/ai/query`
  - `/api/alias`
  - `/api/audit/completeness`
  - `/api/backfill-aliases`
  - `/api/backups/manual`
  - `/api/backups/status`
  - `/api/categories`
  - `/api/coretax/auto-reconcile`
  - `/api/coretax/components/history`
  - `/api/coretax/export`
  - `/api/coretax/export/{file_id}/audit`
  - `/api/coretax/export/{file_id}/download`
  - `/api/coretax/exports`
  - `/api/coretax/import/prior-year`
  - `/api/coretax/import/staging/{batch_id}`
  - `/api/coretax/import/staging/{batch_id}/commit`
  - `/api/coretax/import/staging/{batch_id}/rows/{row_id}`
  - `/api/coretax/mappings`
  - `/api/coretax/mappings/lifecycle`
  - `/api/coretax/mappings/{mapping_id}`
  - `/api/coretax/mappings/{mapping_id}/confirm`
  - `/api/coretax/reconcile-runs`
  - `/api/coretax/reset-from-rules`
  - `/api/coretax/rows`
  - `/api/coretax/rows/{row_id}`
  - `/api/coretax/rows/{row_id}/lock`
  - `/api/coretax/rows/{row_id}/unlock`
  - `/api/coretax/summary`
  - `/api/coretax/unmatched`
  - `/api/coretax/{year}/mappings/assign`
  - `/api/coretax/{year}/mappings/grouped`
  - `/api/coretax/{year}/mappings/rename-candidates`
  - `/api/coretax/{year}/mappings/stale`
  - `/api/coretax/{year}/mappings/suggest`
  - `/api/coretax/{year}/mappings/suggest/preview`
  - `/api/coretax/{year}/mappings/suggest/reject`
  - `/api/coretax/{year}/reconcile/runs/{run_id}/diff`
  - `/api/coretax/{year}/rows/{stable_key}/components`
  - `/api/coretax/{year}/unmapped-pwm`
  - `/api/health`
  - `/api/household/cash-pools/{pool_id}`
  - `/api/household/categories`
  - `/api/household/categories/{code}`
  - `/api/household/settings`
  - `/api/household/transaction/{txn_id}/category`
  - `/api/import`
  - `/api/mail-rules`
  - `/api/mail-rules/{rule_id}`
  - `/api/matching/invariant-log`
  - `/api/matching/shadow-diffs`
  - `/api/matching/stats`
  - `/api/matching/{domain}/mappings`
  - `/api/matching/{domain}/mappings/{mapping_id}`
  - `/api/matching/{domain}/mappings/{mapping_id}/confirm`
  - `/api/nas-sync`
  - `/api/nas-sync/status`
  - `/api/owners`
  - `/api/pdf/local-files`
  - `/api/pdf/local-status/{job_id}`
  - `/api/pdf/local-workspace`
  - `/api/pdf/preflight`
  - `/api/pdf/process-local`
  - `/api/pipeline/run`
  - `/api/pipeline/status`
  - `/api/preferences`
  - `/api/reports/financial-statement`
  - `/api/review-queue`
  - `/api/review-queue/suggest`
  - `/api/summary/year/{year}`
  - `/api/summary/years`
  - `/api/summary/{year}/{month}`
  - `/api/summary/{year}/{month}/explanation`
  - `/api/summary/{year}/{month}/explanation/query`
  - `/api/sync`
  - `/api/transaction/{tx_hash}/category`
  - `/api/transactions`
  - `/api/transactions/foreign`
  - `/api/wealth/balances`
  - `/api/wealth/balances/{balance_id}`
  - `/api/wealth/explanation`
  - `/api/wealth/explanation/query`
  - `/api/wealth/history`
  - `/api/wealth/holdings`
  - `/api/wealth/holdings/rollover`
  - `/api/wealth/holdings/{holding_id}`
  - `/api/wealth/liabilities`
  - `/api/wealth/liabilities/{liability_id}`
  - `/api/wealth/snapshot`
  - `/api/wealth/snapshot/dates`
  - `/api/wealth/summary`
  - `/manifest.webmanifest`
  - `/ping`
  - `/registerSW.js`
  - `/sw.js`
  - `/{full_path:path}`
  - `http`

✅ No /api/mail/* route collisions detected


## Bridge — bridge/server.py + launchd plist

**Declared routes (source scan):**
  - `/alerts/send`
  - `/commands/ack`
  - `/commands/pending`
  - `/health`
  - `/healthz`
  - `/mail/ack`
  - `/mail/pending`
  - `/mail/schema`
  - `/pdf/jobs`
  - `/pdf/preflight`
  - `/pdf/process-file`
  - `/pdf/status/`
  - `/pdf/unlock`
  - `/pipeline/run`
  - `/pipeline/status`

**Plist:** `/Users/g4ndr1k/agentic-ai/launchd/com.agentic.bridge.plist`
  - WorkingDirectory: `/Users/g4ndr1k/agentic-ai` → exists
  - StandardOutPath: `/Users/g4ndr1k/agentic-ai/logs/bridge-launchd.log` → parent dir exists
  - StandardErrorPath: `/Users/g4ndr1k/agentic-ai/logs/bridge-launchd-err.log`
  - KeepAlive: dict (SuccessfulExit=false, Crashed=true)
    ✅ KeepAlive policy is hardened
  - ✅ ThrottleInterval=30s

**Live probe:** GET http://127.0.0.1:9100/health
✅ Bridge responded: {"http": "ok", "applescript": "ok", "messages_app": "fail", "chat_db": "fail", "overall": "degraded", "service": "bridge", "mail_available": true, "timestamp": "2026-05-01T08:45:20.897084+00:00"}


## SQLite — agent state + pdf_jobs.db

**Tables declared in `agent/app/state.py`:**
  - `processed_messages`
  - `processed_commands`
  - `alerts`
  - `agent_flags`
  - `command_log`
  - `imap_accounts`
  - `imap_folder_state`
  - `pdf_attachments`
  - `agent_events`
  - `mail_rules`
  - `mail_rule_conditions`
  - `mail_rule_actions`
  - `mail_needs_reply`
  - `mail_ai_queue`
  - `mail_ai_classifications`
  - `mail_ai_categories`
  - `mail_ai_trigger_rules`
  - `mail_processing_events`
  - `mail_action_approvals`
  - `imap_capability_cache`
  - `mail_action_executions`
  - `mail_action_execution_events`

**Live agent DB:** `/Users/g4ndr1k/agentic-ai/data/agent.db`

  **`processed_messages`** columns:
  - `bridge_id` TEXT PK
  - `message_id` TEXT
  - `processed_at` TEXT
  - `category` TEXT
  - `urgency` TEXT
  - `provider` TEXT
  - `alert_sent` INTEGER
  - `summary` TEXT
  - `status` TEXT
  - `source` TEXT
  → 406 rows

  **`alerts`** columns:
  - `id` INTEGER PK
  - `bridge_id` TEXT
  - `sent_at` TEXT
  - `category` TEXT
  - `recipient` TEXT
  - `alert_text` TEXT
  - `success` INTEGER
  → 0 rows

  **`agent_flags`** columns:
  - `key` TEXT PK
  - `value` TEXT NOT NULL
  → 1 rows

**pdf_jobs.db:** `/Users/g4ndr1k/agentic-ai/data/pdf_jobs.db`
  Tables: ['pdf_jobs']
  - `pdf_jobs`: 397 rows
  - `job_id` TEXT PK
  - `created_at` TEXT NOT NULL
  - `status` TEXT NOT NULL
  - `source_path` TEXT
  - `bank` TEXT
  - `stmt_type` TEXT
  - `period` TEXT
  - `output_path` TEXT
  - `error` TEXT
  - `log` TEXT


## Config — config/settings.toml

### `[bridge]`
  - `host` = '0.0.0.0'
  - `port` = 9100
  - `log_level` = 'INFO'

### `[mail]`
  - `source` = 'gmail'
  - `gmail_secrets_file` = ***
  - `max_batch` = 25
  - `max_body_text_bytes` = 200000
  - `initial_lookback_days` = 15
  - `ai` = {'enabled': True, 'provider': 'ollama', 'base_url': 'http://host.docker.internal:11434', 'model': 'gemma3:4b', 'temperature': 0.1, 'timeout_seconds': 45, 'max_body_chars': 12000, 'urgency_threshold': 8}
  - `rule_ai` = {'enabled': True, 'provider': 'ollama', 'base_url': 'http://host.docker.internal:11434', 'model': 'qwen2.5:7b-instruct-q4_K_M', 'timeout_seconds': 120, 'temperature': 0.0, 'max_request_chars': 1000}
  - `imap_mutations` = {'enabled': False, 'dry_run_default': True, 'allow_mark_read': False, 'allow_mark_unread': False, 'allow_add_label': False, 'allow_move_to_folder': False, 'require_uidvalidity_match': True, 'require_capability_cache': True, 'allow_create_folder': False, 'allow_copy_delete_fallback': False}
  - `approvals` = {'enabled': True, 'require_approval_for_ai_actions': True, 'approval_expiry_hours': 72, 'default_expiry_minutes': 1440, 'started_stale_after_minutes': 30, 'allow_bulk_approve': False, 'auto_expire_pending_after_hours': 24, 'archive_terminal_after_days': 30, 'retain_audit_days': 365, 'cleanup_enabled': False}
  - `imap` = {'accounts': [{'email': 'g4ndr1k@gmail.com', 'provider': 'gmail', 'id': 'gmail_g4ndr1k', 'name': 'g4ndr1k', 'host': 'imap.gmail.com', 'port': 993, 'ssl': True, 'auth_type': 'app_password', 'folders': ['INBOX'], 'lookback_days': 14, 'max_message_mb': 25, 'max_attachment_mb': 20, 'enabled': True, 'auth_source': 'keychain', 'keychain_service': 'agentic-ai-mail-imap'}, {'email': 'dianpratiwi@gmail.com', 'provider': 'gmail', 'id': 'gmail_dianpratiwi', 'name': 'Dian Pratiwi', 'host': 'imap.gmail.com', 'port': 993, 'ssl': True, 'auth_type': 'app_password', 'folders': ['INBOX'], 'lookback_days': 14, 'max_message_mb': 25, 'max_attachment_mb': 20, 'enabled': True, 'auth_source': 'keychain', 'keychain_service': 'agentic-ai-mail-imap'}], 'max_message_mb': 25, 'max_attachment_mb': 20}

### `[imessage]`
  - `primary_recipient` = 'emanuel.adrianto@icloud.com'
  - `authorized_senders` = ['emanuel.adrianto@icloud.com']
  - `command_prefix` = 'agent:'
  - `max_alerts_per_hour` = 60
  - `max_commands_per_hour` = 60
  - `startup_notifications` = True
  - `shutdown_notifications` = False
  - `allow_same_account_commands` = True

### `[classifier]`
  - `provider_order` = ['rule_based']
  - `cloud_fallback_enabled` = False
  - `generic_alert_on_total_failure` = True
  - `finance_api_url` = ''
  - `rule_reload_interval_seconds` = 3600
  - `allowed_sender_domains` = ['maybank.co.id', 'cimbniaga.co.id', 'permatabank.co.id', 'bca.co.id', 'klikbca.com']

### `[agent]`
  - `poll_interval_seconds` = 1800
  - `command_poll_interval_seconds` = 30
  - `alert_on_categories` = ['transaction_alert', 'bill_statement', 'bank_clarification', 'payment_due', 'security_alert', 'financial_other']
  - `mode` = 'draft_only'
  - `safe_default` = 'draft_only'

### `[ollama]`
  - `host` = 'http://host.docker.internal:11434'
  - `model_primary` = 'gemma3:4b'
  - `timeout_seconds` = 60

### `[pdf]`
  - `inbox_dir` = '/Users/g4ndr1k/agentic-ai/data/pdf_inbox'
  - `unlocked_dir` = '/Users/g4ndr1k/agentic-ai/data/pdf_unlocked'
  - `xls_output_dir` = '/Users/g4ndr1k/agentic-ai/output/xls'
  - `bank_passwords_file` = ***
  - `bank_passwords_source` = ***
  - `jobs_db` = '/Users/g4ndr1k/agentic-ai/data/pdf_jobs.db'
  - `attachment_seen_db` = '/Users/g4ndr1k/agentic-ai/data/seen_attachments.db'
  - `attachment_lookback_days` = 60
  - `parser_llm_model` = 'gemma3:4b'
  - `verify_enabled` = True
  - `verify_mode` = 'warn'
  - `verify_ollama_host` = 'http://localhost:11434'
  - `verify_timeout_seconds` = 120
  - `verify_model` = 'gemma3:4b'

### Phase 4F.1b rule AI probe
⚠️  mail.rule_ai.enabled=true
  - `provider` = 'ollama'
  - `model` = 'qwen2.5:7b-instruct-q4_K_M'
  - `base_url` = 'http://host.docker.internal:11434'
  - `timeout_seconds` = 120

### Phase 4E.2 execution safety
✅ Final read-only verification module present
✅ Mock execution module present
✅ mail.imap_mutations.enabled=false
✅ dry_run_default=true
✅ allow_mark_read=false
✅ allow_mark_unread=false
✅ allow_add_label=false
✅ allow_move_to_folder=false
✅ Preflight performs no mailbox mutation



## Filesystem

**NAS mailagent mount:** `/Volumes/Synology/mailagent`
⚠️  /Volumes/Synology not mounted

**secrets/banks.toml:** `/Users/g4ndr1k/agentic-ai/secrets/banks.toml`
✅ Readable
