# System Design

Stable architecture blueprint for the Agentic AI personal finance and mail alert system.

Operational commands live in [OPERATIONS.md](OPERATIONS.md). Symptom-driven fixes live in [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Change history lives in [CHANGELOG.md](CHANGELOG.md). Design rationale lives in [DECISIONS.md](DECISIONS.md).

## System Overview

The project runs as separate cooperating processes:

```text
Mac host
  bridge/                 host Python service on :9100
    mail/iMessage access
    PDF processing jobs
    bridge API

Docker on Mac
  agent/                  mail alert worker
  finance/ + pwa/dist     FastAPI + built Vue PWA on :8090

Optional NAS
  finance API replica     read-only copy of data/finance.db
  household-expense/      LAN-only household expense satellite app
```

Primary data path:

```text
Bank PDFs
  -> bridge/pdf_handler.py
  -> parsers/router.py and bank parser
  -> exporters/xls_writer.py
  -> output/xls/ALL_TRANSACTIONS.xlsx
  -> finance/importer.py
  -> data/finance.db
  -> finance/api.py
  -> pwa/
```

## Component Map

| Component | Responsibility |
|---|---|
| `bridge/server.py` | Host HTTP bridge, bearer auth, route dispatch, bridge startup wiring. |
| `bridge/pdf_handler.py` | PDF job queue, local PDF processing, preflight, status lifecycle, registry updates. |
| `bridge/pdf_unlock.py` | Password-protected PDF unlock helpers. |
| `agent/app/classifier.py` | Mail classifier orchestration using registered providers. |
| `agent/app/providers/` | Provider implementations and `PROVIDERS` registry. |
| `parsers/router.py` | Bank and statement type detection, parser dispatch. |
| `parsers/*.py` | Bank-specific PDF extraction logic. |
| `exporters/xls_writer.py` | XLS output, including `ALL_TRANSACTIONS.xlsx`. |
| `finance/importer.py` | XLSX to SQLite import, deduplication, categorization, header validation. |
| `finance/db.py` | SQLite schema, migrations, resolved transaction view. |
| `finance/api.py` | FastAPI backend, PWA static mount, bridge proxy routes, wealth APIs. |
| `pwa/src/api/client.js` | Frontend API client and cache behavior. |
| `finance/coretax/` | Persistent CoreTax SPT ledger: prior-year import, carry-forward, reconcile, learned mappings, XLSX export. |
| `pwa/src/views/CoreTaxSpt.vue` | CoreTax SPT wizard: import, carry-forward review, reconcile from PWM, manual mapping, export. |
| `pwa/src/stores/coretax.js` | Pinia state for CoreTax rows, staging, mappings, reconcile runs, unmatched rows, and exports. |
| `pwa/src/views/Settings.vue` | Import, backup, PDF workspace, preflight, and operations UI. |
| `pwa/src/utils/pdfFormatters.js` | Shared frontend PDF status vocabulary and display helpers. |
| `household-expense/api/` | Household Expense FastAPI app, auth, SQLite schema, and routers. |
| `household-expense/pwa/` | Assistant-facing Vue PWA for household cash expense entry. |
| `household-expense/deploy_household.sh` | Build, rsync, Docker rebuild, and health check for NAS deployment. |

## Bridge, API, And PWA Responsibilities

The bridge owns host-only capabilities: iMessage, macOS-local files, PDF parsing jobs, and bridge health. It exposes `/pdf/*` endpoints for preflight, queueing, status, and recent jobs.

The finance API owns application data and user-facing HTTP routes. It proxies PDF routes from `/api/pdf/*` to bridge `/pdf/*`, preserving meaningful bridge failures instead of converting them into generic success.

The PWA owns user workflows. Settings -> Process Local PDFs runs preflight first, queues all selected or ready files, polls each job, and reports per-file `done`, `partial`, or `error` results.

## Household Expense Satellite

The Household Expense PWA is an implemented LAN-only satellite service under `household-expense/`. It lets household staff record daily cash expenses from an Android/browser PWA while keeping those records in a separate NAS-local SQLite database.

Current NAS automation defaults:

- SSH user: `chfun`
- SSH port: `22`
- Finance replica sync target: `chfun@192.168.1.44:/volume1/finance/finance_readonly.db`
- Local deploy scripts read the Synology sudo password from `secrets/nas_sudo_password`

Runtime shape:

```text
Android phone / browser
  -> http://192.168.1.44:8088
  -> household-expense FastAPI + static Vue PWA
  -> household.db on NAS

Mac finance PWA Settings
  -> finance/api.py /api/household/*
  -> household API on NAS
```

Responsibilities:

| Component | Responsibility |
|---|---|
| `household-expense/api/main.py` | FastAPI app, CORS, startup, health, static PWA mount. |
| `household-expense/api/auth.py` | Assistant session cookie and API key verification. |
| `household-expense/api/db.py` | `household.db` schema and SQLite pragmas. |
| `household-expense/api/routers/transactions.py` | Expense CRUD and soft-delete. |
| `household-expense/api/routers/categories.py` | Category CRUD and category renames. |
| `household-expense/api/routers/cash_pools.py` | Cash pool CRUD and balance adjustments. |
| `household-expense/api/routers/export.py` | Unreconciled export and reconcile marking. |
| `household-expense/pwa/` | Assistant UI routes: login, add expense, history. |

The household app does not write directly to `data/finance.db`. The main finance API exposes `/api/household/*` proxy/admin endpoints so the main Settings page can manage household categories, recent expenses, and cash pools.

## PDF Processing Flow

```text
Settings.vue
  GET /api/pdf/preflight
  POST /api/pdf/process-local
  GET /api/pdf/local-status/{job_id}

finance/api.py
  reads bridge token
  proxies to bridge
  preserves HTTP errors and response body

bridge/pdf_handler.py
  validates local path
  queues job
  unlocks PDF if needed
  detects parser
  parses statement
  writes secondary balances/holdings where applicable
  exports XLS
  imports XLS into SQLite for manual jobs
  records processed file registry
```

Preflight validates bridge initialization, folders, provider order, database connectivity, and runtime diagnostics before any processing work starts.

## Status Lifecycle

Backend constants in `bridge/pdf_handler.py`:

| Status | Meaning |
|---|---|
| `pending` | Job queued and waiting for worker. |
| `running` | Worker is processing the job. |
| `done` | Parse, export, secondary writes, and import completed. |
| `partial` | Main parse/export succeeded, but secondary balance/holding writes failed. |
| `error` | Parse, export, import, config, path, or runtime failure. |

Frontend constants in `pwa/src/utils/pdfFormatters.js` mirror this vocabulary. `missing_source` is a normalized API/UI status for registry rows whose original PDF no longer exists.

Contracts:

- `partial` must not display as full success.
- `error + "no such file"` normalizes to `missing_source`, not `done`.
- `pending`, `running`, `done`, `partial`, `error`, and `missing_source` are not processable from the UI.
- Batch processing attempts all queued files unless aborted.
- Partial success must be reported as partial success in API/UI state.

## Parser Architecture

Parser extraction logic is separated from job/status orchestration:

```text
parsers/router.py
  detect_bank_and_type()
  detect_and_parse()

parsers/base.py
  Transaction
  StatementResult

parsers/<bank>_<type>.py
  extraction logic only
```

Parsers return structured statement results. The bridge handles unlocking, verification, export, secondary writes, job persistence, and UI-visible status.

## Provider And Classifier Architecture

Provider names are centralized in `agent/app/providers/__init__.py`:

```python
PROVIDERS = {
    "rule_based": RuleBasedProvider,
    "ollama": OllamaProvider,
}
```

`agent/app/classifier.py` raises on unknown provider names. `bridge/pdf_handler.py` preflight uses the same provider registry when available and falls back to the same provider names if import context is unavailable.

Current production provider order is `["rule_based"]`. `rule_based` is a supported provider and must pass config validation.

## Database And Storage Overview

| Path/Table | Role |
|---|---|
| `output/xls/ALL_TRANSACTIONS.xlsx` | Immutable parser output used to rebuild SQLite. |
| `data/finance.db` | Authoritative edited finance store. |
| `transactions` | Raw imported transaction rows keyed by hash. |
| `category_overrides` | User edits that survive re-import. |
| `transactions_resolved` | View merging base rows with overrides. |
| `merchant_aliases` | Exact, contains, and regex categorization rules. |
| `categories` | Category metadata and grouping. |
| `account_balances` | Cash and account balances by snapshot date. |
| `holdings` | Investments and other assets by snapshot date. |
| `liabilities` | Credit cards, loans, and other liabilities. |
| `net_worth_snapshots` | Aggregated monthly net worth rows. |
| `data/coretax/templates/` | User-placed CoreTax XLSX templates (e.g. `CoreTax 2025.xlsx`). |
| `data/coretax/output/` | Exported CoreTax XLSX files: `CoreTax_YEAR_vN.xlsx` + `.audit.json` sidecars. |
| `coretax_rows` | Authoritative tax-version ledger, one row per asset/liability per SPT year. |
| `coretax_taxpayer` | Per-tax-year taxpayer metadata imported from C1/C2/C3. |
| `coretax_mappings` | Learned PWM-to-CoreTax mapping rules keyed by source signatures and target stable keys. |
| `coretax_import_staging` | Prior-year XLSX preview rows with raw Excel coordinate audit fields. |
| `coretax_asset_codes` | Kode Harta lookup and default carry-forward rules. |
| `coretax_reconcile_runs` | Persisted reconcile run summaries and trace JSON. |
| `coretax_unmatched_pwm` | PWM rows not mapped during a reconcile run. |
| `data/pdf_jobs.db` | Bridge PDF job state. |
| `data/processed_files.db` | Processed PDF registry keyed by SHA-256. |
| `household-expense/data/household.db` | Separate household expense store on NAS deployment. |
| `household_transactions` | Household expense records, soft-delete flag, reconciliation status. |
| `household_categories` | Household category codes and Indonesian labels. |
| `cash_pools` | Cash envelopes / ATM-funded pools and remaining balances. |

`finance/importer.py` validates the XLSX header before positional import. Header mismatches are fatal because shifted columns can corrupt data silently.

## Important API Contracts

Bridge:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Unauthenticated liveness. |
| `GET` | `/health` | Authenticated bridge health. |
| `GET` | `/pdf/preflight` | Validate PDF runtime/config before processing. |
| `POST` | `/pdf/process-file` | Queue local PDF from configured folder. |
| `GET` | `/pdf/status/<job_id>` | Fetch PDF job status/result. |
| `GET` | `/pdf/jobs` | List recent PDF jobs. |

Finance API:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Backend health for PWA. |
| `POST` | `/api/import` | Import XLSX into SQLite. |
| `GET` | `/api/pdf/preflight` | Proxy bridge preflight. |
| `GET` | `/api/pdf/local-workspace` | Merge filesystem, registry, and job history. |
| `POST` | `/api/pdf/process-local` | Queue local bridge PDF job. |
| `GET` | `/api/pdf/local-status/{job_id}` | Proxy bridge job status. |
| `GET` | `/api/pipeline/status` | Scheduled pipeline state. |
| `POST` | `/api/pipeline/run` | Trigger scheduled pipeline cycle. |
| `GET` | `/api/household/settings` | Load household base URL, categories, recent transactions, and cash pools. |
| `PUT` | `/api/household/transaction/{txn_id}/category` | Recategorize a household transaction. |
| `POST` | `/api/household/categories` | Create household category. |
| `PUT` | `/api/household/categories/{code}` | Update or rename household category. |
| `DELETE` | `/api/household/categories/{code}` | Soft-disable household category. |
| `PUT` | `/api/household/cash-pools/{pool_id}` | Adjust household cash pool balance/notes/status. |
| `GET` | `/api/reports/financial-statement` | Composite personal financial statement (net worth, income/expense, allocation, cash flow) for a `start_month`/`end_month` range. Read-only; works under `FINANCE_READ_ONLY=true`. |
| `POST` | `/api/coretax/import/prior-year` | Multipart prior-year SPT upload. Parses to staging and returns `{batch_id, row_count, warnings, prior_tax_year, rows}`. |
| `GET/PATCH/POST/DELETE` | `/api/coretax/import/staging/*` | Preview, override carry-forward, commit, or discard staged import rows. |
| `GET` | `/api/coretax/summary` | Totals, lock counts, and coverage for one tax year. |
| `GET/POST/PATCH/DELETE` | `/api/coretax/rows*` | Ledger row list, manual add/edit/delete, and field lock/unlock. |
| `POST` | `/api/coretax/reset-from-rules` | Re-apply carry-forward defaults to unlocked rows only. |
| `POST` | `/api/coretax/auto-reconcile` | Reconcile from PWM `account_balances`, `holdings`, and `liabilities`; persists run trace and unmatched rows. |
| `GET` | `/api/coretax/reconcile-runs` | List recent reconcile runs for a tax year. |
| `GET` | `/api/coretax/unmatched` | Return unmatched PWM rows for a reconcile run, defaulting to the latest run. |
| `GET/POST/DELETE` | `/api/coretax/mappings*` | List, upsert, or delete learned PWM-to-CoreTax mappings. |
| `POST` | `/api/coretax/export` | Export the ledger to XLSX and audit JSON. Returns only `file_id`, `download_url`, and `audit_url` plus totals. |
| `GET` | `/api/coretax/exports` | List prior exports for a tax year. |
| `GET` | `/api/coretax/export/{file_id}/download` | Stream a previously exported XLSX file. |
| `GET` | `/api/coretax/export/{file_id}/audit` | Return the audit JSON for an export. |

### Financial Statement Report

- **Endpoint**: `GET /api/reports/financial-statement?start_month=YYYY-MM&end_month=YYYY-MM&owner=<optional>`
- **UI entry point**: PWA -> CoreTax SPT view -> Reconcile from PWM tab. The same month range drives financial-statement reference data and the CoreTax reconcile stage.
- **Source data**: composes existing helpers — `_get_monthly_summary_data` for per-month income/expense and `net_worth_snapshots`/`account_balances`/`holdings`/`liabilities` rows for opening + closing dates. The endpoint never writes; it requires no DB migration.
- **Read-only behavior**: pure GET, no `require_writable` dep, so the NAS replica serves it identically.
- **Warning policy**: any missing snapshot, uncategorised transactions, owner-filter limitation, or material mismatch between net-worth movement and recorded cash flow is appended to a `warnings[]` array. Missing data must never be returned as a silent zero.
- **Print / PDF**: the modal uses `window.print()` plus a scoped `@media print` stylesheet — no PDF dependency.

### CoreTax Persistent Ledger

- **Core invariant**: reviewed tax values must not be silently overwritten. Manual edits auto-lock the touched field; reconcile can suggest or skip, but it only writes unlocked fields.
- **Row identity**: every row has a non-null `stable_key`. PWM rows use source-derived keys where possible; manual/imported rows use `manual:{kode}:{slug}:{acquisition_year}:{uuid8}`.
- **Carry-forward**: prior-year import creates the next tax year's ledger. Sticky codes such as `061`, `051`, `043`, `042`, and `038` copy prior current value into current value. Refreshable codes such as `012`, `034`, `036`, and `039` are left unset for reconcile.
- **Template year validation**: the parser rejects mismatched E/F headers. A workbook with `E=2025` and `F=2026` is valid only when preparing `target_tax_year=2026`.
- **Reconcile**: PWM cash writes current amount only. Holdings can write current amount and market value independently. Liabilities write current amount. Each field respects its own lock flag.
- **Mappings**: learned mappings use prioritized source signatures such as account number, ISIN, asset signature, and liability signature. A successful mapping resolves to `target_stable_key`, increments `hits`, updates `last_used_tax_year`, and stamps `last_mapping_id` on the row.
- **Export**: exporter loads a canonical XLSX template, writes rows 6-47 only, preserves rows 48+ formulas/styles, and raises before writing if more than 42 asset rows would exceed template capacity.

Household API:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/household/health` | Unauthenticated household health check. |
| `POST` | `/api/household/auth/login` | Assistant login and session cookie. |
| `POST` | `/api/household/auth/logout` | Clear session. |
| `GET/POST` | `/api/household/transactions` | List/create household transactions. |
| `PUT/DELETE` | `/api/household/transactions/{id}` | Update or soft-delete household transaction. |
| `GET/POST` | `/api/household/categories` | List/create household categories. |
| `PUT/DELETE` | `/api/household/categories/{code}` | Update or soft-disable household category. |
| `GET/POST` | `/api/household/cash-pools` | List/create cash pools. |
| `PUT` | `/api/household/cash-pools/{id}` | Update cash pool or apply adjustment. |
| `GET` | `/api/household/export/unreconciled` | Export pending household transactions. |
| `POST` | `/api/household/reconcile` | Mark household transactions reconciled. |

## Security Boundaries

- Bridge bearer token protects bridge routes except `/healthz`.
- Finance API uses `X-Api-Key`.
- Private surfaces are intended to sit behind Cloudflare Access or local/private networking.
- Secret source of truth is macOS Keychain; files under `secrets/` are Docker export artifacts.
- `VITE_FINANCE_API_KEY` is embedded in the PWA bundle; do not reuse that key outside this deployment boundary.

## Stale Or Removed Content

The old root `SYSTEM_DESIGN.md` mixed architecture, operations, troubleshooting, implementation history, and command logs. That content has been split across the docs set. Historical setup snippets that referenced fixed Python executable paths are intentionally not copied here; operations docs now prefer `python3` or the dynamic scripts.
