# Changelog

Human-readable project history. Reverse chronological order.

## 2026-04-28 — Default AI Model Upgrade (Gemma 3)

- Upgraded the default local LLM from `gemma4:e4b` / `qwen2.5:7b` to `gemma3:4b`.
- Updated `config/settings.toml` and `config/settings.example.toml` with the new model.
- Updated hardcoded fallback models in `finance/categorizer.py`, `finance/config.py`, and `bridge/pdf_handler.py`.
- Updated all financial statement parsers that use Ollama fallback (BNI, Stockbit, IPOT) to use `gemma3:4b`.
- Updated `finance/ollama_utils.py` to confirm `gemma3` JSON mode support.

## 2026-04-28 — Generic Matching Engine Foundation

- Added `finance/matching/` as shared infrastructure for auditable mappings, confidence metadata, rejected suggestions, invariant logs, category shadow diffs, and per-domain storage tables.
- Added domain adapters for parser routing, bank-import parser-variant deduplication, expense categorization shadow/cutover support, and CoreTax compatibility.
- Added matching-console API surfaces under `/api/matching/*` for mapping stats, per-domain mappings, shadow diffs, and invariant logs.
- Parser routing, dedup, and categorization engine adoption remain flag-gated so legacy behavior can be retained during shadow and rollout windows.
- Hardened generic matching storage by validating dynamic SQL identifiers for domain/table/field names before interpolation.
- Fixed matching mapping confirmation to return `404` for missing mappings instead of raising an internal error.
- Fixed `update_mapping_fields()` so callers can distinguish missing rows from successful updates.
- Added matching storage safety regression tests. Full suite: `139 passed`.

## 2026-04-27 — CoreTax Mapping-First Reconciliation

- Reordered the CoreTax SPT workflow to Import -> Review -> Mapping -> Reconcile -> Export.
- Added stable PWM fingerprint derivation in `finance/coretax/fingerprint.py`, including hashed `account_number_norm`, normalized `isin`, `holding_signature`, and `liability_signature` keys.
- Added mapping confidence and lifecycle metadata: `confidence_score`, derived `confidence_level`, `source`, `fingerprint_raw`, `last_used_at`, `years_used`, and `times_confirmed`.
- Added `coretax_row_components` for many-to-one CoreTax row breakdowns. Re-runs mark older components `is_current=0` while preserving run history.
- Added `coretax_rejected_suggestions` so explicitly rejected suggestions stop being proposed.
- Reconcile now runs as a two-tier engine: explicit mappings first, then safe 1:1 heuristics for unique ISIN/account matches. Deprecated legacy heuristics default to off behind `CORETAX_LEGACY_HEURISTICS=true`.
- Tier 2 safe matches auto-persist guarded `auto_safe` mappings and use the new mapping in the same run for `last_mapping_id`, `hits`, and confidence tracking.
- Added backward-compatible migration for old mapping keys on first hit.
- Added mapping endpoints for live unmapped PWM rows, grouped mappings, stale/lifecycle buckets, rename candidates, assignment with structured 409 conflicts, confirmation, suggestions, preview, rejection, components, component history, and run diffs.
- Suggestion preview is snapshot-aware and reports conflicts only for duplicate source fingerprints, existing mapping target conflicts, missing target rows, or incompatible asset/liability kinds.
- Expanded CoreTax tests to cover mapping migration, component history, confidence snapshot alignment, rejected suggestions, preview conflicts, and Tier 2 auto-safe first-run usage.

## 2026-04-26 — CoreTax Persistent Ledger

- Replaced the one-shot `finance/coretax_export.py` generator with the persistent `finance/coretax/` package.
- Added a tax-version ledger in SQLite: `coretax_rows`, `coretax_taxpayer`, `coretax_mappings`, `coretax_import_staging`, `coretax_asset_codes`, `coretax_reconcile_runs`, and `coretax_unmatched_pwm`.
- CoreTax now starts from a prior-year SPT upload, stages raw XLSX rows for preview, commits carry-forward rows, reconciles refreshable rows from PWM, records learned mappings, and exports back to XLSX on demand.
- Manual edits now auto-lock amount/market fields. Auto-reconcile skips locked fields and records skipped rows in the trace instead of silently overwriting reviewed tax values.
- Learned mappings now store `target_stable_key`; successful reconcile use increments `hits`, updates `last_used_tax_year`, and stamps `last_mapping_id` on the row.
- Cash reconcile writes only `current_amount_idr`; it never writes `market_value_idr`.
- Added strict E/F year-header validation for uploaded prior-year templates. A workbook with `E=2025` and `F=2026` is only valid for `target_tax_year=2026`.
- Replaced old `/api/coretax/templates`, `/api/coretax/generate`, and `/api/coretax/audit/{filename}` with the persistent-ledger API under `/api/coretax/*`.
- Rewrote the PWA `/coretax` view into a 5-stage persistent-ledger wizard. This initial order was later superseded by the mapping-first flow recorded in the 2026-04-27 entry.
- Replaced stale `tests/test_coretax_export.py` coverage with `tests/test_coretax.py`, covering import, carry-forward, lock guards, learned mappings, export formula rules, template capacity, and CHECK constraints.

## 2026-04-25 — CoreTax SPT Generator

- Added `finance/coretax_export.py` — maps PWM `account_balances` (savings) and `holdings` (investments) to an Indonesian DJP CoreTax XLSX template.
- Column F (closing value) filled from `balance_idr` for savings rows (kode 012) and `cost_basis_idr` for investment rows (kode 034/036/039). Column G filled from `market_value_idr` for investments. Hard-asset rows (real estate, vehicles, gold) are left untouched.
- Per-row audit trace: every filled cell is traceable back to a specific PWM source row. Unmatched, aggregated, and currency-warning rows are surfaced explicitly — no silent zeros.
- `investment_match_mode = "strict"` (default) requires `(institution, asset_class, owner)` match. `"aggregate_with_warning"` sums across institutions with a trace warning.
- Non-IDR holdings are filled via `*_idr` columns with a `currency_warning` status so the user can verify.
- Output written to `data/coretax/output/CoreTax_YEAR_SNAPSHOT_vN.xlsx` with a `<same-name>.audit.json` sidecar. Version number auto-increments to avoid overwriting reviewed runs.
- Added three API endpoints: `GET /api/coretax/templates`, `POST /api/coretax/generate` (supports `dry_run=true` for JSON preview), `GET /api/coretax/audit/{filename}`.
- Generate endpoint returns the filled XLSX as a download when `dry_run=false`; blocked under `FINANCE_READ_ONLY=true`. Dry-run is always available.
- New PWA view `/coretax` (CoreTax SPT) with four cards: Reporting Period, Generate Financial Statement (moved from Settings), CoreTax SPT Template picker, and Generate CoreTax SPT.
- Preview (dry-run) button shows confidence breakdown before the user commits to a file download.
- Generate XLSX button disabled until at least one preview has been run.
- `[coretax]`, `[coretax.owner_aliases]`, and `[coretax.institution_aliases]` added to `config/settings.toml`. Owner and institution mappings are config-driven so they can be updated without code changes.
- Docker env vars `CORETAX_TEMPLATE_DIR` and `CORETAX_OUTPUT_DIR` added to `docker-compose.yml` to map container paths independently of host-absolute settings.toml paths.
- 8 unit tests added in `tests/test_coretax_export.py` covering normalisation, parsing, institution/owner canonicalisation, strict vs aggregate matching, currency warnings, template validation, and unused PWM rows.

## 2026-04-25 — Documentation Split And PDF Hardening Follow-up

- Refactored project documentation into purpose-specific docs:
  - `docs/SYSTEM_DESIGN.md`
  - `docs/OPERATIONS.md`
  - `docs/TROUBLESHOOTING.md`
  - `docs/CHANGELOG.md`
  - `docs/DECISIONS.md`
- Kept root `README.md` short and moved detailed operations/troubleshooting elsewhere.
- Merged Household Expense implementation details into core docs and removed the standalone household plan.
- Added fail-fast XLSX header validation in `finance/importer.py` so shifted columns cannot import positionally.

## 2026-04-25 — PDF Preflight And Silent Failure Hardening

- Added bridge `GET /pdf/preflight`.
- Added finance API `GET /api/pdf/preflight` proxy.
- Added PWA `api.pdfPreflight`.
- Settings -> Process Local PDFs now runs preflight before queueing work.
- Preflight errors and warnings are shown in the UI.
- Bridge token errors now fail clearly when token path is missing, empty, or a directory.
- API proxy preserves meaningful bridge errors.
- PDF service worker routes are network-only/freshness-forced to avoid stale processing state.
- PDF statuses now include shared `pending`, `running`, `done`, `partial`, and `error` vocabulary.
- Partial success is reported separately from success.
- Missing source PDFs normalize to `missing_source`.
- `provider_order = ["rule_based"]` is accepted because `rule_based` is a registered provider.
- Stale Python path references in app/launch scripts were replaced with dynamic resolution.
- Secret export writes `secrets/bridge.token` as a file with mode `600` and removes stale token directories.

## 2026-04-24 — Rule-Based Mail Alerts And Wake Recovery

Source commit: `71b4b83 feat: implement rule-based mail alerts and wake-recovery reliability`

- Rule-based classifier became the production default for mail alerts.
- Wake-recovery reliability improved for the mail agent.
- Provider registration made explicit through the providers package.

## 2026-04-23 — Settings, PDF UI, And DB Race Fixes

Source commit: `149adfa feat: enhance PWA Settings layout, improve PDF pipeline UI, and fix DB race condition`

- Settings UI gained a richer PDF pipeline/workspace experience.
- Backup and household settings became easier to manage from Settings.
- A database race condition was fixed.

## 2026-04-22 — Cloudflare Access And Secret Management

Source commits:

- `9e8d7a3 docs: replace Tailscale with Cloudflare Access throughout`
- `ce9f743 feat(secret): pre-commit secret detector; macOS Keychain secret storage scaffold; CLI for secret management`

- Documentation moved from Tailscale-first wording to Cloudflare Access.
- macOS Keychain became the documented source of truth for secrets.
- Secret export tooling supports Docker/runtime files under `secrets/`.

## 2026-04-21 — Household Management Fixes

Source commit: `14ef4e2 fix: resolve critical bugs and enhance PWA household management`

- Household management in the PWA was hardened.
- Critical household-related bugs were fixed.

## 2026-04-20 — Preferences And Household Expense PWA

Source commits:

- `f5702f6 feat: server-side user preferences and Household Expense PWA refinements`
- `7a96a3b feat: Household Expense PWA (NAS satellite) and model optimization`

- Added server-side preferences for dashboard state.
- Added/refined the Household Expense PWA as a NAS satellite.

## 2026-04-19 — IMAP, Homepage, And Privacy Mode

Source commits:

- `09ac1ad feat: multi-provider IMAP (Gmail + iCloud), Snake game on homepage`
- `16b9965 Add codingholic homepage deployment workflow notes`
- `b19424d feat: add privacy mode to hide all monetary values in PWA`

- Added multi-provider IMAP work.
- Added homepage deployment workflow notes.
- Added PWA privacy mode for hiding monetary values.
