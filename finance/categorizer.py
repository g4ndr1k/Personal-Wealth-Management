"""
Stage 2 — 4-layer expense categorization engine with account-aware matching.

Layer 1   Merchant alias exact match       → auto-assigns, no user input
Layer 1b  Merchant alias contains match    → auto-assigns, no user input
Layer 2   Regex pattern match              → auto-assigns, no user input
Layer 3   Ollama AI suggestion             → pre-fills review queue, user confirms
Layer 4   Review queue fallback            → blank entry, user types manually

Post-processing: cross-account internal transfer matching (Layer 0).

Alias rules support optional owner_filter and account_filter columns.
When set, the rule only matches if the transaction's owner/account matches.
Filtered (specific) rules are always checked before generic rules.

Confirmed Layer 3/4 entries are written back to the Merchant Aliases tab by
the caller (PWA → FastAPI), not by this module.  This module is read-only
with respect to Google Sheets.
"""
from __future__ import annotations
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Fallback category list used when the Sheets Categories tab is empty
DEFAULT_CATEGORIES = [
    # Housing & Bills
    "Housing", "Utilities", "Phone Bill", "Internet",
    # Food & Dining
    "Groceries", "Dining Out", "Delivery & Takeout",
    # Transportation
    "Auto", "Rideshare",
    # Lifestyle & Personal
    "Shopping", "Personal Care", "Entertainment", "Hobbies", "Subscriptions",
    # Health & Family
    "Healthcare", "Family", "Household", "Education", "Gifts & Donations",
    # Travel
    "Flights & Hotels", "Vacation Spending",
    # Financial & Legal
    "Fees & Interest", "Taxes",
    # System / Tracking
    "Income", "Dividends", "Interest Income", "Capital Gains",
    "Other Income", "Transfer", "Cash Withdrawal", "Adjustment", "Other",
]

# ── Legacy → new category name mapping ────────────────────────────────────────
# Applied during sync to migrate old category names on existing transactions.
CATEGORY_MIGRATION_MAP: dict[str, str] = {
    "Internal Transfer":  "Transfer",
    "External Transfer":  "Transfer",
    "Opening Balance":    "Adjustment",
    "Transport":          "Auto",
    "Household Expenses": "Household",
    "Child Support":      "Family",
    "Travel":             "Flights & Hotels",
}

def migrate_category(category: str | None) -> str | None:
    """Map legacy category names to their new equivalents."""
    if category and category in CATEGORY_MIGRATION_MAP:
        return CATEGORY_MIGRATION_MAP[category]
    return category


@dataclass
class CategorizationResult:
    merchant: Optional[str]   # None if not determined
    category: Optional[str]   # None if not determined (= review queue)
    layer: int                # 1=exact  2=regex  3=ollama  4=review
    confidence: str           # "auto" | "suggested" | "none"


class Categorizer:
    """
    4-layer categorization engine.

    Usage:
        cat = Categorizer(aliases, categories, ollama_host=..., ...)
        result = cat.categorize("STARBUCKS SENAYAN CITY", owner="Gandrik", account="2171138631")
        # CategorizationResult(merchant='Starbucks', category='Dining Out',
        #                      layer=1, confidence='auto')
    """

    def __init__(
        self,
        aliases: list[dict],
        categories: list[str],
        ollama_host: str = "http://localhost:11434",
        ollama_model: str = "llama3.2:3b",
        ollama_timeout: int = 60,
        anthropic_api_key: str = "",
        anthropic_model: str = "claude-haiku-4-20250514",
    ):
        self.categories = categories or DEFAULT_CATEGORIES[:]
        self.ollama_host = ollama_host.rstrip("/")
        self.ollama_model = ollama_model
        self.ollama_timeout = ollama_timeout
        self.anthropic_api_key = anthropic_api_key
        self.anthropic_model = anthropic_model

        # Layer 1: exact match  {UPPER_ALIAS: [(merchant, category, owner_filter, account_filter), ...]}
        self._exact: dict[str, list[tuple[str, str, str, str]]] = {}
        # Layer 1b: contains match  [(upper_substring, merchant, category, owner_filter, account_filter)]
        self._contains: list[tuple[str, str, str, str, str]] = []
        # Layer 2: regex match  [(compiled_pattern, merchant, category, owner_filter, account_filter)]
        self._regex: list[tuple[re.Pattern, str, str, str, str]] = []
        # Few-shot examples for Layer 3 (up to 10, FIFO)
        self._examples: list[tuple[str, str, str]] = []  # (desc, merchant, category)

        self._load_aliases(aliases)

    # ── Alias loading ─────────────────────────────────────────────────────────

    @staticmethod
    def _filters_match(
        owner_filter: str, account_filter: str,
        owner: str, account: str,
    ) -> bool:
        """Check whether owner/account filters pass for the given transaction."""
        if owner_filter and owner_filter.upper() != owner.upper():
            return False
        if account_filter and account_filter != account:
            return False
        return True

    def _load_aliases(self, aliases: list[dict]):
        for row in aliases:
            alias     = str(row.get("alias",        "")).strip()
            merchant  = str(row.get("merchant",     "")).strip()
            category  = str(row.get("category",     "")).strip()
            mtype     = str(row.get("match_type",   "exact")).strip().lower()
            owner_f   = str(row.get("owner_filter", "")).strip()
            account_f = str(row.get("account_filter", "")).strip()
            if not alias or not merchant:
                continue
            if mtype == "regex":
                try:
                    self._regex.append(
                        (re.compile(alias, re.IGNORECASE), merchant, category,
                         owner_f, account_f)
                    )
                except re.error as e:
                    log.warning("Invalid regex alias %r: %s", alias, e)
            elif mtype == "contains":
                self._contains.append(
                    (alias.upper(), merchant, category, owner_f, account_f)
                )
            else:
                self._exact.setdefault(alias.upper(), []).append(
                    (merchant, category, owner_f, account_f)
                )

        # Sort contains and regex: filtered (specific) rules first, generic last.
        # This ensures account-aware rules take priority when the same substring
        # matches — e.g. "TARIKAN ATM" for Helen/5500346622 → Household
        # before the generic ATM Withdrawal rule.
        def _specificity(rule):
            return (0 if (rule[3] or rule[4]) else 1)  # filtered=0, generic=1

        self._contains.sort(key=_specificity)
        self._regex.sort(key=_specificity)

        # Also sort exact match entries: filtered first per alias key
        for key_entries in self._exact.values():
            key_entries.sort(key=lambda e: (0 if (e[2] or e[3]) else 1))

    def reload_aliases(self, aliases: list[dict]):
        """Replace all alias rules (call after pulling fresh data from Sheets)."""
        self._exact.clear()
        self._contains.clear()
        self._regex.clear()
        self._load_aliases(aliases)
        log.debug(
            "Aliases reloaded: %d exact, %d contains, %d regex",
            len(self._exact), len(self._contains), len(self._regex),
        )

    # ── Few-shot example management ───────────────────────────────────────────

    def add_confirmed_example(
        self, raw_description: str, merchant: str, category: str
    ):
        """
        Record a user-confirmed (merchant, category) as a few-shot example
        for the Ollama prompt.  Oldest examples are evicted past 10.
        """
        self._examples.append((raw_description, merchant, category))
        if len(self._examples) > 10:
            self._examples = self._examples[-10:]

    # ── Main entry point ──────────────────────────────────────────────────────

    def categorize(
        self,
        raw_description: str,
        owner: str = "",
        account: str = "",
    ) -> CategorizationResult:
        """
        Run the 4-layer pipeline and return a CategorizationResult.

        Args:
            raw_description: Verbatim transaction description from the statement.
            owner:   Transaction owner (e.g. "Gandrik", "Helen") — used for
                     account-aware alias filtering.
            account: Account/card number — used for account-aware alias filtering.

        Layers 1 and 2 return confidence="auto" — the caller should write these
        directly to the Transactions tab without user interaction.

        Layers 3 and 4 return confidence="suggested"/"none" — the caller should
        surface these in the PWA review queue for user confirmation.
        """
        desc = raw_description.strip()

        # ── Layer 1: exact match ──────────────────────────────────────────────
        key = desc.upper()
        if key in self._exact:
            for merchant, category, of, af in self._exact[key]:
                if self._filters_match(of, af, owner, account):
                    log.debug("L1 exact: %r → %s / %s", desc, merchant, category)
                    return CategorizationResult(merchant, category, layer=1, confidence="auto")

        # ── Layer 1b: contains match ─────────────────────────────────────────
        for substring, merchant, category, of, af in self._contains:
            if substring in key and self._filters_match(of, af, owner, account):
                log.debug("L1b contains: %r → %s / %s", desc, merchant, category)
                return CategorizationResult(merchant, category, layer=1, confidence="auto")

        # ── Layer 2: regex match ──────────────────────────────────────────────
        for pattern, merchant, category, of, af in self._regex:
            if pattern.search(desc) and self._filters_match(of, af, owner, account):
                log.debug("L2 regex: %r → %s / %s", desc, merchant, category)
                return CategorizationResult(merchant, category, layer=2, confidence="auto")

        # ── Layer 3: AI suggestion (Ollama primary, Claude fallback) ──────────
        # Ollama runs locally with no API cost.  Claude is used as a fallback
        # if an ANTHROPIC_API_KEY is configured and Ollama fails/is unavailable.
        suggestion = self._ollama_suggest(desc)
        if suggestion:
            merchant, category = suggestion
            log.debug("L3 ollama: %r → %s / %s", desc, merchant, category)
            return CategorizationResult(
                merchant, category, layer=3, confidence="suggested"
            )

        suggestion = self._anthropic_suggest(desc)
        if suggestion:
            merchant, category = suggestion
            log.debug("L3 claude: %r → %s / %s", desc, merchant, category)
            return CategorizationResult(
                merchant, category, layer=3, confidence="suggested"
            )

        # ── Layer 4: review queue ─────────────────────────────────────────────
        log.debug("L4 review: %r → no suggestion", desc)
        return CategorizationResult(None, None, layer=4, confidence="none")

    # ── Shared prompt builder ─────────────────────────────────────────────────

    def _build_prompt(self, desc: str) -> str:
        """Build the categorization prompt used by both Ollama and Anthropic."""
        if self._examples:
            examples_text = "\n".join(
                f'- "{d}" → {m}, {c}' for d, m, c in self._examples
            )
        else:
            examples_text = (
                '- "GRAB* A8NPTNG SOUTH JAKARTA" → Grab, Rideshare\n'
                '- "NETFLIX.COM" → Netflix, Subscriptions\n'
                '- "INDOMARET" → Indomaret, Groceries\n'
                '- "IKEA ALAM SUTERA" → IKEA, Household\n'
                '- "CATHAY PACIFIC AIRWAYS" → Cathay Pacific, Flights & Hotels\n'
                '- "SINGAPORE AIRLINES" → Singapore Airlines, Flights & Hotels\n'
                '- "AIRBNB * XYZ" → Airbnb, Flights & Hotels\n'
                '- "ZARA GRAND INDONESIA" → Zara, Shopping\n'
                '- "CANVA* 12345" → Canva, Subscriptions\n'
                '- "STEAM PURCHASE" → Steam, Entertainment\n'
                '- "GRABFOOD" → GrabFood, Delivery & Takeout\n'
                '- "GOJEK GORIDE" → Gojek, Rideshare\n'
                '- "PERTAMINA SPBU" → Pertamina, Auto'
            )

        categories_text = ", ".join(self.categories)

        return (
            "You are a personal finance categorizer for an Indonesian household.\n\n"
            f"Available categories: {categories_text}\n\n"
            "Category guidance (use these rules to choose precisely):\n"
            "- Auto: fuel (SPBU, Pertamina), vehicle repairs, registration, insurance, parking, toll\n"
            "- Rideshare: ride-hailing apps (Grab, Gojek, Uber) for transport\n"
            "- Delivery & Takeout: food delivery apps (GrabFood, GoFood, ShopeeFood) — track delivery fees separately\n"
            "- Flights & Hotels: airlines, airports, hotels, Airbnb, travel agencies, booking platforms\n"
            "- Vacation Spending: food, transport, and activities while on vacation (overseas transactions)\n"
            "- Household: home furnishings, appliances, hardware (IKEA, ACE Hardware, Informa, Depo Bangunan), cleaning supplies\n"
            "- Shopping: fashion, accessories, electronics, general retail (Zara, H&M, Uniqlo, Tokopedia, Shopee)\n"
            "- Groceries: supermarkets, convenience stores, fresh produce (Indomaret, Alfamart, Ranch Market, Grand Lucky)\n"
            "- Subscriptions: recurring digital services with a period or reference code (Netflix, Spotify, Canva, Adobe, iCloud)\n"
            "- Dining Out: restaurants, cafes, fast food, bars, coffee shops (Starbucks, McDonald's)\n"
            "- Healthcare: clinics, hospitals, pharmacies, dental, lab tests\n"
            "- Education: schools, courses, tutoring, books, stationery, school fees\n"
            "- Entertainment: gaming, cinema, concerts, hobbies (Steam, PlayStation Store)\n"
            "- Personal Care: haircuts, gym, skincare, spa\n"
            "- Family: childcare expenses\n"
            "- Phone Bill: mobile phone bills\n"
            "- Internet: broadband / WiFi bills\n"
            "- Income: salary, wages, bonuses\n"
            "- Dividends: stock dividend payouts\n"
            "- Interest Income: savings account interest, bond coupons\n"
            "- Capital Gains: profit from selling stocks or assets\n"
            "- Other Income: tax refunds, gifts received, side hustles\n"
            "- Transfer: internal account transfers (savings↔checking) and external (paying credit cards)\n"
            "- Adjustment: opening balances or balance corrections\n\n"
            "Rules:\n"
            "- Extract a clean merchant name (remove location suffixes, reference codes, asterisks).\n"
            "- If the raw text is an airline name, always use Flights & Hotels — not Auto or Rideshare.\n"
            "- If the raw text contains a country or city code at the end (e.g. NLD, GBR, USA, KL), "
            "it is likely a foreign transaction; classify by the merchant type, not the location.\n"
            "- GrabFood/GoFood → Delivery & Takeout; GrabCar/GoBike → Rideshare.\n\n"
            f"Recent confirmed examples:\n{examples_text}\n\n"
            f'Transaction: "{desc}"\n\n'
            'Reply with JSON only, no explanation: {"merchant": "...", "category": "..."}'
        )

    # ── Ollama ────────────────────────────────────────────────────────────────

    def _ollama_suggest(self, desc: str) -> Optional[tuple[str, str]]:
        """
        Ask Ollama for a (merchant, category) suggestion.
        Returns None if Ollama is unavailable, times out, or returns garbage.
        """
        prompt = self._build_prompt(desc)
        payload = json.dumps({
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1},  # low temp for deterministic output
        }).encode()

        try:
            req = urllib.request.Request(
                f"{self.ollama_host}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.ollama_timeout) as resp:
                data = json.loads(resp.read())

            raw_response = data.get("response", "").strip()
            return self._parse_ollama_response(raw_response, desc)

        except urllib.error.URLError as e:
            log.debug("Ollama unreachable for %r: %s", desc, e)
        except TimeoutError:
            log.debug("Ollama timed out for %r", desc)
        except (json.JSONDecodeError, KeyError) as e:
            log.debug("Ollama bad response for %r: %s", desc, e)
        except Exception as e:
            log.debug("Ollama unexpected error for %r: %s", desc, e)

        return None

    def _anthropic_suggest(self, desc: str) -> Optional[tuple[str, str]]:
        """
        Ask Anthropic Claude for a (merchant, category) suggestion.
        Primary Layer 3 provider when the API key is configured; Ollama is
        the fallback for offline/air-gapped environments.
        Returns None if the API key is missing, the call fails, or the
        response cannot be parsed.
        """
        if not self.anthropic_api_key:
            return None

        prompt = self._build_prompt(desc)

        payload = json.dumps({
            "model": self.anthropic_model,
            "max_tokens": 100,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": self.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            raw_response = data["content"][0]["text"].strip()
            return self._parse_ollama_response(raw_response, desc)

        except urllib.error.URLError as e:
            log.debug("Anthropic unreachable for %r: %s", desc, e)
        except TimeoutError:
            log.debug("Anthropic timed out for %r", desc)
        except (json.JSONDecodeError, KeyError) as e:
            log.debug("Anthropic bad response for %r: %s", desc, e)
        except Exception as e:
            log.debug("Anthropic unexpected error for %r: %s", desc, e)

        return None

    def _parse_ollama_response(
        self, raw: str, desc: str
    ) -> Optional[tuple[str, str]]:
        """Extract (merchant, category) from Ollama's text response."""
        # Find the first {...} block in the response
        m = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        if not m:
            log.debug("Ollama: no JSON found in response for %r: %.120s", desc, raw)
            return None

        try:
            parsed = json.loads(m.group())
        except json.JSONDecodeError as e:
            log.debug("Ollama: JSON parse error for %r: %s", desc, e)
            return None

        merchant = str(parsed.get("merchant", "")).strip()
        category = str(parsed.get("category", "")).strip()

        if not merchant or not category:
            return None

        # Validate / normalise category against known list
        if category not in self.categories:
            case_match = next(
                (c for c in self.categories if c.lower() == category.lower()),
                None,
            )
            if case_match:
                category = case_match
            else:
                log.debug(
                    "Ollama returned unknown category %r for %r — falling back to Other",
                    category, desc,
                )
                category = "Other"

        return merchant, category


# ── Cross-account internal transfer matching ─────────────────────────────────

# Known internal account pairs: (owner_a, account_a) ↔ (owner_b, account_b)
# Transfers between these pairs should be categorised as "Transfer".
INTERNAL_ACCOUNT_PAIRS: list[tuple[tuple[str, str], tuple[str, str]]] = [
    # Gandrik BCA ↔ Helen BCA (monthly household allowance)
    (("Gandrik", "2171138631"), ("Helen", "5500346622")),
    # Helen Permata ↔ Helen BCA (savings ↔ spending)
    (("Helen", "4123968773"), ("Helen", "2684118322")),
    # Helen Permata ↔ Gandrik Permata
    (("Helen", "4123968773"), ("Gandrik", "4123968447")),
]

# ── Helen BCA cash withdrawal → Household ────────────────────────────────────
# Cash withdrawals from Helen's BCA account are household expenses.
HELEN_BCA_HOUSEHOLD_ACCOUNT = ("Helen", "5500346622")

_TRANSFER_DESCRIPTION_HINTS = (
    "TRSF E-BANKING ",
    "TRF INCOMING ",
    "TRF BIFAST ",
    "TRF LLG ",
    "TRF KE ",
    "TRF DARI ",
    "PB DARI ",
    "PB KE ",
    "BI-FAST ",
)


def _looks_like_transfer(txn) -> bool:
    """Require transfer-like statement text before pairing two accounts."""
    desc = (getattr(txn, "raw_description", "") or "").upper()
    return any(hint in desc for hint in _TRANSFER_DESCRIPTION_HINTS)


def match_internal_transfers(transactions: list) -> int:
    """
    Post-processing: detect matching debit/credit pairs across internal accounts
    and re-categorise both sides as "Transfer".

    Also re-categorises cash withdrawals from Helen BCA (5500346622) as
    "Household" (daily household spending in cash).

    Each transaction must have attributes: date, amount, owner, account,
    category, merchant, raw_description.

    Returns the number of transactions re-categorised.
    """
    # Build lookup: (owner, account, date, abs_amount) → list[txn]
    from collections import defaultdict
    by_key: dict[tuple, list] = defaultdict(list)

    for txn in transactions:
        key = (txn.owner, txn.account, txn.date, abs(txn.amount))
        by_key[key].append(txn)

    matched = 0
    seen = set()  # avoid double-counting

    for (owner_a, acct_a), (owner_b, acct_b) in INTERNAL_ACCOUNT_PAIRS:
        for txn in transactions:
            if id(txn) in seen:
                continue
            # Check if this txn is a debit from account A
            if (txn.owner == owner_a and txn.account == acct_a
                    and txn.amount < 0 and _looks_like_transfer(txn)):
                # Look for matching credit in account B (same date, same abs amount)
                counterpart_key = (owner_b, acct_b, txn.date, abs(txn.amount))
                counterparts = by_key.get(counterpart_key, [])
                for cp in counterparts:
                    if cp.amount > 0 and id(cp) not in seen and _looks_like_transfer(cp):
                        # Found a matching credit — mark both as Transfer
                        for t in (txn, cp):
                            if t.category != "Transfer":
                                t.merchant = "Transfer"
                                t.category = "Transfer"
                                matched += 1
                        seen.add(id(txn))
                        seen.add(id(cp))
                        break

            # Check the reverse direction (debit from B, credit to A)
            elif (txn.owner == owner_b and txn.account == acct_b
                    and txn.amount < 0 and _looks_like_transfer(txn)):
                counterpart_key = (owner_a, acct_a, txn.date, abs(txn.amount))
                counterparts = by_key.get(counterpart_key, [])
                for cp in counterparts:
                    if cp.amount > 0 and id(cp) not in seen and _looks_like_transfer(cp):
                        for t in (txn, cp):
                            if t.category != "Transfer":
                                t.merchant = "Transfer"
                                t.category = "Transfer"
                                matched += 1
                        seen.add(id(txn))
                        seen.add(id(cp))
                        break

    # ── Helen BCA cash withdrawals → Household ───────────────────────────────
    # Any cash withdrawal from Helen's BCA account (5500346622) is re-categorised
    # as "Household" because she uses ATM cash for daily household spending.
    h_owner, h_account = HELEN_BCA_HOUSEHOLD_ACCOUNT
    _ATM_HINTS = ("TARIKAN ATM", "TARIKAN TUNAI", "CASH WITHDRAWAL", "CW-ATM")
    for txn in transactions:
        if id(txn) in seen:
            continue
        if (txn.owner == h_owner and txn.account == h_account
                and txn.amount < 0
                and txn.category == "Cash Withdrawal"):
            desc_upper = (getattr(txn, "raw_description", "") or "").upper()
            if any(hint in desc_upper for hint in _ATM_HINTS):
                txn.category = "Household"
                txn.merchant = "Cash (Household)"
                matched += 1
                seen.add(id(txn))

    if matched:
        log.info("Cross-account matching: %d transactions re-categorised", matched)

    return matched
