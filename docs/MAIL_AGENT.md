# Mail Agent

Single source of truth for the local Mac Mini mail-agent, native mail-dashboard, Gmail IMAP intake, macOS bridge integration, and Phase 4 mail rules roadmap.

This document consolidates mail-agent material that was previously spread across the system design, operations, troubleshooting, changelog, preflight, and legacy mail-agent notes.

Keep the high-level architecture summary in `SYSTEM_DESIGN.md`, keep command-only runbooks in `OPERATIONS.md`, keep symptom-specific fixes in `TROUBLESHOOTING.md`, and keep this file as the detailed mail-agent reference.

---

## Current Status

| Area | Status |
|---|---|
| Dockerized `mail-agent` on Mac Mini | Implemented |
| macOS bridge for iMessage / host-only actions | Implemented |
| Gmail IMAP intake using App Passwords | Implemented |
| Docker credential fallback via `secrets/imap.toml` | Implemented |
| Native Electron `mail-dashboard` | Implemented |
| Dashboard APIs under `127.0.0.1:8090/api/mail/*` | Implemented |
| Mail-agent health/debug API on `127.0.0.1:8080` | Implemented |
| Phase 4A deterministic SQLite-backed rules engine | Implemented |
| Rules UI with account scoping and safe Phase 4A actions | Implemented |
| Phase 4B AI enrichment | Implemented, read-only |
| Phase 4C.1 IMAP mutation primitives | Implemented, gated/audited |
| Phase 4C.3A AI trigger actions | Implemented, preview-only |
| Phase 4E.2 final read-only verification + mock execution API | Implemented, non-mutating |
| Phase 4F natural-language rule builder | Planned, docs-only |
| Unsafe actions such as auto-reply, forward, webhook, unsubscribe | Not exposed |

Important database boundary:

```text
data/agent.db   = mail-agent runtime state, rules, audit, needs-reply, AI queues/classifications
data/finance.db = PWM / finance data only
```

Do not put mail rules in `finance.db`.

---

## Architecture

```text
Gmail IMAP account or bridge fallback
  -> Docker mail-agent (`agent/`)
  -> deterministic rules engine in `data/agent.db`
  -> local classifier providers
  -> bridge iMessage / PDF unlock on Mac host
  -> optional NAS PDF archive at `/mnt/mailagent`
  -> native Electron dashboard (`mail-dashboard/`)
```

Runtime placement:

```text
Mac host
  bridge/server.py          :9100
    - iMessage
    - host-local PDF unlock / PDF processing
    - bridge health

Docker on Mac
  mail-agent                :8080
    - polling / scan loop
    - IMAP intake
    - rule evaluation
    - classifier providers
    - attachment routing
    - local health/debug API

  finance-api               :8090
    - PWA backend
    - mounts `agent.app.api_mail`
    - exposes dashboard-facing `/api/mail/*`

Native app
  mail-dashboard/
    - Electron menu-bar UI
    - talks to `127.0.0.1:8090/api/mail/*`
```

The dashboard is a control surface only. Quitting the dashboard must not stop mail processing.

---

## Main Components

| Component | Responsibility |
|---|---|
| `agent/app/orchestrator.py` | Chooses IMAP or bridge fallback, enforces safety mode, runs rule/classifier flow, advances checkpoints after durable processing. |
| `agent/app/imap_source.py` | Gmail IMAP polling, UID/UIDVALIDITY state, bounded lookback, size guards, message identity, attachment metadata. |
| `agent/app/rules.py` | Phase 4A deterministic rules evaluator. |
| `agent/app/state.py` | SQLite migrations and helpers for `data/agent.db`. |
| `agent/app/pdf_router.py` | Sends PDF bytes to bridge `/pdf/unlock`, validates filenames, checks NAS sentinel, routes attachments. |
| `agent/app/net_guard.py` | Outbound, bridge, IMAP, and NAS readiness probes. |
| `agent/app/api_mail.py` | Mail dashboard API router mounted by finance API. |
| `agent/app/config_manager.py` | Account config persistence and atomic/fallback TOML writes. |
| `bridge/server.py` | Host-only bridge routes including `/pdf/unlock`, `/alerts/send`, `/health`, and `/healthz`. |
| `mail-dashboard/` | Electron + React + Tailwind native dashboard. |
| `scripts/mailagent_preflight.py` | Read-only inventory and sanity report. |
| `scripts/mailagent_status.py` | CLI smoke test for mail APIs. |

---

## API Boundaries

There are two different local API surfaces. Keep them separate.

### Dashboard-facing API

The native dashboard uses the finance API mount:

```text
http://127.0.0.1:8090/api/mail/*
```

Supported account/settings endpoints:

```text
GET    /api/mail/summary
GET    /api/mail/recent?limit=20
GET    /api/mail/accounts
POST   /api/mail/accounts/test
POST   /api/mail/accounts
PATCH  /api/mail/accounts/{account_id}
PATCH  /api/mail/accounts/{account_id}/enabled
DELETE /api/mail/accounts/{account_id}
POST   /api/mail/accounts/{account_id}/reactivate
POST   /api/mail/config/reload
POST   /api/mail/run
```

Rules endpoints should also live under this same `/api/mail/*` namespace.

Current rules endpoints:

```text
GET    /api/mail/rules
POST   /api/mail/rules
GET    /api/mail/rules/{rule_id}
PATCH  /api/mail/rules/{rule_id}
DELETE /api/mail/rules/{rule_id}
PUT    /api/mail/rules/reorder
POST   /api/mail/rules/preview
GET    /api/mail/processing-events
```

Planned Phase 4F AI rule-builder endpoints are documented in [phase-4f-natural-language-rule-builder.md](phase-4f-natural-language-rule-builder.md). They are draft/validate/preview/save-after-human-review surfaces only; AI must not write directly to `mail_rules` or execute mailbox actions.

### Worker health/debug API

The mail-agent container exposes:

```text
http://127.0.0.1:8080
```

Use this for local status/debug, not as the dashboard settings surface.

---

## Secrets And Credentials

Source of truth:

```text
macOS Keychain
```

Docker fallback file:

```text
secrets/imap.toml -> mounted read-only as /app/secrets/imap.toml
```

Expected `secrets/imap.toml` shape:

```toml
[[accounts]]
email = "user@gmail.com"
app_password = "xxxx xxxx xxxx xxxx"
```

Required local permission:

```bash
chmod 600 secrets/imap.toml
```

Hard rules:

- Never commit `secrets/imap.toml`.
- Never commit `scripts/export-finance-key.sh`.
- Do not paste App Passwords into docs, prompts, screenshots, logs, or Git.
- Dashboard/backend should normalize Gmail App Password whitespace before testing or saving.
- Placeholder accounts such as `YOUR_EMAIL@gmail.com` must be ignored by runtime and filtered from the dashboard.

---

## Gmail IMAP Intake

IMAP is enabled only when `[mail.imap].accounts` contains real enabled accounts. If only placeholders exist, the agent should continue using the bridge fallback.

Account state is tracked per account/folder:

```text
(account_id, folder, last_uid, uidvalidity)
```

Message identity:

```text
message_key          = sha256(account + folder + normalized Message-ID)
fallback_message_key = sha256(account + folder + uidvalidity + uid)
attachment_key       = sha256(message_key + sha256(pdf_bytes))
```

Checkpoint rule:

```text
Advance IMAP checkpoint only after message result is persisted and attachment rows are created or processed.
```

A PDF routing failure must not force refetch of the same email forever.

---

## Safety Modes

Configured under `[agent].mode`.

| Mode | Fetch + classify | iMessage | PDF route | Email mutation |
|---|---:|---:|---:|---:|
| `observe` | yes | no | no | no |
| `draft_only` | yes | yes | yes | no |
| `live` | yes | yes | yes | yes |

Current safe operating assumption:

```text
mode = draft_only
```

Invalid or missing mode resolves to `draft_only`, or `observe` when `[agent].safe_default = "observe"`.

Blocked actions must be recorded as `mode_blocked` events.

---

## PDF Attachment Routing

The agent must never pass container-local file paths to the bridge. It sends PDF bytes to bridge `/pdf/unlock` as multipart data, then receives unlocked bytes and metadata headers.

Required NAS routing setup:

```text
Host mount:   /Volumes/Synology/mailagent
Docker mount: /Volumes/Synology/mailagent:/mnt/mailagent
Sentinel:     /mnt/mailagent/.mailagent_mount
Config:       [mail_agent.pdf].mount_sentinel_uuid
```

Before writing attachments, `pdf_router.py` must verify:

- NAS mount exists.
- Sentinel exists.
- Sentinel UUID matches config.
- Write/delete probe succeeds.

If any check fails, attachment jobs should stay pending/retryable. Do not write into ephemeral container storage.

---

## Phase 4A Rules Engine

Phase 4A is deterministic and read-only from the mailbox perspective. It runs before classifier/AI logic and uses SQLite state in `data/agent.db`.

### Tables

```text
mail_rules
mail_rule_conditions
mail_rule_actions
mail_processing_events
mail_needs_reply
```

Future AI tables also stay in `data/agent.db`:

```text
mail_ai_queue
mail_ai_classifications
mail_ai_categories
mail_ai_trigger_rules
```

### SQLite Connection Contract

Every connection opened by `state.py` or new helpers should run:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

`journal_mode=WAL` is database-level and persistent. `foreign_keys` and `busy_timeout` are per-connection and must be set every time.

### Evaluator Semantics

Rules are evaluated in ascending priority.

Global rules and account-specific rules can interleave by priority. Priority uniqueness should treat `NULL` global account scope explicitly, for example through `COALESCE(account_id, '__global__')`.

Match behavior:

```text
match_type = ALL or ANY
enabled = 0 means skipped
stop_processing = halt later deterministic rules after current rule actions finish
skip_ai_inference = do not enqueue future AI inference
```

Each action should log an outcome to `mail_processing_events`. One action failure must not erase prior successful action logs.

### Phase 4A Safe Actions And Phase 4C.1 Mutation Primitives

Allowed in Phase 4A:

```text
mark_pending_alert
skip_ai_inference
add_to_needs_reply
route_to_pdf_pipeline
notify_dashboard
stop_processing
```

Recognized in Phase 4C.1, but gated by `[agent].mode` and `[mail.imap_mutations]`:

```text
move_to_folder
mark_read
mark_unread
mark_flagged
unmark_flagged
```

Still not allowed:

```text
add_label
send_imessage
delete
auto_reply
forward
unsubscribe
external_webhook
```

Do not expose unsafe actions in the dashboard until explicitly requested and implemented with capability checks, audit, and safety mode enforcement.

End-to-end Phase 4A verification has confirmed real mail processing with deterministic rules:

```text
phase4a_scan_summary messages_seen=3 messages_evaluator_ran=3 rules_matched=3 events_written=3
```

---

## Phase 4B AI Enrichment — Read-Only

Phase 4B is implemented as an opt-in, read-only AI triage layer. It runs after deterministic rule evaluation, enqueues eligible messages in `mail_ai_queue`, and stores validated Ollama output in `mail_ai_classifications`.

Default config:

```toml
[mail.ai]
enabled = false
provider = "ollama"
base_url = "http://host.docker.internal:11434"
model = "gemma3:4b"
temperature = 0.1
timeout_seconds = 45
max_body_chars = 12000
urgency_threshold = 8
```

Operational constraints:

- AI enrichment does not mutate email.
- Ollama calls happen outside DB transactions.
- Output is schema-validated before persistence.
- Validation failure writes `status='failed'` and `last_error`; no trigger fires.
- One in-flight Ollama request is enough for v1.
- Manual reprocess uses `manual_nonce` so it does not collide with existing queue uniqueness.
- AI enrichment still does not invoke mutation helpers or trigger actions.

---

## Phase 4C.1 IMAP Mutation Primitives

Phase 4C.1 adds infrastructure-level IMAP primitives only. It does not add AI-triggered actions, bulk mutation, auto-reply, forwarding, unsubscribe, webhooks, delete/expunge as a user-facing action, or AI-triggered iMessage.

Default config:

```toml
[mail.imap_mutations]
enabled = false
dry_run_default = true
allow_mark_read = false
allow_mark_unread = false
allow_add_label = false
allow_move_to_folder = false
require_uidvalidity_match = true
require_capability_cache = true
allow_create_folder = false
allow_copy_delete_fallback = false
```

Safety constraints:

- `observe` and `draft_only` always audit `mode_blocked`.
- `live` still requires `[mail.imap_mutations].enabled = true`.
- UIDVALIDITY is rechecked before any UID mutation.
- MOVE uses `UID MOVE` when supported.
- COPY + STORE `\Deleted` fallback is disabled by default and never calls EXPUNGE in this phase.
- STORE supports only `\Seen` and `\Flagged`.
- Every planned, blocked, dry-run, unsupported, completed, or failed mutation path writes `mail_processing_events`.

Phase 4D.5 adds readiness plumbing without enabling live mailbox mutation. Approval preview/detail can include `current_gate_preview.dry_run_plan` for future reversible candidates (`mark_read`, `mark_unread`, `add_label`, and cautious `move_to_folder`). The plan records account, folder, UID, UIDVALIDITY, the would-be IMAP operation, `would_mutate=false`, safety gates, and a rollback hint. Capability summaries are cached per account/folder in `imap_capability_cache`; discovery is read-only and must not create folders, mark messages, move messages, or label messages. If capability cache is missing/unknown, UIDVALIDITY is missing/mismatched, the account is disabled, or an allow flag is false, preview remains blocked. Under default config, approved mutation attempts still cannot perform real mailbox changes.

---

## Phase 4C.3A AI Trigger Actions — Preview-Only

AI triggers are deterministic rules evaluated after validated Phase 4B AI classifications are saved. They use `mail_ai_trigger_rules` and write `ai_trigger_matched` rows to `mail_processing_events`.

Supported trigger fields:

```text
category
urgency_score
confidence
needs_reply
summary
reason
```

Supported preview-only actions:

```text
notify_dashboard
send_imessage
move_to_folder
mark_read
mark_flagged
add_to_needs_reply
```

Safety constraints:

- Trigger matching is deterministic and never calls another LLM.
- All AI-trigger actions are forced to `dry_run=true`.
- AI triggers never call IMAP mutation helpers, bridge `send_alert`, auto-reply, forwarding, delete, expunge, unsubscribe, or webhooks in this phase.
- Trigger evaluation failure is audited separately and must not mark the AI classification as failed.
- Dashboard copy must clearly label AI triggers as preview-only.

---

## Phase 4D.1 Control Center — Operator Approval

Phase 4D.1 inserts a human approval queue between AI trigger suggestions and any execution attempt.

Default config:

```toml
[mail.approvals]
enabled = true
require_approval_for_ai_actions = true
approval_expiry_hours = 72
started_stale_after_minutes = 30
allow_bulk_approve = false
```

Safety constraints:

- AI triggers create `mail_action_approvals` rows; they do not execute actions.
- Operators approve or reject suggestions in the dashboard Control Center.
- Approval and execution are separate API operations.
- Approval does not bypass `[agent].mode`, `[mail.imap_mutations].enabled`, `dry_run_default`, UIDVALIDITY, folder, or IMAP capability checks.
- `send_imessage`, reply, forward, delete, expunge, unsubscribe, and webhooks remain blocked in this phase.
- Bulk approval is not implemented.

Approval API:

```text
GET  /api/mail/approvals
GET  /api/mail/approvals/{approval_id}
POST /api/mail/approvals/{approval_id}/approve
POST /api/mail/approvals/{approval_id}/reject
POST /api/mail/approvals/{approval_id}/execute
POST /api/mail/approvals/{approval_id}/expire
```

Execution support in this phase is limited to safe mailbox action attempts through the existing gated mutation path plus `add_to_needs_reply`. Unsupported approved actions are marked `blocked` and audited.

Approval detail responses include derived `execution_state`, `expires_at`, `approved_at`, `execution_started_at`, `execution_finished_at`, `blocked_reason`, `execution_error`, `gate_result`, `audit_event_ids`, and chronological `events`. The operator-facing states are `not_requested`, `started`, `executed`, `blocked`, `failed`, `expired`, `rejected`, and `stuck`.

`stuck` is read-only detection for approvals that reached `execution_status='started'` but did not finish before `started_stale_after_minutes`. The optional mark-failed endpoint is valid only for stale started approvals and records an audit event without retrying execution.

Phase 4D.3 adds read-only preview fields to list and detail responses: `preview_title`, `preview_summary`, `message_context`, `trigger_context`, `rule_context`, `risk_level`, `risk_reasons`, `reversibility`, `operator_guidance`, `would_execute_now`, `would_be_blocked_now`, and `current_gate_preview`. These fields are for operator review only. They do not approve, reserve, start, retry, or execute an action.

The Control Center should be read before approval: confirm why the item appeared, what trigger/rule matched, which sender/subject/account/folder/UID is affected, what action/target/value is proposed, and whether the current gate says dry-run, blocked by config, unsupported, terminal, stale/manual-review, or statically ready. `capability='unknown'` is expected when preview cannot know IMAP capabilities cheaply without a live mailbox transaction.

Phase 4D.4 adds lifecycle hygiene:

```text
GET  /api/mail/approvals/cleanup/preview
POST /api/mail/approvals/cleanup
POST /api/mail/approvals/{approval_id}/archive
POST /api/mail/approvals/{approval_id}/unarchive
GET  /api/mail/approvals/export?format=json
```

Cleanup is disabled by default. The preview is read-only. Explicit cleanup may expire old pending approvals and archive old terminal approvals, but started/stuck approvals are excluded and hard delete is not used in this phase. Archive hides terminal approvals from the active Control Center; audit history remains retained and exportable. JSON export includes sanitized approval/message context and optional events, not raw email bodies or secrets.

Phase 4D.5 readiness fields extend the same preview surface. For reversible candidates the Control Center shows mailbox identity, capability status, config blockers, dry-run plan, safety gates, and rollback hint with explicit wording that no mailbox change will occur under current settings. Dangerous actions still do not receive dry-run mutation plans.

---

## Phase 4F Natural Language Rule Builder — Planned

Phase 4F will add AI-assisted rule authoring without changing the mailbox mutation boundary. The intended flow is:

```text
User natural-language request
  -> AI rule drafting
  -> deterministic schema validation
  -> safety allow-list validation
  -> preview/diff
  -> human approve/save
  -> existing deterministic rules engine
```

AI drafts proposed rules only. It does not save rules, execute actions, or mutate Gmail/IMAP. Phase 4F.1 is scoped to safe non-mutating rule actions such as `mark_pending_alert`, `skip_ai_inference`, `add_to_needs_reply`, `route_to_pdf_pipeline`, `notify_dashboard`, and `stop_processing`. Gmail spam/move/label/read/unread mutations remain blocked or deferred unless they later pass through the Phase 4E approval/execution gates.

Detailed plan: [phase-4f-natural-language-rule-builder.md](phase-4f-natural-language-rule-builder.md).

---

## Transaction And I/O Boundary

Hard rule:

```text
No DB transaction may remain open across IMAP, Ollama, bridge, network, or filesystem I/O.
```

Required pattern:

```text
1. Open short transaction -> claim work -> commit.
2. Run external I/O outside any transaction.
3. Open short transaction -> persist result/audit/error -> commit.
```

This avoids SQLite writer contention and protects wake/retry behavior.

---

## Common Commands

Start bridge on Mac host:

```bash
PYTHONPATH=$(pwd) python3 -m bridge.server
```

Start/rebuild mail agent:

```bash
docker compose up --build -d mail-agent
docker compose logs -f mail-agent
```

Run status checks:

```bash
python3 scripts/mailagent_preflight.py
python3 scripts/mailagent_status.py --no-run
python3 scripts/mailagent_status.py
```

Run dashboard locally:

```bash
cd mail-dashboard
npm install
npm run build
npm run dev
```

After Python changes, rebuild the relevant Docker image. Restart alone may not pick up code baked into the image.

---

## Quick Verification Checklist

```bash
# 1. Config syntax
python3 -c "import tomllib; tomllib.load(open('config/settings.toml','rb')); print('OK')"

# 2. Provider registry
python3 -c "from agent.app.providers import PROVIDERS; print(sorted(PROVIDERS))"

# 3. Docker status
docker compose ps

# 4. Mail-agent logs
docker compose logs --tail=120 mail-agent

# 5. Finance API logs, needed because dashboard mail APIs mount there
docker compose logs --tail=120 finance-api

# 6. Mail status
python3 scripts/mailagent_status.py --no-run

# 7. Preflight
python3 scripts/mailagent_preflight.py
```

Expected provider baseline:

```text
rule_based must be accepted as a supported classifier provider.
```

## Phase 4 Release Operator Checklist

Use this checklist before a Phase 4A through Phase 4C.3A checkpoint:

```bash
# 1. Read-only inventory and environment sanity
python3 scripts/mailagent_preflight.py

# 2. Authenticated API status without triggering a scan
python3 scripts/mailagent_status.py --no-run

# 3. Verify AI settings through the dashboard API
curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  http://127.0.0.1:8090/api/mail/ai/settings | python3 -m json.tool

# 4. Test AI classification without queueing or mutating mail
curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8090/api/mail/ai/test \
  -d '{"sender":"alerts@example.com","subject":"Payment due reminder","body":"Your payment is due tomorrow."}' \
  | python3 -m json.tool

# 5. Preview deterministic rules; this endpoint is side-effect-free
curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8090/api/mail/rules/preview \
  -d '{"sender":"alerts@example.com","subject":"Payment due reminder","body":"Your payment is due tomorrow."}' \
  | python3 -m json.tool

# 6. Preview AI triggers; planned actions must remain dry-run
curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8090/api/mail/ai/triggers/preview \
  -d '{"category":"payment_due","urgency_score":8,"confidence":0.9,"needs_reply":false,"summary":"Payment due tomorrow","reason":"Due date detected"}' \
  | python3 -m json.tool
```

Default release posture:

- Keep `[mail.ai].enabled=false` unless deliberately validating read-only AI enrichment.
- Keep `[mail.imap_mutations].enabled=false` for the release checkpoint.
- Keep `[mail.imap_mutations].dry_run_default=true`; deterministic rule-managed mailbox actions should preview/audit unless intentionally promoted in `live`.
- Treat AI triggers as approval-only. They may write `ai_trigger_matched` audit events and pending Control Center approval items, but live autonomous AI-triggered actions are not enabled in Phase 4D.1.

---

## Troubleshooting

### Dashboard shows connection error

Likely causes:

- `finance-api` did not mount `/api/mail/*`.
- `finance-api` lacks dependencies such as `tomlkit` or `keyring`.
- `mail-agent` is not running.
- NAS mount prevented the mail-agent container from starting.
- API key mismatch.

Commands:

```bash
docker compose ps
docker compose logs --tail=120 finance-api
docker compose logs --tail=120 mail-agent
python3 scripts/mailagent_status.py --no-run
```

Fix:

```bash
docker compose up --build -d finance-api mail-agent
export VITE_FINANCE_API_KEY="$FINANCE_API_KEY"
cd mail-dashboard
npm run dev
```

### Gmail account test fails with whitespace / ASCII error

Likely cause: pasted App Password contains spaces or non-breaking spaces.

Fix:

```bash
docker compose up --build -d finance-api
```

Then retry from the dashboard. Current code should strip whitespace automatically.

### Account saves but does not appear

Likely cause: invalid TOML shape under `[mail.imap].accounts` from an older writer.

Diagnose:

```bash
python3 -c "import tomllib; tomllib.load(open('config/settings.toml','rb')); print('OK')"
```

Expected inline account style:

```toml
[mail.imap]
accounts = [
  {email = "user@gmail.com", provider = "gmail", id = "gmail_user", name = "User", host = "imap.gmail.com", port = 993, ssl = true, auth_type = "app_password", folders = ["INBOX"], lookback_days = 14, max_message_mb = 25, max_attachment_mb = 20, enabled = true, auth_source = "keychain", keychain_service = "agentic-ai-mail-imap"},
]
```

### Saving settings fails with `settings.tmp -> settings.toml` busy

Likely cause: Docker bind mount rejects atomic rename on single-file mount.

Fix:

```bash
docker compose up --build -d finance-api
```

Current code should use an in-place fsynced write fallback when atomic rename is blocked.

### Mail-agent container will not start after rebuild

Likely cause: NAS share missing or Docker Desktop cannot access `/Volumes`.

Diagnose:

```bash
ls -la /Volumes
ls -la /Volumes/Synology/mailagent
docker compose ps
docker compose logs --tail=80 mail-agent
```

Fix:

```bash
# Mount Synology share first, then:
docker compose up --build -d mail-agent
python3 scripts/mailagent_status.py --no-run
```

### `data/agent.db` is malformed

Stop the agent first:

```bash
docker compose stop mail-agent
```

Create a timestamped copy before any repair attempt:

```bash
mkdir -p data/db_recovery
cp -a data/agent.db "data/db_recovery/agent.db.$(date +%Y%m%d-%H%M%S).bak"
```

Run integrity check:

```bash
sqlite3 data/agent.db "PRAGMA integrity_check;"
```

Attempt recover into a new DB:

```bash
sqlite3 data/agent.db ".recover" | sqlite3 data/agent.recovered.db
sqlite3 data/agent.recovered.db "PRAGMA integrity_check;"
```

If recovered DB is OK:

```bash
mv data/agent.db "data/agent.db.bad.$(date +%Y%m%d-%H%M%S)"
mv data/agent.recovered.db data/agent.db
docker compose up --build -d mail-agent
```

If recovery fails, keep the backup and recreate only mail-agent runtime state. Do not touch `data/finance.db`.

---

## Related Documentation

Documentation layout:

```text
README.md                  Short entry point and status

docs/SYSTEM_DESIGN.md      Stable architecture and cross-component contracts
docs/MAIL_AGENT.md         This file: detailed mail-agent design, operations, rules, troubleshooting
docs/OPERATIONS.md         Common commands for the whole system; link to MAIL_AGENT.md for mail details
docs/TROUBLESHOOTING.md    Cross-system symptom fixes; link to MAIL_AGENT.md for mail-specific fixes
docs/DECISIONS.md          ADR-style rationale only
docs/CHANGELOG.md          Reverse chronological history only
docs/ch-hp-worklow.md      Keep separate; homepage workflow is a separate project area
```
