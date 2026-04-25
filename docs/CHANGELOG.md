# Changelog

Human-readable project history. Reverse chronological order.

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
