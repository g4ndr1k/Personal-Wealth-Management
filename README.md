# Personal Wealth Management

Self-hosted personal finance, bank statement processing, mail alerting, wealth tracking, and annual tax return (CoreTax SPT) system for a Mac Mini plus optional Synology NAS replica.

The system is local-first: the bridge runs on macOS for host-only capabilities, the finance API and PWA run in Docker, bank statement PDFs are parsed into `output/xls/ALL_TRANSACTIONS.xlsx`, and `data/finance.db` is the authoritative edited store.

## What It Does

| Area | Summary |
|---|---|
| Mail alerts | Polls financial mail, classifies through registered providers, and sends iMessage alerts. |
| PDF processing | Processes local bank PDFs from `data/pdf_inbox` / `data/pdf_unlocked` through bridge jobs. |
| Finance import | Imports `ALL_TRANSACTIONS.xlsx` into SQLite with hash-based deduplication. |
| Wealth tracking | Stores balances, holdings, liabilities, and net worth snapshots. |
| CoreTax SPT | Persistent tax-version ledger: import prior-year SPT XLSX, carry forward manual decisions, auto-reconcile refreshable rows from live wealth data, learn mappings across years, export to CoreTax XLSX template. |
| PWA | Vue 3 dashboard for transactions, wealth, assets, CoreTax SPT wizard, review queue, audit, and settings. |
| NAS services | Optional read-only finance API/PWA replica plus LAN-only Household Expense PWA. |

## Quick Start

```bash
# Finance API
python3 -m finance.server

# Bridge on the Mac host
PYTHONPATH=$(pwd) python3 -m bridge.server

# PWA development
cd pwa
npm install
npm run dev

# Production containers
docker compose up --build -d
```

For first-time setup, secrets, LaunchAgents, PDF workflows, and maintenance commands, use [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Key Paths

| Path | Purpose |
|---|---|
| `config/settings.toml` | Runtime configuration. |
| `bridge/` | Host bridge: mail, iMessage, PDF processing, bridge API. |
| `agent/` | Dockerized mail alert worker and classifier providers. |
| `finance/` | FastAPI backend, importer, categorizer, SQLite schema, backups. |
| `finance/coretax/` | CoreTax SPT ledger: import parser, carry-forward, reconciler, exporter. |
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
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Running, maintaining, validating, and backing up the system. |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptom-driven diagnosis and fixes. |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Human-readable recent change history. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Lightweight design decisions and rationale. |

## Status

- Stage 1 mail alerting: active
- Stage 2 finance import and PWA: active
- Stage 3 wealth management: active
- CoreTax SPT ledger and wizard: active
- PDF preflight and per-file status lifecycle: active
- NAS read-only replica: configured for deployments that enable NAS sync
- Household Expense PWA: active as a LAN-only NAS satellite app

Private project.
