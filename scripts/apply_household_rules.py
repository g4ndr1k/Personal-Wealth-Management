#!/usr/bin/env python3
"""
One-time: re-categorize existing transactions using the new household rules.

Updates both Google Sheets (Transactions tab) and SQLite so changes are
persistent across syncs.

Rules applied:
1. Helen BCA 5500346622 ATM withdrawals → Household Expenses
2. Child support transfers from Gandrik BCA / Helen BCA 2684118322
3. Healthcare transfers from Helen BCA 2684118322
4. Household staff transfer to Fransisca Rini from Helen BCA 2684118322
5. Gandrik salary from KR OTOMATIS LLG-ANZ INDONESIA
6. Helen income in Permata containing ERHA clinic or fee
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from finance.config import load_config, get_sheets_config, get_finance_config
from finance.sheets import SheetsClient

cfg = load_config()
sc = SheetsClient(get_sheets_config(cfg))
finance_cfg = get_finance_config(cfg)

# ── Read Transactions tab to find row numbers ────────────────────────────────
print("Reading Transactions tab…")
rows = sc._get(f"{sc.cfg.transactions_tab}!A:O")
header = rows[0]
data = rows[1:]
print(f"  {len(data)} rows")

# Column indices (0-based in data)
COL_RAW_DESC = 5   # F
COL_MERCHANT = 6   # G
COL_CATEGORY = 7   # H
COL_ACCOUNT  = 9   # J
COL_OWNER    = 10   # K
COL_HASH     = 12   # M

updates = []  # (sheet_row_1indexed, merchant, category, hash, description)

for i, row in enumerate(data):
    r = list(row) + [""] * (15 - len(row))
    desc = (r[COL_RAW_DESC] or "").strip().upper()
    owner = (r[COL_OWNER] or "").strip()
    account = (r[COL_ACCOUNT] or "").strip()
    current_cat = (r[COL_CATEGORY] or "").strip()
    hash_ = (r[COL_HASH] or "").strip()
    sheet_row = i + 2  # 1-indexed, skip header

    is_transfer = "TRSF E-BANKING DB" in desc

    # Rule 1: Helen BCA 5500346622 ATM withdrawals → Household Expenses
    if ("TARIKAN ATM" in desc and owner == "Helen" and account == "5500346622"
            and current_cat != "Household Expenses"):
        updates.append((sheet_row, "Household Cash", "Household Expenses", hash_, desc[:50]))

    # Rule 2: Child support transfers from Gandrik/Helen BCA 2684...
    if (is_transfer and owner == "Gandrik" and account == "2171138631"
            and "KAITLYN GABRIELLE" in desc and current_cat != "Child Support"):
        updates.append((sheet_row, "Child Support (Kaitlyn)", "Child Support", hash_, desc[:50]))
    if (is_transfer and owner == "Gandrik" and account == "2171138631"
            and "KATINA MIKAELA" in desc and current_cat != "Child Support"):
        updates.append((sheet_row, "Child Support (Katina)", "Child Support", hash_, desc[:50]))
    if (is_transfer and owner == "Helen" and account == "2684118322"
            and "KAITLYN GABRIELLE" in desc and current_cat != "Child Support"):
        updates.append((sheet_row, "Child Support (Kaitlyn)", "Child Support", hash_, desc[:50]))
    if (is_transfer and owner == "Helen" and account == "2684118322"
            and "KATINA MIKAELA" in desc and current_cat != "Child Support"):
        updates.append((sheet_row, "Child Support (Katina)", "Child Support", hash_, desc[:50]))

    # Rule 3: Healthcare transfers from Helen BCA 2684118322
    if (is_transfer and owner == "Helen" and account == "2684118322"
            and "DERY GINANJAR" in desc and current_cat != "Healthcare"):
        updates.append((sheet_row, "Healthcare (Dery)", "Healthcare", hash_, desc[:50]))
    if (is_transfer and owner == "Helen" and account == "2684118322"
            and "IVAN" in desc and current_cat != "Healthcare"):
        updates.append((sheet_row, "Healthcare (Ivan)", "Healthcare", hash_, desc[:50]))

    # Rule 4: Household staff transfer
    if (is_transfer and owner == "Helen" and account == "2684118322"
            and "FRANSISCA RINI" in desc and current_cat != "Household Expenses"):
        updates.append((sheet_row, "Household Staff (Rini)", "Household Expenses", hash_, desc[:50]))

    # Rule 5: Gandrik income
    if (owner == "Gandrik" and account == "2171138631"
            and "KR OTOMATIS" in desc and "LLG-ANZ INDONESIA" in desc
            and current_cat != "Income"):
        updates.append((sheet_row, "PwC Indonesia Salary", "Income", hash_, desc[:50]))

    # Rule 6: Helen income in Permata
    if (owner == "Helen" and account == "4123968773"
            and "ERHA CLINIC" in desc and current_cat != "Income"):
        updates.append((sheet_row, "ERHA Clinic (Income)", "Income", hash_, desc[:50]))
    if (owner == "Helen" and account == "4123968773"
            and desc.startswith("TRF ") and "FEE" in desc and current_cat != "Income"):
        updates.append((sheet_row, "Fee Income", "Income", hash_, desc[:50]))

print(f"\n  Found {len(updates)} transactions to update:")
for _, merch, cat, _, desc in updates:
    print(f"    → {cat:25s}  {desc}")

if not updates:
    print("\n  Nothing to update!")
    sys.exit(0)

# ── Update Google Sheets (batch) ──────────────────────────────────────────────
print(f"\nUpdating {len(updates)} rows in Sheets…")
batch_data = []
for sheet_row, merchant, category, _, _ in updates:
    batch_data.append({
        "range": f"{sc.cfg.transactions_tab}!G{sheet_row}:H{sheet_row}",
        "values": [[merchant, category]],
    })

CHUNK = 500
for i in range(0, len(batch_data), CHUNK):
    chunk = batch_data[i:i + CHUNK]
    sc.service.spreadsheets().values().batchUpdate(
        spreadsheetId=sc.cfg.spreadsheet_id,
        body={"valueInputOption": "RAW", "data": chunk},
    ).execute()
    print(f"  Sheets batch {i+1}–{i+len(chunk)} done")

# ── Update SQLite ─────────────────────────────────────────────────────────────
print("Updating SQLite…")
conn = sqlite3.connect(finance_cfg.sqlite_db)
for _, merchant, category, hash_, _ in updates:
    conn.execute(
        "UPDATE transactions SET merchant = ?, category = ? WHERE hash = ?",
        (merchant, category, hash_),
    )
conn.commit()
conn.close()

print(f"\n✅ Updated {len(updates)} transactions")
