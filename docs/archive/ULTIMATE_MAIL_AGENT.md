# Ultimate Mail Agent

Implementation status for the Mac-resident mail agent. This document supersedes the earlier greenfield plan that proposed a separate `mailagent/` tree; the implementation now extends the existing `agent/`, `bridge/`, and `mail-dashboard/` modules.

## Current Shape

```text
IMAP account or bridge fallback
  -> Docker mail-agent (`agent/`)
  -> local classifier providers
  -> bridge iMessage / PDF unlock on the Mac host
  -> agent SQLite state in `data/agent.db`
  -> optional NAS PDF archive at `/mnt/mailagent`
  -> Electron menu-bar dashboard (`mail-dashboard/`)
```

The agent runs in Docker. The bridge runs on the Mac host because it owns host-only capabilities such as iMessage, macOS-local files, and PDF unlock support.

## Implemented Modules

| Module | Role |
|---|---|
| `agent/app/orchestrator.py` | Selects IMAP intake when real accounts are configured, falls back to bridge mail source otherwise, enforces safety mode, and advances IMAP checkpoints after durable processing. |
| `agent/app/imap_source.py` | IMAP polling, UID/UIDVALIDITY tracking, bounded lookback, size guards, idempotency keys, and PDF attachment extraction metadata. |
| `agent/app/pdf_router.py` | PDF unlock via bridge multipart bytes, filename validation, deterministic fallback names, NAS sentinel validation, collision handling, and attachment status updates. |
| `agent/app/net_guard.py` | Outbound, bridge health, IMAP, and NAS advisory probes before scan cycles. |
| `agent/app/api_mail.py` | Dashboard account/query router for summary, recent messages, account CRUD, reload, and manual run. |
| `bridge/server.py` | Authenticated `/pdf/unlock` bytes-in/bytes-out endpoint and structured `/health`. |
| `mail-dashboard/` | Electron + React + Tailwind menu-bar dashboard. |
| `scripts/mailagent_preflight.py` | Read-only inventory report for Docker, FastAPI, bridge, SQLite, config, filesystem, and secrets. |
| `scripts/mailagent_status.py` | CLI smoke test for `/api/mail/*`. |

## Safety Modes

Runtime mode is configured under `[agent].mode`.

| Mode | Fetch + classify | iMessage | PDF route | Email mutation |
|---|---:|---:|---:|---:|
| `observe` | yes | no | no | no |
| `draft_only` | yes | yes | yes | no |
| `live` | yes | yes | yes | yes |

Invalid or missing mode resolves to `draft_only`, or `observe` when `[agent].safe_default = "observe"`. Actions blocked by mode are logged as `mode_blocked` events.

## IMAP Intake

IMAP is enabled only when `[mail.imap].accounts` contains real account entries. Placeholder addresses such as `YOUR_EMAIL@gmail.com` are ignored so the bridge mail path can remain active until credentials are ready.

State is tracked per `(account, folder)` using `last_uid` and `uidvalidity`. A UIDVALIDITY reset writes an event and performs a bounded lookback scan. Checkpoints are advanced by the orchestrator after message persistence, not inside the fetcher.

Phase 4A deterministic mail-rule state also belongs in `data/agent.db`. Phase 4D.1 Control Center approval state lives there as `mail_action_approvals`; AI-triggered actions create approval items, not autonomous execution.
This includes `mail_rules`, `mail_rule_conditions`, `mail_rule_actions`,
`mail_processing_events`, and `mail_needs_reply`. Future AI queue and
classification state (`mail_ai_queue`, `mail_ai_classifications`, categories,
and trigger rules) also stays in `agent.db`. `data/finance.db` remains reserved
for PWM/finance data.

Message identity:

```text
message_key = sha256(account + folder + normalized Message-ID)
fallback_message_key = sha256(account + folder + uidvalidity + uid)
attachment_key = sha256(message_key + sha256(pdf_bytes))
```

## PDF Attachment Routing

The agent never passes container-local paths to the bridge. It posts PDF bytes to bridge `/pdf/unlock` as `multipart/form-data`; the bridge returns unlocked bytes and metadata headers.

Routing requires:

- `/Volumes/Synology/mailagent` mounted on the host
- Docker bind mount `/Volumes/Synology/mailagent:/mnt/mailagent`
- sentinel file `/mnt/mailagent/.mailagent_mount`
- `[mail_agent.pdf].mount_sentinel_uuid` matching the sentinel payload

If the sentinel or write probe fails, attachments stay `pending` and are not written into ephemeral container storage.

## Dashboard

The dashboard is a native Electron menu-bar app, not a website. It reads and mutates mail settings through the finance API mount:

| Method | Path |
|---|---|
| `GET` | `http://127.0.0.1:8090/api/mail/summary` |
| `GET` | `http://127.0.0.1:8090/api/mail/recent?limit=20` |
| `GET` | `http://127.0.0.1:8090/api/mail/accounts` |
| `POST` | `http://127.0.0.1:8090/api/mail/accounts/test` |
| `POST` | `http://127.0.0.1:8090/api/mail/accounts` |
| `PATCH` | `http://127.0.0.1:8090/api/mail/accounts/{account_id}` |
| `PATCH` | `http://127.0.0.1:8090/api/mail/accounts/{account_id}/enabled` |
| `DELETE` | `http://127.0.0.1:8090/api/mail/accounts/{account_id}` |
| `POST` | `http://127.0.0.1:8090/api/mail/accounts/{account_id}/reactivate` |
| `POST` | `http://127.0.0.1:8090/api/mail/config/reload` |
| `POST` | `http://127.0.0.1:8090/api/mail/run` |

The dashboard spawns no Python processes. Quitting it does not stop mail processing.

Implementation notes:

- Gmail App Passwords are normalized to remove pasted whitespace before IMAP login or save.
- Keychain lookup uses service `agentic-ai-mail-imap` and account equal to the Gmail address.
- Docker mail-agent runtime uses `secrets/imap.toml` mounted as `/app/secrets/imap.toml` for IMAP App Passwords. The file must be mode `600`, use `[[accounts]]` entries with `email` and `app_password`, and is gitignored.
- Placeholder rows such as `YOUR_EMAIL@gmail.com` are ignored by the runtime and filtered out of the dashboard account list.

## Verification

```bash
python3 scripts/mailagent_preflight.py
python3 scripts/mailagent_status.py --no-run

cd mail-dashboard
npm install
npm run build
```

After Python changes:

```bash
docker compose up --build -d
docker compose logs -f mail-agent
```

If Docker reports a mount error for `/host_mnt/Volumes/Synology/mailagent`, mount the Synology share and allow Docker Desktop to share `/Volumes`, then recreate `mail-agent`.
