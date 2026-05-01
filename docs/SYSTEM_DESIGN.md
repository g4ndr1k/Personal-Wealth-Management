# System Design

Stable architecture blueprint for the Personal Wealth Management system.

Operational commands live in [OPERATIONS.md](OPERATIONS.md). Symptom-driven fixes live in [TROUBLESHOOTING.md](TROUBLESHOOTING.md). Change history lives in [CHANGELOG.md](CHANGELOG.md). Design rationale lives in [DECISIONS.md](DECISIONS.md).

## System Overview

The project runs as separate cooperating processes:

```text
Mac host
  bridge/                 host Python service on :9100
    mail/iMessage access
    PDF processing jobs
    in-memory PDF unlock for agent attachments
    bridge API

Docker on Mac
  agent/                  mail alert worker + local mail API on :8080
  finance/ + pwa/dist     FastAPI + built Vue PWA on :8090
  mail-dashboard/          Electron app; local API client

Optional NAS
  /Volumes/Synology/mailagent
                          PDF archive mounted into mail-agent as /mnt/mailagent
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
| `agent/` | Dockerized mail worker, IMAP intake, deterministic rule evaluation, classifier providers, attachment routing, and local health/debug API. |
| `agent/app/api_mail.py` | Dashboard-facing mail API router mounted by `finance/api.py` under `/api/mail/*`. |
| `agent/app/providers/` | Provider implementations and `PROVIDERS` registry. |
| `parsers/router.py` | Bank and statement type detection, parser dispatch. |
| `parsers/*.py` | Bank-specific PDF extraction logic. |
| `exporters/xls_writer.py` | XLS output, including `ALL_TRANSACTIONS.xlsx`. |
| `finance/importer.py` | XLSX to SQLite import, deduplication, categorization, header validation. |
| `finance/matching/` | Generic matching engine: fingerprints, mappings, confidence, storage, rejected suggestions, invariant logs, and domain adapters. |
| `finance/db.py` | SQLite schema, migrations, resolved transaction view. |
| `finance/api.py` | FastAPI backend, PWA static mount, bridge proxy routes, wealth APIs. |
| `pwa/src/api/client.js` | Frontend API client and cache behavior. |
| `finance/coretax/` | Persistent CoreTax SPT ledger: prior-year import, carry-forward, fingerprint derivation, mapping suggestions, reconcile, component history, XLSX export. |
| `pwa/src/views/CoreTaxSpt.vue` | CoreTax SPT wizard: import, carry-forward review, mapping, reconcile from PWM, export. |
| `pwa/src/stores/coretax.js` | Pinia state for CoreTax rows, staging, mappings, unmapped PWM items, reconcile runs, component history, and exports. |
| `pwa/src/views/Settings.vue` | Import, backup, PDF workspace, preflight, and operations UI. |
| `pwa/src/utils/pdfFormatters.js` | Shared frontend PDF status vocabulary and display helpers. |
| `household-expense/api/` | Household Expense FastAPI app, auth, SQLite schema, and routers. |
| `household-expense/pwa/` | Assistant-facing Vue PWA for household cash expense entry. |
| `household-expense/deploy_household.sh` | Build, rsync, Docker rebuild, and health check for NAS deployment. |
| `mail-dashboard/` | Electron + React menu-bar dashboard for the mail agent. |

## Mail Agent Responsibilities

The mail agent runs in Docker and handles IMAP intake, bridge fallback, deterministic Phase 4A rule evaluation, classifier providers, iMessage/PDF bridge calls, and attachment routing. It serves worker health/debug on `127.0.0.1:8080`; the native dashboard uses the finance API mount at `127.0.0.1:8090/api/mail/*`.

Mail-agent runtime state, including Phase 4A rules, rule actions, audit events, needs-reply rows, AI queues, and AI classifications, belongs in `data/agent.db`. `data/finance.db` remains reserved for finance/PWM data.

Phase 4B adds read-only AI enrichment after deterministic rule evaluation. Eligible messages are queued in `mail_ai_queue`; a single worker claims one row, commits, calls Ollama `/api/chat` with a JSON schema, then opens a new transaction to save `mail_ai_classifications` or a soft failure. Validation failures and Ollama outages update queue status/attempts/`last_error` and do not block existing classifier, alert, or PDF routing behavior.

Phase 4C.1 implements IMAP mutation primitives only: capability probing, UIDVALIDITY-checked `UID MOVE`, UID-based `STORE` for `\Seen`/`\Flagged`, dry-run enforcement, and audit rows for planned/blocked/dry-run/unsupported/completed/failed outcomes. Mutations are blocked outside `live`, blocked when `[mail.imap_mutations].enabled=false`, and COPY + STORE `\Deleted` fallback is disabled by default and never EXPUNGEs automatically. AI-triggered external actions, labels, delete as a user action, unsubscribe, auto-reply, forwarding, webhooks, and AI-triggered iMessage remain out of scope.

Phase 4C.3A adds preview-only AI trigger evaluation after validated classifications are persisted. Trigger matching is deterministic over category, urgency, confidence, needs-reply, summary, and reason fields. Matched triggers write `ai_trigger_matched` audit events with planned dry-run actions; they do not call IMAP helpers, bridge iMessage send, reply, forward, delete, expunge, unsubscribe, or webhooks.

Phase 4D adds the operator approval layer. AI trigger matches create `mail_action_approvals` rows and approval audit events; they still do not execute actions. Operators may approve or reject in the dashboard Control Center. Execution is a separate explicit step after approval and reuses gated/readiness logic, so `[agent].mode`, `[mail.imap_mutations].enabled`, dry-run defaults, UIDVALIDITY, folder, and IMAP capability checks still decide whether an approved action is blocked or mock/readiness-only. Approval responses expose a derived execution state, current read-only gate preview, message/trigger context, risk guidance, blocked/error reason, and chronological audit trail. Preview never marks execution started and does not mutate mailbox state. Phase 4D.5 adds reversible mutation readiness only: cached account/folder capability summaries, UIDVALIDITY identity checks, dry-run mutation plans, safety gates, and rollback hints for future `mark_read`, `mark_unread`, `add_label`, and cautious `move_to_folder` candidates. Phase 4E.1 adds the non-mutating execution chassis: `mail_action_executions`, immutable execution events, deterministic plan hashes, idempotency keys, a pure gate evaluator, and a mock executor. Phase 4E.2 adds final read-only verification before mock execution: a read-only mailbox adapter selects the folder read-only, confirms UIDVALIDITY, fetches the UID/header identity/flags without mutation, and blocks mock execution if identity changed. Read/unread are still not live, `add_label` and `move_to_folder` remain deferred, and the mock executor does not touch Gmail or issue IMAP mutation commands. Terminal approvals can be archived from the active view via `archived_at` while audit records remain exportable as sanitized JSON. Bulk approval and bulk execute are absent, and unsupported/dangerous actions remain blocked. Phase 4F is a natural-language rule builder: AI drafts proposed deterministic rules, deterministic validation checks schema and safety allow-lists, humans approve/save, and the existing deterministic rules engine applies saved rules later. Phase 4F.1a implements deterministic/local sender suppression drafts only through `POST /api/mail/rules/ai/draft`; it does not write rule tables, does not save without human action, and treats “spam list” as local Mail Agent suppression rather than Gmail Spam. Phase 4F.1b adds a disabled-by-default local Ollama alert-rule draft probe. Saveable alert drafts are limited to sender/domain plus subject/body content conditions and `mark_pending_alert`; direct `send_imessage`, Gmail/IMAP mutation, mailbox execution, webhooks, forwarding, auto-reply, unsubscribe, labels, moves, and read/unread remain blocked by deterministic post-validation. Phase 4F.1g adds local quality/audit tables (`mail_rule_ai_draft_audit`, `mail_rule_ai_golden_probe_runs`) that store hashes, short previews, and compact metrics rather than raw prompts or raw model output. Phase 4F.1 is non-mutating and can happen before Phase 4E.3 because it does not enable live mailbox mutation. See [phase-4f-natural-language-rule-builder.md](phase-4f-natural-language-rule-builder.md).

For detailed mail-agent architecture, API boundaries, rules, credential handling, safe actions, and troubleshooting, see [MAIL_AGENT.md](MAIL_AGENT.md).

## Bridge, API, And PWA Responsibilities

The bridge owns host-only capabilities: iMessage, macOS-local files, PDF parsing jobs, in-memory PDF unlock for agent attachments, and bridge health. It exposes `/pdf/*` endpoints for preflight, queueing, status, recent jobs, and `/pdf/unlock` for multipart bytes-in/bytes-out unlock requests.

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
| `data/finance.db` | Authoritative edited finance/PWM store. |
| `data/agent.db` | Mail-agent runtime state, including Phase 4A mail rules, rule actions, rule audit events, needs-reply rows, Phase 4B AI queue/classification tables, Phase 4C.3A preview-only AI trigger rules, Phase 4D.1 operator approvals, and Phase 4E.1 mock-only execution audit rows. |
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
| `coretax_mappings` | Learned PWM-to-CoreTax mapping rules keyed by stable fingerprints and target stable keys. |
| `coretax_import_staging` | Prior-year XLSX preview rows with raw Excel coordinate audit fields. |
| `coretax_asset_codes` | Kode Harta lookup and default carry-forward rules. |
| `coretax_reconcile_runs` | Persisted reconcile run summaries and trace JSON. |
| `coretax_unmatched_pwm` | Per-run audit copy of PWM rows not mapped during reconcile. The live Mapping tab computes unmapped PWM rows fresh. |
| `coretax_row_components` | Per-run source component breakdown for many-to-one CoreTax rows. Current components have `is_current=1`; older run history is retained with `is_current=0`. |
| `coretax_rejected_suggestions` | Negative-learning table for user-rejected mapping suggestions. |
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
| `POST` | `/pdf/unlock` | Authenticated multipart PDF unlock. Returns unlocked bytes with `X-Was-Encrypted`, `X-Password-Used-Index`, and `X-Page-Count` headers. |

Mail agent:

- Worker health/debug is on `127.0.0.1:8080`.
- Dashboard-facing APIs are mounted by the finance API under `127.0.0.1:8090/api/mail/*`.
- Detailed endpoint contracts live in [MAIL_AGENT.md](MAIL_AGENT.md).

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
| `POST` | `/api/coretax/auto-reconcile` | Reconcile from PWM `account_balances`, `holdings`, and `liabilities`; applies explicit mappings first, then safe 1:1 heuristics; persists run trace, component rows, and unmatched rows. |
| `GET` | `/api/coretax/reconcile-runs` | List recent reconcile runs for a tax year. |
| `GET` | `/api/coretax/unmatched` | Return unmatched PWM rows for a reconcile run, defaulting to the latest run. |
| `GET/POST/DELETE` | `/api/coretax/mappings*` | List, upsert, or delete learned PWM-to-CoreTax mappings. |
| `GET` | `/api/coretax/{year}/unmapped-pwm` | Compute the current unmapped PWM universe against live mappings. |
| `GET` | `/api/coretax/{year}/mappings/grouped` | List mappings grouped by target row. |
| `GET` | `/api/coretax/mappings/lifecycle` | Classify mappings as `STALE`, `WEAK`, `UNUSED`, or `ORPHANED`. |
| `POST` | `/api/coretax/{year}/mappings/assign` | Create/update a mapping with conflict protection. Returns HTTP 409 with structured conflict metadata on target mismatch. |
| `POST` | `/api/coretax/mappings/{mapping_id}/confirm` | Confirm a mapping and raise its confidence floor. |
| `POST` | `/api/coretax/{year}/mappings/suggest` | Return ranked suggestions for unmapped PWM items. |
| `POST` | `/api/coretax/{year}/mappings/suggest/preview` | Preview accepted suggestions without writing. Computes target deltas, component counts, and conflicts. Optional `snapshot_date` aligns amounts with a future reconcile run. |
| `POST` | `/api/coretax/{year}/mappings/suggest/reject` | Persist a rejected suggestion so it is no longer proposed. |
| `GET` | `/api/coretax/{year}/rows/{stable_key}/components` | Return current components for a target row, or run-specific historical components when `run_id` is supplied. |
| `GET` | `/api/coretax/components/history` | Reverse trace for a PWM fingerprint across reconcile runs. |
| `GET` | `/api/coretax/{year}/reconcile/runs/{run_id}/diff` | Compare component membership between reconcile runs. |
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
- **Mapping-first flow**: the PWA order is Import -> Review -> Mapping -> Reconcile -> Export. Mapping is the human decision layer; Reconcile is the execution layer.
- **Fingerprints**: PWM sources derive stable mapping keys in `finance/coretax/fingerprint.py`. Cash uses `account_number_norm = sha256(institution_norm:account_norm)`, holdings use normalized ISIN when available, holdings without ISIN use `holding_signature`, and liabilities use `liability_signature`. `fingerprint_raw` stores the canonical pre-hash string for audit where available.
- **Reconcile**: Tier 1 applies explicit mappings. Tier 2 applies only safe 1:1 heuristics, currently exact unique ISIN and exact unique account-number matches. Tier 2 auto-persists guarded `auto_safe` mappings and uses them in the same run. Deprecated legacy heuristics are disabled by default and require `CORETAX_LEGACY_HEURISTICS=true`.
- **Writes and locks**: PWM cash writes current amount only. Holdings can write current amount and market value independently. Liabilities write current amount. Each field respects its own lock flag.
- **Mappings**: a successful mapping resolves to `target_stable_key`, increments `hits`, updates `last_used_tax_year`, stamps `last_mapping_id` on the row, and contributes a `coretax_row_components` entry for the target row.
- **Confidence and lifecycle**: mappings store `confidence_score`, derived `confidence_level`, source, confirmation count, years used, and last-used data. Lifecycle classification distinguishes `STALE`, `WEAK`, `UNUSED`, and `ORPHANED`; missing PWM fingerprints are orphaned instead of confidence-decayed.
- **Many-to-one**: multiple PWM sources can map to one CoreTax row. Component rows preserve the per-source breakdown and make run diffs auditable.
- **Suggestion preview**: suggestions are read-only until accepted. Preview groups same-target suggestions as components and reports conflicts only for duplicate source fingerprints, existing mapping target conflicts, missing target rows, or incompatible asset/liability target kinds.
- **Export**: exporter loads a canonical XLSX template, writes rows 6-47 only, preserves rows 48+ formulas/styles, and raises before writing if more than 42 asset rows would exceed template capacity.

### Generic Matching Engine

`finance/matching/` is the shared infrastructure for systems that map source rows to durable targets. It currently has domain adapters for:

| Domain | Adapter | Current rollout shape |
|---|---|---|
| CoreTax | `finance/matching/domains/coretax.py` | Compatibility adapter while CoreTax keeps its established ledger API. |
| Parser routing | `finance/matching/domains/parser_routing.py` | Flag-gated by `PARSER_USE_ENGINE=true`; legacy router remains the fallback. |
| Import dedup | `finance/matching/domains/dedup.py` | Flag-gated by `DEDUP_USE_ENGINE=true`; exact hash dedup remains the primary backstop. |
| Categorization | `finance/matching/domains/categorization.py` | Flag-gated by `CATEGORIZATION_USE_ENGINE=shadow|true`; legacy categorizer remains the source of truth unless true mode is enabled. |

Engine-owned tables use a fixed shape per domain:

- `matching_<domain>_mappings`
- `matching_<domain>_components`
- `matching_<domain>_rejected_suggestions`

Engine-wide tables:

- `matching_invariant_log`
- `matching_invariant_diagnostic`
- `matching_drift_metrics`
- `matching_trace_archive`
- `category_shadow_diff`

Runtime contract:

- Domains provide explicit `normalize()`, `derive()`, `rules()`, `resolve_conflict()`, and `on_persist()` hooks.
- `identity_hash` is the stable mapping key; `identity_raw` stores the canonical pre-hash form for audit and fingerprint-version checks.
- Tier 1 uses persisted mappings. Tier 2 runs safe domain rules and may auto-persist only when score, conflict, observation, and per-run guard checks pass.
- Storage helpers validate dynamic table/domain/field identifiers before SQL interpolation. SQLite values remain parameterized.
- Matching-console APIs are exposed under `/api/matching/*` and validate domains against the fixed allow-list: `coretax`, `parser`, `dedup`, `categorization`.
- Not-yet-shipped platform features from the extraction plan, such as deterministic replay, persisted trace sampling, drift response automation, and daily/30-day/per-rule learning caps, should be added centrally in `finance/matching/` rather than inside individual domains.

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
