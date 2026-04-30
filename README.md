# Personal Wealth Management

Self-hosted personal finance, bank statement processing, mail alerting, wealth tracking, and annual tax return (CoreTax SPT) system for a Mac Mini plus optional Synology NAS replica.

The system is local-first: the bridge runs on macOS for host-only capabilities, the finance API and PWA run in Docker, bank statement PDFs are parsed into `output/xls/ALL_TRANSACTIONS.xlsx`, and `data/finance.db` is the authoritative edited finance store. Mail-agent runtime state, including Phase 4A mail rules and audit events, lives in `data/agent.db`.

## What It Does

| Area | Summary |
|---|---|
| Mail alerts | Dockerized mail agent polls configured IMAP accounts or the bridge fallback, classifies through registered providers, and sends iMessage alerts through the Mac bridge. |
| PDF processing | Processes local bank PDFs from `data/pdf_inbox` / `data/pdf_unlocked` through bridge jobs; IMAP PDF attachments can be unlocked through bridge `/pdf/unlock` and routed to `/mnt/mailagent` when the NAS mount sentinel is valid. |
| Finance import | Imports `ALL_TRANSACTIONS.xlsx` into SQLite with hash-based deduplication and guarded parser-variant reconciliation. |
| Wealth tracking | Stores balances, holdings, liabilities, and net worth snapshots. |
| CoreTax SPT | Persistent tax-version ledger: import prior-year SPT XLSX, carry forward manual decisions, map PWM sources before reconcile, auto-reconcile refreshable rows from live wealth data, preserve component history, export to CoreTax XLSX template. |
| Matching engine | Shared `finance/matching/` infrastructure for auditable mappings, confidence, rejected suggestions, shadow diffs, and matching-console APIs. |
| PWA | Vue 3 dashboard for transactions, wealth, assets, CoreTax SPT wizard, review queue, audit, and settings. |
| Mail dashboard | Electron + React menu-bar app under `mail-dashboard/` that reads dashboard mail APIs on `127.0.0.1:8090/api/mail/*`; worker health/debug remains on `127.0.0.1:8080`. |
| NAS services | Optional read-only finance API/PWA replica plus LAN-only Household Expense PWA. |

## Quick Start

### Mail Agent & Dashboard
1. **Start Bridge (Host):** `PYTHONPATH=$(pwd) python3 -m bridge.server` (or `./scripts/run_bridge.sh`)
2. **Start Agent (Docker):** `docker compose up -d mail-agent` (or `./scripts/start_agent.sh`)
3. **Open Dashboard:**
   ```bash
   cd mail-dashboard
   npm run dev
   ```

### Finance API & PWA
1. **Start API:** `docker compose up -d finance-api` (or `python3 -m finance.server`)
2. **Open PWA:**
   ```bash
   cd pwa
   npm run dev
   ```

### Status & Verification
- **Mail Status:** `python3 scripts/mailagent_status.py --no-run`
- **Preflight:** `python3 scripts/mailagent_preflight.py`

For first-time setup, secrets, LaunchAgents, PDF workflows, and maintenance commands, use [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Key Paths

| Path | Purpose |
|---|---|
| `config/settings.toml` | Runtime configuration. |
| `bridge/` | Host bridge: mail, iMessage, PDF processing, bridge API. |
| `agent/` | Dockerized mail alert worker and classifier providers. |
| `mail-dashboard/` | Native Electron dashboard for mail-agent status and manual Run Now. |
| `finance/` | FastAPI backend, importer, categorizer, SQLite schema, backups. |
| `finance/coretax/` | CoreTax SPT ledger: import parser, carry-forward, fingerprint mapping, suggestion engine, reconciler, exporter. |
| `finance/matching/` | Generic matching engine, storage helpers, domain adapters for CoreTax, parser routing, dedup, and categorization. |
| `pwa/` | Vue 3 PWA frontend. |
| `household-expense/` | LAN-only household expense satellite app for NAS. |
| `parsers/` | Bank-specific PDF parsers. |
| `exporters/` | XLS writer for `ALL_TRANSACTIONS.xlsx`. |
| `data/coretax/` | CoreTax XLSX templates and generated output files, gitignored. |
| `data/` | Runtime databases and PDF inboxes, gitignored. |
| `secrets/` | Docker-exported secret files, gitignored. |

## Documentation

| Document | Purpose |
|---|---|
| [docs/SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md) | Stable architecture, contracts, flows, and component responsibilities. |
| [docs/MAIL_AGENT.md](docs/MAIL_AGENT.md) | Detailed mail-agent, dashboard, IMAP, rules, credential, and troubleshooting reference. |
| [docs/phase-4f-natural-language-rule-builder.md](docs/phase-4f-natural-language-rule-builder.md) | Planned AI-assisted mail rule authoring design; AI drafts, deterministic validation checks, human saves. |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Running, maintaining, validating, and backing up the system. |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptom-driven diagnosis and fixes. |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Human-readable recent change history. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Lightweight design decisions and rationale. |
| [docs/ch-hp-worklow.md](docs/ch-hp-worklow.md) | Specialized homepage workflow reference. |

## Status

- Stage 1 mail alerting: active
- Stage 1 IMAP intake and attachment routing: implemented; enabled when `[mail.imap].accounts` contains real accounts, otherwise the bridge mail source remains the fallback
- Native mail dashboard: implemented and buildable; talks to dashboard mail APIs on local port `8090`
- Mail rule roadmap: Phase 4F natural-language rule builder is planned as non-mutating AI draft/preview/human-save only
- Stage 2 finance import and PWA: active
- Stage 3 wealth management: active
- CoreTax SPT mapping-first ledger and wizard: active
- Generic matching engine foundation: active; parser/dedup/categorization engine paths are flag-gated while legacy paths remain available
- PDF preflight and per-file status lifecycle: active
- NAS read-only replica: configured for deployments that enable NAS sync
- NAS mail-agent PDF archive: requires `/Volumes/Synology/mailagent` mounted and shared with Docker as `/mnt/mailagent`
- Household Expense PWA: active as a LAN-only NAS satellite app

Private project.
