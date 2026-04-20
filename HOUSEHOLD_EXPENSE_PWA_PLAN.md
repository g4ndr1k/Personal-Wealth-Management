# Household Expense Capture PWA — Implementation Plan

**Version:** 1.0.0 · Draft
**Author:** Gandrik (refined from initial GPT proposal against SYSTEM_DESIGN.md v3.17.0)
**Platform:** Synology DS920+ (Docker, AMD64) + Android PWA + Mac Mini (agentic-ai reconciliation)
**Last updated:** 2026-04-20

---

## Table of Contents

1. [Goal & Constraints](#1-goal--constraints)
2. [Architecture Overview](#2-architecture-overview)
3. [Network & Access](#3-network--access)
4. [Scope — v1](#4-scope--v1)
5. [User Workflow](#5-user-workflow)
6. [Database Design](#6-database-design)
7. [API Design](#7-api-design)
8. [PWA Screens & UI Language](#8-pwa-screens--ui-language)
9. [Category Taxonomy](#9-category-taxonomy)
10. [Tech Stack](#10-tech-stack)
11. [Docker Deployment](#11-docker-deployment)
12. [Security](#12-security)
13. [PWM / agentic-ai Integration](#13-pwm--agentic-ai-integration)
14. [Build Phases](#14-build-phases)
15. [Acceptance Criteria](#15-acceptance-criteria)
16. [Open Questions & Decisions Required](#16-open-questions--decisions-required)

---

## 1. Goal & Constraints

Build a simple, mobile-friendly PWA hosted on the Synology DS920+ NAS that allows a **personal assistant (household staff)** to record daily household cash expenses in Bahasa Indonesia. The Mac Mini's agentic-ai finance system imports and reconciles these entries later against Helen's BCA ATM cash withdrawals.

### Hard constraints

| Constraint | Detail |
|---|---|
| Device | Android phone, stays inside the house (LAN-only Wi-Fi) |
| No receipt photos | v1 does not capture images |
| Separate database | Dedicated `household.db` on the NAS — **not** inside `finance.db` or any agentic-ai database |
| Reconciliation target | Helen's BCA savings account `5500346622` ATM withdrawals (`TARIKAN ATM` / `TARIKAN TUNAI`), already categorised as `Household` by the Stage 2 categorizer |
| UI language | Bahasa Indonesia (all user-facing labels) |
| Code & data language | English (all source code, DB columns, API fields, logs, comments) |
| Numeric formatting | US/UK notation: `1,234.56` — not Indonesian `1.234,56` |
| NAS host | Synology DS920+ at `192.168.1.44` (AMD64 Docker, DSM 7, SSH port 68) |
| Port | `8088` — distinct from finance-api-nas (8090), homepage-prod (3002), homepage-staging (3003) |

### Relationship to agentic-ai

This system is a **satellite data source**, not a new Stage inside agentic-ai. It produces raw household expense records that the Mac Mini imports, matches against ATM withdrawals, and marks as reconciled. The household PWA never writes to `finance.db`, Google Sheets, or any agentic-ai database directly.

```
Household PWA (NAS, port 8088)       agentic-ai (Mac Mini)
──────────────────────────────       ────────────────────────
household.db                         finance.db
  └─ household_transactions            └─ transactions (Stage 2)
       │                                      │
       │  GET /api/household/export           │
       │  ────────────────────────►           │
       │                              import + match against
       │                              Helen BCA ATM withdrawals
       │  POST /api/household/reconcile       │
       │  ◄────────────────────────           │
       │  (mark matched rows)                 │
```

### NAS port inventory (avoid conflicts)

| Port | Service | Owner |
|---|---|---|
| 8090 | finance-api-nas (read-only PWM replica) | agentic-ai |
| 3002 | codingholic-homepage (prod) | homepage |
| 3003 | codingholic-homepage (staging) | homepage |
| 5000 | DSM HTTP | Synology |
| 5001 | DSM HTTPS | Synology |
| **8088** | **household-api (this project)** | **new** |

---

## 2. Architecture Overview

### Components

| Component | Host | Role |
|---|---|---|
| `household-api` | Synology DS920+ (Docker, port 8088) | FastAPI backend serving API + static PWA |
| `household.db` | Synology DS920+ (Docker volume at `/volume1/docker/household-expense/data/`) | SQLite database for household expenses |
| Household PWA | Android phone (Chrome, installed to home screen) | Data entry UI in Bahasa Indonesia |
| agentic-ai `finance-api` | Mac Mini (Docker, port 8090) | Imports unreconciled rows, performs matching, posts reconciliation |

### Data flow

```
┌─────────────────────────────────────────────────┐
│ Android Phone (LAN Wi-Fi)                        │
│  Chrome PWA → http://192.168.1.44:8088          │
│  UI: Bahasa Indonesia                            │
└──────────────────────┬──────────────────────────┘
                       │ HTTP (LAN only, NOT through
                       │ Cloudflare Tunnel or Tailscale)
┌──────────────────────┴──────────────────────────┐
│ Synology DS920+ (192.168.1.44)                   │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │ household-api (FastAPI, port 8088)         │  │
│  │  · CRUD /api/household/transactions        │  │
│  │  · GET  /api/household/export/unreconciled │  │
│  │  · POST /api/household/reconcile           │  │
│  │  · Serves PWA static files at /            │  │
│  │  · Auth: simple username/password login     │  │
│  └─────────────────────┬──────────────────────┘  │
│                        │                          │
│  ┌─────────────────────┴──────────────────────┐  │
│  │ household.db (SQLite, WAL mode)            │  │
│  │  · household_transactions                  │  │
│  │  · household_categories                    │  │
│  │  · cash_pools                              │  │
│  │  · app_users                               │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ── Also running on this NAS ──                  │
│  finance-api-nas  :8090 (read-only PWM replica)  │
│  homepage-prod    :3002 (public, Cloudflare)      │
│  homepage-staging :3003 (public, Cloudflare)      │
└──────────────────────────────────────────────────┘
                       │
         Periodic import (Mac Mini pulls via LAN)
                       │
┌──────────────────────┴──────────────────────────┐
│ Mac Mini — agentic-ai (127.0.0.1:8090)           │
│  · GET  http://192.168.1.44:8088/api/household/  │
│    export/unreconciled                            │
│  · Match against Helen BCA ATM withdrawals       │
│  · POST http://192.168.1.44:8088/api/household/  │
│    reconcile                                      │
│  · Update finance.db categorisation if needed    │
└──────────────────────────────────────────────────┘
```

---

## 3. Network & Access

### LAN-only (v1)

The household PWA and API are accessible **only** on the local home network. This service is deliberately excluded from Tailscale, Cloudflare Tunnel, and the Synology reverse proxy. No public DNS entry, no SSL certificate, no port forwarding.

| Access method | URL |
|---|---|
| IP-based (primary) | `http://192.168.1.44:8088` |
| Hostname-based | `http://ds920plus:8088` (if mDNS resolves on the Android device) |

### Why no HTTPS

The existing NAS HTTPS setup (§42 in SYSTEM_DESIGN.md) uses Tailscale IP + Cloudflare DNS + acme.sh certificates for `codingholic.fun`. The household app is deliberately **outside** this stack because:

- The Android device does not use Tailscale
- The assistant should not need VPN configuration
- LAN-only access is sufficient for an in-house device
- Plain HTTP over LAN is acceptable for this threat model (no sensitive PII beyond expense amounts)

### PWA installation on Android

1. Open Chrome on the Android phone
2. Navigate to `http://192.168.1.44:8088`
3. Log in
4. Tap Chrome menu → "Add to Home screen"
5. The app icon appears on the home screen and launches in standalone mode

> **Note:** PWA install prompts require HTTPS or localhost. Over plain HTTP on LAN, the automatic install prompt will not appear, but the manual "Add to Home screen" option in the Chrome menu always works. The app will still launch in standalone mode with the correct `display: standalone` manifest.

---

## 4. Scope — v1

### Included

| Feature | Detail |
|---|---|
| Add transaction | Amount, category, merchant/description, payment method, date/time, note |
| View recent transactions | Scrollable list, most recent first |
| Edit transaction | Tap to open, modify any field, save |
| Delete transaction | Soft-delete (`is_deleted = 1`); row preserved for audit |
| Category selection | Dropdown populated from `household_categories` table |
| Cash pool tracking | Track which cash disbursement pool an expense draws from |
| Simple login | Username + password; single session; no role-based access |
| Export for agentic-ai | `GET /api/household/export/unreconciled` returns JSON |
| Reconciliation endpoint | `POST /api/household/reconcile` marks rows matched by Mac Mini |

### Excluded from v1

| Feature | Reason |
|---|---|
| Receipt photo capture | Not needed — assistant records by memory |
| Offline-first / sync queue | LAN is always available inside the house |
| Push notifications | No notification use case identified |
| Public internet access | Security constraint — LAN only |
| Multi-language toggle | UI is Bahasa Indonesia only; no switcher needed |
| Budget tracking | Deferred — agentic-ai Stage 2 handles budgets |

---

## 5. User Workflow

### Daily use (personal assistant)

```
1. Open the app from Android home screen
2. Tap "Tambah Pengeluaran" (Add Expense)
3. Enter:
   - Jumlah (Amount) — numeric keypad, IDR whole numbers
   - Kategori (Category) — dropdown
   - Deskripsi (Description) — free text, e.g. "Ayam 2 kg Pasar Bintaro"
   - Metode Pembayaran (Payment Method) — Cash / Transfer / E-wallet
   - Tanggal & Jam (Date & Time) — defaults to now, adjustable
   - Catatan (Notes) — optional
4. Tap "Simpan" (Save)
5. Toast confirmation: "Tersimpan ✓"
6. Return to recent transactions list
```

### Reconciliation (Gandrik, on Mac Mini)

```
1. agentic-ai scheduled job or manual trigger:
   GET http://192.168.1.44:8088/api/household/export/unreconciled

2. Import into finance pipeline:
   - Match each household expense against Helen BCA ATM withdrawals
   - Group by cash pool if applicable
   - Sum household expenses between two ATM withdrawal dates
   - Compare total against withdrawal amount

3. Mark matched rows:
   POST http://192.168.1.44:8088/api/household/reconcile
   Body: [
     { "client_txn_id": "...", "matched_pwm_txn_id": "hash_of_atm_withdrawal" },
     ...
   ]

4. Household DB updates reconcile_status → "reconciled"
```

---

## 6. Database Design

### File location

```
/volume1/docker/household-expense/data/household.db
```

SQLite, WAL mode, `busy_timeout=5000`, `foreign_keys=ON`, `secure_delete=ON` — matching the `finance.db` PRAGMA pattern from agentic-ai (SYSTEM_DESIGN.md §29).

### Table: `household_transactions`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Internal row ID |
| `client_txn_id` | TEXT | UNIQUE NOT NULL | Client-generated UUID (prevents duplicate submissions) |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC — when the row was inserted |
| `updated_at` | TEXT | NOT NULL | ISO 8601 UTC — last modification |
| `txn_datetime` | TEXT | NOT NULL | ISO 8601 local time (WIB) — when the expense occurred |
| `amount` | INTEGER | NOT NULL CHECK(amount > 0) | IDR whole number, always positive (expenses are always outflows) |
| `currency` | TEXT | NOT NULL DEFAULT 'IDR' | Always IDR for v1 |
| `category_code` | TEXT | NOT NULL | FK to `household_categories.code` |
| `merchant` | TEXT | | Vendor / store name (free text) |
| `description` | TEXT | | What was purchased (free text) |
| `payment_method` | TEXT | NOT NULL DEFAULT 'cash' | `cash`, `transfer`, `ewallet` |
| `cash_pool_id` | TEXT | | FK to `cash_pools.id`; nullable if not tracking pools |
| `recorded_by` | TEXT | NOT NULL | Username of the person who entered the record |
| `note` | TEXT | | Optional free-text note |
| `reconcile_status` | TEXT | NOT NULL DEFAULT 'pending' | `pending`, `reconciled`, `unmatched` |
| `matched_pwm_txn_id` | TEXT | | SHA-256 hash of the matched agentic-ai transaction (set during reconciliation) |
| `reconciled_at` | TEXT | | ISO 8601 UTC — when reconciliation occurred |
| `is_deleted` | INTEGER | NOT NULL DEFAULT 0 | Soft-delete flag (0 = active, 1 = deleted) |

**Indexes:**

```sql
CREATE INDEX idx_htx_txn_datetime ON household_transactions(txn_datetime);
CREATE INDEX idx_htx_reconcile    ON household_transactions(reconcile_status);
CREATE INDEX idx_htx_category     ON household_transactions(category_code);
CREATE INDEX idx_htx_deleted      ON household_transactions(is_deleted);
CREATE INDEX idx_htx_pool         ON household_transactions(cash_pool_id);
```

### Table: `household_categories`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `code` | TEXT | PRIMARY KEY | English slug, e.g. `groceries`, `gas_lpg` |
| `label_id` | TEXT | NOT NULL | Bahasa Indonesia display label, e.g. `Belanja Harian` |
| `sort_order` | INTEGER | NOT NULL DEFAULT 99 | Display order in the dropdown |
| `is_active` | INTEGER | NOT NULL DEFAULT 1 | 0 = hidden from dropdown |

### Table: `cash_pools`

Tracks cash disbursements from Helen's BCA ATM withdrawals. Each withdrawal creates a pool; household expenses draw down from that pool.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | TEXT | PRIMARY KEY | UUID |
| `name` | TEXT | NOT NULL | Descriptive label, e.g. "Tarik ATM 15 April 2026" |
| `funded_amount` | INTEGER | NOT NULL | IDR amount of the ATM withdrawal |
| `funded_at` | TEXT | NOT NULL | ISO 8601 — date of the ATM withdrawal |
| `remaining_amount` | INTEGER | NOT NULL | Server-computed: `funded_amount - SUM(linked active expenses)` |
| `status` | TEXT | NOT NULL DEFAULT 'active' | `active`, `depleted`, `closed` |
| `notes` | TEXT | | Optional |

### Table: `app_users`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | TEXT | PRIMARY KEY | UUID |
| `username` | TEXT | UNIQUE NOT NULL | Login identifier |
| `display_name` | TEXT | NOT NULL | Shown in UI header |
| `password_hash` | TEXT | NOT NULL | bcrypt hash |
| `is_active` | INTEGER | NOT NULL DEFAULT 1 | 0 = disabled |
| `created_at` | TEXT | NOT NULL | ISO 8601 UTC |

---

## 7. API Design

### Base path

```
/api/household
```

### Authentication

Two auth mechanisms coexist:

| Mechanism | Used by | Endpoints |
|---|---|---|
| Session cookie (browser) | Android PWA (assistant) | All CRUD endpoints |
| `X-Api-Key` header | Mac Mini (agentic-ai) | Export + reconcile endpoints only |

Login returns a session cookie; all CRUD endpoints require a valid session. The export and reconcile endpoints accept either a session or an API key, since they are called programmatically by the Mac Mini.

### Auth endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/household/auth/login` | None | Body: `{ username, password }` → set session cookie |
| `POST` | `/api/household/auth/logout` | Session | Clear session |

### Transaction endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/household/transactions` | Session | List transactions. Params: `limit`, `offset`, `from_date`, `to_date`, `category`, `reconcile_status`. Excludes soft-deleted by default. |
| `POST` | `/api/household/transactions` | Session | Create transaction. Body: full transaction object. Server generates `id`, `created_at`, `updated_at`. Client must provide `client_txn_id` (UUID). |
| `PUT` | `/api/household/transactions/{id}` | Session | Update transaction. Body: partial or full object. Server updates `updated_at`. |
| `DELETE` | `/api/household/transactions/{id}` | Session | Soft-delete: sets `is_deleted = 1`, updates `updated_at`. |

### Category endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/household/categories` | Session | List active categories, sorted by `sort_order` |

### Cash pool endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/household/cash-pools` | Session | List cash pools with remaining amounts |
| `POST` | `/api/household/cash-pools` | Session | Create a new cash pool (new ATM withdrawal) |

### Export & reconciliation endpoints (Mac Mini integration)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/household/health` | None | Unauthenticated liveness probe |
| `GET` | `/api/household/export/unreconciled` | API key | Returns all active rows with `reconcile_status = 'pending'` as JSON |
| `POST` | `/api/household/reconcile` | API key | Body: `[{ client_txn_id, matched_pwm_txn_id }]` → sets status, timestamp, match ID |

### Request validation

All Pydantic request models use `Field(max_length=...)` on string fields, matching the agentic-ai API hardening pattern (SYSTEM_DESIGN.md §20). Specifically:

- `merchant`: max 200 chars
- `description`: max 500 chars
- `note`: max 1000 chars
- `category_code`: max 50 chars
- `amount`: `gt=0`, `le=999_999_999`

---

## 8. PWA Screens & UI Language

All user-facing text is in Bahasa Indonesia. All API field names, DB columns, and source code are in English.

### Screen 1 — Tambah Pengeluaran (Add Expense)

| UI Element | Label (ID) | Component |
|---|---|---|
| Amount | Jumlah (Rp) | Numeric input, whole numbers only |
| Category | Kategori | Dropdown from `household_categories` |
| Merchant | Toko / Penjual | Free text input |
| Description | Deskripsi | Free text input |
| Payment method | Metode Pembayaran | Radio: Tunai / Transfer / E-wallet |
| Date | Tanggal | Date picker, defaults to today |
| Time | Jam | Time picker, defaults to now |
| Cash pool | Sumber Dana | Dropdown of active cash pools (optional) |
| Note | Catatan | Optional text area |
| Save button | Simpan | Primary action |
| Cancel button | Batal | Returns to list |

**Validation rules:**

- `amount` is required, must be > 0
- `category` is required
- `merchant` or `description` — at least one must be non-empty
- `txn_datetime` defaults to current local time (WIB), must not be in the future

### Screen 2 — Riwayat (History / Recent Transactions)

- Default view: last 30 days, most recent first
- Each row shows: date, amount (formatted as `Rp 125.000`), category icon + label, merchant/description
- Tap a row to open edit mode (same form as Screen 1, pre-filled)
- Swipe-left or tap delete icon for soft-delete with confirmation dialog: "Hapus transaksi ini?"
- Pull-to-refresh
- Infinite scroll or "Muat lebih banyak" button

### Screen 3 — Login

- Username + password fields
- "Masuk" (Login) button
- Error: "Username atau password salah"
- On success: redirect to Tambah screen

### Navigation bar (bottom, fixed)

| Tab | Label (ID) | Icon |
|---|---|---|
| Add | Tambah | ➕ |
| History | Riwayat | 📋 |
| Logout | Keluar | 🚪 |

### Toast / status messages

| Event | Message (ID) |
|---|---|
| Save success | Tersimpan ✓ |
| Save failure | Gagal menyimpan |
| Delete success | Dihapus ✓ |
| No transactions | Belum ada transaksi |
| Login success | Selamat datang, {display_name} |
| Login failure | Username atau password salah |
| Session expired | Sesi habis, silakan masuk kembali |

---

## 9. Category Taxonomy

Categories are seeded once during setup and stored in `household_categories`. The `code` (English) is used in the DB and API; the `label_id` (Indonesian) is shown in the UI.

| `code` | `label_id` | `sort_order` | Maps to agentic-ai Stage 2 category |
|---|---|---|---|
| `groceries` | Belanja Harian | 1 | Groceries |
| `meals` | Makanan & Minuman | 2 | Dining Out |
| `snacks` | Jajan / Camilan | 3 | Dining Out |
| `gas_lpg` | Gas LPG | 4 | Utilities |
| `electricity_token` | Token Listrik | 5 | Utilities |
| `water` | Air (Galon / PDAM) | 6 | Utilities |
| `transport` | Transportasi | 7 | Auto |
| `household_supplies` | Peralatan Rumah Tangga | 8 | Household |
| `laundry` | Laundry | 9 | Household |
| `cleaning` | Kebersihan | 10 | Household |
| `medical` | Kesehatan / Obat | 11 | Healthcare |
| `children` | Anak-anak | 12 | Family |
| `donation` | Sedekah / Donasi | 13 | Gifts & Donations |
| `staff_salary` | Gaji ART / Driver | 14 | Household |
| `other` | Lainnya | 99 | Other |

> **Category mapping at import time:** When the Mac Mini imports household transactions, it maps `category_code` to the corresponding Stage 2 category using this table. The mapping lives in `finance/household_import.py` on the Mac Mini, not in the household DB.

---

## 10. Tech Stack

### Backend

| Component | Technology | Notes |
|---|---|---|
| Framework | FastAPI (Python 3.12) | Same base as agentic-ai `finance-api` (SYSTEM_DESIGN.md §30) |
| Database | SQLite 3 (WAL mode) | Single-file DB on NAS Docker volume |
| Auth | bcrypt + session cookie | `passlib[bcrypt]` for hashing |
| Validation | Pydantic v2 | Request/response models with `max_length` bounds |
| Server | uvicorn | Standard ASGI server |
| Base image | `python:3.12-slim` | Same as `finance/Dockerfile` |

### Frontend

| Component | Technology | Notes |
|---|---|---|
| Framework | Vue 3 + Composition API | Same as agentic-ai PWA |
| Build tool | Vite + vite-plugin-pwa | PWA manifest + minimal service worker |
| Styling | Tailwind CSS (utility classes) | Mobile-first, lightweight |
| Language | Indonesian UI labels, English source | Labels stored in a constants file |

### Deployment

| Component | Technology | Notes |
|---|---|---|
| Container | Docker (single container, AMD64) | FastAPI serves both API and static PWA |
| Host | Synology DS920+ (DSM 7, Container Manager + Docker Compose CLI) | Always-on, low power |
| Port | 8088 | No conflict with 8090, 3002, 3003 |

---

## 11. Docker Deployment

### Container design

Single container running FastAPI + uvicorn. The pre-built Vue PWA (`dist/`) is copied into the image at build time and served as static files at `/`. This matches the agentic-ai `finance-api` deployment pattern.

### Directory structure on NAS

```
/volume1/docker/household-expense/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── api/                    # FastAPI backend source
│   ├── __init__.py
│   ├── main.py             # FastAPI app, CORS, static mount, startup
│   ├── auth.py             # Login, session middleware, API key check
│   ├── models.py           # Pydantic request/response models
│   ├── db.py               # SQLite schema, open_db(), WAL mode
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── transactions.py
│   │   ├── categories.py
│   │   ├── cash_pools.py
│   │   └── export.py       # Export + reconcile endpoints
│   └── seed.py             # Category + default user seeder
├── pwa/                    # Vue 3 PWA source
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.vue
│       ├── main.js
│       ├── views/
│       ├── components/
│       └── api/
├── dist/                   # Built PWA output (gitignored, built before Docker build)
├── data/                   # Docker volume mount point
│   └── household.db
└── secrets/
    └── household_api.key   # API key for Mac Mini access
```

### `docker-compose.yml`

```yaml
version: "3.8"

services:
  household-api:
    build:
      context: .
      dockerfile: Dockerfile
      platform: linux/amd64
    container_name: household-api
    restart: unless-stopped
    ports:
      - "8088:8088"
    volumes:
      - ./data:/app/data
      - ./secrets:/app/secrets:ro
    environment:
      - HOUSEHOLD_DB_PATH=/app/data/household.db
      - API_KEY_FILE=/app/secrets/household_api.key
    mem_limit: 256m
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: >
        python -c "import urllib.request;
        urllib.request.urlopen('http://127.0.0.1:8088/api/household/health', timeout=5).read()"
      interval: 30s
      timeout: 10s
      retries: 3
```

### `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ ./api/
COPY dist/ ./dist/

EXPOSE 8088
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8088"]
```

### Build and deploy workflow

Since Node.js may not be available on the NAS, the recommended workflow mirrors the `deploy_nas.sh` pattern from agentic-ai (SYSTEM_DESIGN.md §41):

```bash
# On Mac Mini — build PWA and transfer to NAS
cd /path/to/household-expense

npm run build --prefix pwa        # outputs to dist/

# Transfer project to NAS (SSH port 22)
rsync -avz -e "ssh -p 22" \
  --exclude node_modules --exclude .git --exclude pwa/node_modules \
  . g4ndr1k@192.168.1.44:/volume1/docker/household-expense/

# On NAS (SSH port 22)
ssh -p 22 g4ndr1k@192.168.1.44
cd /volume1/docker/household-expense
docker compose build --no-cache
docker compose up -d

# Verify
curl -s http://localhost:8088/api/household/health
```

---

## 12. Security

### LAN-only — no public exposure

- Port 8088 is **not** forwarded on the router
- **Not** routed through Cloudflare Tunnel (which handles `codingholic.fun` → ports 3002/3003)
- **Not** proxied through the Synology Reverse Proxy (which handles HTTPS for Tailscale)
- **Not** accessible via Tailscale
- Reachable only from devices on the home Wi-Fi network

### Authentication

- Login with username + password (bcrypt-hashed in `app_users`)
- Session cookie with `httpOnly` flag and configurable expiry (default: 7 days)
- All `/api/household/*` endpoints (except `/health` and `/auth/login`) require a valid session or API key

### API key for Mac Mini

- Export and reconcile endpoints accept `X-Api-Key` header instead of session cookie
- API key is a 64-character hex token stored in `/volume1/docker/household-expense/secrets/household_api.key` on the NAS
- The Mac Mini stores a copy at `~/agentic-ai/secrets/household_api.key`
- Constant-time comparison using `hmac.compare_digest` (matching the agentic-ai auth pattern from SYSTEM_DESIGN.md §20)

### Data protection

- `household.db` lives on a Docker volume — persists across container restarts and NAS reboots
- Soft-delete preserves all records for audit trail
- No PII beyond staff display names (no addresses, no government IDs, no bank account numbers)

---

## 13. PWM / agentic-ai Integration

### Import flow

The Mac Mini periodically pulls unreconciled household expenses and matches them against Helen's BCA ATM withdrawals.

```
agentic-ai (Mac Mini)
│
├── New module: finance/household_import.py
│   │
│   ├── Step 1: GET http://192.168.1.44:8088/api/household/export/unreconciled
│   │           Headers: X-Api-Key: <contents of secrets/household_api.key>
│   │           Returns: JSON array of household transactions
│   │
│   ├── Step 2: Query finance.db for Helen BCA ATM withdrawals
│   │           SELECT * FROM transactions_resolved
│   │           WHERE owner = 'Helen'
│   │             AND institution = 'BCA'
│   │             AND (raw_description LIKE '%TARIKAN ATM%'
│   │                  OR raw_description LIKE '%TARIKAN TUNAI%')
│   │             AND category = 'Household'
│   │
│   ├── Step 3: Match by date range and amount
│   │           Sum household expenses between withdrawal[N] and withdrawal[N+1]
│   │           Compare sum against withdrawal amount
│   │           Tolerance: configurable (default ±5%)
│   │
│   ├── Step 4: POST http://192.168.1.44:8088/api/household/reconcile
│   │           Body: [{ client_txn_id, matched_pwm_txn_id }]
│   │
│   └── Step 5: Log results + optional iMessage notification via bridge
│
└── Config: settings.toml [household] section
```

### Category mapping (at import time)

When household transactions appear in agentic-ai summary views or reports, their `category_code` is mapped to Stage 2 categories:

```python
HOUSEHOLD_TO_FINANCE_CATEGORY = {
    "groceries":          "Groceries",
    "meals":              "Dining Out",
    "snacks":             "Dining Out",
    "gas_lpg":            "Utilities",
    "electricity_token":  "Utilities",
    "water":              "Utilities",
    "transport":          "Auto",
    "household_supplies": "Household",
    "laundry":            "Household",
    "cleaning":           "Household",
    "medical":            "Healthcare",
    "children":           "Family",
    "donation":           "Gifts & Donations",
    "staff_salary":       "Household",
    "other":              "Other",
}
```

### Settings addition to `config/settings.toml` on Mac Mini

```toml
[household]
api_url                 = "http://192.168.1.44:8088"
api_key_file            = "secrets/household_api.key"
reconcile_tolerance_pct = 5
import_enabled          = false   # Feature flag — enable when integration is tested
```

### Secret storage on Mac Mini

Following the Keychain-first pattern (SYSTEM_DESIGN.md §21):

```bash
# Store the household API key in macOS Keychain
security add-generic-password -s agentic-ai-bridge -a HOUSEHOLD_API_KEY -w "<key>"

# Export for Docker (if needed)
python3 scripts/export-secrets-for-docker.py
```

The `household_api.key` file at `~/agentic-ai/secrets/` is a Docker export artifact. The Keychain entry is the source of truth on the Mac side.

---

## 14. Build Phases

### Phase 1 — Foundation (DB + API skeleton)

- SQLite schema creation (`db.py` with WAL, foreign keys, secure_delete)
- Category seed data + default user seeder (`seed.py`)
- Health endpoint (`GET /api/household/health`)
- Auth endpoints (login, logout, session middleware)
- API key middleware for export/reconcile endpoints
- Docker setup (Dockerfile, docker-compose.yml)
- Verify container runs on NAS at `http://192.168.1.44:8088`

**Deliverable:** Container starts, health endpoint responds, login works.

### Phase 2 — Transaction CRUD API

- Transaction create/read/update/soft-delete endpoints
- Pydantic request models with validation bounds
- Category list endpoint
- Cash pool create/list endpoints
- Date filtering, pagination (`limit`/`offset`)
- `client_txn_id` deduplication (reject duplicate UUIDs)

**Deliverable:** All CRUD operations work via curl.

### Phase 3 — PWA Frontend

- Vue 3 project scaffold with Vite + vite-plugin-pwa
- "Tambah Pengeluaran" form (Screen 1)
- "Riwayat" transaction list (Screen 2)
- Login screen
- Bottom navigation bar (Tambah / Riwayat / Keluar)
- Toast notifications (Tersimpan ✓, Gagal menyimpan, etc.)
- All labels in Bahasa Indonesia
- PWA manifest (icon, theme color, `display: standalone`)

**Deliverable:** Add and view expenses on Android Chrome.

### Phase 4 — NAS Deployment & Polish

- Production Docker build (multi-stage optional: Node → Python runtime)
- PWA served as static files by FastAPI `StaticFiles` mount
- Test on actual Android device over home Wi-Fi
- Performance tuning (target: < 10 seconds from app open to "Tersimpan ✓")
- Edit/delete from history list
- Category icon display in list rows
- Android home screen installation verification

**Deliverable:** Full end-to-end flow on Android device connected to home Wi-Fi.

### Phase 5 — agentic-ai Integration

- Export endpoint (`GET /api/household/export/unreconciled`)
- Reconcile endpoint (`POST /api/household/reconcile`)
- `finance/household_import.py` module on Mac Mini
- ATM withdrawal matching logic against `transactions_resolved`
- `[household]` section in `config/settings.toml`
- `HOUSEHOLD_API_KEY` stored in macOS Keychain
- Test full reconciliation cycle end-to-end
- Optional: iMessage notification after successful reconciliation

**Deliverable:** Mac Mini can import, match, and reconcile household expenses.

---

## 15. Acceptance Criteria

| # | Criterion | Measurable target |
|---|---|---|
| 1 | Add expense speed | < 10 seconds from app open to "Tersimpan ✓" toast |
| 2 | Data persistence | Transactions survive container restart and NAS reboot |
| 3 | Android home screen | PWA installs via "Add to Home screen" and launches in standalone mode |
| 4 | Mac Mini import | `GET /export/unreconciled` returns valid JSON with all pending rows |
| 5 | Reconciliation | `POST /reconcile` correctly marks matched rows; status changes to `reconciled` |
| 6 | UI language | All user-visible text is in Bahasa Indonesia |
| 7 | Code language | All source code, DB columns, API fields, logs, comments are in English |
| 8 | Numeric storage | Amounts stored as whole IDR integers (no decimals) |
| 9 | Numeric display | UI formats amounts as `Rp 125.000` (dot as thousands separator per Indonesian convention in display only) |
| 10 | Soft-delete | Deleted transactions hidden from UI but preserved in DB with `is_deleted = 1` |
| 11 | Auth | Unauthenticated requests to protected endpoints return 401 |
| 12 | Port isolation | Container listens on 8088; no conflict with existing NAS services |
| 13 | LAN isolation | Service is not reachable from Tailscale, Cloudflare Tunnel, or public internet |

---

## 16. Open Questions & Decisions Required

These items need Gandrik's input before implementation begins.

| # | Question | Options | Impact |
|---|---|---|---|
| 1 | **Cash pool tracking scope** — should the assistant select a cash pool for every expense, or is this optional? | (a) Required field — every expense must link to a pool (b) Optional field — pool is a convenience, not enforced (c) Defer entirely to v1.1 | Affects form complexity and reconciliation matching logic |
| 2 | **Number of app users** — will only the personal assistant log expenses, or should Gandrik/Helen also have accounts? | (a) Single user (assistant only) (b) Multiple users (assistant + Gandrik + Helen) | Affects initial user seed and auth complexity |
| 3 | **Reconciliation trigger** — how should the Mac Mini initiate reconciliation? | (a) Manual only — button in PWM Settings or CLI command (b) Scheduled — runs daily/weekly via pipeline (c) On-demand via `agent: reconcile-household` iMessage command | Affects pipeline.py integration and scheduling |
| 4 | **PWA build location** — where should the Vue PWA be built? | (a) On Mac Mini, then rsync `dist/` to NAS before Docker build (b) Multi-stage Dockerfile with Node.js build step (c) Install Node.js on NAS directly | Affects Dockerfile complexity and deploy workflow |
| 5 | **Amount display format** — should the UI show IDR amounts with Indonesian dot-thousands (`Rp 125.000`) or US comma-thousands (`Rp 125,000`)? | (a) Indonesian dot format (natural for the assistant) (b) US comma format (consistent with SYSTEM_DESIGN.md formatting rules) | Recommendation: Indonesian display in the PWA (user-facing), US format in API JSON responses. The SYSTEM_DESIGN.md formatting mandate applies to technical output, not end-user UI for a non-technical operator. |
| 6 | **Deploy script** — should this project get its own `deploy_household.sh` on the Mac, mirroring the `deploy_nas.sh` pattern? | (a) Yes — scripted build + transfer + container restart (b) No — manual SSH + docker compose workflow is fine for now | Affects operational convenience |
| 7 | **Backup strategy** — should `household.db` be included in the agentic-ai backup pipeline? | (a) Yes — add SSH-based backup pull to `finance/backup.py` (b) Separate — NAS-local backup only (c) Defer | Affects data durability |

---

*End of document.*
