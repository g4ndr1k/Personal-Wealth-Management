"""
One-time script: seed permanent Layer-1b (contains) aliases for merchants
that LLMs consistently miscategorize.

Run:   python3 -m finance._seed_aliases [--dry-run]
"""
from __future__ import annotations
import argparse
import logging
import sys
from datetime import date

from finance.config import load_config, get_sheets_config
from finance.sheets import SheetsClient

log = logging.getLogger(__name__)

# ── Permanent aliases (contains match, no owner/account filter) ────────────────
# Format: (alias_pattern, canonical_merchant, category)
PERMANENT_ALIASES: list[tuple[str, str, str]] = [
    # Home & household
    ("IKEA",           "IKEA",           "Household"),
    ("ACE HARDWARE",   "ACE Hardware",   "Household"),
    ("INFORMA",        "Informa",        "Household"),
    ("COURTS",         "Courts",         "Household"),
    # Travel — airlines
    ("CATHAY",         "Cathay Pacific", "Travel"),
    ("GARUDA",         "Garuda Indonesia","Travel"),
    ("CITILINK",       "Citilink",       "Travel"),
    ("LION AIR",       "Lion Air",       "Travel"),
    ("BATIK AIR",      "Batik Air",      "Travel"),
    ("AIRASIA",        "AirAsia",        "Travel"),
    ("SRIWIJAYA",      "Sriwijaya Air",  "Travel"),
    ("SUPER AIR JET",  "Super Air Jet",  "Travel"),
    ("WINGS AIR",      "Wings Air",      "Travel"),
    # Travel — booking platforms & accommodation
    ("AIRBNB",         "Airbnb",         "Travel"),
    ("BOOKING.COM",    "Booking.com",    "Travel"),
    ("AGODA",          "Agoda",          "Travel"),
    ("TRAVELOKA",      "Traveloka",      "Travel"),
    ("TIKET.COM",      "Tiket.com",      "Travel"),
    ("KLOOK",          "Klook",          "Travel"),
]


def main():
    parser = argparse.ArgumentParser(description="Seed permanent merchant aliases.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be added without writing to Sheets")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    cfg        = load_config()
    sheets_cfg = get_sheets_config(cfg)
    client     = SheetsClient(sheets_cfg)

    # Read existing aliases so we don't duplicate
    log.info("Reading existing aliases from Sheets …")
    existing = client.read_aliases()
    # Key on lower-cased alias pattern (column 'alias') + match_type
    existing_keys: set[tuple[str, str]] = {
        (r.get("alias", "").strip().lower(), r.get("match_type", "").strip().lower())
        for r in existing
    }
    log.info("  %d aliases already in Sheets.", len(existing_keys))

    added = 0
    skipped = 0
    today = date.today().isoformat()

    for alias_pat, merchant, category in PERMANENT_ALIASES:
        key = (alias_pat.lower(), "contains")
        if key in existing_keys:
            log.info("  SKIP  %-22s (already exists)", alias_pat)
            skipped += 1
            continue

        if args.dry_run:
            log.info("  [DRY] ADD  %-22s → %-22s  [%s]", alias_pat, merchant, category)
        else:
            client.append_alias(
                merchant=merchant,
                alias=alias_pat,
                category=category,
                match_type="contains",
            )
            log.info("  ADD   %-22s → %-22s  [%s]", alias_pat, merchant, category)
        added += 1

    print()
    if args.dry_run:
        print(f"[DRY RUN]  Would add {added} aliases, skip {skipped} (duplicates).")
    else:
        print(f"Done.  Added {added} aliases, skipped {skipped} (already present).")


if __name__ == "__main__":
    main()
