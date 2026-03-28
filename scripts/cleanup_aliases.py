#!/usr/bin/env python3
"""
One-time cleanup of Merchant Aliases tab.

1. Removes exact matches already covered by existing regex rules
2. Converts date/month-specific exact matches to regex/contains
3. Adds new contains/regex rules for uncategorised transaction patterns
4. Removes conflicting duplicates
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from finance.config import load_config, get_sheets_config
from finance.sheets import SheetsClient

cfg = load_config()
sc = SheetsClient(get_sheets_config(cfg))

# ── Read current aliases ──────────────────────────────────────────────────────
rows = sc._get(f"{sc.cfg.aliases_tab}!A:E")
header = rows[0]
data = rows[1:]
print(f"Read {len(data)} alias rows")

# ── Build new alias list ──────────────────────────────────────────────────────
today = datetime.now().strftime("%Y-%m-%d")

# --- REGEX rules (existing + new consolidated) ---
regex_rules = [
    # Existing regex rules (kept as-is)
    ["Grab",                        r"^Grab\*",                                         "Transport",          "regex"],
    ["ATM Withdrawal",              r"^TARIKAN ATM",                                    "Cash Withdrawal",    "regex"],
    ["Debit Card Transaction",      r"^TRANSAKSI DEBIT TGL:",                           "Other",              "regex"],
    ["Internal Transfer",           r"^BI-FAST DB BIF TRANSFER",                        "Internal Transfer",  "regex"],
    ["External Transfer",           r"^BI-FAST CR BIF TRANSFER",                        "External Transfer",  "regex"],
    ["BI-Fast Transaction Fee",     r"^BI-FAST DB BIF BIAYA TXN",                       "Fees & Interest",    "regex"],
    ["Internal Transfer",           r"^TRSF E-BANKING CR \d{4}/ATBLK/WS9$",            "Internal Transfer",  "regex"],
    ["External Transfer",           r"^TRSF E-BANKING CR \d{4}/[A-Z]+/WS9$",           "External Transfer",  "regex"],
    ["External Transfer",           r"^TRSF E-BANKING DB \d{4}/FTSCY/WS9$",            "External Transfer",  "regex"],
    ["Airbnb",                      r"^AIRBNB",                                         "Travel",             "regex"],
    ["Amazon",                      r"^AMAZON DIGI",                                    "Subscriptions",      "regex"],
    ["Allianz",                     r"Allianz",                                         "Other",              "regex"],
    ["CIMB Niaga Installment",      r"^\(0\.00% 12BLN\)",                               "Fees & Interest",    "regex"],
    ["External Transfer",           r"^TRF BIFAST KE",                                  "External Transfer",  "regex"],
    ["Steam",                       r"^STEAM",                                          "Entertainment",      "regex"],
    ["Tokopedia",                   r"^(WWW\.TOKOPEDIA\.COM|Tokopedia)",                 "Shopping",           "regex"],
    ["Trip.com",                    r"^Tripcom",                                        "Travel",             "regex"],
    ["Bill Payment",                r"^PAY BILLING CODE",                               "Utilities",          "regex"],
    ["Credit Card Payment",         r"^PAY KARTU KREDIT",                               "Internal Transfer",  "regex"],
    ["Dufrital Duty Free",          r"^DUFRITAL",                                       "Shopping",           "regex"],
    ["Oh!Some",                     r"OH.?SOME",                                        "Shopping",           "regex"],
    ["La Rinascente",               r"^LA RINASCENTE",                                  "Shopping",           "regex"],
    # NEW consolidated regex (replacing groups of exact matches)
    ["Admin Fee (Permata)",         r"^Biaya adm\. bulan",                              "Fees & Interest",    "regex"],
    ["Stamp Duty",                  r"^BEA M[AE]TE?RAI",                                "Fees & Interest",    "regex"],
    ["Home Loan (Hello Park)",      r"^CICILAN BCA KE \d+ DARI \d+",                    "Housing",            "regex"],
    ["Bond Coupon",                 r"^PB DARI PERMATA BANK Coupon Payment",            "Income",             "regex"],
    ["Erha Clinic Salary",          r"^TRF DARI ERHA CLINIC",                           "Income",             "regex"],
    ["Erha Clinic Salary",          r"^TRF INCOMING BIFAST DARI ERHA CLINI",            "Income",             "regex"],
    ["Internal Transfer",           r"^TRF INCOMING BIFAST DARI EMANUEL",               "Internal Transfer",  "regex"],
    ["Internal Transfer",           r"^TRF LLG KE EMANUEL",                             "Internal Transfer",  "regex"],
    ["Internal Transfer",           r"^TRF KE EMANUEL",                                 "Internal Transfer",  "regex"],
    ["Internal Transfer",           r"^PB DARI DR DIAN PRATIWI",                        "Internal Transfer",  "regex"],
    ["BCA CC Payment",              r"^KARTU KREDIT/PL",                                "Internal Transfer",  "regex"],
    ["Shopee",                      r"^PAY SHOPEE",                                     "Shopping",           "regex"],
    ["Telkom",                      r"^PAY TELKOM",                                     "Utilities",          "regex"],
    ["GoPay Top-up",                r"^PAY GO-PAY",                                     "Other",              "regex"],
]

# --- CONTAINS rules (new - for merchant names embedded in descriptions) ---
contains_rules = [
    # Financial patterns
    ["SPayLater Payment",           "SPAYLATER",                                        "Fees & Interest",    "contains"],
    ["Loan Payment",                "PB KE KREDITUR DIRECT DEBIT",                      "Other",              "contains"],
    ["Loan Repayment Receipt",      "PB DARI KREDITUR BULK PAYMENT",                    "Income",             "contains"],
    ["Opening Balance",             "SALDO AWAL",                                       "Opening Balance",    "contains"],
    ["SPBU Pertamina",              "SPBU Pertamina",                                   "Transport",          "contains"],
    ["Mastercard Cashback",         "Cash Back Mastercard",                              "Income",             "contains"],
    ["Safe Deposit Box",            "SDB 8820001165",                                   "Fees & Interest",    "contains"],
    ["Home Loan Payment",           "Teguh Pranoto Chen",                                "Housing",            "contains"],
    # Dining (for QR PAYMENT and debit card transactions)
    ["Starbucks",                   "Sbux",                                             "Dining Out",         "contains"],
    ["Starbucks",                   "STARBUCKS",                                        "Dining Out",         "contains"],
    ["Chatime",                     "CHATIME",                                          "Dining Out",         "contains"],
    ["Krispy Kreme",                "KRISPY KREME",                                     "Dining Out",         "contains"],
    ["Sour Sally",                  "Sour Sally",                                       "Dining Out",         "contains"],
    ["Sederhana",                   "SEDERHANA SA",                                     "Dining Out",         "contains"],
    ["D'Crepes",                    "DCREPES",                                          "Dining Out",         "contains"],
    ["Ice Honey",                   "ICE HONEY",                                        "Dining Out",         "contains"],
    ["Monami",                      "MONAMI",                                           "Dining Out",         "contains"],
    ["Juice Box",                   "JUICE BOX",                                        "Dining Out",         "contains"],
    ["Common Grounds",              "COMMON GROUNDS",                                   "Dining Out",         "contains"],
    ["Chateraise",                  "CHATERAISE",                                       "Dining Out",         "contains"],
    ["Chef's Table",                "CHEFS TABLE",                                      "Dining Out",         "contains"],
    ["Haidilao",                    "HAIDILAO",                                         "Dining Out",         "contains"],
    ["Mamaroz",                     "MAMAROZ",                                          "Dining Out",         "contains"],
    ["Monsieur Spoon",              "MONSIEUR SPOON",                                   "Dining Out",         "contains"],
    ["Okinawa Restaurant",          "OKINAWA",                                          "Dining Out",         "contains"],
    ["Song Fa Bak Kut Teh",         "SONG FA BAK KUT TEH",                              "Dining Out",        "contains"],
    ["Teh 63",                      "TEH 63",                                           "Dining Out",         "contains"],
    ["The Coffee Club",             "COFFEE CLUB",                                      "Dining Out",         "contains"],
    ["Tous les Jours",              "TOUS LES JOURS",                                   "Dining Out",         "contains"],
    ["Delice Maison",               "DELICE MAISON",                                    "Dining Out",         "contains"],
    ["Baby Dutch",                  "BABY DUTCH",                                       "Dining Out",         "contains"],
    ["Cafe Serasan",                "CAFE SERASAN",                                     "Dining Out",         "contains"],
    ["Croco",                       "CROCO",                                            "Dining Out",         "contains"],
    ["Autogrill",                   "AUTOGRILL",                                        "Dining Out",         "contains"],
    ["Five Guys",                   "FIVE GUYS",                                        "Dining Out",         "contains"],
    ["McDonald's",                  "MC DONALDS",                                       "Dining Out",         "contains"],
    # Shopping
    ["Abercrombie & Fitch",         "ABERCROMBIE",                                      "Shopping",           "contains"],
    ["Gramedia",                    "GRAMEDIA",                                         "Shopping",           "contains"],
    ["Optik Melawai",               "OPTIK MELAWAI",                                    "Shopping",           "contains"],
    ["Modern Pet",                  "MODERN PET",                                       "Shopping",           "contains"],
    ["Grand Wingheng",              "GRAND WINGHENG",                                   "Shopping",           "contains"],
    ["Bata",                        "BATA",                                             "Shopping",           "contains"],
    ["Carpisa",                     "CARPISA",                                          "Shopping",           "contains"],
    ["Muji",                        "MUJI",                                             "Shopping",           "contains"],
    ["Pandora",                     "Pandora Italy",                                    "Shopping",           "contains"],
    ["UPIM",                        "UPIM",                                             "Shopping",           "contains"],
    ["QDF Duty Free",               "QDF",                                              "Shopping",           "contains"],
    ["Midtrans Payment",            "MIDTRANS",                                         "Shopping",           "contains"],
    # Groceries
    ["Carrefour",                   "CARREFOUR",                                        "Groceries",          "contains"],
    ["Conad",                       "CONAD",                                            "Groceries",          "contains"],
    ["Superindo",                   "SUPERINDO",                                        "Groceries",          "contains"],
    ["Kebun Buah",                  "KEBUN BUAH",                                       "Groceries",          "contains"],
    ["FoodHall",                    "FOODHALL",                                         "Groceries",          "contains"],
    # Healthcare
    ["Siloam Hospitals",            "SILOAM HOSPITALS",                                 "Healthcare",         "contains"],
    ["Smart Pharmacy",              "SMART PHARMACY",                                   "Healthcare",         "contains"],
    ["Aji Waras Apotek",            "AJI WARAS",                                        "Healthcare",         "contains"],
    ["Farmacia",                    "FARMACIA",                                         "Healthcare",         "contains"],
    ["Century Pharmacy",            "CENTURY CHC",                                      "Healthcare",         "contains"],
    # Personal Care
    ["KIKO Milano",                 "KIKO MILANO",                                      "Personal Care",      "contains"],
    ["Karada Massage",              "KARADA",                                           "Personal Care",      "contains"],
    ["One Piece Hair Studio",       "ONE PIECE HAIR",                                   "Personal Care",      "contains"],
    ["Tammia Indonesia",            "TAMMIA",                                           "Personal Care",      "contains"],
    ["Snapy Photo",                 "SNAPY",                                            "Personal Care",      "contains"],
    # Subscriptions
    ["Google One",                  "Google One",                                       "Subscriptions",      "contains"],
    ["Google Play",                 "Google Play",                                      "Subscriptions",      "contains"],
    ["Disney+",                     "Disney PLUS",                                      "Subscriptions",      "contains"],
    ["Netflix",                     "NETFLIX",                                          "Subscriptions",      "contains"],
    ["Spotify",                     "Spotify",                                          "Subscriptions",      "contains"],
    ["Apple",                       "APPLE.COM/BILL",                                   "Subscriptions",      "contains"],
    ["Kobo eBooks",                 "KOBO",                                             "Subscriptions",      "contains"],
    ["VoiceDub AI",                 "VOICEDUB",                                         "Subscriptions",      "contains"],
    # Entertainment
    ["GOG.com",                     "GOG.com",                                          "Entertainment",      "contains"],
    ["Nintendo",                    "NINTENDO",                                         "Entertainment",      "contains"],
    # Travel
    ["China Airlines",              "CHINA AIR",                                        "Travel",             "contains"],
    ["Fiumicino Airport",           "FIUMICINO",                                        "Travel",             "contains"],
    # Transport
    ["Venice Water Transport",      "VENICE TRANSPORT",                                 "Transport",          "contains"],
    # Education
    ["Binus University",            "BINUS",                                            "Education",          "contains"],
    ["Clastify",                    "CLASTIFY",                                         "Education",          "contains"],
    # Gifts & Donations
    ["Church Donation",             "GEREJA",                                           "Gifts & Donations",  "contains"],
]

# --- EXACT rules (keep only those that CAN'T be generalized) ---
exact_rules = [
    # BCA e-banking transfers — can't distinguish internal vs external by pattern alone
    # (same FTSCY pattern, different destinations)
    ["Internal Transfer",           "TRSF E-BANKING DB 0502/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["Internal Transfer",           "TRSF E-BANKING CR 0502/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["Internal Transfer",           "TRSF E-BANKING DB 0801/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["Internal Transfer",           "TRSF E-BANKING CR 0801/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["Internal Transfer",           "TRSF E-BANKING DB 1701/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["Internal Transfer",           "TRSF E-BANKING CR 1701/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["Internal Transfer",           "TRSF E-BANKING DB 3001/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["Internal Transfer",           "TRSF E-BANKING CR 3001/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["Internal Transfer",           "TRSF E-BANKING CR 2202/FTSCY/WS9",                "Internal Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING DB 2202/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING DB 0301/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING DB 1402/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING DB 2002/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING DB 2301/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING DB 2802/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING CR 0201/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING CR 0402/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING CR 0501/FTSCY/WS9",                "External Transfer",  "exact"],
    ["External Transfer",           "TRSF E-BANKING CR 2002/FTSCY/WS9",                "External Transfer",  "exact"],
    # BCA incoming transfer (specific person — can't be regex'd without catching others)
    ["Internal Transfer",           "TRF INCOMING BIFAST DARI EMANUEL GU NARIS ADRIANTO 2171138631 BANK CENTRAL ASIA 21:25:55 -", "Internal Transfer", "exact"],
    # Fees
    ["E-Billing Fee",               "BIAYA E-BILLING",                                  "Fees & Interest",    "exact"],
    ["SMS Notification Fee",        "XA BIAYA NOTIFIKASI",                               "Fees & Interest",    "exact"],
    ["Debit Card Fee",              "DEBIT CARD CHARGES",                                "Fees & Interest",    "exact"],
    ["Interest Expense",            "Interest",                                          "Fees & Interest",    "exact"],
    ["Withholding Tax",             "WITHHOLDING TAX",                                   "Fees & Interest",    "exact"],
    ["Interest Tax",                "PAJAK BUNGA",                                       "Fees & Interest",    "exact"],
    ["Interest Tax",                "PAJAK ATAS BUNGA",                                  "Fees & Interest",    "exact"],
    ["Islamic Profit Share Tax",    "PB Pajak Bagi Hasil",                               "Fees & Interest",    "exact"],
    # Income
    ["Bank Interest Income",        "BUNGA",                                             "Income",             "exact"],
    ["Bank Interest Income",        "PENDAPATAN BUNGA",                                  "Income",             "exact"],
    ["Bank Interest Income",        "CREDIT INTEREST",                                   "Income",             "exact"],
    ["Islamic Profit Share",        "PB Bagi Hasil",                                     "Income",             "exact"],
    ["ANZ Indonesia Salary",        "KR OTOMATIS LLG-ANZ INDONESIA",                     "Income",             "exact"],
    ["BMRI Dividend",               "PB DARI PT INDO PREMIER SEKURITAS D CMS 09:20:36 AAA IPS C DIVIDEN BMRI IPS C DIVIDEN BMRI 0897014005700970", "Income", "exact"],
    # CC Payments (unique strings not covered by ^PAY KARTU KREDIT regex)
    ["Credit Card Payment",         "BILLPAYMENT TO CCARD",                              "Internal Transfer",  "exact"],
    ["Credit Card Payment",         "PAYMENT-THANK YOU",                                 "Internal Transfer",  "exact"],
    ["Credit Card Payment",         "PEMBAYARAN AD 596",                                 "Internal Transfer",  "exact"],
    ["Credit Card Payment",         "PEMBAYARAN VIA AUTODEBET",                          "Internal Transfer",  "exact"],
    ["Credit Card Payment",         "PEMBAYARAN - MBCA",                                 "Internal Transfer",  "exact"],
    ["Auto Debit Payment",          "PEMBAYARAN - DEBET OTOMATIS",                       "Internal Transfer",  "exact"],
    # Bank admin
    ["Bank Admin Fee",              "BIAYA ADM",                                         "Fees & Interest",    "exact"],
    ["Domestic Debit",              "DB DEBIT DOMESTIK TRN DEBIT DOM",                   "Other",              "exact"],
    # Unique one-off merchants (no pattern to generalize)
    ["Wedding Gift (Samuel)",       "TRF BIFAST KE SAMUEL HOWARD PINNARW AN ATAU HILDEGARD S 23810062015 BANK OCBC NISP Permata ME 18:11:12 Selamat ya. Bahagia selalu nggih 000127360548", "Gifts & Donations", "exact"],
    ["SMA Bina Nusantara",          "TRF BIFAST KE PERKUMPULAN PENELITI 30018676452 BANK MANDIRI Permata ME 19:37:27 YISF SMA Bina Nusantara Kaityln Gabrielle Adrianto 000048097679", "Education", "exact"],
    ["IPB University",              "TRF BIFAST KE REKTOR IPB CQ PSSP LP PM 3898329 BANK NEGARA INDONESIA Permata ME 21:15:29 Kaitlyn Gabrielle Adrianto 000127849311", "Education", "exact"],
    ["University Tuition",          "TRSF E-BANKING DB 2901/FTFVA/WS9 / 12005/UNIV-TUITION", "Education",     "exact"],
    ["Buku Digital",                "TRSF E-BANKING DB 1002/FTFVA/WS9 / 70070/BUKU DIGITAL", "Education",     "exact"],
    ["Vatican Library",             "LIB.GIOV.PAOLO II Citta del VatVAT",                "Education",          "exact"],
    ["Vatican Bookshop",            "Bookshop Desk D4 Citta del VatVAT",                 "Education",          "exact"],
    ["Shopee",                      "TRSF E-BANKING DB 1602/FTFVA/WS9 / 12608/SHOPEE",  "Shopping",           "exact"],
    ["Tokopedia",                   "TRSF E-BANKING DB 2502/FTFVA/WS9 / 80777/TOKOPEDIA","Shopping",          "exact"],
    ["Jamtangan.com",               "TRSF E-BANKING DB 1002/FTFVA/WS9 / 38433/JAMTANGAN.CO", "Shopping",      "exact"],
    ["Binus University",            "TRSF E-BANKING DB 1201/FTFVA/WS9 / 71201/BINUS S SIMP", "Education",     "exact"],
    # Italy dining (unique restaurant names — keep as exact, too specific for contains)
    ["Brindisi Venezia",            "BRINDISI SRLS VENEZIA ITA",                         "Dining Out",         "exact"],
    ["C'era Una Volta",             "C'ERA UNA VOLTA VENEZIA ITA",                       "Dining Out",         "exact"],
    ["Cavour Roma",                 "CAVOUR ROMA ITA",                                   "Dining Out",         "exact"],
    ["Chapati N Karak Doha",        "CHAPATI N KARAK AN06 Doha QAT",                     "Dining Out",         "exact"],
    ["Città Leonina Roma",          "CITTA' LEONINA SRL ROMA ITA",                       "Dining Out",         "exact"],
    ["Mercato Centrale Roma",       "MERCATO CENTRALE ROMA TE ROMA ITA",                 "Dining Out",         "exact"],
    ["Osteria Olimpia Roma",        "OSTERIA OLIMPIA ROMA ITA",                          "Dining Out",         "exact"],
    ["Osteria Roma",                "OSTERIA ROMA ITA",                                  "Dining Out",         "exact"],
    ["Pansy Gourmet Milano",        "PANSY GOURMET S.R.L.S. MILANO ITA",                 "Dining Out",         "exact"],
    ["Pellegrini Brera Milano",     "PELLEGRINI BRERA MILANO ITA",                       "Dining Out",         "exact"],
    ["QDF F&B Doha",                "QDF F AND B Doha QAT",                              "Dining Out",         "exact"],
    ["Relax & Caffè Venezia",       "RELAX&CAFFE' CAFFETTE.. VENEZIA ITA",               "Dining Out",         "exact"],
    ["Ristoro della Salute",        "Ristoro della Salut Roma ITA",                      "Dining Out",         "exact"],
    ["Tagliatella Roma",            "TAGLIATELLA ROMA ITA",                              "Dining Out",         "exact"],
    ["Bar Miro Milano",             "BAR MIRO - TTQ SRL MILANO ITA",                     "Dining Out",         "exact"],
    # External Transfer — specific unique Permata transfers that need exact match
    # (these are covered by ^TRF LLG KE or ^TRF BIFAST KE regex, so actually redundant)
    # NOT included — regex handles them
    # Mutual fund
    ["Ashmore Mutual Fund",         "PB DARI RD ASHMORE DANA EKUITAS NUS ANTARA SIAR BATCH 16:13:11", "Income", "exact"],
    # Unique bill payment with admin fee
    ["Telkom Admin Fee",            "BIAYA ADM. PAY TELKOM 0215358989 Pe rmata ME 13:35:59 0215358989", "Fees & Interest", "exact"],
    # External transfer to specific company
    ["External Transfer",           "TRF LLG KE KEMENANGAN INTI PERMATA 1980585758 BCA Permata ME 07:01:06 -", "External Transfer", "exact"],
]


# ── Combine and write ─────────────────────────────────────────────────────────
all_rules = []
for rule in regex_rules + contains_rules + exact_rules:
    all_rules.append(rule + [today])

print(f"\nNew alias list: {len(all_rules)} rows")
print(f"  Regex:    {sum(1 for r in all_rules if r[3] == 'regex')}")
print(f"  Contains: {sum(1 for r in all_rules if r[3] == 'contains')}")
print(f"  Exact:    {sum(1 for r in all_rules if r[3] == 'exact')}")
print(f"  (was {len(data)} rows)")

# Confirm
if "--dry-run" in sys.argv:
    print("\n[DRY RUN] Would write the above. Pass without --dry-run to execute.")
    for i, r in enumerate(all_rules, 1):
        print(f"  {i:3d} | {r[3]:8s} | {r[0]:35s} | {r[1][:60]:60s} | {r[2]}")
    sys.exit(0)

# Clear existing data (keep header)
tab = sc.cfg.aliases_tab
max_row = len(data) + 1
if max_row > 1:
    sc._update(f"{tab}!A2:E{max_row}", [[""] * 5] * len(data))
    print(f"Cleared rows 2–{max_row}")

# Write new data
sc._update(f"{tab}!A2:E{len(all_rules) + 1}", all_rules)
print(f"Wrote {len(all_rules)} rows")

# If new list is shorter, clear leftover rows
if len(all_rules) < len(data):
    leftover = len(data) - len(all_rules)
    start = len(all_rules) + 2
    end = len(data) + 1
    sc._update(f"{tab}!A{start}:E{end}", [[""] * 5] * leftover)
    print(f"Cleared {leftover} leftover rows ({start}–{end})")

print("\n✅ Done! Run: python3 -m finance.sync")
