"""
Stage 2 — One-time Google Sheet structure setup.

Usage
─────
  python3 -m finance.setup_sheets

What it does
────────────
  1. Verifies the spreadsheet is reachable
  2. Creates any missing tabs (Transactions, Merchant Aliases, Categories,
     Currency Codes, Import Log)
  3. Writes column headers to each tab (row 1)
  4. Populates Categories with defaults (if the tab was just created or is empty)
  5. Populates Currency Codes with Indonesian card-holder defaults (same condition)
  6. Formats header rows: bold white text on dark blue background

Safe to re-run — existing tabs with data are left untouched.
Only empty or missing tabs are initialised.

After running this script, open the spreadsheet URL printed at the end and
confirm the structure looks correct before running the importer.
"""
from __future__ import annotations
import argparse
import logging
import sys

from googleapiclient.errors import HttpError

from finance.config import load_config, get_sheets_config
from finance.sheets import SheetsClient, _build_service

log = logging.getLogger(__name__)

# ── Column headers ─────────────────────────────────────────────────────────────

TABS: dict[str, list[str]] = {
    "transactions_tab": [
        "date", "amount", "original_currency", "original_amount",
        "exchange_rate", "raw_description", "merchant", "category",
        "institution", "account", "owner", "notes",
        "hash", "import_date", "import_file",
    ],
    "aliases_tab": [
        "merchant", "alias", "category", "match_type", "added_date",
        "owner_filter", "account_filter",
    ],
    "categories_tab": [
        "category", "icon", "sort_order", "is_recurring", "monthly_budget",
        "category_group", "subcategory",
    ],
    "currency_tab": [
        "currency_code", "currency_name", "symbol",
        "flag_emoji", "country_hints", "decimal_places",
    ],
    "import_log_tab": [
        "import_date", "import_file", "rows_added",
        "rows_skipped", "rows_total", "duration_s", "notes",
    ],
    "pdf_import_log_tab": [
        "month", "label", "expected", "actual", "status", "files", "last_processed",
    ],
}

# ── Default seed data ──────────────────────────────────────────────────────────

DEFAULT_CATEGORIES = [
    # [category, icon, sort_order, is_recurring, monthly_budget, category_group, subcategory]
    # ── 1. Housing & Bills ────────────────────────────────────────────────────
    ["Housing",          "🏠", 1,  "TRUE",  "", "Housing & Bills",      "Housing"],
    ["Utilities",        "⚡", 2,  "TRUE",  "", "Housing & Bills",      "Utilities"],
    ["Phone Bill",       "📞", 3,  "TRUE",  "", "Housing & Bills",      "Communication"],
    ["Internet",         "🌐", 4,  "TRUE",  "", "Housing & Bills",      "Communication"],
    # ── 2. Food & Dining ─────────────────────────────────────────────────────
    ["Groceries",        "🛒", 5,  "FALSE", "", "Food & Dining",        "Groceries"],
    ["Dining Out",       "🍽️", 6,  "FALSE", "", "Food & Dining",        "Dining Out"],
    ["Delivery & Takeout","🛵", 7,  "FALSE", "", "Food & Dining",        "Delivery & Takeout"],
    # ── 3. Transportation ────────────────────────────────────────────────────
    ["Auto",             "🚗", 8,  "FALSE", "", "Transportation",       "Auto"],
    ["Rideshare",        "🚕", 9,  "FALSE", "", "Transportation",       "Rideshare"],
    # ── 4. Lifestyle & Personal ──────────────────────────────────────────────
    ["Shopping",         "🛍️", 10, "FALSE", "", "Lifestyle & Personal", "Shopping"],
    ["Personal Care",    "💇", 11, "FALSE", "", "Lifestyle & Personal", "Personal Care"],
    ["Entertainment",    "🎬", 12, "FALSE", "", "Lifestyle & Personal", "Entertainment"],
    ["Hobbies",          "🎮", 13, "FALSE", "", "Lifestyle & Personal", "Hobbies"],
    ["Subscriptions",    "📱", 14, "TRUE",  "", "Lifestyle & Personal", "Subscriptions"],
    # ── 5. Health & Family ───────────────────────────────────────────────────
    ["Healthcare",       "🏥", 15, "FALSE", "", "Health & Family",      "Healthcare"],
    ["Family",           "👨‍👩‍👧", 16, "TRUE",  "", "Health & Family",      "Family"],
    ["Household",        "🧺", 17, "FALSE", "", "Health & Family",      "Household"],
    ["Education",        "📚", 18, "FALSE", "", "Health & Family",      "Education"],
    ["Gifts & Donations","🎁", 19, "FALSE", "", "Health & Family",      "Gifts & Donations"],
    # ── 6. Travel ────────────────────────────────────────────────────────────
    ["Flights & Hotels", "✈️", 20, "FALSE", "", "Travel",               "Flights & Hotels"],
    ["Vacation Spending","🏖️", 21, "FALSE", "", "Travel",               "Vacation Spending"],
    # ── 7. Financial & Legal ─────────────────────────────────────────────────
    ["Fees & Interest",  "🏦", 22, "FALSE", "", "Financial & Legal",    "Fees & Interest"],
    ["Taxes",            "📋", 23, "FALSE", "", "Financial & Legal",    "Taxes"],
    # ── 8. System / Tracking (Non-Expense Categories) ────────────────────────
    ["Income",           "💰", 24, "FALSE", "", "System / Tracking",    "Income"],
    ["Dividends",        "📈", 25, "FALSE", "", "System / Tracking",    "Dividends"],
    ["Interest Income",  "🏦", 26, "FALSE", "", "System / Tracking",    "Interest"],
    ["Capital Gains",    "📊", 27, "FALSE", "", "System / Tracking",    "Capital Gains"],
    ["Other Income",     "💵", 28, "FALSE", "", "System / Tracking",    "Other Income"],
    ["Transfer",         "🔁", 29, "FALSE", "", "System / Tracking",    "Transfer"],
    ["Cash Withdrawal",  "🏧", 30, "FALSE", "", "System / Tracking",    "Cash Withdrawal"],
    ["Adjustment",       "🔧", 31, "FALSE", "", "System / Tracking",    "Adjustment"],
    ["Other",            "❓", 32, "FALSE", "", "System / Tracking",    "Other"],
]

DEFAULT_CURRENCIES = [
    # [code, name, symbol, flag, country_hints, decimal_places]
    ["USD", "US Dollar",          "$",    "🇺🇸", "US, USA, UNITED STATES",                 2],
    ["SGD", "Singapore Dollar",   "S$",   "🇸🇬", "SG, SGP, SINGAPORE",                     2],
    ["MYR", "Malaysian Ringgit",  "RM",   "🇲🇾", "MY, MYS, MALAYSIA",                      2],
    ["JPY", "Japanese Yen",       "¥",    "🇯🇵", "JP, JPN, JAPAN",                         0],
    ["THB", "Thai Baht",          "฿",    "🇹🇭", "TH, THA, THAILAND",                      2],
    ["EUR", "Euro",               "€",    "🇪🇺", "EU, EUR, EUROPE, DE, FR, IT, ES, NL",    2],
    ["GBP", "British Pound",      "£",    "🇬🇧", "GB, GBR, UK, UNITED KINGDOM",            2],
    ["AUD", "Australian Dollar",  "A$",   "🇦🇺", "AU, AUS, AUSTRALIA",                     2],
    ["HKD", "Hong Kong Dollar",   "HK$",  "🇭🇰", "HK, HKG, HONG KONG",                    2],
    ["KRW", "South Korean Won",   "₩",    "🇰🇷", "KR, KOR, KOREA, SOUTH KOREA",            0],
    ["CNY", "Chinese Yuan",       "¥",    "🇨🇳", "CN, CHN, CHINA",                         2],
    ["TWD", "Taiwan Dollar",      "NT$",  "🇹🇼", "TW, TWN, TAIWAN",                        0],
    ["INR", "Indian Rupee",       "₹",    "🇮🇳", "IN, IND, INDIA",                         2],
    ["SAR", "Saudi Riyal",        "SR",   "🇸🇦", "SA, SAU, SAUDI ARABIA",                  2],
    ["AED", "UAE Dirham",         "د.إ",  "🇦🇪", "AE, ARE, UAE, DUBAI, ABU DHABI",         2],
    ["NZD", "New Zealand Dollar", "NZ$",  "🇳🇿", "NZ, NZL, NEW ZEALAND",                   2],
    ["CHF", "Swiss Franc",        "Fr",   "🇨🇭", "CH, CHE, SWITZERLAND",                   2],
    ["CAD", "Canadian Dollar",    "C$",   "🇨🇦", "CA, CAN, CANADA",                        2],
]

# Header row style (batchUpdate)
_HEADER_BG    = {"red": 0.184, "green": 0.310, "blue": 0.561}  # #2F4F8F
_HEADER_FG    = {"red": 1.0,   "green": 1.0,   "blue": 1.0}    # white


# ── Main ───────────────────────────────────────────────────────────────────────

def setup(cfg_path: str | None = None):
    raw_cfg    = load_config(cfg_path)
    sheets_cfg = get_sheets_config(raw_cfg)

    if not sheets_cfg.spreadsheet_id:
        log.error(
            "google_sheets.spreadsheet_id is empty in settings.toml.\n"
            "Create a Google Sheet, copy the ID from the URL, and paste it in."
        )
        sys.exit(1)

    log.info("Building Sheets service …")
    client  = SheetsClient(sheets_cfg)   # triggers OAuth if needed
    service = _build_service(sheets_cfg)

    spreadsheet_url = (
        f"https://docs.google.com/spreadsheets/d/{sheets_cfg.spreadsheet_id}"
    )

    # Map config key → desired tab title
    tab_map = {
        "transactions_tab":   sheets_cfg.transactions_tab,
        "aliases_tab":        sheets_cfg.aliases_tab,
        "categories_tab":     sheets_cfg.categories_tab,
        "currency_tab":       sheets_cfg.currency_tab,
        "import_log_tab":     sheets_cfg.import_log_tab,
        "pdf_import_log_tab": sheets_cfg.pdf_import_log_tab,
    }

    # ── Step 1: get existing sheet metadata ───────────────────────────────────
    log.info("Fetching spreadsheet metadata …")
    metadata = service.spreadsheets().get(
        spreadsheetId=sheets_cfg.spreadsheet_id
    ).execute()
    existing_tabs = {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in metadata["sheets"]
    }
    log.info("Existing tabs: %s", list(existing_tabs.keys()))

    # ── Step 2: create any missing tabs ───────────────────────────────────────
    for cfg_key, tab_title in tab_map.items():
        if tab_title not in existing_tabs:
            log.info("Creating tab: %s", tab_title)
            sheet_id = _create_tab(service, sheets_cfg.spreadsheet_id, tab_title)
            existing_tabs[tab_title] = sheet_id
        else:
            log.info("Tab already exists: %s", tab_title)

    # ── Step 3: write headers if row 1 is empty ───────────────────────────────
    for cfg_key, tab_title in tab_map.items():
        headers = TABS[cfg_key]
        range_  = f"{tab_title}!A1:{_col_letter(len(headers))}1"
        existing = client._get(f"{tab_title}!A1:A1")
        if not existing or not existing[0]:
            log.info("Writing headers to %s …", tab_title)
            client._update(range_, [headers])
            # Apply header formatting
            _format_header_row(
                service,
                sheets_cfg.spreadsheet_id,
                existing_tabs[tab_title],
                len(headers),
            )
        else:
            log.info("Headers already present in %s — skipping.", tab_title)

    # ── Step 4: seed Categories if empty ──────────────────────────────────────
    cat_tab = sheets_cfg.categories_tab
    existing_cats = client._get(f"{cat_tab}!A2:A2")
    if not existing_cats or not existing_cats[0]:
        log.info("Seeding %s with %d default categories …", cat_tab, len(DEFAULT_CATEGORIES))
        client._append(f"{cat_tab}!A:G", DEFAULT_CATEGORIES)
    else:
        log.info("%s already has data — skipping seed.", cat_tab)

    # ── Step 5: seed Currency Codes if empty ──────────────────────────────────
    cur_tab = sheets_cfg.currency_tab
    existing_cur = client._get(f"{cur_tab}!A2:A2")
    if not existing_cur or not existing_cur[0]:
        log.info(
            "Seeding %s with %d currencies …", cur_tab, len(DEFAULT_CURRENCIES)
        )
        client._append(f"{cur_tab}!A:F", DEFAULT_CURRENCIES)
    else:
        log.info("%s already has data — skipping seed.", cur_tab)

    log.info("Setup complete.")
    print(f"\n✅  Google Sheet is ready.\n    {spreadsheet_url}\n")
    print(
        "Next steps:\n"
        "  1. Open the URL above and confirm the tabs and headers look correct.\n"
        "  2. Run the importer:\n"
        "       python3 -m finance.importer --dry-run\n"
        "  3. If the dry run looks right, run without --dry-run."
    )


# ── Sheets API helpers ────────────────────────────────────────────────────────

def _create_tab(service, spreadsheet_id: str, title: str) -> int:
    """Add a new sheet tab. Returns the new sheetId."""
    body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body=body
    ).execute()
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def _format_header_row(service, spreadsheet_id: str, sheet_id: int, n_cols: int):
    """Apply bold white text on dark-blue background to row 1."""
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId":          sheet_id,
                    "startRowIndex":    0,
                    "endRowIndex":      1,
                    "startColumnIndex": 0,
                    "endColumnIndex":   n_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": _HEADER_BG,
                        "textFormat": {
                            "foregroundColor": _HEADER_FG,
                            "bold":            True,
                            "fontSize":        10,
                            "fontFamily":      "Arial",
                        },
                        "horizontalAlignment": "CENTER",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            }
        },
        # Freeze header row
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId":     sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
    ]
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()
    except HttpError as e:
        log.warning("Could not format header row for sheetId %d: %s", sheet_id, e)


def _col_letter(n: int) -> str:
    """Convert 1-based column number to letter (1→A, 26→Z, 27→AA)."""
    result = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="One-time Google Sheet structure setup for Stage 2.",
    )
    parser.add_argument(
        "--config", metavar="PATH",
        help="Path to settings.toml (default: config/settings.toml)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    setup(args.config)


if __name__ == "__main__":
    main()
