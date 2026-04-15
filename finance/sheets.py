"""
Google Sheets API v4 client for Stage 2 finance data.

Handles OAuth 2.0 token management (personal account), reading aliases /
categories / hashes, and writing transactions / aliases / import log rows.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
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

ALIASES_HEADERS  = ["merchant", "alias", "category", "match_type", "added_date", "owner_filter", "account_filter"]
CATEGORIES_HEADERS = ["category", "icon", "sort_order", "is_recurring", "monthly_budget"]
CURRENCY_HEADERS = [
    "currency_code", "currency_name", "symbol",
    "flag_emoji", "country_hints", "decimal_places",
]
IMPORT_LOG_HEADERS = [
    "import_date", "import_file", "rows_added",
    "rows_skipped", "rows_total", "duration_s", "notes",
]
OVERRIDES_HEADERS = [
    "hash", "category", "notes", "updated_at",
    "txn_date", "txn_amount", "txn_description",
    "txn_institution", "txn_account", "txn_owner",
]
PDF_IMPORT_LOG_HEADERS = [
    "month", "label", "expected", "actual", "status", "files", "last_processed",
]
HOLDINGS_HEADERS = [
    "snapshot_date", "asset_class", "asset_group", "asset_name", "institution",
    "owner", "currency", "market_value_idr", "cost_basis_idr",
    "unrealised_pnl_idr", "last_appraised_date", "notes", "import_date",
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

    def _invalidate_service(self):
        """Reset the cached service so it is rebuilt on next access."""
        self._service = None

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
            if e.resp.status == 401:
                self._invalidate_service()
            log.error("Sheets read failed (%s): %s", range_, e)
            return []

    def _append(self, range_: str, rows: list[list]):
        """Append rows to a tab."""
        try:
            self.service.spreadsheets().values().append(
                spreadsheetId=self.cfg.spreadsheet_id,
                range=range_,
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            ).execute()
        except HttpError as e:
            if e.resp.status == 401:
                self._invalidate_service()
            raise

    def _update(self, range_: str, rows: list[list]):
        """Overwrite a specific range."""
        try:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.cfg.spreadsheet_id,
                range=range_,
                valueInputOption="RAW",
                body={"values": rows},
            ).execute()
        except HttpError as e:
            if e.resp.status == 401:
                self._invalidate_service()
            raise

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

    def read_existing_categories(self) -> dict[str, str]:
        """Return {hash: category} for all rows in the Transactions tab that
        have a non-empty category. Used by --overwrite to avoid replacing
        manually-set categories with null when the categorizer can't match."""
        rows = self._get(f"{self.cfg.transactions_tab}!H:M")  # category(H) … hash(M)
        result: dict[str, str] = {}
        for i, row in enumerate(rows):
            if i == 0:
                continue  # skip header
            r = list(row) + [""] * (6 - len(row))
            category = (r[0] or "").strip()   # col H
            tx_hash  = (r[5] or "").strip()   # col M (H+5 = M)
            if tx_hash and category:
                result[tx_hash] = category
        return result

    def read_aliases(self) -> list[dict]:
        """Return Merchant Aliases rows as list of dicts (columns A–G)."""
        rows = self._get(f"{self.cfg.aliases_tab}!A:G")
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

    # ── Category Overrides ──────────────────────────────────────────────────

    def read_overrides(self) -> dict[str, dict]:
        """Return {hash: {"category": ..., "notes": ..., "updated_at": ...}}
        from the Category Overrides tab."""
        qtab = f"'{self.cfg.overrides_tab}'"
        rows = self._get(f"{qtab}!A:J")
        result: dict[str, dict] = {}
        if len(rows) < 2:
            return result
        for row in rows[1:]:
            r = list(row) + [""] * (10 - len(row))
            h = (r[0] or "").strip()
            cat = (r[1] or "").strip()
            if h and cat:
                result[h] = {
                    "category":   cat,
                    "notes":      (r[2] or "").strip(),
                    "updated_at": (r[3] or "").strip(),
                    "txn_date":        (r[4] or "").strip(),
                    "txn_amount":      (r[5] or "").strip(),
                    "txn_description": (r[6] or "").strip(),
                    "txn_institution": (r[7] or "").strip(),
                    "txn_account":     (r[8] or "").strip(),
                    "txn_owner":       (r[9] or "").strip(),
                }
        return result

    def write_override(
        self,
        tx_hash: str,
        category: str,
        notes: str = "",
        *,
        txn_date: str = "",
        txn_amount: str = "",
        txn_description: str = "",
        txn_institution: str = "",
        txn_account: str = "",
        txn_owner: str = "",
    ):
        """Append or update one row in the Category Overrides tab.

        If the hash already exists, overwrites that row in-place.
        Otherwise appends a new row.

        Columns A–J: hash, category, notes, updated_at,
                     txn_date, txn_amount, txn_description,
                     txn_institution, txn_account, txn_owner.

        KNOWN LIMITATION: Read-then-write race condition.  Two concurrent
        requests for the same hash could both see "not found" and both
        append, creating a duplicate.  Google Sheets API does not support
        conditional writes.  Mitigated by: (1) single-user personal app,
        (2) the dedup scan below removes extras on each call.
        """
        qtab = f"'{self.cfg.overrides_tab}'"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        new_row = [
            tx_hash, category, notes, now,
            txn_date, txn_amount, txn_description,
            txn_institution, txn_account, txn_owner,
        ]

        # Check if hash already exists so we can overwrite instead of duplicating
        rows = self._get(f"{qtab}!A:A")
        for i, row in enumerate(rows):
            if i == 0:
                continue  # skip header
            if row and row[0].strip() == tx_hash:
                # Overwrite existing row
                self._update(
                    f"{qtab}!A{i + 1}:J{i + 1}",
                    [new_row],
                )
                log.info("Override updated: %s → %s", tx_hash[:16], category)
                return

        # Hash not found — append new row
        self._append(f"{qtab}!A:J", [new_row])
        log.info("Override added: %s → %s", tx_hash[:16], category)

    def ensure_overrides_tab(self):
        """Create the Category Overrides tab with headers if it doesn't exist."""
        tab = self.cfg.overrides_tab
        # Check if tab exists by listing all sheets in the spreadsheet
        meta = self.service.spreadsheets().get(
            spreadsheetId=self.cfg.spreadsheet_id,
            fields="sheets.properties.title",
        ).execute()
        existing_tabs = {s["properties"]["title"] for s in meta.get("sheets", [])}

        if tab not in existing_tabs:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.cfg.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
            ).execute()
            log.info("Created tab: %s", tab)

        # Write headers if row 1 is empty
        qtab = f"'{tab}'"
        existing = self._get(f"{qtab}!A1:J1")
        if not existing or not existing[0]:
            self._update(f"{qtab}!A1:J1", [OVERRIDES_HEADERS])
            log.info("Wrote headers to %s", tab)

    def append_alias(
        self,
        merchant: str,
        alias: str,
        category: str,
        match_type: str = "exact",
        owner_filter: str = "",
        account_filter: str = "",
    ):
        """Append one row to the Merchant Aliases tab (columns A–G)."""
        try:
            self._append(
                f"{self.cfg.aliases_tab}!A:G",
                [[merchant, alias, category, match_type,
                  datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                  owner_filter, account_filter]],
            )
        except HttpError as e:
            raise RuntimeError(f"Sheets alias write failed: {e}") from e

    def update_alias_category(self, alias: str, new_category: str):
        """Update the category column for an existing alias row (matched by alias column B)."""
        try:
            rows = self._get(f"{self.cfg.aliases_tab}!A:G")
            for i, row in enumerate(rows):
                if i == 0:
                    continue  # skip header
                r = list(row) + [""] * (7 - len(row))
                if r[1].strip() == alias:
                    self._update(
                        f"{self.cfg.aliases_tab}!C{i + 1}",
                        [[new_category]],
                    )
                    log.info("Updated alias category row %d: %s → %s", i + 1, alias[:40], new_category)
                    return
            log.warning("Alias not found for update: %s", alias[:40])
        except HttpError as e:
            raise RuntimeError(f"Sheets alias category update failed: {e}") from e

    def write_pdf_import_log(self, rows: list[list]):
        """Rewrite all data rows in the PDF Import Log tab.

        Clears all existing data rows (keeping row 1 header), then appends the
        new rows.  Safe to call repeatedly — always produces a clean snapshot.
        """
        tab = self.cfg.pdf_import_log_tab
        qtab = f"'{tab}'"
        try:
            self.service.spreadsheets().values().clear(
                spreadsheetId=self.cfg.spreadsheet_id,
                range=f"{qtab}!A2:G",
                body={},
            ).execute()
        except HttpError as e:
            log.warning("Could not clear PDF Import Log rows: %s", e)

        if rows:
            try:
                self._append(f"{qtab}!A:G", rows)
                log.info("PDF Import Log: wrote %d rows.", len(rows))
            except HttpError as e:
                log.error("Failed to write PDF Import Log: %s", e)
                raise

    def write_holdings(self, rows: list[list]):
        """Rewrite all data rows in the Holdings tab.

        Clears existing data rows (keeping row 1 header) then writes the new rows.
        Creates the tab with headers if it doesn't exist.  Safe to call repeatedly.
        """
        tab  = self.cfg.holdings_tab
        qtab = f"'{tab}'"

        # Ensure tab exists
        try:
            meta = self.service.spreadsheets().get(
                spreadsheetId=self.cfg.spreadsheet_id,
                fields="sheets.properties.title",
            ).execute()
            existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
            if tab not in existing:
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.cfg.spreadsheet_id,
                    body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
                ).execute()
                log.info("Created Holdings tab: %s", tab)
        except HttpError as e:
            log.warning("Could not check/create Holdings tab: %s", e)

        # Write header row if empty
        try:
            hdr = self._get(f"{qtab}!A1:M1")
            if not hdr or not hdr[0]:
                self._update(f"{qtab}!A1:M1", [HOLDINGS_HEADERS])
        except HttpError as e:
            log.warning("Could not write Holdings headers: %s", e)

        # Clear existing data rows
        try:
            self.service.spreadsheets().values().clear(
                spreadsheetId=self.cfg.spreadsheet_id,
                range=f"{qtab}!A2:M",
                body={},
            ).execute()
        except HttpError as e:
            log.warning("Could not clear Holdings rows: %s", e)

        if rows:
            try:
                self._append(f"{qtab}!A:M", rows)
                log.info("Holdings tab: wrote %d rows.", len(rows))
            except HttpError as e:
                log.error("Failed to write Holdings tab: %s", e)
                raise

    def delete_rows_by_institution(self, institution: str) -> int:
        """Delete all rows in the Transactions tab where institution == institution.

        Returns the number of rows deleted.
        Uses batchUpdate deleteDimension (reverse order to preserve row indices).
        Column I (index 8, 0-based) = institution.
        """
        rows = self._get(f"{self.cfg.transactions_tab}!A:O")
        to_delete: list[int] = []
        for i, row in enumerate(rows):
            if i == 0:
                continue  # skip header
            r = list(row) + [""] * (15 - len(row))
            if r[8].strip() == institution:
                to_delete.append(i + 1)  # 1-indexed sheet row

        if not to_delete:
            log.info("No rows found for institution='%s'", institution)
            return 0

        sheet_id = self._get_transactions_sheet_id()
        # Build deleteDimension requests in REVERSE order so earlier row indices
        # aren't shifted by deletions of earlier rows.
        requests = []
        for row_num in sorted(to_delete, reverse=True):
            requests.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": row_num - 1,  # 0-indexed inclusive
                        "endIndex": row_num,         # 0-indexed exclusive
                    }
                }
            })

        CHUNK = 500
        deleted = 0
        for i in range(0, len(requests), CHUNK):
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.cfg.spreadsheet_id,
                body={"requests": requests[i:i + CHUNK]},
            ).execute()
            deleted += len(requests[i:i + CHUNK])

        log.info("Deleted %d rows where institution='%s'", deleted, institution)
        return deleted

    def _get_transactions_sheet_id(self) -> int:
        """Return the numeric sheetId for the transactions tab."""
        meta = self.service.spreadsheets().get(
            spreadsheetId=self.cfg.spreadsheet_id,
            fields="sheets.properties",
        ).execute()
        tab_name = self.cfg.transactions_tab
        for sheet in meta.get("sheets", []):
            if sheet["properties"]["title"] == tab_name:
                return sheet["properties"]["sheetId"]
        raise ValueError(f"Transactions tab not found: {tab_name}")

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
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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


def _get_credentials(cfg: SheetsConfig):
    # ── Keychain support ────────────────────────────────────────────────────
    # If google_sheets.source = "keychain" in settings.toml, read credentials
    # from macOS Keychain instead of files. OAuth tokens are also written
    # back to Keychain on refresh.
    _use_keychain = _keychain_enabled()

    # ── Service account (preferred) ─────────────────────────────────────────
    if cfg.service_account_file:
        if _use_keychain:
            sa_json = _read_keychain_json("google_service_account_json")
            if sa_json:
                log.info("Using Google service account from Keychain")
                return ServiceAccountCredentials.from_service_account_info(
                    sa_json, scopes=SCOPES
                )
        if os.path.exists(cfg.service_account_file):
            log.info("Using Google service account credentials: %s", cfg.service_account_file)
            return ServiceAccountCredentials.from_service_account_file(
                cfg.service_account_file,
                scopes=SCOPES,
            )
        raise FileNotFoundError(
            f"Google service account file not found: {cfg.service_account_file}\n"
            "Create a service account key JSON in Google Cloud Console, save it locally, "
            "then share the target spreadsheet with the service account email."
        )

    # ── OAuth fallback ──────────────────────────────────────────────────────
    creds: Optional[Credentials] = None

    # Try Keychain first, then file
    if _use_keychain:
        tok_json = _read_keychain_json("google_token_json")
        if tok_json:
            creds = Credentials.from_authorized_user_info(tok_json, SCOPES)
            log.info("Loaded Google OAuth token from Keychain")

    if not creds and os.path.exists(cfg.token_file):
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
            # Try Keychain for credentials, then file
            client_json = None
            if _use_keychain:
                client_json = _read_keychain_json("google_credentials_json")
            if client_json:
                flow = InstalledAppFlow.from_client_config(client_json, SCOPES)
            else:
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

        # Write back: Keychain + file (for compatibility)
        if _use_keychain:
            _write_keychain_json("google_token_json", creds.to_json())
            log.info("OAuth token saved → Keychain")
        os.makedirs(os.path.dirname(cfg.token_file), exist_ok=True)
        with open(cfg.token_file, "w") as f:
            f.write(creds.to_json())
        log.info("OAuth token saved → %s", cfg.token_file)

    return creds


# ── Keychain helpers ────────────────────────────────────────────────────────

def _keychain_enabled() -> bool:
    """Check if Keychain source is enabled for Google Sheets in settings.toml."""
    try:
        import tomllib
        from pathlib import Path
        settings_path = Path(__file__).resolve().parent.parent / "config" / "settings.toml"
        if not settings_path.exists():
            return False
        with open(settings_path, "rb") as f:
            settings = tomllib.load(f)
        return settings.get("google_sheets", {}).get("source", "file") == "keychain"
    except Exception:
        return False


def _read_keychain_json(key: str) -> dict | None:
    """Read a JSON blob from macOS Keychain and parse it."""
    try:
        from bridge.secret_manager import get_from_keychain, _keychain_service
        settings = _load_settings_for_keychain()
        service = _keychain_service(settings)
        raw = get_from_keychain(service, key)
        if raw:
            import json as _json
            return _json.loads(raw)
    except Exception:
        pass
    return None


def _write_keychain_json(key: str, json_str: str) -> bool:
    """Write a JSON string to macOS Keychain."""
    try:
        from bridge.secret_manager import set_in_keychain, _keychain_service
        settings = _load_settings_for_keychain()
        service = _keychain_service(settings)
        return set_in_keychain(service, key, json_str)
    except Exception:
        return False


def _load_settings_for_keychain() -> dict:
    """Load settings.toml for Keychain helper functions."""
    try:
        import tomllib
        from pathlib import Path
        sp = Path(__file__).resolve().parent.parent / "config" / "settings.toml"
        if sp.exists():
            with open(sp, "rb") as f:
                return tomllib.load(f)
    except Exception:
        pass
    return {}
