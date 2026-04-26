# CoreTax SPT — Persistent Tax-Version Ledger + Reconciliation Workflow

## Context

The current CoreTax flow (`finance/coretax_export.py` + `/api/coretax/generate` + `pwa/src/views/CoreTaxSpt.vue`) is a one-shot generator: take an XLSX template, fill it from PWM wealth tables, write the result. That assumes "tax book == real book", which it isn't. The user maintains **two** sets of figures — the real book (already in PWM) and a **tax-version book** that diverges (acquisition-cost basis, fewer accounts shown, manual prior-year adjustments). Re-running the auto-fill every year wipes those manual decisions.

This plan replaces the one-shot generator with a **persistent tax-version ledger** that:
- Imports the prior-year SPT XLSX as the seed
- Carries forward last year's tax values row-by-row (rule defaults + manual override)
- Reconciles refreshable categories (cash, holdings, liabilities) from the FS report
- Lets the user manually map / adjust the rest, **records those mappings as learned rules**, and applies them in future years
- Exports back into the existing template layout (XLSX) on demand

> **Implementation principle (load-bearing):** Do not silently overwrite user-reviewed tax values. Any value edited manually must become locked by default. Auto-reconcile may suggest a replacement, but it must not apply it unless the row is unlocked or the user explicitly accepts the update. The lock flag is the heart of the whole system.

---

## Locked design decisions (from clarification round + revision feedback)

| Decision | Choice |
|---|---|
| Row schema | **One row per (asset, tax_year)** — `prior_amount_idr` + `current_amount_idr` + `market_value_idr` columns mirror E/F/G in the export. No two-rows-per-asset pivot. |
| Row identity | `stable_key NOT NULL` (PWM-derived for reconcilable rows, `manual:{kode}:{slug}:{acq_year}:{uuid8}` for hand-entered — UUID, **not** row id) → `UNIQUE(tax_year, stable_key)` |
| Carry-forward | **Hybrid**: rule defaults by Kode Harta, user reviews/overrides per row |
| Manual override | Explicit `amount_locked` / `market_value_locked` flags; auto-reconcile skips locked rows |
| Refreshable rows for next year | **Don't pre-materialize zero placeholders.** Next-year row created only when a learned mapping exists, prior row had a stable_key, or PWM still has the source. Otherwise → "unmatched / candidate" list. |
| Prior-year template source | **Upload via PWA** file picker (multipart: file + `target_tax_year` Form field) |
| Mapping persistence | **Global learned mappings**, cross-year (keyed by stable identifiers) |
| Output dir | `data/coretax/output/` (already gitignored under `data/`) |
| Output return value | `{ file_id, download_url, audit_url }` — PWA **never** sees server filesystem paths |
| FS role | Both — feeds Reconcile-from-PWM AND renders as side-panel reference for Review & Manual Mapping |
| New rows | Manual "Add row" + one-click "Create from unmatched PWM row" |
| Legacy code | **Replace** — delete `finance/coretax_export.py`, `/api/coretax/{templates,generate,audit}`, `CoreTaxSpt.vue` |
| Liabilities | Same table, `kind = 'liability'`, sourced from PWM `liabilities` |
| Preview-before-commit | **Staging table** with full raw-Excel-coordinate audit trail |

---

## Data model (SQLite, in `data/finance.db`)

### `coretax_rows` — the authoritative tax-version book (one row per asset per SPT year)

```
id                       INTEGER PRIMARY KEY
tax_year                 INTEGER NOT NULL          -- SPT year being prepared, e.g. 2025
kind                     TEXT    NOT NULL CHECK (kind IN ('asset','liability'))
stable_key               TEXT    NOT NULL          -- always set at creation; see "Stable-key recipes" below
kode_harta               TEXT                      -- '012','034','036','039','042','043','051','061','038',…
asset_type_label         TEXT                      -- 'Tabungan','Saham','Tanah & Bangunan',…
keterangan               TEXT                      -- description (template col C / H content combined)
owner                    TEXT                      -- 'Emanuel G. Adrianto' / 'Dian Pratiwi' / …
institution              TEXT                      -- bank / broker / null
account_number_masked    TEXT                      -- masked acct no for cash; null otherwise
external_ref             TEXT                      -- ISIN / SID / property cert no / etc.
acquisition_year         INTEGER                   -- "Tahun Perolehan"
prior_amount_idr         REAL                      -- export col E (carried from prior year's current_amount)
current_amount_idr       REAL                      -- export col F (this year's tax value)
market_value_idr         REAL                      -- export col G (current fair value)
prior_amount_source      TEXT CHECK (prior_amount_source   IN ('imported','carried_forward','manual','unset'))
current_amount_source    TEXT CHECK (current_amount_source IN ('carried_forward','auto_reconciled','manual','unset'))
market_value_source      TEXT CHECK (market_value_source   IN ('imported','auto_reconciled','manual','unset'))
amount_locked            INTEGER NOT NULL DEFAULT 0
market_value_locked      INTEGER NOT NULL DEFAULT 0
locked_reason            TEXT
last_user_edited_at      TEXT
last_mapping_id          INTEGER                   -- FK to coretax_mappings.id
notes_internal           TEXT                      -- private working notes (not exported)
created_at               TEXT NOT NULL
updated_at               TEXT NOT NULL
UNIQUE(tax_year, stable_key)
```

**Important:** `stable_key` is `NOT NULL`. Every row must receive a stable identity at creation time (SQLite treats NULLs as distinct in unique indexes — leaving it nullable would silently allow duplicates). Generate the key **before** insert; do **not** depend on the SQLite row id.

Stable-key recipes:
- Cash: `pwm:account:{institution_norm}:{account_number_norm}`
- Investments: `pwm:holding:{asset_class}:{institution_norm}:{external_ref or asset_name_norm}:{owner_norm}`
- Liabilities: `pwm:liability:{type_norm}:{name_norm}:{owner_norm}`
- Hard assets / manual / imported-without-PWM-match: `manual:{kode_harta}:{keterangan_slug}:{acquisition_year}:{uuid8}` (8-char random hex from `uuid4().hex[:8]`) — generated at creation time, never re-derived
- Imported-from-prior-year row that already had a `stable_key` in the previous year's ledger: reuse that key

### `coretax_taxpayer` — per-year metadata

```
tax_year                 INTEGER PRIMARY KEY
nama_wajib_pajak         TEXT
npwp                     TEXT
notes                    TEXT
created_at, updated_at
```
Pre-seeded from prior template C1/C2/C3 on import.

### `coretax_mappings` — global learned PWM→CoreTax mapping rules

```
id                       INTEGER PRIMARY KEY
match_kind               TEXT    -- 'account_number' | 'isin' | 'asset_signature' | 'liability_signature' | 'keterangan_norm'
match_value              TEXT
target_kode_harta        TEXT
target_kind              TEXT    -- 'asset' | 'liability'
target_keterangan_template TEXT
confidence               REAL DEFAULT 1.0
created_from_tax_year    INTEGER
last_used_tax_year       INTEGER
hits                     INTEGER NOT NULL DEFAULT 0
created_at, updated_at
UNIQUE(match_kind, match_value)
```
Upserted automatically when the user accepts an auto-reconcile suggestion or makes a manual mapping in Review & Manual Mapping. `hits` increments each time the rule is applied successfully.

### `coretax_reconcile_runs` — every reconcile invocation is persisted

```
id                       INTEGER PRIMARY KEY
tax_year                 INTEGER NOT NULL
fs_start_month           TEXT                       -- 'YYYY-MM'
fs_end_month             TEXT
snapshot_date            TEXT
created_at               TEXT NOT NULL
summary_json             TEXT NOT NULL              -- counts: filled, suggested, locked_skipped, unmatched
trace_json               TEXT NOT NULL              -- full per-row trace (CoretaxRowTrace[])
```

### `coretax_unmatched_pwm` — PWM rows that didn't map, scoped to a run

```
id                       INTEGER PRIMARY KEY
reconcile_run_id         INTEGER NOT NULL REFERENCES coretax_reconcile_runs(id) ON DELETE CASCADE
tax_year                 INTEGER NOT NULL
source_kind              TEXT NOT NULL              -- 'account_balance' | 'holding' | 'liability'
proposed_stable_key      TEXT
payload_json             TEXT NOT NULL              -- the raw PWM row for "Create from unmatched"
created_at               TEXT NOT NULL
INDEX(reconcile_run_id), INDEX(tax_year)
```

The UI defaults to the latest `reconcile_run_id` for a `tax_year` but the backend always knows which run produced a given unmatched list — page refresh and multi-run scenarios stay coherent.

### `coretax_import_staging` — preview area for prior-year import (full raw audit trail)

```
id                       INTEGER PRIMARY KEY
staging_batch_id         TEXT NOT NULL
target_tax_year          INTEGER NOT NULL
source_file_name         TEXT NOT NULL
source_sheet_name        TEXT NOT NULL
source_row_no            INTEGER NOT NULL
source_col_b_kode        TEXT
source_col_c_keterangan  TEXT
source_col_d_acq_year    TEXT
source_col_e_value       TEXT     -- raw cell as text (E header = prior_tax_year)
source_col_f_value       TEXT     -- raw cell as text (F header = prior_tax_year + 1 = target_tax_year)
source_col_g_value       TEXT
source_col_h_note        TEXT
parsed_kode_harta        TEXT
parsed_keterangan        TEXT
parsed_acquisition_year  INTEGER
parsed_prior_amount_idr  REAL    -- normalized E (becomes prior_amount of new row)
parsed_carry_amount_idr  REAL    -- normalized F (becomes current_amount when carry_forward = true)
parsed_market_value_idr  REAL
parsed_kind              TEXT    -- 'asset' | 'liability'
proposed_stable_key      TEXT
rule_default_carry_forward INTEGER  -- 0/1 from coretax_asset_codes
user_override_carry_forward INTEGER -- nullable; written by review UI before commit
parse_warning            TEXT
created_at               TEXT NOT NULL
INDEX(staging_batch_id)
```
Wiped on commit or replaced on re-import for the same `target_tax_year`. Survives page refresh.

### `coretax_asset_codes` — code lookup, seeded from prior templates

```
kode                     TEXT PRIMARY KEY
label                    TEXT NOT NULL
kind                     TEXT NOT NULL              -- 'asset' | 'liability'
default_carry_forward    INTEGER NOT NULL           -- 0/1
```

Seed (initial migration):

| kode | label | kind | default_carry_forward | rationale |
|---|---|---|---|---|
| 012 | Tabungan | asset | 0 | refreshed from PWM |
| 034 | Obligasi | asset | 0 | refreshed from PWM |
| 036 | Reksadana | asset | 0 | refreshed from PWM |
| 039 | Saham | asset | 0 | refreshed from PWM |
| 038 | Penyertaan Modal | asset | 1 | manual, sticky |
| 042 | Motor | asset | 1 | acquisition cost, sticky |
| 043 | Mobil | asset | 1 | acquisition cost, sticky |
| 051 | Logam mulia | asset | 1 | acquisition cost, sticky |
| 061 | Tanah & Bangunan | asset | 1 | acquisition cost, sticky |

---

## Carry-forward semantics (Carry Forward Review stage)

For each staging row with `target_tax_year = T`, on commit:

1. Always create a `coretax_rows` record for `tax_year = T`:
   - `prior_amount_idr = parsed_carry_amount_idr` (column F of prior template = value-at-end-of-(T-1) = E of new template)
   - `current_amount_idr`:
     - If `carry_forward = true` (rule default OR user override) → copy `prior_amount_idr`, `current_amount_source = 'carried_forward'`
     - If `carry_forward = false` → leave NULL, `current_amount_source = 'unset'`
   - `market_value_idr = parsed_market_value_idr`
2. **Do not pre-materialize zero placeholders for refreshable codes.** Reconcile-from-PWM will populate them later. If no PWM source matches and no learned mapping exists, the row stays in the table with `current_amount_idr = NULL` and is surfaced under "Unmatched — needs review".

This avoids stale `=0` rows polluting the export when an account has been closed.

---

## API surface (`finance/api.py`)

Replace the existing three CoreTax endpoints with these. All write endpoints honor `FINANCE_READ_ONLY` (NAS replica returns 403).

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/coretax/import/prior-year` | Multipart: `file: UploadFile`, `target_tax_year: int = Form(...)`. Parses → writes `coretax_import_staging`. Returns `{ batch_id, row_count, warnings, prior_tax_year, rows }`. Requires `python-multipart`. |
| GET  | `/api/coretax/import/staging/{batch_id}` | Returns staged rows for preview UI. |
| PATCH | `/api/coretax/import/staging/{batch_id}/rows/{id}` | Set `user_override_carry_forward` per row. |
| POST | `/api/coretax/import/staging/{batch_id}/commit` | Apply carry-forward rules + overrides → upsert into `coretax_rows` for `target_tax_year`. |
| DELETE | `/api/coretax/import/staging/{batch_id}` | Discard a staging batch. |
| GET  | `/api/coretax/summary?tax_year=` | Totals by Kode Harta + lock counts + reconcile coverage % — drives the dashboard at the top of the editor. |
| GET  | `/api/coretax/rows?tax_year=&kind=` | List rows for the editor. |
| PATCH | `/api/coretax/rows/{id}` | Edit any field. Sets `last_user_edited_at`. Auto-locks the touched amount/market_value field. |
| POST | `/api/coretax/rows` | Manual add row. |
| DELETE | `/api/coretax/rows/{id}` | Remove row. |
| POST | `/api/coretax/rows/{id}/lock` | Body: `{ field: 'amount'\|'market_value', reason? }`. |
| POST | `/api/coretax/rows/{id}/unlock` | Body: `{ field }`. |
| POST | `/api/coretax/reset-from-rules` | Body: `{ tax_year, kind?, kode_harta? }`. Re-applies carry-forward rule defaults to **unlocked** rows only. Useful after editing rules in the codes table. |
| POST | `/api/coretax/auto-reconcile` | Body: `{ tax_year, fs_range: {start_month, end_month}, snapshot_date? }`. Reads FS data + `coretax_mappings`. Writes `current_amount_idr` and (when applicable) `market_value_idr` only on rows where the corresponding `*_locked = 0`. **Creates a `coretax_reconcile_runs` row** + persists unmatched PWM rows under that `run_id`. Returns `{ run_id, summary, trace, unmatched }`. |
| GET  | `/api/coretax/reconcile-runs?tax_year=` | List recent runs (id, timestamps, summary). |
| GET  | `/api/coretax/unmatched?tax_year=&run_id=` | Unmatched PWM rows for a run (defaults to latest run for the tax_year if `run_id` omitted). |
| POST | `/api/coretax/mappings` | Upsert a learned mapping (called from manual-mapping UI). Returns `{ id }`. |
| GET  | `/api/coretax/mappings` | List learned mappings (admin/diagnostics view). |
| DELETE | `/api/coretax/mappings/{id}` | Remove a stale rule. |
| POST | `/api/coretax/export` | Body: `{ tax_year }`. Writes XLSX + audit JSON under `data/coretax/output/`. Returns **`{ file_id, download_url, audit_url }`** only — no filesystem paths leaked to the PWA. |
| GET  | `/api/coretax/exports?tax_year=` | Lists prior exports (file_id, created_at, totals). Server resolves to fs paths internally. |
| GET  | `/api/coretax/export/{file_id}/download` | Streams the XLSX with `Content-Disposition: attachment`. `file_id` = bare filename, validated against an allowlist scoped to the output dir (no path traversal). |
| GET  | `/api/coretax/export/{file_id}/audit` | Returns audit JSON. |

---

## Backend modules

Replace `finance/coretax_export.py` entirely with a package:

- `finance/coretax/__init__.py`
- `finance/coretax/db.py` — table creation + CRUD helpers. Hooks into `finance/db.py:open_db()` migration.
- `finance/coretax/import_parser.py` — parses prior-year XLSX:
  - Reads C1/C2/C3 for taxpayer + prior-tax-year (e.g. 2024)
  - Identifies columns by row-4 headers: E=prior_tax_year, F=prior_tax_year+1, G=Nilai saat ini, H=Keterangan
  - Caller passes `target_tax_year` (e.g. 2025); parser asserts `prior_tax_year + 1 == target_tax_year`
  - Iterates rows 6 → first blank or row 47, stops at row 48
  - Writes raw cell text + parsed normalized values into staging
  - Generates `proposed_stable_key` heuristically (account number from keterangan regex, ISIN, etc.)
- `finance/coretax/carry_forward.py` — applies rule defaults + user overrides; upserts into `coretax_rows`.
- `finance/coretax/reconcile.py` — Reconcile-from-PWM stage:
  - Reuses the internal helper that powers `/api/reports/financial-statement` (`api.py:3465`) — direct Python call, **not** HTTP
  - For each PWM source row, builds match keys → looks up `coretax_mappings`
  - Hit → updates target `coretax_rows` row only if `amount_locked = 0` (and `market_value_locked = 0` for market value)
  - Miss → emits to "unmatched" list for the UI
  - Returns trace shaped like the existing `CoretaxRowTrace` dataclass for audit JSON continuity
- `finance/coretax/exporter.py` — XLSX writer:
  - **Loads canonical template** (`data/coretax/templates/CoreTax_template.xlsx`, kept chart/image-free to survive openpyxl roundtrip)
  - **Modifies only known cells**: C1, C2, C3, then rows 6+ for each `coretax_rows` record (A=No, B=kode_harta, C=keterangan, D=acquisition_year, E=prior_amount_idr, **F = exact rule below**, G=market_value_idr, H=keterangan-long/note)
  - **F-cell rule (deterministic, do not infer from numeric equality):**
    ```
    if current_amount_source == 'carried_forward':
        write formula '=E{row}'
    else:
        write literal current_amount_idr (or leave blank if NULL)
    ```
  - **Template capacity (v1):** template body is rows 6–47 (42 rows). If `len(rows_to_export) > 42`, exporter **must raise a clear validation error before touching the workbook** — no partial XLSX written. Row insertion / formula shifting is out of scope for v1.
  - **Does not** recreate the workbook from scratch; **does not** touch row 48+ formulas; **does not** rewrite styles
  - Saves as `CoreTax_{tax_year}_v{n}.xlsx` (auto-increment n) — `file_id` returned to API = bare filename
  - Writes audit JSON next to it (same shape as today's `CoretaxResult`/`CoretaxRowTrace`)
  - Caveat documented in code: openpyxl does not recalc formulas (Excel/LibreOffice will on open). `data_only` flag controls formula-vs-cached-value reads. Charts/images may not roundtrip — template must stay chart-free.

---

## PWA (`pwa/src/views/CoreTaxSpt.vue` — full rewrite)

Replace the one-screen view with a tabbed wizard, scoped per `tax_year`:

**Top bar:** `tax_year` selector + summary chip (totals, lock count, coverage %).

1. **Import Previous SPT** (auto-shown when `coretax_rows` empty for the chosen year):
   - File picker → POST `/api/coretax/import/prior-year` with `target_tax_year`
   - Preview table from staging endpoint, rule-default carry-forward checkboxes per row
   - "Commit to ledger" → commit endpoint
2. **Carry Forward Review**:
   - Editable table of `coretax_rows`, lock toggles per amount field
   - "Reset unlocked rows from rules" → POST `/api/coretax/reset-from-rules`
3. **Reconcile from PWM**:
   - "Generate FS" subsection (start/end month picker) → embeds existing FS view component as side panel
   - "Run reconcile" → POST `/api/coretax/auto-reconcile`. Three result lists:
     - Filled (green) — locked rows skipped, badge shown
     - Suggested (amber) — needs explicit accept (writes mapping + value) or reject
     - Unmatched PWM rows (separate panel)
4. **Review & Manual Mapping**:
   - Main: all `coretax_rows`, blank `current_amount_idr` highlighted
   - Side panel: FS data filterable by account / asset class / liability
   - Click-to-map FS row → CoreTax row → POST `/api/coretax/mappings` then PATCH the target
   - "Add row" (manual). "Create from unmatched" on each unmatched PWM row (auto-populates kode_harta, keterangan, owner, stable_key)
5. **Export CoreTax XLSX**:
   - Preview totals (sum E, sum F, F − E delta, total liabilities)
   - "Export" → POST `/api/coretax/export` → triggers download via returned `download_url`
   - Recent exports list with download + audit-view links

Pinia store: `useCoretaxStore` with `taxYear`, `summary`, `rows`, `mappings`, `staging`, `fsData`, `unmatched`, `lastReconcileTrace`.

---

## Critical files to touch

| File | Action |
|---|---|
| `finance/coretax_export.py` | **Delete** |
| `finance/coretax/__init__.py` | New |
| `finance/coretax/db.py` | New |
| `finance/coretax/import_parser.py` | New |
| `finance/coretax/carry_forward.py` | New |
| `finance/coretax/reconcile.py` | New (preserve `CoretaxRowTrace`/`CoretaxResult` shape for audit continuity) |
| `finance/coretax/exporter.py` | New |
| `finance/db.py` | Add migration: **7 new tables** (`coretax_rows`, `coretax_taxpayer`, `coretax_mappings`, `coretax_import_staging`, `coretax_asset_codes`, `coretax_reconcile_runs`, `coretax_unmatched_pwm`), seed `coretax_asset_codes` |
| `finance/api.py` | Remove old `/api/coretax/{templates,generate,audit}` (~lines 3322–3460), add ~18 new endpoints. Add `python-multipart` to deps if not already present. Honor `FINANCE_READ_ONLY` on every write endpoint. |
| `finance/config.py` | Keep `data/coretax/output/` and `data/coretax/templates/` paths |
| `pwa/src/views/CoreTaxSpt.vue` | **Full rewrite** (5-stage wizard) |
| `pwa/src/stores/coretax.js` | New Pinia store |
| `pwa/src/router/index.js` | Route stays; view import updated |
| `requirements.txt` (or pyproject) | Add `python-multipart` if missing |

---

## Reused existing code

- `finance/db.py:open_db()` — SQLite connection + migration entrypoint
- `/api/reports/financial-statement` internal helper (`api.py:3465`) — imported directly by `reconcile.py`
- `account_balances`, `holdings`, `liabilities` schemas (`db.py:123/142/173`) — read-only consumer
- Audit JSON writer pattern (`_write_audit_json`, currently `coretax_export.py:271`) — port to `exporter.py` keeping JSON shape
- FS view component in PWA — embed as side panel
- `FINANCE_READ_ONLY` guard pattern — apply to all write endpoints

---

## Verification

1. **Migration runs cleanly**: delete `data/finance.db`, re-run `python3 -m finance.importer`, start `python3 -m finance.server`. Confirm new tables: `sqlite3 data/finance.db ".schema coretax_rows"` etc.
2. **Prior-year import + raw audit**: upload `~/Library/CloudStorage/OneDrive-Personal/Finance/SPT/2024/CoreTax 2024.xlsx` against `target_tax_year=2025`. Confirm staging has ~40 rows and every row has `source_row_no`, `source_col_e_value`, `source_col_f_value`, `source_col_g_value`, `source_col_h_note` populated. Commit; verify `coretax_rows` for tax_year 2025 has prior_amount = parsed F-of-2024 template.
3. **Carry-forward defaults**: confirm 061/051/043/042/038 rows commit with `current_amount_source='carried_forward'`; 012/034/036/039 rows commit with `current_amount_idr IS NULL` and `current_amount_source='unset'`.
4. **Reconcile from PWM**: with 2025 PWM data loaded, run reconcile. Verify all 14 Tabungan rows fill from `account_balances`, all 3 Saham + 3 Obligasi + 2 Reksadana from `holdings`. Trace JSON written to `data/coretax/output/`.
5. **Mapping learning**: manually map an unmatched FS row to a CoreTax row, then unset `current_amount_idr`, re-run reconcile — confirm same mapping applied automatically and `coretax_mappings.hits` incremented.
6. **Manual lock test (load-bearing)**: manually edit one auto-filled amount → verify it auto-locks. Re-run reconcile. Confirm value is **not** overwritten and the row appears in trace as `skipped_locked`.
7. **Duplicate account test**: add two accounts with same `kode_harta + keterangan + acquisition_year` but different account numbers. Confirm both survive import and export with distinct `stable_key`s.
8. **Removed account test**: prior-year row whose underlying PWM account no longer exists → confirm row stays in `coretax_rows` with `current_amount_idr = NULL`, surfaces in "Unmatched / needs review", does **not** get silently exported with stale prior value as current.
9. **Export round-trip**: export → open `CoreTax_2025_v1.xlsx` in Excel. Verify C1/C2/C3 correct, rows 6+ populated, F-column has `=E{n}` formula **strictly when `current_amount_source='carried_forward'`** and literal values otherwise (do not infer from E==F numeric equality), **rows 48+ formulas/styles intact and recalculate** (E48 = SUM(E6:E47), F49 = F48 − E48).
10. **Template capacity guard**: stage 43+ rows for one tax_year and call export — confirm a clear validation error is returned and **no XLSX file is written** to `data/coretax/output/`.
11. **No-fs-path leak**: grep every CoreTax JSON response in DevTools — confirm no string starting with `data/` or absolute paths is returned. Only `file_id` + `download_url` + `audit_url`.
12. **Read-only guard**: deploy to NAS with `FINANCE_READ_ONLY=true`. Hit **every** POST/PATCH/DELETE endpoint (import, staging mutations, rows CRUD, lock/unlock, reset-from-rules, auto-reconcile, mappings, export) → confirm 403. GETs still work.
13. **Multipart wiring**: confirm `pip list | grep multipart` shows `python-multipart`; upload endpoint accepts both `file` and `target_tax_year` form field.
14. **Reconcile-run persistence**: trigger two reconcile runs back-to-back. Confirm two `coretax_reconcile_runs` rows exist; `GET /api/coretax/unmatched?tax_year=2025` (no `run_id`) returns the latest; `GET /api/coretax/unmatched?tax_year=2025&run_id={older}` returns the older list.
15. **CHECK constraint catches typos**: attempt to insert a row with `current_amount_source='auto_reconcile'` (missing 'd') → SQLite must reject.

---

## Post-verification gaps

The first verification pass found these blockers. Treat these as precise specs, not optional polish.

| ID | Gap | Required behavior |
|---|---|---|
| G1 | Learned mappings were not actionable | `coretax_mappings` must store `target_stable_key`; reconcile must resolve mappings by prioritized `(match_kind, match_value)` candidates to a row for the active `tax_year`; successful mapping use must set `last_mapping_id`, increment `hits`, and update `last_used_tax_year`. |
| G2 | Locked values could still be overwritten | Reconcile must guard `amount_locked` and `market_value_locked` independently. Cash rows (`kode_harta='012'`) must only write `current_amount_idr`; cash reconcile must never write `market_value_idr`, locked or unlocked. |
| G3 | Upload parsed rows but PWA could not preview | `POST /api/coretax/import/prior-year` must return staged rows in addition to `{ batch_id, row_count, warnings, prior_tax_year }`, so the PWA can immediately show the preview and commit controls without requiring a second fetch. |
| G4 | Deleted exporter left stale tests | Remove old `tests/test_coretax_export.py` coverage tied to `finance.coretax_export`; replace it with tests for the persistent ledger workflow: import, carry-forward, lock guards, mapping learning, export formula rule, capacity guard, and enum CHECK constraints. |
| G5 | Wrong-year templates could be imported | Parser must reject a workbook when the detected E/F year headers do not match the requested `target_tax_year`. For a prior template with `E=2025` and `F=2026`, only `target_tax_year=2026` is valid. |

PWA manual mapping details:
- "Create from unmatched" must infer `kode_harta` from PWM source (`012` cash, `034` bond, `036` mutual fund, `039` stock), create the row, then persist a mapping using the returned `stable_key`.
- Cash unmatched payloads use `value` for the amount; the UI must not create zero-valued cash rows by only checking `balance_idr`.
- If a source kind cannot infer a tax code, row creation may still proceed, but mapping creation must be skipped rather than posting an invalid blank `target_kode_harta`.

---

## Out of scope (explicit non-goals)

- Multi-NPWP support (single Wajib Pajak; spouse accounts under primary)
- Auto-discovery of prior template from OneDrive (user uploads via PWA)
- Liability **import** from anywhere other than PWM `liabilities` + manual entry
- Currency conversion (assume IDR throughout)
- Form-side changes to template rows 48+ beyond what existing template formulas already do
- Charts/images in template (keep template chart-free; openpyxl roundtrip caveat)
