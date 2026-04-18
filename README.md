# Agentic AI — Personal Finance & Mail Alert System

A self-hosted, privacy-first system for email monitoring, iMessage alerts, bank statement processing, and household wealth tracking. Runs on an Apple Silicon Mac + Synology NAS, with no cloud dependencies in the hot path.

> Full architecture and operations reference: [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md)

---

## What it does

| Capability | How |
|---|---|
| Monitors Mail.app for financial emails | Reads Apple Mail's local SQLite DB every 60 m |
| Classifies emails with a local LLM | Ollama + `gemma4:e4b` — no cloud API required |
| Sends iMessage alerts to iPhone | Messages.app via AppleScript |
| Parses password-protected bank PDFs | pdfplumber + pikepdf; 13 bank/statement parsers |
| Imports transactions into SQLite | Deduplication by SHA-256 fingerprint |
| Categorizes transactions | 4-layer engine: exact alias → contains → regex → Ollama |
| Tracks net worth over time | Account balances, investment holdings, liabilities |
| Serves a mobile-first wealth PWA | Vue 3 + FastAPI; offline-capable via IndexedDB |
| Syncs a read-only replica to NAS | SSH backup after every import |

---

## Deployment architecture

```
Internet (via Cloudflare Tunnel)
  └── codingholic.fun  ·  Next.js public homepage  ·  port 3003 on NAS
        Public: about, articles, planned BaZi app
        Tool cards: private services labelled "Requires Tailscale"

Tailscale VPN (private access only)
  ├── mac.codingholic.fun  →  Mac finance API + PWA (:8090)  read/write
  └── ro.codingholic.fun   →  NAS finance API + PWA (:8090)  read-only demo

Mac Mini (host)
  ├── Bridge  ·  Python  ·  127.0.0.1:9100
  │     Reads Mail.app + Messages.app SQLite DBs
  │     Sends iMessage via AppleScript
  │     Processes bank PDFs → XLS → SQLite
  ├── Mail Agent  (Docker)
  │     Polls bridge → classifies via Ollama → sends alerts
  ├── Finance API + PWA  (Docker  ·  :8090)
  │     FastAPI backend + Vue 3 PWA
  └── Ollama  (host  ·  :11434)
        gemma4:e4b — classification + categorization

Synology DS920+ (always-on)
  └── Finance API NAS  (Docker  ·  :8090)
        FINANCE_READ_ONLY=true
        DB synced from Mac via SSH after each import
```

### Access tiers

| Surface | URL | Access |
|---|---|---|
| Public homepage | codingholic.fun | Open internet |
| Finance dashboard (read/write) | mac.codingholic.fun | Tailscale only |
| NAS read-only demo | ro.codingholic.fun | Tailscale only |

---

## Data flows

### Mail alerts

```
Mail.app syncs locally
  ↓  bridge polls Envelope Index (SQLite) every 30 s
Agent classifies via Ollama (gemma4:e4b)
  ↓  financial category detected
iMessage alert sent to iPhone
```

### Bank statement pipeline

```
Bank PDF  (email attachment or dropped into data/pdf_inbox/)
  ↓  bridge/pipeline.py  —  unlock → parse → XLS export
output/xls/ALL_TRANSACTIONS.xlsx
  ↓  finance/importer.py  —  dedup by SHA-256 hash
SQLite  data/finance.db
  ↓  post-import: tiered backup → NAS SSH sync → snapshot rebuilt
FastAPI  finance/api.py  (:8090)
  ↓
PWA  (Vue 3)
```

---

## Supported banks

| Bank | Statement types |
|---|---|
| Maybank | Credit card, consolidated portfolio |
| BCA | Credit card, savings (Tahapan) |
| Permata | Credit card, savings (Rekening Koran) |
| CIMB Niaga | Credit card, consolidated portfolio |
| IPOT (Indo Premier) | Client portfolio, client statement (RDN) |
| BNI Sekuritas | Portfolio statement, consolidated account statement |
| Stockbit Sekuritas | Statement of Account |

Detection is automatic — the router reads the first page of any PDF and identifies bank + statement type without manual selection.

---

## Project layout

```
agentic-ai/
├── agent/              # Dockerized mail agent (classifier, iMessage command handler)
├── bridge/             # Host Python HTTP server (Mail.app, Messages.app, PDF processor)
├── parsers/            # Bank statement parsers (one file per bank/statement type)
├── exporters/          # XLS writer (ALL_TRANSACTIONS.xlsx + per-bank files)
├── finance/            # FastAPI backend, SQLite schema, importer, categorizer, backup
├── pwa/                # Vue 3 PWA (mobile-first wealth dashboard)
├── scripts/            # Batch processor, NAS deploy, seed scripts, setup helpers
├── config/
│   └── settings.toml   # All runtime config (Stage 1 + Stage 2 + Stage 3)
├── data/               # Runtime SQLite DBs (gitignored)
└── secrets/            # Docker secret files exported from Keychain (gitignored)
```

---

## Stage 1 — Mail agent

The **bridge** (`bridge/`) runs on the Mac host and exposes a bearer-authenticated HTTP API at `127.0.0.1:9100`. The **mail agent** (`agent/`) runs in Docker and polls the bridge every cycle.

### Alert categories

| Category | Triggers on |
|---|---|
| `transaction_alert` | Purchase confirmations, bank transfers |
| `bill_statement` | Monthly bills, credit card statements |
| `bank_clarification` | Document requests, verification emails |
| `payment_due` | Due dates, overdue notices |
| `security_alert` | Login attempts, OTP codes, fraud alerts |
| `financial_other` | Other finance-adjacent emails |

### iMessage commands (from your iPhone)

```
agent: status     — system health summary
agent: pause      — pause all alerts
agent: resume     — resume alerts
agent: quiet      — suppress non-critical for 8 h
```

---

## Stage 2 — Finance pipeline

### Categorization engine

`finance/categorizer.py` applies four layers in order:

1. **Exact alias** — literal match against the `merchant_aliases` SQLite table
2. **Contains alias** — substring match, specificity-sorted by length so longer patterns win
3. **Regex alias** — pattern match
4. **Ollama suggestion** — `gemma4:e4b` enriches unmatched rows; user confirms in Review Queue

Aliases survive re-imports. Manual category edits live in a separate `category_overrides` layer that is never overwritten by a fresh import.

### SQLite schema (key tables)

| Table | Role |
|---|---|
| `transactions` | Raw parser output, keyed by SHA-256 hash |
| `category_overrides` | Human edits — survive re-imports |
| `transactions_resolved` | View merging base rows with overrides |
| `merchant_aliases` | Exact / contains / regex categorization rules |
| `categories` | Category reference data (name, group, icon, budget) |
| `account_balances` | Monthly cash + account balances |
| `holdings` | Investment holdings by month |
| `liabilities` | Credit cards, loans by month |
| `net_worth_snapshots` | 24-column aggregated net worth per month |

**Key invariant:** `data/finance.db` is the authoritative edited store. If it is deleted, re-run `python3 -m finance.importer` to rebuild from `output/xls/ALL_TRANSACTIONS.xlsx`.

### PWA views

| View | Path | Purpose |
|---|---|---|
| Dashboard | `/` | Net worth hero, asset allocation doughnut, cash-flow chart |
| Flows | `/flows` | Monthly income/expense bars + category breakdown |
| Wealth | `/wealth` | Month-over-month net worth trend + AI explanation |
| Assets | `/holdings` | Balances, holdings, liabilities CRUD |
| Transactions | `/transactions` | Filterable, paginated list with inline category edit |
| Review Queue | `/review` | Confirm AI-suggested categorizations |
| Foreign Spend | `/foreign` | Transactions grouped by currency with flag emojis |
| Adjustment | `/adjustment` | Quick-edit market value + date for real estate / retirement |
| Audit | `/audit` | 2-month call-over comparison + PDF completeness grid |
| Settings | `/settings` | Import, NAS sync, pipeline controls, category editor |

---

## Stage 3 — Wealth management

Extends Stage 2 with `/api/wealth/` endpoints for tracking net worth across asset classes over time.

### Asset classes

`cash` · `stock` · `mutual_fund` · `bond` · `real_estate` · `gold` · `vehicle` · `retirement` · `other`

### Key behaviours

- **Carry-forward** — real estate, gold, retirement, and vehicle holdings propagate forward each month automatically, preventing dashboard gaps between PDF uploads
- **Snapshot generation** — `POST /api/wealth/snapshot` aggregates all balances and holdings into a 24-column `net_worth_snapshots` row
- **AI explanation** — `GET /api/wealth/explanation` returns a natural-language month-over-month summary; follow-up Q&A via `POST /api/wealth/explanation/query`

---

## Backup strategy

| Tier | Frequency | Retention |
|---|---|---|
| Hourly | Automatic | 24 copies |
| Daily | Automatic | 31 copies |
| Weekly | Automatic | 5 copies |
| Monthly | Automatic | 12 copies |
| Manual | On-demand | 10 copies |

After each successful import the pipeline creates a new backup and syncs it to the NAS via SSH. The NAS database is the read-only replica served to mobile users via `ro.codingholic.fun`.

---

## Security

- **Network boundary** — private tools are Tailscale-only. No public port forwarding.
- **Authentication** — bridge bearer token: constant-time `hmac.compare_digest` with length equality pre-check. Finance API `X-Api-Key`: constant-time compare resolved at startup.
- **Injection defenses** — NAS SSH remote path uses `shlex.quote()`; mdfind predicate validates RFC 2822 message-ID format; PDF password tempfiles use `chmod 0o600` + zero-wipe before deletion.
- **API hardening** — CORS wildcards forbidden at startup; all `limit=` params server-side capped at 1000; Pydantic request models have `max_length` bounds; `snapshot_date` validated as `YYYY-MM-DD` at the API boundary.
- **Local-first** — financial data never leaves your Mac/NAS. No Google APIs, no external database, no cloud auth.

---

## Requirements

| Requirement | Notes |
|---|---|
| Apple Silicon Mac | 16 GB RAM recommended |
| macOS — Full Disk Access | Required for Mail.app + Messages.app SQLite access |
| Python 3.14 (Homebrew) | `brew install python@3.14` |
| Ollama + `gemma4:e4b` | `ollama pull gemma4:e4b` |
| Docker Desktop | For agent + finance API containers |
| Node.js 20+ | For PWA build |
| Synology NAS *(optional)* | For read-only replica and NAS sync |
| Tailscale *(optional)* | For private remote access from iPhone |

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/g4ndr1k/agentic-ai.git ~/agentic-ai
cd ~/agentic-ai

# 2. Install Python PDF dependencies
brew install python@3.14
/opt/homebrew/bin/pip3 install pikepdf pdfplumber openpyxl

# 3. Pull Ollama model
OLLAMA_HOST=0.0.0.0 ollama serve &
ollama pull gemma4:e4b

# 4. Set up .app bundle for stable Full Disk Access
./scripts/setup-app.sh
# Then: System Settings → Privacy & Security → Full Disk Access → add AgenticAI.app

# 5. Store bridge token in Keychain + export for Docker
python3 -c "import secrets; print(secrets.token_hex(32))" | \
  xargs -I{} security add-generic-password -s agentic-ai-bridge -a bridge_token -w {}
python3 scripts/export-secrets-for-docker.py

# 6. Edit config
cp config/settings.toml config/settings.toml.bak
nano config/settings.toml   # set primary_recipient and authorized_senders

# 7. Build PWA
cd pwa
cp .env.example .env.local   # add VITE_FINANCE_API_KEY=your-key
npm install && npm run build
cd ..

# 8. Start containers
docker compose up --build -d

# 9. Start bridge on host
PYTHONPATH=$(pwd) python3 -m bridge.server
# (Or load the LaunchAgent — see SYSTEM_DESIGN.md §15)
```

---

## Configuration

All config lives in `config/settings.toml`:

```toml
[finance]
sqlite_db  = "data/finance.db"
xlsx_input = "output/xls/ALL_TRANSACTIONS.xlsx"

[fastapi]
host         = "127.0.0.1"
port         = 8090
cors_origins = ["http://localhost:5173"]

[ollama_finance]
model           = "gemma4:e4b"
timeout_seconds = 60

[imessage]
primary_recipient  = "you@icloud.com"
authorized_senders = ["you@icloud.com"]
```

Docker containers override paths via env vars (`FINANCE_SQLITE_DB`, `FINANCE_XLSX_INPUT`, etc.).

---

## Secrets

All secrets are stored in the **macOS Keychain** under service `agentic-ai-bridge`. The `secrets/` directory holds Docker export artifacts (gitignored — regenerate with `python3 scripts/export-secrets-for-docker.py`).

| File | Contents |
|---|---|
| `secrets/bridge.token` | Bearer token for bridge API |
| `secrets/banks.toml` | Bank PDF passwords |
| `secrets/nas_sync_key` | SSH private key for NAS sync |
| `secrets/nas_sync_key.pub` | SSH public key (add to NAS `authorized_keys`) |

---

## Documentation

| File | Purpose |
|---|---|
| [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) | Full architecture, schema, operations, and troubleshooting reference |
| [`CLAUDE.md`](CLAUDE.md) | Working commands and conventions for Claude Code |

---

## Status

- Stage 1 — Mail agent: ✅ complete
- Stage 2 — Finance pipeline + PWA: ✅ complete
- Stage 3 — Wealth management: ✅ complete
- NAS read-only replica: ✅ live
- Security hardening (April 2026): ✅ applied

---

## License

Private / personal project.
