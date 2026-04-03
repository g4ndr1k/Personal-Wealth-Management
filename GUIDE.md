# Agentic Mail Alert & Personal Finance System — Build & Operations Guide

**Version:** 3.0.0-rewrite  
**Platform:** Apple Silicon Mac on macOS  
**Status:**
- **Stage 1:** Production and in use
- **Stage 2:** Built and operating
- **Stage 3:** Planned

---

## Overview

This repository contains a **personal automation system** that combines three related functions:

1. **Mail monitoring and alerting** for finance-related email
2. **iMessage command and notification handling** using Messages.app
3. **Bank statement PDF processing** into structured Excel outputs
4. **Personal finance ingestion, categorization, and dashboarding** built on top of the parsed statement data

The system is designed specifically for **macOS**, because it depends on local Apple application databases and automation capabilities:

- **Mail.app** for message data
- **Messages.app** for iMessage sending and command polling
- **launchd / LaunchAgents** for startup automation
- **AppleScript** for outbound iMessage delivery

It is split across two execution environments:

- A **host bridge** running directly on macOS for all host-sensitive operations
- One or more **Docker services** for agent logic and finance API services

---

## What This System Does

### Stage 1 capabilities

Stage 1 handles the mail-alert and PDF-processing workflow.

It can:
- Read Apple Mail’s local SQLite database
- Detect new messages from the local Mail store
- Classify financial relevance using **Ollama** first and **Anthropic** as fallback
- Suppress likely promotions using Apple Mail metadata
- Send iMessage alerts to your device through Messages.app
- Poll Messages.app for inbound `agent:` commands
- Parse password-protected bank statement PDFs into structured Excel workbooks
- Scan Mail attachments for supported bank statement PDFs

### Stage 2 capabilities

Stage 2 builds a personal finance workflow on top of Stage 1 output.

It can:
- Import `ALL_TRANSACTIONS.xlsx` into Google Sheets
- Categorize transactions using aliases, regex rules, and AI fallback
- Sync Google Sheets into a local SQLite cache
- Expose a FastAPI backend for summaries and transaction operations
- Serve a Vue 3 PWA for dashboard, review, and transaction views

### Stage 3 status

Stage 3 is **planned**, not implemented as a live production workflow yet. It is intended to extend the system into holdings, balances, and net-worth tracking.

---

## Scope Boundaries

### What the system does not do

- It does **not** send email replies
- It does **not** modify mailboxes or move messages
- It does **not** browse websites
- It does **not** use OpenAI or Gemini in the current production path
- It does **not** run the host bridge inside Docker

### Intended environment

This guide assumes:
- a single-user, personal deployment
- an Apple Silicon Mac
- local Mail.app and Messages.app access
- Docker Desktop installed on the same Mac

---

## High-Level Architecture

### Component summary

| Component | Runs Where | Purpose |
|---|---|---|
| Bridge service | macOS host | Mail DB access, Messages DB access, iMessage sending, PDF processor, host API |
| Mail agent | Docker | Poll bridge, classify email, send alerts, process commands |
| Ollama | macOS host | Local LLM inference |
| Finance API | Docker | Stage 2 API and static PWA serving |
| PWA | Browser / FastAPI static files | Personal finance UI |
| Google Sheets | Cloud | Stage 2 working source of truth |
| SQLite DBs | Local disk | Runtime state and local read cache |

### Trust boundaries

- **Host-only:** Mail DB, Messages DB, AppleScript, Full Disk Access
- **Containerized:** agent logic and finance API
- **External:** Anthropic fallback and Google Sheets APIs, if enabled

### Data flow

#### Stage 1

1. Mail.app syncs email locally
2. Bridge reads the Mail database
3. Agent fetches pending mail from bridge
4. Agent classifies messages
5. Agent asks bridge to send iMessage alerts
6. Bridge polls Messages DB for inbound `agent:` commands
7. Agent fetches and executes commands

#### PDF pipeline

1. PDFs arrive by upload, watched inbox, or detected mail attachment
2. Bridge queues PDF jobs
3. Parser router identifies bank and statement type
4. Processor unlocks the PDF if needed
5. Parser extracts structured transactions
6. XLSX output is written, including `ALL_TRANSACTIONS.xlsx`

#### Stage 2

1. `ALL_TRANSACTIONS.xlsx` is imported
2. Data is categorized and enriched
3. Google Sheets becomes the editable working source
4. Sync process mirrors Sheets into local SQLite
5. FastAPI serves analytics and PWA data

---

## Current Implementation Status

## Production-ready and implemented

The following are implemented and described in this guide:

- Host bridge service
- Dockerized mail agent
- Mail.app SQLite polling and schema validation
- Messages.app command polling
- Outbound iMessage delivery via AppleScript
- Ollama primary classifier
- Anthropic fallback classifier
- Apple Mail category pre-filtering
- Persistent runtime flags (`paused`, `quiet`)
- Bridge and agent SQLite state databases
- LaunchAgents for host automation
- PDF processing pipeline with multiple bank parsers
- Stage 2 importer, categorizer, Sheets integration, SQLite sync, FastAPI backend, and Vue PWA

## Present but not part of the production path

- OpenAI provider stub
- Gemini provider stub
- Stage 3 design sections

## Known implementation caveats

- `max_commands_per_hour` exists in config but is not currently enforced by code
- The bridge must remain on the host because Docker cannot safely replace Mail/Messages DB access or AppleScript delivery
- Full Disk Access must be granted to the **actual Python binary path**, not just Terminal

---

## Prerequisites

### Hardware

- Apple Silicon Mac recommended
- 16 GB RAM or more recommended
- Enough disk space for Mail cache, Docker images, Ollama models, logs, PDFs, and XLS outputs

### Software

Install core packages:

```bash
brew install ollama jq sqlite
brew install --cask docker
brew install python@3.13
```

Docker Desktop should be configured to start automatically at login.

### Python requirement

Use **Homebrew Python 3.13** for the host bridge.

The bridge depends on `tomllib`, which is in the standard library for Python 3.11+.
The macOS system Python is typically too old.

Recommended verification:

```bash
/opt/homebrew/bin/python3.13 --version
/opt/homebrew/bin/python3.13 -c "import tomllib, sqlite3; print('OK')"
```

If you want an unversioned `python3` symlink:

```bash
ln -sf /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3
```

Do **not** mix Homebrew Python with Miniconda or the python.org installer for this deployment.

### PDF processor dependencies

```bash
/opt/homebrew/bin/pip3 install pikepdf pdfplumber openpyxl
```

Verify:

```bash
/opt/homebrew/bin/python3 -c "import pikepdf, pdfplumber, openpyxl; print('OK')"
```

### Ollama

```bash
OLLAMA_HOST=0.0.0.0 ollama serve &
ollama pull llama3.2:3b
ollama list
```

### Mail.app

- At least one mail account must be configured
- Mail.app must be running for local data to stay current

### Messages.app

- iMessage must be active
- The configured recipient must be reachable through Messages.app

### Full Disk Access

The bridge reads protected local databases:

- `~/Library/Mail/V*/MailData/Envelope Index`
- `~/Library/Messages/chat.db`

When launched by `launchd`, the bridge does not inherit Terminal’s privacy permissions. Grant Full Disk Access to the **real Python executable path** used by the bridge.

Verification helper:

```bash
realpath /opt/homebrew/bin/python3
```

---

## Project Layout

A simplified layout:

```text
agentic-ai/
├── agent/                  # Dockerized mail agent
├── bridge/                 # Host bridge service
├── finance/                # Stage 2 backend and sync logic
├── pwa/                    # Vue 3 frontend
├── parsers/                # PDF statement parsers
├── scripts/                # Utility scripts and launch helpers
├── config/                 # settings.toml
├── data/                   # Runtime databases and local state
├── logs/                   # Host-side logs
├── output/xls/             # XLSX exports
└── secrets/                # tokens, bank passwords, local secrets
```

Key responsibilities:
- `bridge/`: host-only integration with Mail, Messages, PDF jobs, and alert sending
- `agent/`: alerting/classification loop and iMessage command execution
- `finance/`: importer, categorizer, sync engine, and API
- `parsers/`: bank-specific PDF parsing logic

---

## Quick Start

This is the shortest safe path to a working Stage 1 deployment.

### 1. Clone the repository

```bash
git clone https://github.com/g4ndr1k/agentic-ai.git ~/agentic-ai
cd ~/agentic-ai
```

### 2. Create the bridge token

```bash
mkdir -p secrets
python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/bridge.token
chmod 600 secrets/bridge.token
```

### 3. Edit config

```bash
cp config/settings.toml config/settings.toml.bak
nano config/settings.toml
```

At minimum, update:

```toml
[auth]
token_file = "/Users/YOUR_USERNAME/agentic-ai/secrets/bridge.token"

[imessage]
primary_recipient = "you@icloud.com"
authorized_senders = ["you@icloud.com"]
```

### 4. Optional: enable Anthropic fallback

```bash
cat > .env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-your-key-here
EOF
chmod 600 .env
```

If you do not want cloud fallback, set:

```toml
[classifier]
cloud_fallback_enabled = false
```

### 5. Start Ollama and pull the model

```bash
OLLAMA_HOST=0.0.0.0 ollama serve &
sleep 3
ollama pull llama3.2:3b
```

### 6. Grant Full Disk Access

Grant FDA to the exact Python binary that will run the bridge.

### 7. Start and test the bridge

```bash
cd ~/agentic-ai
PYTHONPATH=$(pwd) python3 -m bridge.server
```

Expected startup indicators:
- config loaded
- auth token loaded
- Mail DB found
- schema verified
- listening on `127.0.0.1:9100`

### 8. Test bridge endpoints

```bash
TOKEN=$(cat secrets/bridge.token)

curl -s http://127.0.0.1:9100/healthz | python3 -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9100/health | python3 -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:9100/mail/pending?limit=2" | python3 -m json.tool
```

### 9. Start the Docker agent

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f mail-agent
```

### 10. Set up PDF processing

```bash
/opt/homebrew/bin/pip3 install pikepdf pdfplumber openpyxl
mkdir -p ~/agentic-ai/data/pdf_inbox
mkdir -p ~/agentic-ai/data/pdf_unlocked
mkdir -p ~/agentic-ai/output/xls
cp secrets/banks.toml.template secrets/banks.toml
chmod 600 secrets/banks.toml
nano secrets/banks.toml
```

Then open:

- `http://127.0.0.1:9100/pdf/ui`

---

## Configuration Reference

Configuration lives in `config/settings.toml`.

### `[bridge]`

| Key | Default | Description |
|---|---|---|
| `host` | `127.0.0.1` | Bridge listen address; keep local-only |
| `port` | `9100` | Bridge listen port |
| `log_level` | `INFO` | Host bridge log level |

### `[auth]`

| Key | Description |
|---|---|
| `token_file` | Required absolute path to `secrets/bridge.token` |

### `[mail]`

| Key | Default | Description |
|---|---|---|
| `source` | `mailapp` | Active mail source |
| `max_batch` | `25` | Max messages returned per scan |
| `max_body_text_bytes` | `200000` | Max body bytes before truncation |
| `initial_lookback_days` | `7` | First-run lookback window |

### `[imessage]`

| Key | Default | Description |
|---|---|---|
| `primary_recipient` | — | Required destination for alerts |
| `authorized_senders` | — | Required command allowlist |
| `command_prefix` | `agent:` | Command prefix |
| `max_alerts_per_hour` | `60` | Alert rate limit |
| `max_commands_per_hour` | `60` | Defined in config; not currently enforced |
| `startup_notifications` | `true` | Send startup iMessage |
| `shutdown_notifications` | `false` | Send shutdown iMessage |
| `allow_same_account_commands` | `true` | Allow self-sent commands |

### `[classifier]`

| Key | Default | Description |
|---|---|---|
| `provider_order` | `['ollama','anthropic']` | Provider execution order |
| `cloud_fallback_enabled` | `true` | Permit Anthropic fallback |
| `generic_alert_on_total_failure` | `true` | Alert instead of drop if classification totally fails |

### `[ollama]`

| Key | Default | Description |
|---|---|---|
| `host` | `http://host.docker.internal:11434` | Ollama endpoint from inside Docker |
| `model_primary` | `llama3.2:3b` | Primary local model |
| `timeout_seconds` | `60` | Request timeout |

### `[anthropic]`

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | Enable Anthropic fallback |
| `model` | `claude-sonnet-4-20250514` | Anthropic model |
| `api_key_env` | `ANTHROPIC_API_KEY` | Env var containing the key |

### `[agent]`

| Key | Default | Description |
|---|---|---|
| `poll_interval_seconds` | `1800` | Mail scan interval |
| `command_poll_interval_seconds` | `30` | Command polling interval |
| `alert_on_categories` | see config | Categories that trigger alerts |

Default categories:

```toml
alert_on_categories = [
  "transaction_alert",
  "bill_statement",
  "bank_clarification",
  "payment_due",
  "security_alert",
  "financial_other"
]
```

### `[pdf]`

| Key | Default | Description |
|---|---|---|
| `inbox_dir` | `data/pdf_inbox` | Pending PDF directory |
| `unlocked_dir` | `data/pdf_unlocked` | Unlocked PDF output directory |
| `xls_output_dir` | `output/xls` | XLS export location |
| `bank_passwords_file` | `secrets/banks.toml` | PDF password file |
| `jobs_db` | `data/pdf_jobs.db` | PDF job queue DB |
| `attachment_seen_db` | `data/seen_attachments.db` | Mail attachment dedupe DB |
| `attachment_lookback_days` | `60` | Mail attachment scan window |
| `parser_llm_model` | `llama3.2:3b` | PDF parser fallback model |

### `[owners]`

Maps PDF-detected customer names to canonical owner labels.

Example:

```toml
[owners]
"Emanuel" = "Gandrik"
"Dian Pratiwi" = "Helen"
```

Fallback owner is `Unknown`.

### `[finance]`

| Key | Description |
|---|---|
| `xlsx_input` | Absolute path to `ALL_TRANSACTIONS.xlsx` (Stage 1 output; immutable raw baseline) |
| `sqlite_db` | Absolute path to `data/finance.db` (local read cache — delete and rebuild anytime) |

### `[google_sheets]`

| Key | Description |
|---|---|
| `credentials_file` | Path to `secrets/google_credentials.json` (download from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID → Desktop app → Download JSON) |
| `token_file` | Path to `secrets/google_token.json` (written automatically after first OAuth consent; refreshes on expiry) |
| `spreadsheet_id` | ID from the Google Sheet URL: `https://docs.google.com/spreadsheets/d/<ID>/edit` |
| `transactions_tab` | Sheet tab name for raw transactions (default: `Transactions`) |
| `aliases_tab` | Sheet tab name for merchant alias rules (default: `Merchant Aliases`) |
| `categories_tab` | Sheet tab name for category taxonomy (default: `Categories`) |
| `currency_tab` | Sheet tab name for currency codes (default: `Currency Codes`) |
| `import_log_tab` | Sheet tab name for import history (default: `Import Log`) |
| `overrides_tab` | Sheet tab name for manual category overrides (default: `Category Overrides`) |
| `pdf_import_log_tab` | Sheet tab name for PDF processing log (default: `PDF Import Log`) |

### `[fastapi]`

| Key | Default | Description |
|---|---|---|
| `host` | `0.0.0.0` | Finance API listen address inside Docker (`0.0.0.0` binds all interfaces) |
| `port` | `8090` | Finance API port (distinct from bridge `:9100` and agent health `:8080`) |
| `cors_origins` | `["http://localhost:5173", "https://adrianto.synology.me:8443"]` | Allowed CORS origins — add your own NAS or reverse-proxy domain |

### `[ollama_finance]`

| Key | Default | Description |
|---|---|---|
| `host` | `http://localhost:11434` | Ollama host when running directly on Mac; Docker overrides via `OLLAMA_FINANCE_HOST` env var |
| `model` | `qwen2.5:7b` | Model for Layer 3 expense categorization (more accurate than `llama3.2:3b` for JSON output) |
| `timeout_seconds` | `60` | Request timeout |

Pull the finance categorization model before first use:

```bash
ollama pull qwen2.5:7b
```

---

## Bridge Service

The bridge is the host-side control plane.

### Responsibilities

- Load and validate config
- Load and verify bearer token
- Discover and validate Mail DB
- Access Messages DB
- Expose HTTP endpoints to Docker services
- Send iMessage alerts via AppleScript
- Manage runtime state in `data/bridge.db`
- Host PDF upload, processing, and UI endpoints

### Startup sequence

1. Load settings
2. Load auth token
3. Initialize bridge state DB
4. Initialize PDF jobs DB
5. Discover and validate Mail DB
6. Open Messages DB
7. Start HTTP server

If Mail DB discovery or schema validation fails, the bridge exits immediately.

### Logs

| File | Purpose |
|---|---|
| `logs/bridge.log` | Main bridge application log |
| `logs/bridge-launchd.log` | launchd stdout |
| `logs/bridge-launchd-err.log` | launchd stderr and startup failures |

### Manual run

```bash
cd ~/agentic-ai
PYTHONPATH=$(pwd) python3 -m bridge.server
```

### Recovery rule

Do **not** delete `bridge.db` while the bridge is running.
Always stop the agent and unload the bridge LaunchAgent before deleting runtime DBs.

---

## Mail Database Access

### Discovery

The bridge discovers the Mail DB automatically from:

```text
~/Library/Mail/V*/MailData/Envelope Index
```

The newest matching path is used.

### Schema dependencies

The bridge joins Mail data across these tables:

- `messages`
- `addresses`
- `subjects`
- `summaries`
- `mailboxes`
- `message_global_data`

Startup includes schema validation. Missing required tables cause bridge startup to fail.

### Fields returned to the agent

Typical mail payload fields include:

- `bridge_id`
- `source_rowid`
- `message_id`
- `mailbox`
- `sender`
- `sender_email`
- `sender_name`
- `subject`
- `date_received`
- `date_sent`
- `snippet`
- `body_text`
- `apple_category`
- `apple_high_impact`
- `apple_urgent`
- `is_read`
- `is_flagged`
- `attachments` (currently always empty)

### Date handling

Mail uses the **Unix epoch**.
Messages uses the **Apple epoch**.
Do not mix them when debugging timestamps.

---

## iMessage Handling

### Receiving commands

A message is treated as an agent command only when:
- it starts with the configured command prefix, and
- it is from an authorized sender or from self when self-commands are allowed

Default prefix:

```text
agent:
```

### Sending alerts

Outgoing messages are sanitized before AppleScript execution:
- control characters removed
- newlines normalized
- length capped

The bridge passes sanitized text as AppleScript arguments rather than interpolating raw text into a script body.

### Epoch reminder

- Mail.app dates: Unix epoch (`1970-01-01`)
- Messages.app dates: Apple epoch (`2001-01-01`)

---

## Agent Service (Docker)

The mail agent is the long-running container that polls bridge endpoints and performs classification.

### Startup behavior

1. Load config
2. Open `data/agent.db`
3. Initialize providers
4. Restore `paused` and `quiet` flags
5. Start health server on `127.0.0.1:8080`
6. Retry bridge connectivity
7. Optionally send startup notification
8. Enter main loop

### Main loop

- Mail scan runs on `poll_interval_seconds`
- Command scan runs on `command_poll_interval_seconds`
- A manual `agent: scan` command can request an immediate scan

### Mail scan cycle

1. Fetch pending mail from bridge
2. Deduplicate by bridge ID and message ID
3. Classify with provider chain
4. Send alert if category is configured for alerting
5. ACK processed checkpoint back to bridge

### Command scan cycle

1. Fetch pending commands
2. Execute each command
3. Send response via bridge alert endpoint
4. ACK commands back to bridge

### Health endpoint

`GET http://127.0.0.1:8080`

Returns counters such as emails seen, alerts sent, classification failures, and last scan time.

### Agent state DB

`data/agent.db` stores:
- processed messages
- processed commands
- alert history
- persistent flags (`paused`, `quiet`)

---

## Classification and Provider Flow

### Pre-filtering

Before LLM use, the classifier checks Apple Mail metadata.
Promotions are suppressed when Apple categorized the message as promotional and it is neither high-impact nor urgent.

### Provider order

Default provider order:

```text
ollama -> anthropic
```

### Circuit breaker

Each provider has an in-memory circuit breaker:
- opens after 3 consecutive failures
- remains open for 300 seconds
- retries after cooldown

### Ollama behavior

- Calls `/api/generate` with non-streaming output
- Extracts JSON from model output
- Normalizes category and urgency values
- Uses prompt instructions to ignore adversarial text embedded in emails

### Anthropic behavior

- Used as fallback when enabled and configured
- Disabled if API key is missing or config disables it

### Total failure handling

If every provider fails:
- `generic_alert_on_total_failure = true` returns `financial_other`
- `generic_alert_on_total_failure = false` drops the mail as `not_financial`

### Classification output schema

```python
category: transaction_alert | bill_statement | bank_clarification | payment_due | security_alert | financial_other | not_financial
urgency: low | medium | high
summary: short text
requires_action: bool
provider: provider name
```

---

## Command Interface

Send commands from Messages.app using the configured prefix.

| Command | Effect |
|---|---|
| `agent: help` | Show command list |
| `agent: status` | Show paused / quiet state |
| `agent: summary` | Show recent alert summary |
| `agent: test` | Confirm agent responsiveness |
| `agent: scan` | Trigger immediate scan |
| `agent: pause` | Pause mail scanning |
| `agent: resume` | Resume mail scanning |
| `agent: quiet on` | Suppress outgoing alerts |
| `agent: quiet off` | Re-enable alerts |
| `agent: health` | Return simple health status |
| `agent: last 5` | Show recent alerts |

### Persistence

`paused` and `quiet` survive container restarts because they are stored in `data/agent.db`.

### Authorization

Commands are accepted only from authorized handles or self when explicitly allowed.

---

## Docker Deployment

### `docker-compose.yml` expectations

The `mail-agent` service should:
- mount config read-only
- mount data persistently
- mount the bridge token as a secret-like file
- expose `host.docker.internal`
- use `restart: unless-stopped`
- include a healthcheck against `127.0.0.1:8080`

### Standard lifecycle

Build:

```bash
docker compose build
```

Start:

```bash
docker compose up -d
docker compose ps
docker compose logs -f mail-agent
```

Stop:

```bash
docker compose down
```

Rebuild from scratch:

```bash
docker compose build --no-cache
docker compose up -d
```

### Validate Docker to Ollama connectivity

```bash
docker run --rm --add-host=host.docker.internal:host-gateway   curlimages/curl:latest   curl -s http://host.docker.internal:11434/api/tags
```

---

## LaunchAgents and Startup Automation

Four LaunchAgents are used for host-side startup behavior:

| Label | Purpose | KeepAlive |
|---|---|---|
| `com.agentic.ollama` | Start Ollama | `true` |
| `com.agentic.bridge` | Start bridge | `true` |
| `com.agentic.mailapp` | Launch Mail.app once | `false` |
| `com.agentic.agent` | Start Docker agent wrapper | `false` |

### Operational notes

- The Docker agent LaunchAgent should call a startup script that waits for Docker Desktop readiness
- Mail.app is launched once to keep the Mail DB updating
- The bridge LaunchAgent must use the exact Python executable path that has Full Disk Access

### Post-reboot validation

After login, verify:

```bash
launchctl list | grep agentic
docker compose ps
curl -s http://127.0.0.1:9100/healthz
```

---

## Testing and Validation

### Validate host Python

```bash
python3 --version
python3 -c "import tomllib, sqlite3, http.server, signal, re; print('OK')"
```

### Validate PDF dependencies

```bash
/opt/homebrew/bin/python3 -c "import pikepdf, pdfplumber, openpyxl; print('OK')"
```

### Check Mail DB presence

```bash
find ~/Library/Mail -path "*/MailData/Envelope Index" 2>/dev/null
```

### Validate Mail schema

```bash
~/agentic-ai/scripts/tahoe_validate.sh
```

### Test the bridge

```bash
cd ~/agentic-ai
TOKEN=$(cat ~/agentic-ai/secrets/bridge.token)

curl -s http://127.0.0.1:9100/healthz | python3 -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9100/health | python3 -m json.tool
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:9100/mail/pending?limit=2" | python3 -m json.tool
```

### Test alert sending

```bash
curl -s -X POST   -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d '{"text":"Bridge test alert from curl"}'   http://127.0.0.1:9100/alerts/send | python3 -m json.tool
```

### Test Ollama

```bash
curl -s http://127.0.0.1:11434/api/tags | python3 -m json.tool
```

### Test the agent

```bash
cd ~/agentic-ai
docker compose build
docker compose up -d
sleep 10
docker compose ps
docker compose logs --tail 50 mail-agent
```

---

## Day-to-Day Operations

### Health checks

```bash
TOKEN=$(cat ~/agentic-ai/secrets/bridge.token)

curl -s http://127.0.0.1:9100/healthz
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9100/health | python3 -m json.tool
docker exec mail-agent python3 -c   "import urllib.request,json; print(json.dumps(json.loads(urllib.request.urlopen('http://127.0.0.1:8080').read()), indent=2))"
```

### Logs

```bash
tail -50 ~/agentic-ai/logs/bridge.log
cat ~/agentic-ai/logs/bridge-launchd-err.log
docker compose logs --tail 50 mail-agent
docker compose logs -f mail-agent
```

### Restart services

```bash
docker compose restart mail-agent
launchctl unload ~/Library/LaunchAgents/com.agentic.bridge.plist
launchctl load ~/Library/LaunchAgents/com.agentic.bridge.plist
launchctl list | grep agentic
```

### Reset runtime state safely

```bash
cd ~/agentic-ai
docker compose down
launchctl unload ~/Library/LaunchAgents/com.agentic.bridge.plist
rm -f data/agent.db data/bridge.db
launchctl load ~/Library/LaunchAgents/com.agentic.bridge.plist
sleep 3
docker compose up -d
```

Before resetting first-run behavior, adjust `initial_lookback_days` in `config/settings.toml`.

---

## Bridge API Reference

### Authentication

All endpoints except `/healthz` require:

```http
Authorization: Bearer <token>
```

### Core endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | Unauthenticated liveness probe |
| GET | `/health` | Authenticated bridge health |
| GET | `/mail/schema` | Mail schema debug info |
| GET | `/mail/pending?limit=N` | Fetch pending mail |
| POST | `/mail/ack` | Advance mail checkpoint |
| GET | `/commands/pending?limit=N` | Fetch pending commands |
| POST | `/commands/ack` | Advance command checkpoint |
| POST | `/alerts/send` | Send iMessage alert |

### PDF endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/pdf/upload` | Upload PDF |
| POST | `/pdf/process` | Process queued job |
| GET | `/pdf/status/<job_id>` | Get job status |
| GET | `/pdf/download/<job_id>` | Download XLS output |
| GET | `/pdf/jobs?limit=N` | List recent jobs |
| GET | `/pdf/attachments` | List discovered PDF attachments |
| GET | `/pdf/ui` | Web UI |

### Common payloads

ACK payload:

```json
{ "ack_token": "12345" }
```

Alert payload:

```json
{ "text": "Your alert message here" }
```

### Rate limiting

`/alerts/send` is limited by `max_alerts_per_hour` and tracked through bridge request logs.

---

## PDF Statement Processor

### Overview

The PDF processor runs in the bridge on the macOS host. It converts supported bank statement PDFs into structured Excel outputs.

### Supported statement families

Current coverage includes parsers for:
- Maybank credit card and consolidated statements
- BCA credit card and savings statements
- Permata credit card and savings statements
- CIMB Niaga credit card and consolidated portfolio statements

Detection is automatic through PDF content routing.

### Processing pipeline

1. Accept PDF from upload, inbox, or attachment scan
2. Unlock PDF using configured bank passwords or fallback methods
3. Detect bank and statement type
4. Parse using bank-specific parser
5. Fall back across extraction layers where needed
6. Export bank/owner workbooks and flat transaction workbook

### Parsing strategy

The processor uses a layered approach:

1. `pdfplumber` table extraction
2. Python regex and heuristics
3. Ollama fallback for difficult layouts

### Outputs

Typical outputs include:
- per-bank or per-owner workbooks like `{Bank}_{Owner}.xlsx`
- combined `ALL_TRANSACTIONS.xlsx`

### PDF UI

The bridge serves a local UI at:

```text
http://127.0.0.1:9100/pdf/ui
```

### Validation recommendations

After adding a new parser or changing one:
- test direct detection on sample PDFs
- compare totals to statement summaries
- verify owner mapping behavior
- confirm year-boundary handling for month-crossing statements

### Bank parser inventory

All parsers live in `parsers/`. The router (`parsers/router.py`) identifies the bank from the PDF filename or embedded content and delegates to the correct module.

| Module | Handles |
|---|---|
| `bca_cc.py` | BCA credit card statements |
| `bca_savings.py` | BCA savings / tabungan statements |
| `maybank_cc.py` | Maybank credit card statements |
| `maybank_consol.py` | Maybank consolidated portfolio statements |
| `permata_cc.py` | Permata credit card statements |
| `permata_savings.py` | Permata savings / tabungan statements |
| `cimb_niaga_cc.py` | CIMB Niaga credit card statements |
| `cimb_niaga_consol.py` | CIMB Niaga consolidated / portfolio statements |

Each parser returns a `StatementResult` containing:
- `bank` — bank name string
- `statement_type` — e.g. `cc`, `savings`, `consolidated`
- `transactions` — list of `Transaction` objects (see schema below)
- `summary` — `AccountSummary` with totals and closing balance
- `exchange_rates` — dict of `{currency: rate}` for multi-currency statements

### Transaction dataclass schema

Every transaction produced by any parser has these fields:

| Field | Type | Description |
|---|---|---|
| `date_transaction` | `str` | `DD/MM/YYYY` — transaction date (may be `""` for synthetic rows) |
| `date_posted` | `str \| None` | `DD/MM/YYYY` — posting date for CC; `None` for savings |
| `description` | `str` | Raw description from statement |
| `currency` | `str` | ISO currency code: `IDR`, `USD`, `SGD`, `JPY`, etc. |
| `foreign_amount` | `float \| None` | Amount in original foreign currency; `None` for IDR-only |
| `exchange_rate` | `float \| None` | Bank-applied IDR conversion rate |
| `amount_idr` | `float` | Amount always expressed in IDR |
| `tx_type` | `str` | `"Credit"` or `"Debit"` |
| `balance` | `float \| None` | Running balance (savings/koran only) |
| `account_number` | `str` | Card or account number (`""` if unknown) |
| `owner` | `str` | Canonical owner label (`Gandrik`, `Helen`, …) from `[owners]` mapping |

### XLSX exporter outputs

`exporters/xls_writer.py` writes Excel files to `output/xls/`:

| File | Contents |
|---|---|
| `BCA_Gandrik.xlsx` | BCA transactions for owner Gandrik |
| `BCA_Helen.xlsx` | BCA transactions for owner Helen |
| `Maybank_Gandrik.xlsx` | Maybank transactions for owner Gandrik |
| `Permata_Gandrik.xlsx` | Permata transactions for owner Gandrik |
| `Permata_Helen.xlsx` | Permata transactions for owner Helen |
| `CIMB Niaga_Gandrik.xlsx` | CIMB Niaga transactions for owner Gandrik |
| `ALL_TRANSACTIONS.xlsx` | Flat combined file; input for Stage 2 importer |

`ALL_TRANSACTIONS.xlsx` contains a sheet named `ALL_TRANSACTIONS` with columns matching the `FinanceTransaction` model used by the Stage 2 importer.

### `secrets/banks.toml` format

This file holds PDF passwords, one entry per bank. It is gitignored. Create it from the template:

```bash
cp secrets/banks.toml.template secrets/banks.toml
chmod 600 secrets/banks.toml
```

Example format:

```toml
[bca]
cc_password = "123456"
savings_password = ""

[maybank]
cc_password = "DDMMYYYY"

[permata]
cc_password = "XXXXXXXX"

[cimb_niaga]
cc_password = "XXXXXXXX"
```

Passwords are tried automatically during PDF unlock. Leave blank if the PDF has no password.

---

## Security Notes

- Keep the bridge bound to `127.0.0.1` unless you have a deliberate reverse-proxy design
- Protect `secrets/bridge.token` and `secrets/banks.toml` with restrictive permissions
- Do not commit `.env`, token files, or bank password files
- Grant Full Disk Access only to the exact Python executable required
- Prefer local Ollama classification where possible
- Treat all mail content and statement PDFs as sensitive personal financial data
- Review outbound alert content if message summaries may expose sensitive details on lock screens

---

## Known Limitations

- The system is macOS-specific
- Bridge functions depend on Apple private/local app database structure
- Mail schema changes across macOS releases may require query updates
- `attachments` in normal mail payloads are not yet implemented
- `max_commands_per_hour` is not enforced even though it is documented in config
- Stage 3 is still planning material, not an implemented deployment path

---

## Troubleshooting

### Bridge will not start

Check:
- `logs/bridge-launchd-err.log`
- Python version
- Full Disk Access on the exact binary
- Mail DB path exists and schema still matches expected tables

### Agent cannot reach bridge

Check:
- bridge running on host
- Docker can resolve `host.docker.internal`
- token file mounted correctly
- `BRIDGE_URL` and `SETTINGS_FILE` values in container env

### Ollama classification fails

Check:
- Ollama is running
- model is pulled
- Docker can reach `host.docker.internal:11434`
- timeout is not too low for the host load

### No iMessage alerts are sent

Check:
- Messages.app is signed in
- recipient is valid for iMessage
- AppleScript permissions are intact
- alert rate limit has not been exceeded
- `quiet` mode is not enabled

### Commands are ignored

Check:
- sender is in `authorized_senders`
- prefix exactly matches `agent:`
- self-command behavior is enabled if testing from your own account
- Messages DB access works and timestamps are interpreted using Apple epoch

### PDF processing fails

Check:
- `secrets/banks.toml` contains the correct password
- parser supports that statement type
- dependencies (`pikepdf`, `pdfplumber`, `openpyxl`) are installed
- OCR-like or highly nonstandard PDFs may require parser updates

### Safe recovery procedure

If state becomes inconsistent:

```bash
cd ~/agentic-ai
docker compose down
launchctl unload ~/Library/LaunchAgents/com.agentic.bridge.plist
rm -f data/agent.db data/bridge.db
launchctl load ~/Library/LaunchAgents/com.agentic.bridge.plist
sleep 3
docker compose up -d
```

---

## Stage 2 — Personal Finance Dashboard

### Status and design

Stage 2 is built and operating on top of Stage 1 parser output.

Principles:
- Stage 1 XLSX output is the immutable raw baseline
- Google Sheets is the editable working source of truth
- Local SQLite is a disposable read cache that can be rebuilt from Sheets at any time
- IDR is authoritative for all summaries
- FX rate is derived from bank-applied amounts rather than external lookups

### Stage 2 components

| Component | Purpose |
|---|---|
| `finance/importer.py` | XLSX → Google Sheets import with categorization |
| `finance/categorizer.py` | 4-layer expense categorization engine |
| `finance/sheets.py` | Google Sheets API client (OAuth2) |
| `finance/db.py` | SQLite schema and connection helpers |
| `finance/sync.py` | Sheets → SQLite sync engine |
| `finance/api.py` | FastAPI backend (all `/api/*` endpoints) |
| `finance/server.py` | Uvicorn entry point |
| `finance/config.py` | Config loaders for all Stage 2 subsystems |
| `finance/models.py` | `FinanceTransaction` dataclass and `make_hash()` |
| `finance/setup_sheets.py` | One-time Google Sheet structure setup |
| `finance/_seed_aliases.py` | Populate initial merchant alias rules |
| `finance/pdf_log_sync.py` | Sync PDF processing results to Google Sheets |
| `pwa/` | Vue 3 + Pinia + Chart.js mobile-first PWA |

### Stage 2 data flow

```
output/xls/ALL_TRANSACTIONS.xlsx
        │
        ▼ python3 -m finance.importer
Google Sheets (Transactions tab + Import Log)
        │
        ▼ python3 -m finance.sync   (or POST /api/sync)
data/finance.db  (SQLite read cache)
        │
        ▼ python3 -m finance.server
FastAPI :8090
        │
        ▼ HTTP /api/*
pwa/dist/ (Vue 3 SPA served as static files from FastAPI)
```

---

### Stage 2 prerequisites

#### Node.js (for PWA build only)

```bash
brew install node
node --version   # 18+ recommended
```

#### Python packages (host, for running outside Docker)

```bash
/opt/homebrew/bin/pip3 install --break-system-packages \
  google-auth>=2.28.0 \
  google-auth-oauthlib>=1.2.0 \
  google-api-python-client>=2.124.0 \
  rapidfuzz>=3.6.0 \
  "fastapi>=0.110.0" \
  "uvicorn[standard]>=0.27.0"
```

Verify:

```bash
/opt/homebrew/bin/python3 -c "import fastapi, uvicorn, googleapiclient, rapidfuzz; print('OK')"
```

#### Ollama model for finance categorization

```bash
ollama pull qwen2.5:7b
```

---

### Stage 2 first-time setup

#### 1. Create a Google Cloud project and credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a new project
2. Enable the **Google Sheets API** for the project
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
4. Application type: **Desktop app**
5. Download the JSON file → save as `secrets/google_credentials.json`
6. chmod 600 secrets/google_credentials.json

#### 2. Create a Google Sheet

Create a blank Google Sheet and copy its ID from the URL:

```text
https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit
```

Update `config/settings.toml`:

```toml
[google_sheets]
spreadsheet_id = "your-sheet-id-here"
```

#### 3. Initialize the sheet structure

```bash
cd ~/agentic-ai
PYTHONPATH=$(pwd) python3 -m finance.setup_sheets
```

This creates all required tabs (`Transactions`, `Merchant Aliases`, `Categories`, `Currency Codes`, `Import Log`, `Category Overrides`, `PDF Import Log`) with headers and default data. Safe to re-run — existing tabs with data are untouched.

A browser window opens for Google OAuth consent on first run. The token is saved to `secrets/google_token.json`.

#### 4. Seed initial merchant aliases (optional)

```bash
PYTHONPATH=$(pwd) python3 -m finance._seed_aliases
```

Populates default categorization rules for common merchants.

#### 5. Build the PWA

```bash
cd ~/agentic-ai/pwa
npm install
npm run build
```

The production build goes to `pwa/dist/`. The finance Dockerfile copies this directory into the container image, so build before `docker compose build`.

#### 6. Build and start finance-api

```bash
cd ~/agentic-ai
docker compose build finance-api
docker compose up -d finance-api
docker compose ps
docker compose logs -f finance-api
```

The service is healthy when `/api/health` returns a 200 response.

#### 7. Run the first import

```bash
# Dry run first to preview
PYTHONPATH=$(pwd) python3 -m finance.importer --dry-run

# Then run the real import
PYTHONPATH=$(pwd) python3 -m finance.importer
```

Or use the PWA: open `http://localhost:8090` → Settings → Import.

#### 8. Sync Google Sheets to SQLite

```bash
PYTHONPATH=$(pwd) python3 -m finance.sync
```

Or use the PWA: Settings → Sync.

---

### Stage 2 environment variables

The `finance-api` Docker service reads these environment variables (set in `docker-compose.yml`):

| Variable | Purpose |
|---|---|
| `SETTINGS_FILE` | Path to `config/settings.toml` inside the container |
| `FINANCE_SQLITE_DB` | Override `[finance].sqlite_db` with container path |
| `FINANCE_XLSX_INPUT` | Override `[finance].xlsx_input` with container path |
| `GOOGLE_CREDENTIALS_FILE` | Override `[google_sheets].credentials_file` |
| `GOOGLE_TOKEN_FILE` | Override `[google_sheets].token_file` |
| `ANTHROPIC_API_KEY` | Anthropic API key (passed from `.env`) |
| `OLLAMA_FINANCE_HOST` | Override `[ollama_finance].host` (set to `http://host.docker.internal:11434` in Docker) |

---

### Categorization engine

`finance/categorizer.py` applies rules in this order:

| Layer | Method | Result |
|---|---|---|
| **1** | Exact merchant alias match (case-insensitive) | Auto-assigned, no user input |
| **1b** | Contains substring alias match | Auto-assigned, no user input |
| **2** | Regex pattern match | Auto-assigned, no user input |
| **3** | Ollama LLM suggestion (`qwen2.5:7b`) | Pre-fills review queue; user confirms |
| **4** | Review queue fallback | Blank entry; user types manually |
| **0** | Cross-account transfer matching + Helen BCA ATM rule (post-processing) | See special logic below |

**Alias rules** live in the `Merchant Aliases` Google Sheet tab with columns:

| Column | Description |
|---|---|
| `merchant` | Normalized merchant name to display |
| `alias` | Raw text to match against (exact or substring) |
| `category` | Category to assign |
| `match_type` | `exact`, `contains`, or `regex` |
| `added_date` | ISO date the rule was added |
| `owner_filter` | If set, rule only applies to this owner |
| `account_filter` | If set, rule only applies to this account number |

Filtered (owner/account-specific) rules are checked before generic rules. When a Layer 3 or Layer 4 entry is confirmed by the user in the PWA, it is written back to the `Merchant Aliases` tab automatically.

#### Special post-processing logic (Layer 0)

Two rules run after all layers, as a post-processing pass:

1. **Cross-account internal transfer matching** — Detects matching debit/credit pairs across known internal account pairs (same date, same absolute amount) and marks both sides as `Transfer`. Known pairs:
   - Gandrik BCA ↔ Helen BCA
   - Helen Permata ↔ Helen BCA
   - Helen Permata ↔ Gandrik Permata

2. **Helen BCA ATM cash → Household** — Cash withdrawals from Helen's BCA account (`5500346622`) with ATM-like descriptions (`TARIKAN ATM`, etc.) are automatically re-categorised as `Household`, since this cash is used for daily household spending.

#### Legacy category migration

During sync, old category names are automatically mapped to the new taxonomy:

| Old name | New name |
|---|---|
| `Internal Transfer` | `Transfer` |
| `External Transfer` | `Transfer` |
| `Opening Balance` | `Adjustment` |
| `Transport` | `Auto` |
| `Household Expenses` | `Household` |
| `Child Support` | `Family` |
| `Travel` | `Flights & Hotels` |

---

### Category taxonomy

Categories are organised into **8 groups** with subcategories. Two metadata columns (`category_group`, `subcategory`) are stored in the `Categories` tab (columns F–G) and in SQLite.

| # | Group | Category | Subcategory | Icon | Recurring |
|---|---|---|---|---|---|
| 1 | **Housing & Bills** | Housing | Housing | 🏠 | ✓ |
| | | Utilities | Utilities | ⚡ | ✓ |
| | | Phone Bill | Communication | 📞 | ✓ |
| | | Internet | Communication | 🌐 | ✓ |
| 2 | **Food & Dining** | Groceries | Groceries | 🛒 | |
| | | Dining Out | Dining Out | 🍽️ | |
| | | Delivery & Takeout | Delivery & Takeout | 🛵 | |
| 3 | **Transportation** | Auto | Auto | 🚗 | |
| | | Rideshare | Rideshare | 🚕 | |
| 4 | **Lifestyle & Personal** | Shopping | Shopping | 🛍️ | |
| | | Personal Care | Personal Care | 💇 | |
| | | Entertainment | Entertainment | 🎬 | |
| | | Subscriptions | Subscriptions | 📱 | ✓ |
| 5 | **Health & Family** | Healthcare | Healthcare | 🏥 | |
| | | Family | Family | 👨‍👩‍👧 | ✓ |
| | | Household | Household | 🧺 | |
| | | Education | Education | 📚 | |
| | | Gifts & Donations | Gifts & Donations | 🎁 | |
| 6 | **Travel** | Flights & Hotels | Flights & Hotels | ✈️ | |
| | | Vacation Spending | Vacation Spending | 🏖️ | |
| 7 | **Financial & Legal** | Fees & Interest | Fees & Interest | 🏦 | |
| | | Taxes | Taxes | 📋 | |
| 8 | **System / Tracking** | Income | Income | 💰 | |
| | | Dividends | Dividends | 📈 | |
| | | Interest Income | Interest | 🏦 | |
| | | Capital Gains | Capital Gains | 📊 | |
| | | Other Income | Other Income | 💵 | |
| | | Transfer | Transfer | 🔁 | |
| | | Cash Withdrawal | Cash Withdrawal | 🏧 | |
| | | Adjustment | Adjustment | 🔧 | |
| | | Other | Other | ❓ | |

> **Non-expense categories** (Group 8: `Transfer`, `Adjustment`) are excluded from all income/expense totals and percentage calculations in the API and PWA.

---

### Google Sheets structure

The setup script (`finance/setup_sheets.py`) creates these tabs:

| Tab | Purpose | Key columns |
|---|---|---|
| `Transactions` | All imported transactions | date, amount, original_currency, original_amount, exchange_rate, raw_description, merchant, category, institution, account, owner, notes, hash, import_date, import_file |
| `Merchant Aliases` | Categorization rules | merchant, alias, category, match_type, added_date, owner_filter, account_filter |
| `Categories` | Category taxonomy | category, icon, sort_order, is_recurring, monthly_budget, **category_group**, **subcategory** |
| `Currency Codes` | Supported currencies | code, name, symbol |
| `Import Log` | Import run history | import_date, file, added, skipped, total, duration_s |
| `Category Overrides` | Manual overrides by hash | hash, category, note |
| `PDF Import Log` | PDF processing history | synced by `finance/pdf_log_sync.py` |

---

### Finance API reference

All endpoints are served at port `8090`.

#### Read endpoints (query SQLite only)

| Method | Path | Query params | Description |
|---|---|---|---|
| GET | `/api/health` | — | Service health check |
| GET | `/api/owners` | — | List all owner names |
| GET | `/api/categories` | — | List all categories with group, subcategory, icon, sort order |
| GET | `/api/transactions` | `year`, `month`, `owner`, `category`, `q`, `limit`, `offset` | Paginated transaction list with filters |
| GET | `/api/transactions/foreign` | `year`, `month`, `owner` | Foreign currency spending grouped by month |
| GET | `/api/summary/years` | — | List of years with transaction data |
| GET | `/api/summary/year/{year}` | — | Yearly totals by category |
| GET | `/api/summary/{year}/{month}` | — | Monthly breakdown |
| GET | `/api/review-queue` | `limit` | Uncategorized transactions awaiting review |

#### Write endpoints (also update Google Sheets)

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/alias` | `{hash, alias, merchant, category, match_type, apply_to_similar}` | Create or update merchant alias rule |
| PATCH | `/api/transaction/{hash}/category` | `{category, notes?}` | Assign category to a transaction by hash |
| POST | `/api/sync` | — | Pull from Google Sheets → rebuild SQLite cache |
| POST | `/api/import` | `{dry_run?, overwrite?}` | Import `ALL_TRANSACTIONS.xlsx` into Google Sheets |

---

### Importer CLI

```bash
# Standard import (skip duplicates by hash)
PYTHONPATH=$(pwd) python3 -m finance.importer

# Preview without writing
PYTHONPATH=$(pwd) python3 -m finance.importer --dry-run

# Re-import all rows, replacing existing by hash
PYTHONPATH=$(pwd) python3 -m finance.importer --overwrite

# Use a specific XLSX file
PYTHONPATH=$(pwd) python3 -m finance.importer --file /path/to/file.xlsx
```

The importer requires `ALL_TRANSACTIONS.xlsx` to have a sheet named `ALL_TRANSACTIONS`. It runs the full 4-layer categorization engine on each row.

---

### Sync CLI

```bash
# Full sync — reads all Sheets tabs, replaces all SQLite data
PYTHONPATH=$(pwd) python3 -m finance.sync

# Show last sync time and counts, then exit
PYTHONPATH=$(pwd) python3 -m finance.sync --status

# Verbose output
PYTHONPATH=$(pwd) python3 -m finance.sync -v
```

The SQLite database is a pure cache. Delete `data/finance.db` and re-run sync to rebuild from scratch.

---

### PWA development

```bash
cd ~/agentic-ai/pwa
npm install
npm run dev        # dev server at http://localhost:5173 (proxies /api to :8090)
npm run build      # production build to pwa/dist/
npm run preview    # preview production build locally
```

PWA views:
- **Dashboard** — spending overview with charts
- **Transactions** — filterable transaction list
- **Review Queue** — confirm or override AI category suggestions
- **Foreign Spend** — multi-currency analysis
- **Settings** — import, sync, and configuration controls

---

### Stage 2 day-to-day operations

Check finance-api health:

```bash
curl -s http://localhost:8090/api/health | python3 -m json.tool
```

View logs:

```bash
docker compose logs -f finance-api
```

Restart after code or PWA changes:

```bash
cd ~/agentic-ai/pwa && npm run build
cd ~/agentic-ai && docker compose build finance-api && docker compose up -d finance-api
```

Sync manually from terminal:

```bash
PYTHONPATH=$(pwd) python3 -m finance.sync
```

Sync PDF import log to Google Sheets:

```bash
PYTHONPATH=$(pwd) python3 -m finance.pdf_log_sync
```

---

### Stage 2 troubleshooting

**OAuth consent window never opens**
- Run the importer or sync on the Mac host directly (not inside Docker) for first-time auth
- Ensure `secrets/google_credentials.json` exists and is valid
- Delete `secrets/google_token.json` to force re-authentication

**Import fails with "Sheet 'ALL_TRANSACTIONS' not found"**
- Run Stage 1 PDF processing first to generate `output/xls/ALL_TRANSACTIONS.xlsx`
- Verify the file has a sheet named `ALL_TRANSACTIONS` (not `Sheet1`)

**finance-api container can't reach Ollama**
- Verify `OLLAMA_FINANCE_HOST=http://host.docker.internal:11434` is set in the container
- Run: `docker run --rm --add-host=host.docker.internal:host-gateway curlimages/curl:latest curl -s http://host.docker.internal:11434/api/tags`

**PWA shows blank / API errors**
- Check `cors_origins` in `[fastapi]` includes your access domain
- Rebuild PWA after changing frontend code: `npm run build` then rebuild Docker image

**Review queue shows no suggestions (Layer 3 not working)**
- Confirm `qwen2.5:7b` is pulled: `ollama list`
- Check `OLLAMA_FINANCE_HOST` env var is correct inside the container

---

## Stage 3 — Wealth Management (Planned)

Stage 3 is a design target for extending the system into net worth and holdings management.

Planned additions include:
- holdings tracking
- account balances
- net worth snapshots
- new API endpoints
- new PWA views for wealth summary and holdings management

Because this stage is not yet implemented, treat all Stage 3 material as roadmap documentation rather than operating instructions.

---

## Verification Checklist

Use this quick checklist after setup or major changes:

**Stage 1**
- [ ] Homebrew Python 3.13 works
- [ ] Full Disk Access granted to exact Python binary
- [ ] Mail.app running and syncing
- [ ] Messages.app signed in
- [ ] Ollama running with `llama3.2:3b` and `qwen2.5:7b`
- [ ] Bridge returns healthy responses
- [ ] Docker agent is healthy
- [ ] Test iMessage alert sends successfully
- [ ] PDF UI opens at `http://127.0.0.1:9100/pdf/ui`
- [ ] `secrets/banks.toml` created and populated with PDF passwords
- [ ] PDF processing produces `output/xls/ALL_TRANSACTIONS.xlsx`

**Stage 2**
- [ ] `secrets/google_credentials.json` present and valid
- [ ] `python3 -m finance.setup_sheets` completed without error
- [ ] `secrets/google_token.json` written after first OAuth consent
- [ ] `spreadsheet_id` in `config/settings.toml` is correct
- [ ] `pwa/dist/` built (`npm run build` in `pwa/`)
- [ ] `finance-api` Docker container is healthy
- [ ] Finance API responds at `http://localhost:8090/api/health`
- [ ] Importer successfully imports `ALL_TRANSACTIONS.xlsx`
- [ ] Sync populates `data/finance.db`
- [ ] PWA loads at `http://localhost:8090`
- [ ] Review queue shows AI category suggestions

---

## Change Management and Recovery

### Before making risky changes

- back up `config/settings.toml`
- back up `data/` if preserving runtime history matters
- export or archive important XLS outputs
- stop services before deleting runtime DBs

### Rollback guidance

If a configuration or code change breaks the system:
1. stop Docker services
2. unload host LaunchAgents if needed
3. restore previous config or code revision
4. restart bridge first
5. restart Docker services
6. re-run health checks

---

## Versioning Note

This rewritten guide replaces the previous long-form mixed-status document with a more operational structure.

If you still need detailed historical release notes, parser-by-parser commentary, or exact Stage 3 design tables, preserve the previous guide in version control or split those details into separate documents such as:
- `GUIDE_OPERATIONS.md`
- `GUIDE_PDF_PARSERS.md`
- `GUIDE_FINANCE.md`
- `ROADMAP.md`
