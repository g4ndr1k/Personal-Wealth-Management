# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

---

## Deployment Architecture

```
Internet
  ↓ Cloudflare Tunnel
codingholic.fun  (public · Next.js · port 3003 on NAS)
  ├── /about, /articles — public reading pages
  ├── /future-public-web — planned BaZi public app
  └── Tool cards → private tools (clearly labelled "Requires Tailscale")

Tailscale VPN (private access only)
  ├── mac.codingholic.fun  → Mac finance API + PWA (:8090) — read/write
  └── ro.codingholic.fun   → NAS finance API + PWA (:8090) — read-only demo
```

**Access tiers:**

| Surface | URL | Access | Notes |
|---|---|---|---|
| Public homepage | codingholic.fun | Open internet | Next.js v2 on NAS port 3003 |
| Finance dashboard | mac.codingholic.fun | Tailscale only | Mac-hosted, read/write |
| NAS read-only demo | ro.codingholic.fun | Tailscale only | Synology DS920+, `FINANCE_READ_ONLY=true` |

---

## Common Commands

### Public Homepage v2 (`codingholic-homepage-v2/`)

```bash
cd codingholic-homepage-v2

# Local dev
npm install
npm run dev        # dev at http://localhost:3000

# Production build
npm run build

# Docker deploy (port 3003 staging, then switch reverse proxy when happy)
./deploy.sh        # docker compose up -d --build
```

> The homepage lives in a **separate folder** from this repo (`codingholic-homepage-v2/`).
> Edit tool cards and articles in `app/lib/content.ts`.
> To regenerate from scratch: `bash ~/Downloads/setup_v2.sh`.

### Finance API (Stage 2)

```bash
# Start the API server (reads config/settings.toml)
python3 -m finance.server

# Dev mode with auto-reload
python3 -m finance.server --reload

# Import XLSX → SQLite (dry-run to preview)
python3 -m finance.importer --dry-run
python3 -m finance.importer

# API docs (once server is running)
# http://localhost:8090/docs
```

### PWA Frontend (Stage 2)

```bash
cd pwa
npm install
npm run dev        # dev server at http://localhost:5173
npm run build      # build to pwa/dist/ (required before Docker build)
```

### Docker

```bash
# Always rebuild when Python code changes — restart alone won't pick up changes
docker compose up --build -d

# Logs
docker compose logs -f finance-api
docker compose logs -f mail-agent
```

### Bridge (Stage 1 — runs on host, not in Docker)

```bash
# Bridge exposes the Mac's Messages.app and Mail.app over HTTP at :9100
# Usually managed by launchd; see scripts/start_agent.sh for manual start
PYTHONPATH=$(pwd) python3 -m bridge.server
```

### NAS deployment

```bash
# Deploy read-only replica to Synology DS920+ (see scripts/deploy_nas.sh)
bash scripts/deploy_nas.sh
```

### Household Expense PWA (`household-expense/`)

```bash
cd ~/agentic-ai/household-expense

# Build PWA
cd pwa && npm install && npm run build && cd ..

# Deploy to NAS (build + rsync + Docker restart)
bash deploy_household.sh

# API is at http://192.168.1.44:8088
# Login: kaksum / rumah123
```

---

## Architecture Overview

This project has four layers that run as separate processes:

### Homepage v2 — Public front door (`codingholic-homepage-v2/`)

Next.js 14 + Tailwind + Framer Motion app that acts as the public face of the system.
- Public pages (about, articles, future BaZi app) are reachable by anyone.
- Tool cards for private services are visible but clearly marked "Requires Tailscale".
- Served from Synology NAS via Docker on port 3003, fronted by Cloudflare Tunnel.
- Content lives in `app/lib/content.ts` (`tools[]`, `articles[]`).

### Stage 1 — Mail Agent (`agent/`, `bridge/`)

The **bridge** is a Python HTTP server running on the host Mac. It reads `~/Library/Messages/chat.db` directly via SQLite (read-only), polls Mail.app, and sends iMessages via AppleScript. It exposes an authenticated HTTP API on port 9100.

The **mail-agent** runs in Docker, polls the bridge, classifies emails via Ollama (local only — cloud fallbacks removed), and sends iMessage alerts for financial categories. Config in `config/settings.toml` under `[bridge]`, `[mail]`, `[imessage]`, `[classifier]`, `[agent]`.

Flow: `bridge/messages_source.py` (iMessage read/send) + mail source → `agent/app/orchestrator.py` → `agent/app/classifier.py` → bridge `send_alert`

### Stage 2 — Personal Finance Pipeline (`parsers/`, `exporters/`, `finance/`)

**Data flow:** Bank PDFs → `parsers/router.py` (routes to bank-specific parser) → `parsers/base.py` dataclasses (`Transaction`, `StatementResult`) → `exporters/` (writes `output/xls/ALL_TRANSACTIONS.xlsx`) → `finance/importer.py` (dedup by hash, writes directly to SQLite `data/finance.db`) → `finance/api.py` (FastAPI on :8090) → PWA

**Key design invariant:** `output/xls/ALL_TRANSACTIONS.xlsx` is the immutable parser output. SQLite (`data/finance.db`) is the authoritative edited store — delete it and re-run `python3 -m finance.importer` to rebuild from the XLSX.

**Transaction dedup** is by a 32-char SHA-256 fingerprint of `date|amount|raw_description|institution|owner|account` (see `finance/models.py:make_hash`).

**Categorization** uses a 4-layer engine in `finance/categorizer.py`:
1. Exact alias match (from `merchant_aliases` SQLite table)
2. Contains/fuzzy alias match
3. Regex pattern match
4. Ollama AI suggestion → review queue fallback

**Parsers** (`parsers/`): one file per bank/statement type. All use `parsers/base.py` dataclasses. Indonesian number format (`1.234.567,89`) is parsed by `parse_idr_amount()`. `parsers/owner.py` maps account numbers to owner names ("Gandrik" or "Helen").

### Stage 3 — Wealth Management (in `finance/api.py`)

Extends Stage 2 with `/api/wealth/` endpoints for net worth snapshots, account balances, investment holdings, and liabilities. Data stored in `account_balances`, `holdings`, `liabilities`, and `net_worth_snapshots` tables in the same SQLite DB.

### PWA (`pwa/`)

Vue 3 + Vite + Pinia + Vue Router. Bundled as static files and served by FastAPI at `http://localhost:8090`. API calls go to `/api/*` on the same origin. API key is set via `VITE_FINANCE_API_KEY` env var at build time (see `pwa/.env.example`). The `pwa/dist/` directory must be built before the Docker image is assembled.

### NAS Read-Only Replica

A second Docker container runs on the Synology DS920+ with `FINANCE_READ_ONLY=true`. All write endpoints return 403. The database is synced from the Mac via SSH after each import. Mobile users on Tailscale bookmark the NAS URL for faster read access.

---

## Configuration

All config lives in `config/settings.toml`. Key sections:
- `[finance]` — `sqlite_db` path, `xlsx_input` path
- `[fastapi]` — host (default `127.0.0.1`), port (default 8090), CORS origins
- `[ollama_finance]` — model for transaction categorization AI
- `[bridge]`, `[mail]`, `[imessage]`, `[classifier]`, `[agent]` — Stage 1 settings

Docker containers override host-absolute paths from `settings.toml` via environment variables (`FINANCE_SQLITE_DB`, etc.).

---

## Secrets

`secrets/` directory (not committed):
- `bridge.token` — shared secret for bridge HTTP API auth
- `banks.toml` — bank PDF passwords (exported from Keychain)
- `nas_sync_key` / `nas_sync_key.pub` — SSH key pair for NAS sync

All secrets are stored in the **macOS Keychain** under service `agentic-ai-bridge` as the primary source. The `secrets/` files are Docker export artifacts — regenerate with `python3 scripts/export-secrets-for-docker.py`.

The `FINANCE_API_KEY` env var is required to authenticate requests to the FastAPI backend (header: `X-Api-Key`).

For the PWA, create `pwa/.env.local` based on `pwa/.env.example`:
```bash
VITE_FINANCE_API_KEY=your-api-key-here
```

---

## Security Notes

- **Network boundary:** private tools are only accessible via Tailscale. No public port forwarding.
- **API key in bundle:** `VITE_FINANCE_API_KEY` is embedded in the built PWA JS — visible in DevTools. This is intentional; Tailscale ACLs are the real auth boundary. Do not reuse this key elsewhere.
- **CORS:** `cors_origins` must not contain `"*"` — the API asserts this at startup.
- **Rate limiting:** bridge `/alerts/send` is capped by `max_alerts_per_hour`; all `/api/*` endpoints are rate-limited at 60 req/min per path; all `limit=` params are server-side capped at 1000.
- **Injection defenses:** NAS SSH remote path uses `shlex.quote()`; mdfind predicate validates RFC 2822 message-ID; PDF password tempfiles use `chmod 0o600` + zero-wipe before deletion.
- **Auth:** bridge bearer token uses `hmac.compare_digest` with a length equality pre-check; finance API key is constant-time compared at startup.
- **Default bind:** FastAPI defaults to `127.0.0.1` (not `0.0.0.0`). Set explicitly in `settings.toml [fastapi] host` if the Docker container needs a different value.

See `SYSTEM_DESIGN.md §20` for the full security posture reference.

---

## Backup Strategy

After each successful import, the pipeline automatically:
1. Creates a tiered SQLite backup in `~/agentic-ai/data/backups/` (hourly/daily/weekly/monthly/manual).
2. Syncs the latest backup to the NAS via SSH (`NAS_SYNC_TARGET` env var).

Manual backup:
```bash
python3 -m finance.backup --kind manual
```

---

## Key Invariants

| Invariant | Detail |
|---|---|
| SQLite is authoritative | Delete `data/finance.db` and re-run `python3 -m finance.importer` to rebuild from XLSX |
| Rebuild Docker after Python changes | `docker compose restart` does not pick up code changes — always use `up --build -d` |
| PWA must be built before Docker | Run `cd pwa && npm run build` first; `pwa/dist/` is copied into the Docker image |
| NAS gets read-only replica | iPhone hits NAS (ro.codingholic.fun), not the Mac; use `scripts/deploy_nas.sh` to update |
| Stop hook auto-deploys PWA | After edits to `pwa/`, the stop hook runs `npm run build` + `docker compose up --build -d` automatically |
