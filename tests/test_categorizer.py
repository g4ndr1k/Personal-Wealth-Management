import unittest
from dataclasses import dataclass

from finance.categorizer import Categorizer, match_internal_transfers


@dataclass
class DummyTxn:
    date: str
    amount: float
    owner: str
    account: str
    raw_description: str
    merchant: str | None = None
    category: str | None = None


class CategorizerTests(unittest.TestCase):
    def test_account_filtered_contains_rule_wins(self):
        cat = Categorizer(
            aliases=[
                {
                    "merchant": "ATM Withdrawal",
                    "alias": "^TARIKAN ATM",
                    "category": "Cash Withdrawal",
                    "match_type": "regex",
                },
                {
                    "merchant": "Household Cash",
                    "alias": "TARIKAN ATM",
                    "category": "Household Expenses",
                    "match_type": "contains",
                    "owner_filter": "Helen",
                    "account_filter": "5500346622",
                },
            ],
            categories=[],
        )

        helen = cat.categorize(
            "TARIKAN ATM CABANG KEBAYORAN",
            owner="Helen",
            account="5500346622",
        )
        gandrik = cat.categorize(
            "TARIKAN ATM CABANG KEBAYORAN",
            owner="Gandrik",
            account="2171138631",
        )

        self.assertEqual(helen.category, "Household Expenses")
        self.assertEqual(gandrik.category, "Cash Withdrawal")

    def test_internal_transfer_requires_transfer_descriptions(self):
        debit = DummyTxn(
            date="2026-03-01",
            amount=-5000000,
            owner="Gandrik",
            account="2171138631",
            raw_description="TRSF E-BANKING DB 0103/FTSCY/WS9",
            category="Other",
            merchant="Unknown",
        )
        credit = DummyTxn(
            date="2026-03-01",
            amount=5000000,
            owner="Helen",
            account="5500346622",
            raw_description="TRSF E-BANKING CR 0103/FTSCY/WS9",
            category="Other",
            merchant="Unknown",
        )

        matched = match_internal_transfers([debit, credit])

        self.assertEqual(matched, 2)
        self.assertEqual(debit.category, "Transfer")
        self.assertEqual(credit.category, "Transfer")

    def test_same_amount_pair_is_not_forced_when_description_is_not_transfer_like(self):
        debit = DummyTxn(
            date="2026-03-01",
            amount=-5000000,
            owner="Gandrik",
            account="2171138631",
            raw_description="GROCERY PAYMENT",
            category="Groceries",
            merchant="Supermarket",
        )
        credit = DummyTxn(
            date="2026-03-01",
            amount=5000000,
            owner="Helen",
            account="5500346622",
            raw_description="SALARY CREDIT",
            category="Income",
            merchant="Employer",
        )

        matched = match_internal_transfers([debit, credit])

        self.assertEqual(matched, 0)
        self.assertEqual(debit.category, "Groceries")
        self.assertEqual(credit.category, "Income")


if __name__ == "__main__":
    unittest.main()
