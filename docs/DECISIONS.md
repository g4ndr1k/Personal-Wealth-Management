# Decisions

Lightweight ADR-style notes. Keep entries short and link to operational details instead of repeating them.

## Use A Shared Matching Engine With Flag-Gated Domains

### Decision

Keep the generic matching engine under `finance/matching/` and adopt it per domain behind feature flags.

### Context

CoreTax, parser routing, bank-import deduplication, and transaction categorization all need similar concepts: stable source fingerprints, persistent mappings, confidence/lifecycle metadata, rejected suggestions, and an audit trail. They historically implemented those ideas separately.

### Rationale

A shared engine prevents drift between matching systems while allowing risky user-visible domains to remain on legacy behavior until shadow tests and rollout gates are green. Domain-specific code should live in `finance/matching/domains/`; generic mapping/component/rejection storage belongs in `finance/matching/storage.py`.

### Consequences

- Parser routing, dedup, and categorization keep legacy paths available while their engine paths are flag-gated.
- Dynamic engine table access must validate domain and field identifiers before interpolating SQL.
- Matching-console APIs operate on a fixed domain allow-list.
- Larger plan items such as replay mode, persisted traces, drift automation, and long-horizon learning budgets should be implemented as engine features, not one-off domain code.

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

## Preserve Reviewed CoreTax Values In A Ledger

### Decision

CoreTax SPT uses a persistent tax-version ledger in SQLite instead of a one-shot XLSX generator.

### Context

The tax book intentionally diverges from the real PWM book. Acquisition-cost rows, manually adjusted rows, hidden/merged accounts, and prior-year tax decisions must survive future refreshes.

### Rationale

Re-running a generator from current PWM data can erase reviewed tax values. A ledger with stable row identities, explicit lock flags, staging, reconcile traces, and learned mappings preserves tax decisions while still letting refreshable rows update from PWM.

### Consequences

- `coretax_rows` is the authoritative tax-version book.
- Manual edits auto-lock the touched amount or market-value field.
- Reconcile must skip locked fields and record the skip.
- Learned mappings resolve to `target_stable_key`, not only a code or description.
- Prior-year imports must reject mismatched E/F tax-year headers.
- XLSX export is a projection of the ledger, not the source of truth.

## Use Mapping-First CoreTax Reconciliation

### Decision

CoreTax reconciliation runs after an explicit Mapping step. Mapping is the human decision layer; Reconcile is a deterministic execution layer.

### Context

CoreTax SPT rows often aggregate multiple PWM items into one tax row. Earlier reconcile behavior mixed learned mappings with fallback heuristics, including weak single-row or substring matches. That made first-time setup convenient but allowed silent target changes when PWM data or CoreTax rows changed.

### Rationale

Persistent mappings make annual reconciliation auditable and repeatable. Safe 1:1 heuristics are still allowed for first-run usability, but only exact unique ISIN and account-number matches can auto-persist as `auto_safe` mappings. Ambiguous cases belong in the Mapping tab as suggestions, not automatic reconcile decisions.

### Consequences

- PWA workflow is Import -> Review -> Mapping -> Reconcile -> Export.
- Reconcile applies explicit mappings first and safe 1:1 heuristics second.
- Deprecated legacy heuristics are off by default behind `CORETAX_LEGACY_HEURISTICS=true`.
- Source keys are derived through `finance/coretax/fingerprint.py`; ad hoc hashes should not appear elsewhere.
- Many-to-one is first-class through `coretax_row_components`.
- Suggestion preview must report real conflicts without treating same-target many-to-one suggestions as conflicts.
- Mapping lifecycle buckets (`STALE`, `WEAK`, `UNUSED`, `ORPHANED`) are part of the review workflow.

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

## Require Operator Approval For AI-Triggered Action Attempts

### Decision

Phase 4D.1 uses a Control Center approval queue for AI-triggered action suggestions. AI trigger matches create pending rows in `data/agent.db:mail_action_approvals`; they never execute directly.

### Context

The mail agent now has safe IMAP mutation primitives, but AI classification and trigger matching can still be wrong. The dangerous boundary is not only whether an action is allowed, but whether an AI suggestion can cross from planning into mailbox or operator-visible side effects without a human checkpoint.

### Rationale

Approval keeps the human operator in the path while preserving the existing safety ladder. An approval records intent, not entitlement: execution remains a separate attempt and still runs through mode, mutation config, dry-run, UIDVALIDITY, IMAP capability, and action allow/block checks.

### Consequences

- No autonomous AI-triggered execution exists in Phase 4D.1.
- Bulk approval is intentionally absent.
- Rejected, expired, already executed, blocked, or failed approval rows are terminal for execution.
- Unsupported actions such as `send_imessage`, reply, forward, delete, expunge, unsubscribe, and webhooks remain blocked even after approval.
