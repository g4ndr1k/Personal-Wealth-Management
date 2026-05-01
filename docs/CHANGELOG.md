# Changelog

Human-readable project history. Reverse chronological order.

## 2026-05-01 — Phase 4F.1b Local LLM Drafted Alert Rule Probe

- Added a local Ollama-only alert-rule draft path to `POST /api/mail/rules/ai/draft` with modes `auto`, `sender_suppression`, and `alert_rule`.
- Kept `[mail.rule_ai]` disabled by default; when disabled, alert-rule mode returns an unsupported draft response and saves nothing.
- Hardened the local Ollama request to use structured JSON schema output where supported, requiring array-shaped `rule.conditions`, `rule.actions`, `explanation`, and `warnings`.
- Simplified the prompt around the single local-alert transformation and included an exact valid Permata example.
- Added deterministic post-validation for LLM output: alert drafts must use `match_type=ALL`, include `from_domain` or `from_email`, include a `subject` or `body` content condition, and use only `mark_pending_alert` with no stop-processing behavior.
- Added local bank-domain hints for known banks, including mapping “Permata Bank” to `permatabank.co.id`; the validator prefers these hints over model-supplied domains.
- Added deterministic content-keyword normalization for credit-card transaction clarification requests.
- Blocked unsafe LLM outputs such as delete, move, label, read/unread, direct `send_imessage`, forward, auto-reply, unsubscribe, webhook, route-to-PDF, suppression actions, and overbroad drafts.
- Updated the dashboard AI Rule Builder to expose alert-rule probe mode, show provider/model/confidence, and label drafts as non-mutating, not sending iMessage now, and requiring Save Rule.
- The draft endpoint remains read-only for rule tables, does not call IMAP, does not call the bridge, and does not send iMessage.

## 2026-05-01 — Phase 4F.1a AI Drafted Sender Suppression Rules

- Added deterministic/local `agent/app/rule_ai_builder.py` for single-sender suppression requests such as spam-list, block alerts, suppress, stop processing, ignore, and mute.
- Hardened the builder with strict single-line/string/length validation, display-name extraction, malformed email rejection, multi-recipient rejection, and stale dashboard draft invalidation after request edits.
- Added draft-only `POST /api/mail/rules/ai/draft`, which returns `from_email equals <sender>` plus `skip_ai_inference` and `stop_processing` only.
- The draft endpoint does not write `mail_rules`, `mail_rule_conditions`, or `mail_rule_actions`; human save still uses existing `POST /api/mail/rules`.
- The dashboard Rules area now has an AI Rule Builder box labelled AI Rule Draft, Safe Local Suppression, and This does not mutate Gmail.
- Explicit live mailbox requests such as delete, archive, mark read/unread, label, forward, reply, unsubscribe, and move-to-spam are blocked/unsupported.
- “Spam list” currently means local Mail Agent suppression, not Gmail Spam. No live Gmail/IMAP mutation was added.

## 2026-05-01 — Planned Phase 4F Natural Language Rule Builder

- Added roadmap/design documentation for AI-assisted rule authoring.
- The planned builder translates natural-language user requests into proposed deterministic mail rules.
- AI drafts only; it does not save rules or execute mailbox actions.
- Human review/save remains required.
- Phase 4F.1 is scoped to safe non-mutating rule actions only.
- Gmail/IMAP mutations such as move_to_spam, add_label, move_to_folder, mark_read, and mark_unread remain blocked/deferred unless routed through future execution gates.

## 2026-05-01 — Phase 4E.2 Final Read-Only Verification

- Added a final read-only verification layer with a mailbox adapter boundary and fake adapter tests before mock execution records can be written.
- Approval mailbox execution now follows `gate evaluator -> final read-only verifier -> mock executor`; verification failures write blocked execution audit rows and do not run the mock executor.
- Added mock execution API response details for gate results, final verification, execution mode, and blocker metadata.
- Updated the Control Center detail view to label the action as mock verification/audit and surface final verification blockers, warnings, mailbox identity, message identity, and current flags.
- Preserved the safety boundary: no live Gmail/IMAP mutation, no `STORE`/flag mutation from approval execution, no bulk execute, no auto-execute, and `add_label` / `move_to_folder` remain deferred.

## 2026-05-01 — Phase 4E.1 Execution Chassis Mock-Only

- Added future execution tables, deterministic plan hashing, idempotency keys, and a pure execution gate evaluator for approved mailbox actions.
- Added a mock-only executor that writes metadata-only execution and immutable event audit rows without touching Gmail or issuing IMAP mutation commands.
- Limited executable readiness to `mark_read` and `mark_unread`; `add_label`, `move_to_folder`, and dangerous actions remain blocked.
- Preserved the safety boundary: approval records human intent only, read/unread are not live, no real IMAP mutation exists in this phase, no bulk execute, no auto-execute, and conservative mutation defaults remain unchanged.

## 2026-05-01 — Phase 4E Live Reversible Mutation Design Review

- Added a design-only Phase 4E spec for future live reversible IMAP mutations.
- Defined execution gates, idempotency keys, audit records, rollback semantics, Gmail/IMAP caveats, capability verification, operator flow, failure handling, and later implementation tests.
- Recommended the first live phase support only `mark_read` and `mark_unread`; `add_label` and `move_to_folder` remain deferred.
- Preserved the safety boundary: no live mutation by default, no autonomous execution, no auto-execute after approval, no bulk actions, no dangerous actions, and no mutation when identity or rollback safety is uncertain.

## 2026-04-30 — Phase 4D.7 Synthetic Approval Visual QA

- Added a dev-only, frontend-only Control Center synthetic approval fixture mode gated by `VITE_APPROVAL_FIXTURES=1` during Vite development.
- Fixture records cover populated readiness, approved-but-live-disabled, dangerous blocked, missing UID, missing UIDVALIDITY, UIDVALIDITY mismatch, missing capability cache, disabled account, terminal/archived, and cleanup preview states.
- Synthetic mode is visibly labelled, uses fake `fixture.gmail.local` data, disables approval/execution/archive/cleanup endpoint controls, and does not write `data/agent.db` or touch Gmail/IMAP.
- Preserved the Phase 4D safety boundary: no live mutation enablement, no auto-approval, no bulk approval, no auto-retry, and no default safety config changes.

## 2026-04-30 — Phase 4D.6 Control Center Operator Polish

- Polished the Control Center approval detail view with a top safety banner, summary, blockers, capability state, dry-run plan, safety preview, and lifecycle/audit context.
- Updated operator copy so readiness, dry-run planning, approval, blocked states, and archived terminal records are visually distinct and do not imply Gmail or mailbox mutation.
- Added frontend helper coverage for readiness-only labels, live-disabled approval wording, dangerous blocked wording, missing capability cache, UIDVALIDITY mismatch, `would_mutate=false`, and safe-default config blockers.
- Preserved the Phase 4D safety boundary: live mailbox mutation remains disabled, approval records human intent only, no bulk approval or auto-execution was added, and safe/off/dry-run defaults remain intact.

## 2026-04-30 — Phase 4D.4 Approval Lifecycle Hygiene

- Added `archived_at` lifecycle metadata for terminal approvals so old items can be hidden from the active Control Center while retaining audit history.
- Added read-only cleanup preview, explicit cleanup execution, manual archive/unarchive, and sanitized JSON export endpoints for approval/audit history.
- Added active/history Control Center filters, cleanup preview counts, one-by-one archive/unarchive actions, and current-filter JSON export.
- Cleanup remains conservative: disabled by default, no hard delete in this phase, and started/stuck approvals are excluded.

## 2026-04-30 — Phase 4D.3 Approval Preview Quality

- Added read-only approval preview fields for operator confidence: title, summary, message context, trigger/rule context, risk level/reasons, reversibility, operator guidance, and current gate preview.
- Gate preview now explains whether approval would be blocked, dry-run only, unsupported, terminal, stale/manual-review, or statically ready under current config without starting execution or writing audit events.
- Updated the Control Center cards and detail panel to show risk, gate status, message context, proposed action, safety preview, and review guidance before approval.
- Preserved the pre-live boundary: no autonomous execution, no bulk approval, no new action types, and no default mailbox mutation enablement.

## 2026-04-30 — Phase 4D.2 Approval Result Visibility

- Added normalized approval detail responses with derived execution state, expiry, decision timestamps, blocked reason, execution error, gate result, and audit event IDs.
- Added chronological approval audit events via `/api/mail/approvals/{approval_id}/events` and embedded events in approval detail responses.
- Added stale `execution_status='started'` detection with `started_stale_after_minutes=30` and a safe mark-failed endpoint for stale started approvals only; it does not retry execution.
- Updated the Control Center with compact result visibility, a detail panel, proposed value/gate result display, audit timeline, and stuck-execution guidance.
- Preserved the Phase 4D safety boundary: no bulk approval, no autonomous execution, no new action types, and existing mutation/dry-run gates still decide final outcome.

## 2026-04-30 — Phase 4D.1 Control Center Operator Approval

- Added `mail_action_approvals` in `data/agent.db` for human review of AI-triggered action suggestions.
- AI trigger matches now keep dry-run audit behavior and create pending approval items instead of executing actions.
- Added approval API endpoints for list/get/approve/reject/execute/expire with API-key protection and complete audit events.
- Added a native dashboard Control Center tab for pending, approved, executed, blocked, failed, rejected, and expired approvals.
- Approved execution remains explicit and reuses existing gated action paths; mode, mutation config, dry-run, UIDVALIDITY, and IMAP capabilities still decide the outcome.
- Live autonomous AI actions, iMessage sends, reply, forward, delete, expunge, unsubscribe, webhooks, and bulk approval remain unavailable.

## 2026-04-30 — Phase 4A-4C.3A Release Stabilization

- Confirmed the release boundary across docs: deterministic Phase 4A rules, read-only Phase 4B AI enrichment, gated rule-managed Phase 4C.1/4C.2 mailbox mutations, and preview-only Phase 4C.3A AI triggers.
- Added an operator checklist for preflight, authenticated status, AI settings, AI classification test, deterministic rule preview, AI trigger preview, and conservative config defaults.
- Reaffirmed that live AI-triggered actions remain intentionally disabled for this checkpoint.

## 2026-04-30 — Phase 4C.3A Preview-Only AI Triggers

- Added deterministic AI trigger rules over validated AI classification fields: category, urgency score, confidence, needs-reply, summary, and reason.
- Added `/api/mail/ai/triggers*` CRUD and preview endpoints plus `/api/mail/messages/{message_id}/ai-triggers`.
- Trigger actions are recognized as preview-only plans: dashboard notification, iMessage, mailbox move/read/flag, and needs-reply planning.
- Matched triggers write `ai_trigger_matched` audit events with dry-run planned actions.
- AI triggers do not call IMAP mutation helpers, bridge iMessage send, reply, forward, delete, expunge, unsubscribe, or external webhooks.
- Dashboard Settings gained an AI Triggers section with condition/action builders and explicit preview-only copy.

## 2026-04-30 — Mail Rules Phase 4B Through 4C.2

- Added Phase 4B read-only AI enrichment with `mail_ai_queue`, `mail_ai_classifications`, Ollama JSON-schema validation, manual reprocess, and dashboard AI status display.
- Added Phase 4C.1 IMAP mutation primitives: capability probing, UIDVALIDITY-checked `UID MOVE`, safe `UID STORE` for `\Seen` / `\Flagged`, conservative config gates, dry-run enforcement, and mutation audit rows.
- Added Phase 4C.2 rule-managed safe mailbox actions for deterministic rules: `move_to_folder`, `mark_read`, `mark_unread`, `mark_flagged`, and `unmark_flagged`.
- Kept mailbox mutations blocked outside `live`, blocked when `[mail.imap_mutations].enabled=false`, and dry-run protected by default.
- Updated the native mail dashboard Rules UI to expose only safe mailbox actions with target handling, preview gate results, and clear preview/dry-run safety labels.
- Verified the accepted checkpoint with backend regression tests, dashboard build, authenticated `mailagent_status.py --no-run`, Docker rebuild, and bounded mail-agent logs.

## 2026-04-29 — Mail Account Settings Hardening

- Moved the native `mail-dashboard` account-management flow onto the finance API mount at `127.0.0.1:8090/api/mail/*`, with `agent.app.api_mail` imported and mounted by `finance/api.py`.
- Fixed mail router import failures by using package-relative imports in `agent/app/api_mail.py` and logging finance-side mount failures explicitly.
- Updated the `finance-api` Docker image to install the full `finance/requirements.txt` set so `tomlkit`, `keyring`, and related mail-account dependencies are present at runtime.
- Added defensive dashboard response parsing so HTML fallback responses now surface as actionable API errors instead of `Unexpected token '<'`.
- Added Gmail app-password whitespace normalization in the dashboard and backend so pasted passwords with spaces or non-breaking spaces test correctly.
- Fixed `config/settings.toml` account persistence to write valid inline TOML entries under `[mail.imap].accounts`.
- Added fallback handling for bind-mounted `settings.toml` writes when atomic rename is blocked by Docker `EBUSY`.
- Placeholder IMAP rows such as `YOUR_EMAIL@gmail.com` are now filtered from `/api/mail/accounts` and no longer appear in the native dashboard.

## 2026-04-29 — Mail Agent IMAP, PDF Routing, And Native Dashboard Wiring

- Wired the Docker mail agent to use agent-side IMAP intake when real `[mail.imap].accounts` are configured, while preserving the bridge mail source as fallback.
- Added IMAP UID/UIDVALIDITY state, bounded re-scan handling, size guards, and durable message/attachment idempotency keys.
- Added attachment routing through `agent/app/pdf_router.py`: bridge multipart `/pdf/unlock`, strict filename validation, deterministic fallback names, NAS sentinel checks, collision handling, and explicit `pending_review` / `failed_retryable` states.
- Added server-side safety modes: `observe`, `draft_only`, and `live`; blocked actions are recorded as `mode_blocked`.
- Added local mail-agent worker APIs and a smoke harness in `scripts/mailagent_status.py`; dashboard-facing mail APIs were later mounted through the finance API at `127.0.0.1:8090/api/mail/*`, while worker health/debug remains on `127.0.0.1:8080`.
- Added the Electron + React `mail-dashboard/` menu-bar app and fixed its production build.
- Hardened bridge health checking so the agent consults authenticated `/health` and requires `overall=ok` instead of relying on `/healthz`.
- Updated `scripts/mailagent_preflight.py` to report Docker compose restart policies, healthchecks, volumes, API collisions, SQLite schemas, config keys, and NAS mount state more accurately.
- Deployment note: the rebuilt `finance-api` image started healthy; `mail-agent` requires `/Volumes/Synology/mailagent` to be mounted and shared with Docker before it can start.

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

## 2026-04-30 — Phase 4D.5 Reversible Mutation Readiness

- Added conservative `[mail.imap_mutations]` allow flags and UIDVALIDITY/capability-cache requirements, all disabled by default.
- Added cached IMAP capability readiness storage and read-only readiness summaries.
- Added dry-run mutation plans, safety gates, UID/UIDVALIDITY identity checks, and rollback hints for future reversible action candidates.
- Updated Control Center detail to show readiness-only mutation plans without offering live enablement.
- Preserved dangerous action blocking, no bulk approval, no auto-retry, and no live mailbox mutation by default.

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
## 2026-04-30 — Phase 4B Read-Only Mail AI Enrichment

- Added `[mail.ai]` settings with atomic TOML read/write APIs.
- Added `mail_ai_queue` lifecycle helpers and a single-worker Ollama `/api/chat` enrichment loop.
- Added Pydantic validation for structured AI categories, urgency, confidence, summary, and raw JSON persistence.
- Added `/api/mail/ai/settings`, `/api/mail/ai/test`, and `/api/mail/messages/{message_id}/reprocess`.
- Dashboard now shows AI status/category/urgency/confidence/summary separately from deterministic classification and can queue manual reprocess.
- Phase 4B remains read-only: no IMAP mutations, auto-reply, forwarding, delete, unsubscribe, webhooks, or AI-triggered iMessage actions.
