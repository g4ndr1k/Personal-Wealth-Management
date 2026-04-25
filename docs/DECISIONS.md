# Decisions

Lightweight ADR-style notes. Keep entries short and link to operational details instead of repeating them.

## Use Preflight Validation Before PDF Processing

### Decision

Run `GET /api/pdf/preflight` before Settings queues any local PDF processing work.

### Context

PDF processing depends on bridge startup state, provider config, local folders, token access, and SQLite job tables. Previous failures could appear as "nothing happened" in the UI.

### Rationale

Preflight surfaces runtime/config problems before work is queued, making failures visible and actionable.

### Consequences

- Settings blocks processing when preflight fails.
- Bridge and finance API need to preserve clear error details.
- Troubleshooting starts with preflight output.

## Keep Parser Extraction Separate From Processing And Status Logic

### Decision

Bank-specific extraction stays in `parsers/`; job state, preflight, unlocking, export, secondary writes, and API status stay in bridge/finance/PWA layers.

### Context

Parser changes are frequent and bank-specific. Processing/status bugs are cross-cutting.

### Rationale

Separation keeps parser tests focused and prevents workflow/status changes from rewriting extraction logic.

### Consequences

- New bank support should touch `parsers/router.py`, parser files, and focused parser tests.
- Bridge code should call parser APIs, not embed extraction rules.

## Keep `rule_based` As A Supported Classifier Provider

### Decision

`rule_based` remains a first-class provider in `agent/app/providers/PROVIDERS`.

### Context

Production mail alerting uses `provider_order = ["rule_based"]`. A validator once rejected this even though runtime supported it.

### Rationale

Provider names must come from one registry so config validation and runtime behavior do not drift.

### Consequences

- Unknown providers raise clearly at classifier startup.
- Preflight validates provider order against the same provider vocabulary when available.
- New providers must update the registry and docs.

## Surface Bridge, Config, And Runtime Failures To UI

### Decision

Do not silently fall back to success when bridge/config/runtime failures occur.

### Context

Missing Python executables, bad token paths, bridge downtime, invalid provider names, and parser/import failures can otherwise look like successful no-ops.

### Rationale

The user needs to know whether a file processed, partially processed, failed, or never started.

### Consequences

- Missing executable/path/config should fail fast.
- API proxies should preserve meaningful backend error bodies.
- Frontend catch blocks should show actionable messages.
- Partial success is a distinct status.

## Treat `ALL_TRANSACTIONS.xlsx` As Immutable Parser Output

### Decision

`output/xls/ALL_TRANSACTIONS.xlsx` is the parser/export contract. SQLite is the authoritative edited store.

### Context

The importer maps XLSX columns positionally. Manual edits or exporter drift can corrupt imported rows silently.

### Rationale

Keeping XLSX immutable makes rebuilds deterministic and lets user edits live in SQLite override layers.

### Consequences

- Importer validates the header and fails fast on mismatch.
- Manual edits should happen in the PWA/API, not in the XLSX file.
- Exporter/importer column changes must be coordinated and tested.

## Keep Operational Detail Out Of The Architecture Blueprint

### Decision

`docs/SYSTEM_DESIGN.md` describes stable architecture and contracts. Commands and runbooks live in `docs/OPERATIONS.md`; symptom fixes live in `docs/TROUBLESHOOTING.md`.

### Context

The previous root `SYSTEM_DESIGN.md` mixed architecture, setup, troubleshooting, history, and temporary notes.

### Rationale

Smaller purpose-specific docs are easier to keep current.

### Consequences

- Root `SYSTEM_DESIGN.md` is now only a compatibility pointer.
- New bug-fix history belongs in `docs/CHANGELOG.md`.
- New rationale belongs here.

## Keep Household Expense As A Satellite Store

### Decision

The Household Expense PWA stores assistant-entered expenses in its own `household.db` and exposes integration through APIs instead of writing directly to `data/finance.db`.

### Context

Household expense capture is a LAN/NAS workflow with a simpler assistant-facing UI and separate operational needs from the main finance dashboard.

### Rationale

Keeping a separate store limits blast radius, preserves a clean reconciliation boundary, and lets the household app remain useful even when the main finance API is unavailable.

### Consequences

- Main finance Settings uses `/api/household/*` proxy/admin endpoints for household categories, recent expenses, and cash pools.
- Reconciliation into the main finance model must be explicit.
- Household deployment and health checks belong in operations docs, not a standalone implementation plan.
