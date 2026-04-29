# Mail-Agent Preflight Report

**Generated:** 2026-04-29T00:30:08Z  
**Repo:** `/Users/g4ndr1k/agentic-ai`  
**Python:** 3.14.4  


## Docker — docker-compose.yml

**File:** `/Users/g4ndr1k/agentic-ai/docker-compose.yml`

**Services found:** finance-api, mail-agent

### Service: `finance-api`
- **restart:** `unless-stopped`
- **healthcheck:** interval=30s timeout=10s retries=3 start_period=20s
- **volumes (6):**
  - `./config/settings.toml:/app/config/settings.toml:ro`
  - `./data:/app/data`
  - `./output/xls:/app/output/xls:ro`
  - `./secrets/bridge.token:/run/secrets/bridge.token:ro`
  - `./household-expense/secrets/household_api.key:/run/secrets/household_api.key:ro`
  - `./secrets/nas_sync_key:/run/secrets/nas_sync_key:ro`

### Service: `mail-agent`
- **restart:** `unless-stopped`
- **healthcheck:** interval=30s timeout=10s retries=3 start_period=30s
- **volumes (4):**
  - `./config/settings.toml:/app/config/settings.toml:ro`
  - `./data:/app/data`
  - `./secrets/bridge.token:/run/secrets/bridge.token:ro  # file, not directory`
  - `/Volumes/Synology/mailagent:/mnt/mailagent`



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
⚠️  Bridge not reachable: URLError: <urlopen error [Errno 1] Operation not permitted>


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
  - `mail_processing_events`
  - `mail_needs_reply`
  - `mail_ai_queue`
  - `mail_ai_classifications`
  - `mail_ai_categories`
  - `mail_ai_trigger_rules`

**Live agent DB:** `/Users/g4ndr1k/agentic-ai/data/agent.db`

Phase 4A mail rules, rule actions, rule audit events, needs-reply rows, and
future AI queue/classification tables are mail-agent runtime state in
`data/agent.db`. They are intentionally not stored in `data/finance.db`.

  **`processed_messages`** columns:
  - `bridge_id` TEXT PK
  - `message_id` TEXT
  - `processed_at` TEXT
  - `category` TEXT
  - `urgency` TEXT
  - `provider` TEXT
  - `alert_sent` INTEGER
  - `summary` TEXT
⚠️  Cannot open agent DB: database disk image is malformed

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
  - `imap` = {'accounts': [{'name': 'gmail', 'host': 'imap.gmail.com', 'port': 993, 'email': 'YOUR_EMAIL@gmail.com', 'folders': ['INBOX'], 'lookback_days': 15, 'auth_source': 'keychain', 'keychain_service': 'agentic-ai-mail-imap'}], 'max_message_mb': 25, 'max_attachment_mb': 20}

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



## Filesystem

**NAS mailagent mount:** `/Volumes/Synology/mailagent`
⚠️  /Volumes/Synology not mounted

**secrets/banks.toml:** `/Users/g4ndr1k/agentic-ai/secrets/banks.toml`
✅ Readable
