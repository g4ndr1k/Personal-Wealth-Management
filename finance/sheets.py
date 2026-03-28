"""
Google Sheets API v4 client for Stage 2 finance data.

Handles OAuth 2.0 token management (personal account), reading aliases /
categories / hashes, and writing transactions / aliases / import log rows.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from finance.config import SheetsConfig
from finance.models import FinanceTransaction

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ── Column headers (written by setup_sheets.py, matched here for reference) ──

TRANSACTIONS_HEADERS = [
    "date", "amount", "original_currency", "original_amount", "exchange_rate",
    "raw_description", "merchant", "category", "institution", "account",
    "owner", "notes", "hash", "import_date", "import_file",
]
# hash is column M (index 12, 1-based = 13)
HASH_COL_LETTER = "M"

ALIASES_HEADERS  = ["merchant", "alias", "category", "match_type", "added_date"]
CATEGORIES_HEADERS = ["category", "icon", "sort_order", "is_recurring", "monthly_budget"]
CURRENCY_HEADERS = [
    "currency_code", "currency_name", "symbol",
    "flag_emoji", "country_hints", "decimal_places",
]
IMPORT_LOG_HEADERS = [
    "import_date", "import_file", "rows_added",
    "rows_skipped", "rows_total", "duration_s", "notes",
]


class SheetsClient:
    """Thin wrapper around the Sheets API v4 for the finance package."""

    def __init__(self, cfg: SheetsConfig):
        self.cfg = cfg
        self._service = None

    # ── Service / auth ────────────────────────────────────────────────────────

    @property
    def service(self):
        if self._service is None:
            self._service = _build_service(self.cfg)
        return self._service

    def _get(self, range_: str) -> list[list]:
        """Read a range; returns list of rows (each row is a list of values)."""
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .get(spreadsheetId=self.cfg.spreadsheet_id, range=range_)
                .execute()
            )
            return result.get("values", [])
        except HttpError as e:
            log.error("Sheets read failed (%s): %s", range_, e)
            return []

    def _append(self, range_: str, rows: list[list]):
        """Append rows to a tab."""
        self.service.spreadsheets().values().append(
            spreadsheetId=self.cfg.spreadsheet_id,
            range=range_,
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

    def _update(self, range_: str, rows: list[list]):
        """Overwrite a specific range."""
        self.service.spreadsheets().values().update(
            spreadsheetId=self.cfg.spreadsheet_id,
            range=range_,
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()

    # ── Reads ─────────────────────────────────────────────────────────────────

    def read_existing_hashes(self) -> set[str]:
        """Return all hashes already in the Transactions tab (column M)."""
        rows = self._get(f"{self.cfg.transactions_tab}!{HASH_COL_LETTER}:{HASH_COL_LETTER}")
        return {r[0] for r in rows[1:] if r}  # skip header row

    def read_existing_hashes_with_rows(self) -> dict[str, list[int]]:
        """Return {hash: [sheet_row_numbers]} for --overwrite mode (1-indexed).

        A hash may appear multiple times in Sheets (e.g. identical ATM withdrawals
        on the same day re-imported from the XLSX).  Returning all row numbers lets
        overwrite_transactions update every duplicate so none are left empty.
        """
        rows = self._get(f"{self.cfg.transactions_tab}!{HASH_COL_LETTER}:{HASH_COL_LETTER}")
        result: dict[str, list[int]] = {}
        for i, row in enumerate(rows):
            if i == 0:
                continue  # skip header
            if row:
                result.setdefault(row[0], []).append(i + 1)  # Sheets rows are 1-indexed
        return result

    def read_aliases(self) -> list[dict]:
        """Return Merchant Aliases rows as list of dicts."""
        rows = self._get(f"{self.cfg.aliases_tab}!A:E")
        if len(rows) < 2:
            return []
        headers = [h.strip().lower() for h in rows[0]]
        return [
            dict(zip(headers, row + [""] * (len(headers) - len(row))))
            for row in rows[1:]
            if any(v.strip() for v in row if isinstance(v, str))
        ]

    def read_categories(self) -> list[str]:
        """Return list of category names from the Categories tab (column A)."""
        rows = self._get(f"{self.cfg.categories_tab}!A:A")
        return [r[0].strip() for r in rows[1:] if r and r[0].strip()]

    def read_currency_hints(self) -> dict[str, str]:
        """
        Return {country_hint_upper: currency_code} from the Currency Codes tab.
        e.g. {"US": "USD", "USA": "USD", "JP": "JPY", ...}
        """
        rows = self._get(f"{self.cfg.currency_tab}!A:F")
        hints: dict[str, str] = {}
        if len(rows) < 2:
            return hints
        for row in rows[1:]:
            if len(row) < 5:
                continue
            code = row[0].strip().upper()
            for hint in row[4].split(","):
                h = hint.strip().upper()
                if h:
                    hints[h] = code
        return hints

    # ── Writes ────────────────────────────────────────────────────────────────

    def append_transactions(self, txns: list[FinanceTransaction]) -> int:
        """Batch-append transactions to the Transactions tab. Returns row count written."""
        if not txns:
            return 0
        try:
            self._append(
                f"{self.cfg.transactions_tab}!A:O",
                [t.to_sheet_row() for t in txns],
            )
        except HttpError as e:
            log.error("Failed to append %d transactions: %s", len(txns), e)
            raise
        log.debug("Appended %d transactions.", len(txns))
        return len(txns)

    def overwrite_transactions(
        self,
        txns: list[FinanceTransaction],
        hash_to_row: dict[str, list[int]],
    ):
        """
        Update specific rows in the Transactions tab for --overwrite mode.
        Uses a single batchUpdate call (chunked at 500) instead of one API
        call per row, avoiding Sheets API rate-limit failures.
        Updates ALL rows that share a hash (handles duplicate rows in Sheets).
        Skips any transaction whose hash isn't in hash_to_row.
        """
        data = []
        for txn in txns:
            row_nums = hash_to_row.get(txn.hash)
            if not row_nums:
                continue
            sheet_row = txn.to_sheet_row()
            for row_num in row_nums:
                data.append({
                    "range": f"{self.cfg.transactions_tab}!A{row_num}:O{row_num}",
                    "values": [sheet_row],
                })

        if not data:
            return

        CHUNK = 500
        for i in range(0, len(data), CHUNK):
            chunk = data[i:i + CHUNK]
            try:
                self.service.spreadsheets().values().batchUpdate(
                    spreadsheetId=self.cfg.spreadsheet_id,
                    body={"valueInputOption": "RAW", "data": chunk},
                ).execute()
                log.debug("Batch-overwrote rows %d–%d", i + 1, i + len(chunk))
            except HttpError as e:
                log.error("Batch overwrite failed (chunk %d): %s", i // CHUNK, e)
                raise

    def append_alias(
        self,
        merchant: str,
        alias: str,
        category: str,
        match_type: str = "exact",
    ):
        """Append one row to the Merchant Aliases tab."""
        try:
            self._append(
                f"{self.cfg.aliases_tab}!A:E",
                [[merchant, alias, category, match_type,
                  datetime.now().strftime("%Y-%m-%d")]],
            )
        except HttpError as e:
            log.error("Failed to append alias (%s → %s): %s", alias, merchant, e)

    def log_import(
        self,
        import_file: str,
        rows_added: int,
        rows_skipped: int,
        rows_total: int,
        duration_s: float,
        notes: str = "",
    ):
        """Append one row to the Import Log tab."""
        try:
            self._append(
                f"{self.cfg.import_log_tab}!A:G",
                [[
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    import_file,
                    rows_added,
                    rows_skipped,
                    rows_total,
                    round(duration_s, 2),
                    notes,
                ]],
            )
        except HttpError as e:
            log.error("Failed to write import log: %s", e)


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _build_service(cfg: SheetsConfig):
    return build("sheets", "v4", credentials=_get_credentials(cfg))


def _get_credentials(cfg: SheetsConfig) -> Credentials:
    creds: Optional[Credentials] = None

    if os.path.exists(cfg.token_file):
        creds = Credentials.from_authorized_user_file(cfg.token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing Google OAuth token …")
            creds.refresh(Request())
        else:
            log.info(
                "No valid token found — starting OAuth consent flow.\n"
                "A browser window will open. Sign in with your personal Google account."
            )
            if not os.path.exists(cfg.credentials_file):
                raise FileNotFoundError(
                    f"Google credentials file not found: {cfg.credentials_file}\n"
                    "Download it from Google Cloud Console → APIs & Services → "
                    "Credentials → OAuth 2.0 Client ID → Desktop app → Download JSON."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                cfg.credentials_file, SCOPES
            )
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(cfg.token_file), exist_ok=True)
        with open(cfg.token_file, "w") as f:
            f.write(creds.to_json())
        log.info("OAuth token saved → %s", cfg.token_file)

    return creds
