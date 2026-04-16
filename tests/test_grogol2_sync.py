from finance.db import open_db
import finance.importer as importer


def _seed_txn(conn, *, date, amount, raw_description, merchant="", category="", owner="Gandrik", account="4123968447"):
    conn.execute(
        """
        INSERT INTO transactions (
            date, amount, raw_description, merchant, category, institution, account, owner,
            notes, hash, import_date, import_file, synced_at
        ) VALUES (?, ?, ?, ?, ?, 'Permata', ?, ?, '', ?, '2026-04-16', 'test.xlsx', '2026-04-16 10:00:00')
        """,
        (date, amount, raw_description, merchant, category, account, owner, f"{date}|{amount}|{raw_description}"),
    )


def test_sync_grogol_2_creates_monthly_real_estate_holdings_from_teguh_transactions(tmp_path):
    db_path = str(tmp_path / "finance.db")
    conn = open_db(db_path)
    _seed_txn(
        conn,
        date="2026-02-11",
        amount=-50_003_782,
        raw_description="TRF IFT KE Teguh Pranoto Chen 12 Ma 0140258485 Permata ME Cicilan 1, Feb 11 2026",
        merchant="Home Loan Payment",
        category="Housing",
    )
    _seed_txn(
        conn,
        date="2026-02-11",
        amount=-50_000,
        raw_description="CABLE FEE TRF IFT KE Teguh Pranoto Chen 12 Ma 0140258485 Permata ME Cicilan 1, Feb 11 2026",
        merchant="Home Loan Payment",
        category="Housing",
    )
    _seed_txn(
        conn,
        date="2026-03-02",
        amount=-50_003_767,
        raw_description="TRF IFT KE Teguh Pranoto Chen 12 Ma 0140258485 Permata ME Cicilan ke 2",
        merchant="Home Loan Payment",
        category="Housing",
    )
    _seed_txn(
        conn,
        date="2026-03-02",
        amount=-50_000,
        raw_description="CABLE FEE TRF IFT KE Teguh Pranoto Chen 12 Ma 0140258485 Permata ME Cicilan ke 2",
        merchant="Home Loan Payment",
        category="Housing",
    )
    conn.execute(
        "INSERT INTO account_balances (snapshot_date, account_type, institution, account, owner, currency, balance, balance_idr, import_date) VALUES ('2026-04-30', 'cash', 'Permata', '4123968447', 'Gandrik', 'IDR', 1, 1, '2026-04-16')"
    )
    conn.commit()
    conn.close()

    importer.sync_grogol_2_from_transactions(db_path)

    conn = open_db(db_path)
    rows = conn.execute(
        """
        SELECT snapshot_date, asset_name, market_value_idr, last_appraised_date, notes
        FROM holdings
        WHERE asset_class='real_estate' AND asset_name='Grogol 2'
        ORDER BY snapshot_date
        """
    ).fetchall()
    conn.close()

    assert [(r["snapshot_date"], r["asset_name"], r["market_value_idr"], r["last_appraised_date"]) for r in rows] == [
        ("2026-01-31", "Grogol 2", 0.0, "2026-01-31"),
        ("2026-02-28", "Grogol 2", 50_053_782.0, "2026-02-11"),
        ("2026-03-31", "Grogol 2", 100_107_549.0, "2026-03-02"),
        ("2026-04-30", "Grogol 2", 100_107_549.0, "2026-03-02"),
    ]
    assert all("Teguh Pranoto Chen" in r["notes"] for r in rows)
