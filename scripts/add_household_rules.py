#!/usr/bin/env python3
"""
Add household-specific categorization rules.

1. Adds household-specific categories to the Categories tab
2. Adds new Merchant Aliases header columns (owner_filter, account_filter) if missing
3. Adds account-aware alias rules for common household transactions

Run once:  python3 scripts/add_household_rules.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from finance.config import load_config, get_sheets_config
from finance.sheets import SheetsClient

cfg = load_config()
sc = SheetsClient(get_sheets_config(cfg))
today = datetime.now().strftime("%Y-%m-%d")

# ── 1. Add new categories ───────────────────────────────────────────────────

print("Reading Categories tab…")
cat_rows = sc._get(f"{sc.cfg.categories_tab}!A:E")
existing_cats = {r[0].strip() for r in cat_rows[1:] if r}

new_categories = [
    # [category, icon, sort_order, is_recurring, monthly_budget]
    ["Household Expenses", "🧺", "20", "FALSE", ""],
    ["Child Support",      "👧", "21", "TRUE",  ""],
]

for cat_row in new_categories:
    if cat_row[0] not in existing_cats:
        sc._append(f"{sc.cfg.categories_tab}!A:E", [cat_row])
        print(f"  ✅ Added category: {cat_row[1]} {cat_row[0]}")
    else:
        print(f"  ⏭  Category already exists: {cat_row[0]}")


# ── 2. Update Aliases header to include new columns ─────────────────────────

print("\nReading Merchant Aliases tab…")
alias_rows = sc._get(f"{sc.cfg.aliases_tab}!A:G")
header = alias_rows[0] if alias_rows else []

# Check if columns F and G (owner_filter, account_filter) exist in header
if len(header) < 7 or header[5].strip().lower() != "owner_filter":
    print("  Adding owner_filter and account_filter headers…")
    # Extend header row
    sc._update(
        f"{sc.cfg.aliases_tab}!F1:G1",
        [["owner_filter", "account_filter"]],
    )
    print("  ✅ Header updated")
else:
    print("  ⏭  Header already has owner_filter/account_filter columns")


# ── 3. Add new account-aware alias rules ─────────────────────────────────────

# Format: [merchant, alias, category, match_type, added_date, owner_filter, account_filter]
new_aliases = [
    # ── Child Support ──
    # Transfers from Gandrik BCA / Helen BCA 2684118322 to Katina/Kaitlyn
    ["Child Support (Katina)",   "KATINA MIKAELA",    "Child Support",      "contains", today, "Gandrik", "2171138631"],
    ["Child Support (Kaitlyn)",  "KAITLYN GABRIELLE", "Child Support",      "contains", today, "Gandrik", "2171138631"],
    ["Child Support (Katina)",   "KATINA MIKAELA",    "Child Support",      "contains", today, "Helen",   "2684118322"],
    ["Child Support (Kaitlyn)",  "KAITLYN GABRIELLE", "Child Support",      "contains", today, "Helen",   "2684118322"],

    # ── Healthcare ──
    # Transfers to Dery Ginanjar from Helen BCA 2684118322 = Healthcare
    ["Healthcare (Dery)",        "DERY GINANJAR",     "Healthcare",         "contains", today, "Helen", "2684118322"],
    # Transfers to Ivan from Helen BCA 2684118322 = Healthcare
    # Note: "IVAN" is generic, so we restrict to Helen's specific account
    ["Healthcare (Ivan)",        "IVAN",              "Healthcare",         "contains", today, "Helen", "2684118322"],

    # ── Household Expenses ──
    # Transfers to Fransisca Rini from Helen BCA 2684118322 = Household Expenses
    ["Household Staff (Rini)",   "FRANSISCA RINI",    "Household Expenses", "contains", today, "Helen", "2684118322"],
    # Cash withdrawals from Helen BCA 5500346622 = Household Expenses
    # (overrides the generic ATM Withdrawal → Cash Withdrawal regex rule)
    ["Household Cash",           "TARIKAN ATM",       "Household Expenses", "contains", today, "Helen", "5500346622"],

    # ── Income ──
    # KR OTOMATIS from ANZ in Gandrik BCA = Income (salary)
    ["PwC Indonesia Salary",     "KR OTOMATIS LLG-ANZ INDONESIA", "Income", "exact", today, "Gandrik", "2171138631"],
    # ERHA clinic / fee incoming in Helen Permata = Income (wife's income)
    ["ERHA Clinic (Income)",     "ERHA CLINIC",       "Income",             "contains", today, "Helen", "4123968773"],
    ["Fee Income",               r"^TRF .*FEE",       "Income",             "regex",    today, "Helen", "4123968773"],
]

# Check which aliases already exist to avoid duplicates
existing_aliases = set()
for row in alias_rows[1:]:
    r = list(row) + [""] * (7 - len(row))
    # Key: (alias_upper, owner_filter, account_filter) for uniqueness
    existing_aliases.add((r[1].strip().upper(), r[5].strip(), r[6].strip()))

print(f"\n  Existing aliases: {len(alias_rows) - 1}")

added = 0
for rule in new_aliases:
    key = (rule[1].upper(), rule[5], rule[6])
    if key in existing_aliases:
        print(f"  ⏭  Already exists: {rule[0]} ({rule[1]})")
        continue
    sc._append(f"{sc.cfg.aliases_tab}!A:G", [rule])
    print(f"  ✅ Added: {rule[0]} → {rule[2]} (owner={rule[5] or '*'}, acct={rule[6] or '*'})")
    added += 1

print(f"\n  Added {added} new alias rules")


# ── 4. Fix existing rules that conflict ──────────────────────────────────────

# The existing regex rule "^TARIKAN ATM" → "Cash Withdrawal" will now be
# overridden by the more specific contains rule for Helen BCA 5500346622.
# The contains rules in Layer 1b are checked BEFORE regex rules in Layer 2,
# and account-filtered contains rules are checked in order.
# So Helen/5500346622 ATM withdrawals → Household Expenses,
# while all other ATM withdrawals → Cash Withdrawal (via regex).
# No changes needed here — the layering handles it correctly.

print("\n✅ Done! Run 'python3 -m finance.sync' to sync changes to SQLite.")
print("   Then restart the FastAPI server to reload aliases.")
