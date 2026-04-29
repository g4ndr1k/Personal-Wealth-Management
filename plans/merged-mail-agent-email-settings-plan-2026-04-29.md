# Merged Plan — Native Mail Dashboard + IMAP Account Settings

## Purpose

Merge and revise:

- `/Users/g4ndr1k/Downloads/email-config-plan.md`
- `/Users/g4ndr1k/Downloads/users-g4ndr1k-agentic-ai-docs-ultimate-parallel-thunder.md`

This version is aligned to the current repo and the implementation work already completed on 2026-04-29.

It preserves the larger mail-agent direction from the refined "Ultimate Mail Agent" plan, but updates the IMAP account settings plan to reflect the actual architecture:

- `mail-agent` remains the worker in Docker
- `bridge` remains the host-only Mac service
- the native Electron dashboard lives in `mail-dashboard/`
- the dashboard calls `127.0.0.1:8090/api/mail/*`
- `finance/api.py` mounts `agent.app.api_mail`
- account metadata lives in `config/settings.toml`
- credentials use macOS Keychain first, with optional TOML fallback

## Current Architecture Baseline

### Mail path

```text
IMAP account(s)
  -> Docker mail-agent (`agent/`)
  -> local classifier / PDF router / state
  -> bridge on macOS for iMessage + PDF unlock
  -> native Electron dashboard (`mail-dashboard/`)
  -> finance API mount at 127.0.0.1:8090/api/mail/*
```

### Important corrections vs older plan drafts

1. The native dashboard does not talk to `127.0.0.1:8080/api/mail/*` for settings.
   It talks to `127.0.0.1:8090/api/mail/*`, mounted by `finance/api.py`.

2. Mail account CRUD belongs in `agent/app/api_mail.py`, mounted by `finance/api.py`.
   It should not be implemented in `agent/app/health.py`.

3. `[mail.imap].accounts` must be persisted as an array of inline TOML tables, not malformed partial table fragments.

4. Gmail App Passwords in Keychain use:
   - service: `agentic-ai-mail-imap`
   - account: email address

5. Placeholder IMAP rows such as `YOUR_EMAIL@gmail.com` are not valid live accounts.
   They must be ignored by runtime logic and filtered from the dashboard API.

## Goals

### Goal A — Stable native dashboard account management

The `Settings` tab in `mail-dashboard/` should let the user:

- add a Gmail IMAP account
- test connection before saving
- edit allowed mutable fields
- disable and re-enable polling
- soft-delete an account
- reactivate a soft-deleted account
- reload config safely

This is a local-only control surface for the existing backend.

### Goal B — Preserve the broader Ultimate Mail Agent direction

The account-management work must fit into the larger roadmap:

- IMAP-first intake
- direct PDF attachment handling in the Python agent
- bridge for host-only capabilities
- server-enforced safety modes
- native menu-bar dashboard

## Scope

## Phase 0 — Implemented / already established

The following is already true or should now be treated as baseline:

- `finance/api.py` mounts `agent.app.api_mail` under `/api/mail`
- `mail-dashboard` reads mail dashboard/account APIs through `127.0.0.1:8090`
- dashboard-side response parsing handles accidental HTML fallback cleanly
- Gmail App Password whitespace is normalized in both UI and backend
- bind-mounted `config/settings.toml` writes fall back safely when atomic rename hits Docker `EBUSY`
- placeholder accounts are filtered from `/api/mail/accounts`

## Phase 1 — IMAP account settings v1

### Supported provider

Gmail App Password accounts only.

For v1:

- provider = `gmail`
- host = `imap.gmail.com`
- port = `993`
- ssl = `true`
- auth_type = `app_password`

Outlook remains deferred until OAuth2 device-code flow is implemented.

### UI fields

- display name
- email address
- app password
- enabled

### API surface

All endpoints require `X-Api-Key`.

- `GET /api/mail/accounts`
- `POST /api/mail/accounts/test`
- `POST /api/mail/accounts`
- `PATCH /api/mail/accounts/{account_id}`
- `PATCH /api/mail/accounts/{account_id}/enabled`
- `DELETE /api/mail/accounts/{account_id}`
- `POST /api/mail/accounts/{account_id}/reactivate`
- `POST /api/mail/config/reload`
- `POST /api/mail/run`

### Required behavior

#### `GET /api/mail/accounts`

- returns config-backed account metadata merged with runtime health
- excludes placeholder accounts
- excludes soft-deleted accounts unless explicitly building an admin-only view later
- never returns credentials

#### `POST /api/mail/accounts/test`

- accepts email + app password
- strips whitespace and non-breaking spaces from pasted passwords
- validates Gmail IMAP login over SSL
- confirms `SELECT INBOX` succeeds
- does not persist anything

#### `POST /api/mail/accounts`

- runs IMAP test first
- writes metadata to `config/settings.toml`
- stores credential in Keychain or fallback file
- returns `409` on duplicate normalized email
- returns `409` + reactivation hint for soft-deleted duplicate
- marks config reload pending

#### `PATCH /api/mail/accounts/{account_id}`

- allows display name
- allows enabled flag
- allows folders
- allows lookback days
- allows size limits
- if password changes, re-tests before save

#### `PATCH /api/mail/accounts/{account_id}/enabled`

- soft-disables or re-enables polling
- preserves message history and attachment history

#### `DELETE /api/mail/accounts/{account_id}`

- soft-deletes by default
- sets `enabled = false`
- sets `deleted_at`
- optional `purge_secret=true` removes the stored credential

#### `POST /api/mail/accounts/{account_id}/reactivate`

- clears `deleted_at`
- re-enables the account

#### `POST /api/mail/config/reload`

- sets reload-pending state for the worker
- applies between poll cycles

## Storage Model

### Metadata in `config/settings.toml`

The live shape should be:

```toml
[mail.imap]
accounts = [
  {email = "user@gmail.com", provider = "gmail", id = "gmail_user", name = "User", host = "imap.gmail.com", port = 993, ssl = true, auth_type = "app_password", folders = ["INBOX"], lookback_days = 14, max_message_mb = 25, max_attachment_mb = 20, enabled = true, auth_source = "keychain", keychain_service = "agentic-ai-mail-imap"},
]
```

Notes:

- `name` is the runtime-facing field used by the IMAP poller
- `id` is immutable and used by CRUD routes
- account entries must be valid inline tables inside the `accounts = [ ... ]` array

### Credentials

Preferred:

- macOS Keychain via Python `keyring`
- service = `agentic-ai-mail-imap`
- account = email address

Fallback only:

- `secrets/imap.toml`
- enabled only when `[mail.imap].credential_store = "toml"`
- runtime maps this to `auth_source = "file"`

Example fallback file:

```toml
[[accounts]]
email = "user@gmail.com"
app_password = "redacted"
```

### Security rules

- `app_password` is write-only
- API responses must never return it
- logs must never include it
- dashboard state must never expose it after submission
- Keychain write failure must return an error, not silently downgrade to file storage

## Write Semantics

### Required write flow

1. Load current TOML
2. Merge requested change
3. Validate renderable TOML
4. Write temp file
5. `fsync`
6. Replace original
7. If Docker bind-mount blocks replace with `EBUSY`, fall back to in-place write + `fsync`
8. Write backup under `config/backups/`

### Rollback rules

- if secret write succeeds but settings write fails, rollback the secret
- never leave an enabled account without a valid credential
- settings mutations should emit redacted audit events

## Dashboard UX

### Account list

Show:

- display name
- email
- provider
- enabled/disabled state
- last successful poll
- last error
- remove from polling action

### Add account modal

Show:

- Display Name
- Gmail Address
- App Password
- `Test Connection`
- `Save`

Rules:

- `Save` disabled until test succeeds
- copy should say Gmail App Password, not Google account password
- soft-deleted duplicate should surface a `Reactivate` action

### Error handling

If the backend returns HTML instead of JSON, the UI should show a clear API-mount/config error, not a raw JSON parse exception.

## Phase 2 — Reliability and IMAP runtime alignment

This phase continues the broader Ultimate Mail Agent roadmap but is constrained by current repo structure.

### IMAP intake

- keep `agent/app/imap_source.py` as the mail-agent IMAP runtime
- use per-account folder checkpoint state
- track `uidvalidity`
- ignore placeholder accounts
- treat downstream PDF failures separately from message checkpoint advancement

### Network and wake resilience

- keep `network_ok()` gate at poll-loop top
- bridge `/health` must remain structured and action-relevant
- Docker `mail-agent` uses restart policy

## Phase 3 — Native dashboard completion

The current Settings flow is only one part of the native dashboard.

The full native dashboard should still preserve:

- menu-bar app form factor
- Dashboard / Emails / Drafts / Settings tabs
- KPI cards
- source split
- classification summary
- recent activity
- local-only operation

But account CRUD must remain backend-driven through `/api/mail/*`, not implemented in the renderer alone.

## Deferred Work

- Outlook OAuth2 device-code flow
- Gmail OAuth2 migration away from App Password bootstrap
- richer Settings editing UI for folders and size limits
- explicit admin view for soft-deleted accounts
- dashboard toast/success confirmation after account save
- tests for bind-mounted config writes and malformed TOML recovery

## Verification Checklist

### Account settings

- Add valid Gmail account -> test passes -> save succeeds -> account appears in Settings
- Add wrong app password -> test fails -> nothing saved
- Add duplicate email -> `409`
- Add soft-deleted duplicate -> reactivation flow offered
- Delete/disable account -> polling stops but history remains
- Restart services -> metadata and credentials still resolve

### Config and secrets

- `config/settings.toml` remains valid TOML after each mutation
- account entry is written as a valid inline table
- secret never appears in API output or logs
- bind-mounted config writes succeed under Docker

### Runtime

- placeholder accounts do not appear in Settings
- `/api/mail/accounts` merges config with runtime health
- `/api/mail/config/reload` causes agent to pick up changes

## Recommended File Ownership

### Backend

- `agent/app/api_mail.py`
- `agent/app/config_manager.py`
- `agent/app/imap_source.py`
- `agent/app/state.py`
- `finance/api.py`
- `finance/Dockerfile`

### Native dashboard

- `mail-dashboard/src/api/mail.tsx`
- `mail-dashboard/src/views/Settings.tsx`

### Docs

- `docs/OPERATIONS.md`
- `docs/SYSTEM_DESIGN.md`
- `docs/TROUBLESHOOTING.md`
- `docs/ULTIMATE_MAIL_AGENT.md`
- `docs/CHANGELOG.md`

## Decision Summary

This merged plan keeps the strategic direction of the refined Ultimate Mail Agent document, but revises the email-settings implementation to match the working system:

- native dashboard remains local-only
- account CRUD is mounted through the finance API at `:8090`
- IMAP account config is TOML-backed
- credentials are Keychain-first
- placeholder rows are not real accounts
- Dockerized runtime constraints such as bind-mounted config writes are handled explicitly

