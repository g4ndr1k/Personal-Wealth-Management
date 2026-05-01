# Operations

How to run, validate, and maintain the system. Architecture details live in [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md); symptom-driven fixes live in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## Common Commands

### Bridge

```bash
PYTHONPATH=$(pwd) python3 -m bridge.server
```

The bridge runs on the Mac host, not inside Docker. It exposes `http://127.0.0.1:9100` or the configured host/port in `config/settings.toml`.

Health checks:

```bash
curl http://127.0.0.1:9100/healthz
TOKEN=$(cat secrets/bridge.token)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9100/health | python3 -m json.tool
```

### Finance API

```bash
python3 -m finance.server
python3 -m finance.server --reload
```

Docker:

```bash
docker compose up --build -d
docker compose logs -f finance-api
```

Always rebuild the Docker image after Python changes. A container restart alone does not pick up changed source files baked into the image.

The finance image now also hosts the native mail-dashboard account-management routes under `/api/mail/*`. If dashboard mail settings fail after a Python change, rebuild `finance-api`, not just `mail-agent`.

### Mail Agent

The mail agent runs as the `mail-agent` Docker service. Worker health/debug is on `127.0.0.1:8080`; dashboard-facing APIs are mounted by `finance-api` at `127.0.0.1:8090/api/mail/*`. Detailed mail-agent operations live in [MAIL_AGENT.md](MAIL_AGENT.md).

```bash
docker compose up --build -d mail-agent
docker compose logs -f mail-agent
python3 scripts/mailagent_status.py --no-run
python3 scripts/mailagent_status.py
python3 scripts/mailagent_preflight.py
```

`POST /api/mail/run` queues a scan cycle; it does not run mail processing inside the dashboard process.
Mail-agent runtime state, rules, audit events, needs-reply rows, and future AI queues live in `data/agent.db`, not `data/finance.db`.

### Mail Dashboard

```bash
cd mail-dashboard
npm install
npm run build
npm run dev
```

The dashboard is an Electron menu-bar app. It is a viewer/control surface only; mail processing continues when the dashboard is closed. For account setup, Gmail App Password handling, safe rule actions, and rules UI details, see [MAIL_AGENT.md](MAIL_AGENT.md).

Phase 4F natural-language rule authoring is documented in [phase-4f-natural-language-rule-builder.md](phase-4f-natural-language-rule-builder.md). Phase 4F.1a is implemented for deterministic/local sender suppression drafts only. Treat it as an AI draft/validate/human-save workflow only. It must not save rules without review, execute actions, or mutate Gmail/IMAP. “Spam list” currently means local Mail Agent suppression, not Gmail Spam.

#### Synthetic approval visual QA

For Phase 4D.7 Control Center review, run the dashboard in development with synthetic approval fixtures enabled:

```bash
cd mail-dashboard
VITE_APPROVAL_FIXTURES=1 npm run dev
```

Open Control Center and use the `Synthetic QA on` toggle. The populated approvals are frontend-only fake records for `fixture.gmail.local` / `INBOX`; they do not require Gmail, IMAP credentials, or rows in `data/agent.db`.

Synthetic mode is visibly labelled in the header, rows, and detail panel. Approval, execution, archive, and cleanup controls are read-only in this mode, and cleanup/export data comes from local fixtures. This mode is for UI verification before Phase 4E only and must not be used as evidence that live mailbox mutation is enabled.

### PWA

```bash
cd pwa
npm install
npm run dev
npm run build
```

`pwa/dist/` must exist before building production Docker images that serve the PWA from FastAPI.

### Household Expense PWA

```bash
cd household-expense/pwa
npm install
npm run build

cd ~/agentic-ai/household-expense
bash deploy_household.sh
```

The household app runs on the NAS as `household-api` on port `8088`. The deploy script builds the Vue PWA, copies icons into `household-expense/dist`, rsyncs to `/volume1/docker/household-expense`, rebuilds the NAS Docker container, and verifies `/api/household/health`.

Health checks:

```bash
curl http://192.168.1.44:8088/api/household/health
curl -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/household/settings
```

### Import And Backup

```bash
python3 -m finance.importer --dry-run
python3 -m finance.importer
python3 -m finance.importer --overwrite
python3 -m finance.backup --kind manual
```

`finance.importer` reads `output/xls/ALL_TRANSACTIONS.xlsx` and writes to `data/finance.db`.

NAS automation defaults:

- NAS SSH user: `chfun`
- NAS SSH port: `22`
- Finance replica sync target: `chfun@192.168.1.44:/volume1/finance/finance_readonly.db`
- Local sudo password file for NAS automation: `secrets/nas_sudo_password`

## Process Local PDF Workflow

Use Settings -> Process Local PDFs in the PWA for manual processing.

1. Drop PDFs into `data/pdf_inbox/` or use already-unlocked files in `data/pdf_unlocked/`.
2. Open Settings -> Process Local PDFs.
3. Open PDF Workspace.
4. Select specific files, or leave none selected to process all ready files.
5. Click Process Selected / Process Ready PDFs.
6. The PWA runs preflight, queues all files, polls each job, and shows per-file status.

Status meanings:

| Status | Action |
|---|---|
| `new` | Ready to process. |
| `pending` | Queued. |
| `running` | Processing. |
| `done` | Fully processed. |
| `partial` | Main parse succeeded; secondary write failed. Inspect details. |
| `error` | Processing failed. Inspect details and bridge logs. |
| `missing_source` | Registry row points to a PDF that no longer exists. |

## IMAP PDF Attachment Routing

IMAP PDF attachments are handled by the Docker mail agent, not the Electron renderer. Detailed routing and safety checks are documented in [MAIL_AGENT.md](MAIL_AGENT.md).

The Docker bind mount must map the host NAS path:

```yaml
mail-agent:
  volumes:
    - /Volumes/Synology/mailagent:/mnt/mailagent
```

Current mount recovery checklist:

```bash
ls -la /Volumes/Synology/mailagent
docker compose ps
docker compose up --build -d mail-agent
python3 scripts/mailagent_status.py --no-run
```

## Preflight Validation

The PWA calls `GET /api/pdf/preflight`, which proxies bridge `GET /pdf/preflight`, before queueing work.

Preflight checks:

- bridge initialized its PDF handler config
- provider order contains only supported providers
- PDF inbox and unlocked folders exist
- bridge job database is reachable
- runtime diagnostics are available

Manual checks:

```bash
TOKEN=$(cat secrets/bridge.token)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:9100/pdf/preflight | python3 -m json.tool

curl -s -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/pdf/preflight | python3 -m json.tool
```

## Config Validation

Main config file: `config/settings.toml`.

Important sections:

| Section | Used by |
|---|---|
| `[bridge]` | Bridge host, port, log level. |
| `[auth]` | Bridge token source and file/keychain settings. |
| `[mail]` | Mail source and Gmail secrets. |
| `[classifier]` | Provider order and rule loading. |
| `[pdf]` | PDF folders, password source, job DB, verification. |
| `[pipeline]` | Scheduled PDF/import/backup pipeline. |
| `[finance]` | SQLite DB path and XLSX input. |
| `[fastapi]` | Finance API host, port, CORS. |
| `[ollama_finance]` | Finance categorization AI settings. |
| `[household]` | Household satellite base URL and API key file. |
| `[coretax]` | CoreTax template/output dirs. Legacy match/rounding/alias keys are no longer used by the persistent ledger. |

Validate TOML syntax:

```bash
python3 -c "import tomllib; tomllib.load(open('config/settings.toml','rb')); print('OK')"
```

For IMAP accounts under `[mail.imap]`, keep `accounts` as an array of inline tables, for example:

```toml
[mail.imap]
accounts = [
  {email = "user@gmail.com", provider = "gmail", id = "gmail_user", name = "User", host = "imap.gmail.com", port = 993, ssl = true, auth_type = "app_password", folders = ["INBOX"], lookback_days = 14, max_message_mb = 25, max_attachment_mb = 20, enabled = true, auth_source = "keychain", keychain_service = "agentic-ai-mail-imap"},
]
```

Validate provider configuration:

```bash
python3 -c "from agent.app.providers import PROVIDERS; print(sorted(PROVIDERS))"
```

## Matching Engine Operations

The generic matching engine stores per-domain mappings and audit data in `matching_*` tables. Parser routing, dedup, and categorization engine paths are intentionally flag-gated during rollout:

| Flag | Values | Effect |
|---|---|---|
| `PARSER_USE_ENGINE` | `true` / `false` | Route PDFs through the parser matching domain before legacy fallback. |
| `DEDUP_USE_ENGINE` | `true` / `false` | Use the dedup domain for parser-variant reconciliation during import. |
| `CATEGORIZATION_USE_ENGINE` | `false` / `shadow` / `true` | `shadow` logs Tier-1 disagreements while returning legacy results; `true` allows Tier-1 cached categorization. |
| `MATCHING_ENABLED_<DOMAIN>` | `true` / `false` | Runtime-config default for generic engine domain status where callers use `finance.matching.runtime_config`. |

Useful API probes:

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" http://127.0.0.1:8090/api/matching/stats | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/matching/parser/mappings?limit=20" | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/matching/shadow-diffs?limit=20" | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/matching/invariant-log?limit=20" | python3 -m json.tool
```

Validation:

```bash
python3 -m pytest tests/matching -q
python3 -m pytest tests/test_categorizer.py tests/test_importer_duplicate_reconcile.py tests/matching -q
```

Operational notes:

- Keep legacy flags off unless actively testing a domain rollout.
- Use `CATEGORIZATION_USE_ENGINE=shadow` before `true`; shadow mode records disagreements in `category_shadow_diff`.
- A missing mapping confirmation should return `404 mapping_not_found`; unexpected 500s on `/api/matching/*` are bugs.
- Dynamic matching table access must go through `finance.matching.storage`; do not hand-build `matching_<domain>` SQL in new code unless the domain is validated by API allow-list or storage helpers.

## Secrets

macOS Keychain is the source of truth. `secrets/` files are runtime/export artifacts for Docker and local scripts.

Common commands:

```bash
python3 -m bridge.secret_manager init
python3 scripts/export-secrets-for-docker.py
bash scripts/export-secrets-for-docker.sh
```

Expected secret files:

| File | Purpose |
|---|---|
| `secrets/bridge.token` | Bridge bearer token. |
| `secrets/imap.toml` | Docker runtime Gmail IMAP app passwords. Mounted into mail-agent as `/app/secrets/imap.toml`. |
| `secrets/gmail.toml` | Legacy Gmail app passwords if older bridge fallback is used. |
| `secrets/banks.toml` | Bank PDF passwords if file fallback is used. |
| `secrets/nas_sync_key` | NAS SSH private key. |
| `secrets/nas_sudo_password` | Synology sudo password used by NAS deploy scripts. |
| `secrets/finance_api.key` | Exported finance API key used by NAS finance deploy script. |

`secrets/bridge.token` must be a file, not a directory, and should have mode `600`.

## Logs

| Log | Command |
|---|---|
| Bridge app log | `tail -f logs/bridge.log` |
| Docker finance API | `docker compose logs -f finance-api` |
| Docker mail agent | `docker compose logs -f mail-agent` |
| Batch processor | `tail -f logs/batch_process.log` |

LaunchAgent stdout/stderr locations depend on the installed plist. Check `launchd/` and the plist `StandardOutPath` / `StandardErrorPath` values.

## Maintenance

Backups:

- The finance DB is backed up with SQLite online backup helpers.
- Manual backups can be triggered from Settings or `python3 -m finance.backup --kind manual`.
- NAS sync uses SSH and the configured NAS target.
- Current default NAS sync target is `chfun@192.168.1.44:/volume1/finance/finance_readonly.db`.
- `finance.backup` now uses SSH port `22` by default; override with `NAS_SYNC_SSH_PORT` only if the NAS SSH port changes.

PDF registry:

- Processed files are tracked by SHA-256, so renaming a file does not make it new.
- `missing_source` means a registry entry points to a file no longer on disk.
- Failed jobs can be retried after correcting parser/config/password issues.

PWA cache:

- Mobile/offline GET caching is intentional.
- Settings includes a Refresh Mobile Data action.
- PDF endpoints are network-only or freshness-forced so local processing state does not come from stale cache.

Household maintenance:

- `household-expense/data/household.db` is separate from `data/finance.db`.
- Household transactions are soft-deleted with `is_deleted`.
- Settings -> Household Expense manages categories, recent expense category corrections, and cash pools through the finance API proxy.
- The household API key is loaded from `household-expense/secrets/household_api.key` via `[household].api_key_file`.
- Reconciliation import into the main finance model should be verified before documenting it as fully automated.

## CoreTax SPT Workflow

Use PWA → CoreTax SPT for the annual Indonesian DJP tax return (SPT) export.

The current implementation is a persistent tax-version ledger. It is not a one-shot template filler. SQLite stores reviewed tax values, lock flags, learned mappings, import staging rows, and reconcile history.

1. Confirm the canonical export template exists under `data/coretax/templates/`. Keep it chart/image-free because `openpyxl` does not roundtrip those reliably.
2. Upload the prior-year submitted SPT XLSX in the **Import Previous SPT** tab. For example, a workbook with headers `E=2025` and `F=2026` must be uploaded with `target_tax_year=2026`.
3. Review staged rows. The staging table preserves raw Excel cells for B/H descriptions and E/F/G values so the import can be audited before commit.
4. Commit staging to create `coretax_rows` for the target tax year. Sticky codes carry forward; refreshable codes stay unset.
5. Use **Review** to edit rows or lock/unlock fields. Any manual edit to `current_amount_idr` or `market_value_idr` auto-locks that field.
6. Use **Mapping** before reconcile. The live unmapped list is computed from current PWM rows and learned mappings, not from the last reconcile run. Map PWM items to existing rows, create rows for genuinely new tax rows, or run suggestions and preview high-confidence bulk accepts.
7. Use **Reconcile from PWM** with the relevant month range. Reconcile reads `account_balances`, `holdings`, and `liabilities` for the snapshot date and only writes unlocked fields. It applies explicit mappings first, then safe 1:1 heuristics (unique ISIN or account-number matches) that can be learned as `auto_safe` mappings.
8. Use **Export CoreTax XLSX**. The API writes `CoreTax_{tax_year}_vN.xlsx` plus `CoreTax_{tax_year}_vN.audit.json` under `data/coretax/output/` and returns only `file_id`, `download_url`, and `audit_url`.

Carry-forward defaults:

| Kode | Behavior |
|---|---|
| `012`, `034`, `036`, `039` | Refreshable from PWM; committed with `current_amount_idr = NULL`. |
| `038`, `042`, `043`, `051`, `061` | Sticky/acquisition-cost rows; committed with `current_amount_idr = prior_amount_idr`. |

Useful API probes:

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/coretax/summary?tax_year=2026" | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/coretax/rows?tax_year=2026" | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/coretax/2026/unmapped-pwm" | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/coretax/mappings/lifecycle?year=2026" | python3 -m json.tool
curl -s -H "X-Api-Key: $FINANCE_API_KEY" "http://127.0.0.1:8090/api/coretax/reconcile-runs?tax_year=2026" | python3 -m json.tool
```

Mapping notes:

- Mapping fingerprints are generated by `finance/coretax/fingerprint.py`; do not hand-type hashes unless debugging.
- `account_number_norm` values are SHA-256 hashes of `institution_norm:account_norm`; use `fingerprint_raw` in API responses for audit/display.
- `isin` values are stored as normalized ISINs.
- `holding_signature` and `liability_signature` are more volatile because they include editable names; expect lower confidence and lifecycle review.
- Mapping assignment conflicts return HTTP 409 with structured details instead of silently retargeting a source fingerprint.
- Rejected suggestions are remembered in `coretax_rejected_suggestions`; use the Mapping tab controls to reverse or manually override them.

Reconcile notes:

- `CORETAX_LEGACY_HEURISTICS` defaults to off. Only enable it temporarily for migration/debugging because it can attach rows by weaker substring or single-row heuristics.
- Component history is retained in `coretax_row_components`. Current components have `is_current=1`; older run components are kept with `is_current=0` for run diff/debugging.
- Low-confidence mapping matches are allowed but should be reviewed in the Mapping tab.

CLI smoke test using a local workbook and scratch DB:

```bash
python3 - <<'PY'
import sqlite3
from pathlib import Path
from finance.coretax.db import ensure_coretax_tables
from finance.coretax.import_parser import parse_prior_year_xlsx
from finance.coretax.carry_forward import commit_staging_batch

workbook = Path('/Users/g4ndr1k/Library/CloudStorage/OneDrive-Personal/Finance/SPT/2025/CoreTax 2025.xlsx')
conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
ensure_coretax_tables(conn)
result = parse_prior_year_xlsx(workbook, 2026, conn)
print(result['row_count'], 'rows staged')
print(commit_staging_batch(conn, result['batch_id'], 2026))
PY
```

NAS replica: all CoreTax write endpoints are blocked under `FINANCE_READ_ONLY=true` and return 403. GET endpoints remain available for review/download of already-synced data.

## Mail AI Enrichment

Phase 4B AI enrichment is read-only. It adds an Ollama-backed triage layer on top of deterministic mail rules and the existing classifier; it does not move, label, delete, reply, forward, unsubscribe, or trigger iMessage actions.

Phase 4C.1 IMAP mutation primitives are available only behind the safety ladder. Keep `[mail.imap_mutations].enabled=false` and `dry_run_default=true` unless deliberately testing in `live`; `observe` and `draft_only` always audit `mode_blocked`.

Phase 4C.3A AI triggers write `ai_trigger_matched` dry-run audit events after AI classification. In Phase 4D.1, matched actions also create pending Control Center approval items in `data/agent.db:mail_action_approvals`; they still do not execute autonomously.

Approval authorizes only human intent for a later execution attempt. In Phase 4E.2, the execution chassis is still mock-only: gates can report readiness/blockers, final verification can read mailbox identity with read-only IMAP selection/fetches, and the mock executor can write metadata-only audit rows only after verification passes. Read/unread are still not live and no approval path touches Gmail with mutating commands or issues IMAP flag mutations. The existing gates still decide the final outcome: `[agent].mode`, `[mail.imap_mutations].enabled`, `dry_run_default`, UIDVALIDITY, folder metadata, IMAP capabilities, and the action allow/block list. There is no bulk approval or bulk execute path. `send_imessage`, reply, forward, delete, expunge, unsubscribe, labels, folder moves, and webhooks remain blocked and should appear only as planned/blocked/audited records.

Phase 4F can be developed before Phase 4E.3 only because its first scope is non-mutating rule authoring. AI drafts proposed deterministic rules, deterministic validation checks them, and a human saves them. Gmail spam, move, label, read, unread, reply, forward, unsubscribe, webhook, and delete-like mutations remain blocked/deferred unless a future Phase 4E execution gate explicitly supports them.

Phase 4D.6 Control Center polish is visual/operator clarity only. Readiness preview is not live mutation. For future reversible candidates, the Control Center shows a safety banner, summary, blockers, dry-run plan, UID/UIDVALIDITY identity, cached capability status, config blockers, and rollback hint. Treat **Readiness only**, **Live mutation disabled**, **Approval records human intent only**, and **Requires explicit config enablement in a later phase** literally; there is no UI control to enable live mutation from the dashboard.

Operator workflow:

1. Open the dashboard Control Center and review pending approval items one at a time.
2. Read the preview title, message sender/subject/account/folder/UID, AI trigger or rule explanation, proposed action, risk badge, and current gate preview.
3. Treat **Dry-run only**, **Blocked by config**, **Unsupported action**, and **No mailbox change would occur under current settings** as expected safety outcomes, not live mutation.
4. Use **Approve attempt** only when the preview context is acceptable; this still does not execute anything by itself and records human intent only.
5. Use **Audit gated attempt** on an approved item. Read the resulting execution state: `executed`, `blocked`, `failed`, `expired`, `rejected`, `started`, or `stuck`. When mock execution is available in dev/test, final verification must pass first and the result must be labelled mock-only.
6. For `blocked`, inspect the gate result. Dry-run and mutation-disabled outcomes mean no mailbox change was made.
7. For `stuck`, do not retry automatically. Review the audit trail and mark failed only after confirming the worker did not finish.
8. Use **Archive from active view** only for terminal approvals. Archive hides the row from the active view; audit is retained and the row can be unarchived from History.
9. Use **Cleanup preview** to inspect how many old pending approvals would expire and how many terminal approvals would archive. The preview is read-only and excludes started/stuck approvals.
10. Use **Export JSON** for sanitized approval/audit history when reviewing lifecycle decisions.

Settings live in `config/settings.toml`:

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

Lifecycle:

1. IMAP scan evaluates deterministic rules.
2. If AI is enabled and rules did not set `skip_ai_inference`, the message is inserted into `data/agent.db:mail_ai_queue`.
3. The single AI worker claims one pending row, commits, calls Ollama outside SQLite, then persists completion or failure.
4. Completed rows write `mail_ai_classifications`; failed rows retain `last_error` and attempts.
5. Manual reprocess creates a second queue row with a fresh `manual_nonce`.

Useful probes:

```bash
curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  http://127.0.0.1:8090/api/mail/ai/settings | python3 -m json.tool

curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  'http://127.0.0.1:8090/api/mail/approvals?status=pending&limit=50' \
  | python3 -m json.tool

curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  "http://127.0.0.1:8090/api/mail/approvals/$APPROVAL_ID" \
  | python3 -m json.tool

curl -s -H "X-Api-Key: $FINANCE_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8090/api/mail/ai/test \
  -d '{"sender":"alerts@example.com","subject":"Payment due reminder","body":"Your payment is due tomorrow."}' \
  | python3 -m json.tool
```

Phase 4F local rule drafting probe:

```toml
[mail.rule_ai]
enabled = false
provider = "ollama"
base_url = "http://host.docker.internal:11434"
model = "qwen2.5:7b-instruct-q4_K_M"
timeout_seconds = 30
temperature = 0.0
max_request_chars = 1000
```

Keep this disabled unless you are explicitly testing local Ollama rule drafting. The current recommended local model for the narrow Phase 4F alert-rule flow is `qwen2.5:7b-instruct-q4_K_M`; Gemma failed the initial schema probe, and Qwen only became reliable after schema hardening. Phase 4F.1c expands deterministic bank/domain hints and Indonesian/English intent normalization, but the probe still drafts only safe local alert rules, uses structured Ollama JSON schema output where supported, post-validates model output, and never saves rules automatically. The draft endpoint does not send iMessage, mutate Gmail/IMAP, call mailbox execution code, or write rule rows; Save Rule remains the separate human-triggered `POST /api/mail/rules` path. Deterministic validation remains authoritative; cloud LLM support remains deferred.

Phase 4F.1c supported deterministic bank hints include Permata, BCA, KlikBCA, CIMB Niaga, Maybank, Mandiri/Livin, BNI, BRI, OCBC NISP, UOB, HSBC, DBS, Jenius, and BSI. Supported intent normalization covers credit-card clarification/confirmation, suspicious/security/login alerts, payment due/billing, OTP/verification-code, and failed/declined transactions. Unsupported or overbroad requests return `status=unsupported`, `saveable=false`, and no rule; no rule is saved by the draft endpoint.

Manual local-Qwen golden prompt smoke probe:

```bash
export FINANCE_API_KEY="$(cat secrets/finance_api.key 2>/dev/null || echo "$FINANCE_API_KEY")"
python3 scripts/mail_rule_ai_golden_probe.py \
  --api-base http://127.0.0.1:8090 \
  --fixture agent/tests/fixtures/rule_ai_golden_prompts.json \
  --timeout 120
```

Expected shape:

```text
PASS bca_suspicious_transaction -> bca.co.id
PASS cimb_credit_card_confirmation -> cimbniaga.co.id
...
10 passed, 0 failed
```

Run this only when intentionally testing local rule AI with `[mail.rule_ai].enabled=true` and local Ollama reachable. The probe calls only `POST /api/mail/rules/ai/draft`; it never calls Save Rule, never sends iMessage, never mutates Gmail/IMAP, and never creates labels, moves mail, marks read/unread, deletes, archives, forwards, replies, unsubscribes, or calls webhooks. Keep `[mail.rule_ai].enabled=false` when not actively testing.

Dashboard golden probe:

```text
Settings -> Rules -> Rule AI Golden Probe -> Run Golden Probe
```

The dashboard calls `POST /api/mail/rules/ai/golden-probe`, which uses the same shared validation logic as the CLI and returns a compact disabled/pass/fail report. The panel is manual only. It does not expose Save Rule for probe results, does not create normal AI Rule Builder drafts, does not save rules, does not send iMessage, does not call the bridge, and does not mutate Gmail or IMAP. Disabled mode is expected when `[mail.rule_ai].enabled=false`; treat it as a safe-default state, not an operational failure.

Save/preview compatibility:

```text
AI draft -> human Save Rule -> saved deterministic rule -> local preview/evaluation
```

Phase 4F.1f verifies this workflow for both sender suppression drafts and local alert-rule drafts. The draft and golden-probe endpoints remain read-only for `mail_rules`, `mail_rule_conditions`, and `mail_rule_actions`; only an explicit human-triggered `POST /api/mail/rules` writes rule rows. Saved AI-drafted rules are normal deterministic rules. Preview uses `POST /api/mail/rules/preview` with synthetic/sample message data and must not inspect a real mailbox, call IMAP, call the bridge, mutate Gmail, or send iMessage.

Field compatibility note: AI drafts may use `from_email` and `from_domain`. The deterministic engine maps `from_email` to `sender_email` and derives `from_domain` / `sender_domain` from `sender_email` when no explicit domain field is present. This keeps saved alert drafts match-compatible with normal IMAP message payloads.

Rule explanation / dry-run inspector:

```http
POST /api/mail/rules/explain
```

Phase 4F.2a adds a deterministic inspector for saved rules. Operators provide a synthetic/sample message payload and optionally a `rule_id`; the endpoint evaluates saved rules with `preview=true` and returns why each condition matched or failed, expected vs actual values, planned local actions, and flags such as `would_skip_ai`, `stopped`, `enqueue_ai`, and `route_to_pdf_pipeline`.

This is read-only operator debugging. It does not fetch real mailbox messages, call IMAP, call the bridge, send iMessage, mutate Gmail, create labels, move mail, mark read/unread, write processing events, write approval rows, write action execution rows, save rules, call Ollama, or call a cloud LLM. Mutation actions that already exist on saved rules are shown as dry-run/blocked preview metadata only. The dashboard **Explain Rule** panel uses this endpoint and can synthesize sample messages from AI drafts, including deriving `alerts@domain` samples for `from_domain` conditions.

Rule AI audit and quality metrics:

```text
GET /api/mail/rules/ai/audit/recent?limit=50
GET /api/mail/rules/ai/audit/summary
GET /api/mail/rules/ai/golden-probe/runs?limit=20
```

Phase 4F.1g records local observability rows for draft/probe attempts. Draft audit stores a SHA-256 request hash, a sanitized/truncated preview, status/saveability, safety status, provider/model, rule shape counts, warnings/explanations, and optional saved-rule linkage when a human later saves the draft. It does not store full raw prompts, raw model output, API keys, app passwords, bridge tokens, email bodies, or mailbox identifiers. Audit writes are best-effort: a draft should still return even if audit persistence fails, and failures are logged for operator follow-up.

The dashboard **Rule AI Quality** panel shows totals, saveable rate, latest golden probe result, and recent draft attempts. It has no rerun, save, execute, or mailbox action controls. Golden probe runs are quality metrics only; the endpoint still does not save rules, send iMessage, call the bridge, call IMAP, or mutate Gmail.

Rebuild `finance-api` after changing Python code or config that affects this path. The safe default posture remains `[mail.rule_ai].enabled=false` until an operator intentionally tests AI rule drafting.

## Existing Specialized Docs

These docs remain as focused notes and were not folded into the core docs set:

- `docs/ch-hp-worklow.md`
