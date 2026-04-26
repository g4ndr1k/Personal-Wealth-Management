from finance.db import open_db
import finance.api as wealth_api


def _seed_holding(
    conn,
    *,
    snapshot_date,
    asset_class,
    asset_name,
    institution,
    account="",
    owner="Gandrik",
    market_value_idr=0,
    quantity=1,
    unit_price=1,
    cost_basis_idr=0,
    notes="",
):
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
             institution, account, owner, currency, quantity, unit_price,
             market_value, market_value_idr, cost_basis, cost_basis_idr,
             unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
             last_appraised_date, notes, import_date)
        VALUES (?, ?, 'Physical Assets', ?, ?, ?, ?, ?, 'IDR', ?, ?,
                ?, ?, ?, ?, 0, 1, '', 0, '', ?, '2026-04-11')
        """,
        (
            snapshot_date,
            asset_class,
            asset_name,
            asset_name,
            institution,
            account,
            owner,
            quantity,
            unit_price,
            market_value_idr,
            market_value_idr,
            cost_basis_idr,
            cost_basis_idr,
            notes,
        ),
    )


def test_holdings_identity_allows_same_asset_across_institutions(tmp_path):
    conn = open_db(str(tmp_path / "finance.db"))
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
             institution, account, owner, currency, quantity, unit_price,
             market_value, market_value_idr, cost_basis, cost_basis_idr,
             unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
             last_appraised_date, notes, import_date)
        VALUES (?, ?, 'Investments', ?, ?, ?, '', ?, 'IDR', 1, 1,
                100, 100, 90, 90, 10, 1, '', 0, '', '', '2026-04-11')
        ON CONFLICT(snapshot_date, asset_class, asset_name, owner, institution)
        DO UPDATE SET market_value_idr = excluded.market_value_idr
        """,
        ("2026-03-31", "stock", "BMRI", "BMRI", "IPOT", "Gandrik"),
    )
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_class, asset_group, asset_name, isin_or_code,
             institution, account, owner, currency, quantity, unit_price,
             market_value, market_value_idr, cost_basis, cost_basis_idr,
             unrealised_pnl_idr, exchange_rate, maturity_date, coupon_rate,
             last_appraised_date, notes, import_date)
        VALUES (?, ?, 'Investments', ?, ?, ?, '', ?, 'IDR', 1, 1,
                200, 200, 180, 180, 20, 1, '', 0, '', '', '2026-04-11')
        ON CONFLICT(snapshot_date, asset_class, asset_name, owner, institution)
        DO UPDATE SET market_value_idr = excluded.market_value_idr
        """,
        ("2026-03-31", "stock", "BMRI", "BMRI", "Stockbit", "Gandrik"),
    )
    rows = conn.execute(
        """
        SELECT institution, market_value_idr
        FROM holdings
        WHERE snapshot_date='2026-03-31' AND asset_name='BMRI'
        ORDER BY institution
        """
    ).fetchall()
    conn.close()

    assert [(row["institution"], row["market_value_idr"]) for row in rows] == [
        ("IPOT", 100.0),
        ("Stockbit", 200.0),
    ]


def test_carry_forward_fills_only_missing_stable_assets(tmp_path, monkeypatch):
    db_path = str(tmp_path / "finance.db")
    conn = open_db(db_path)
    _seed_holding(
        conn,
        snapshot_date="2026-03-31",
        asset_class="gold",
        asset_name="Antam 100g",
        institution="Physical",
        market_value_idr=150_000_000,
        cost_basis_idr=120_000_000,
    )
    _seed_holding(
        conn,
        snapshot_date="2026-03-31",
        asset_class="gold",
        asset_name="Antam 50g",
        institution="Physical",
        market_value_idr=75_000_000,
        cost_basis_idr=60_000_000,
    )
    _seed_holding(
        conn,
        snapshot_date="2026-04-30",
        asset_class="gold",
        asset_name="Antam 100g",
        institution="Physical",
        market_value_idr=151_000_000,
        cost_basis_idr=120_000_000,
        notes="manually updated",
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(wealth_api, "_db_path", db_path)
    monkeypatch.setattr(wealth_api, "_auto_snapshot", lambda snapshot_date: None)

    result = wealth_api.carry_forward_holdings(
        wealth_api.CarryForwardRequest(snapshot_date="2026-04-30")
    )

    conn = open_db(db_path)
    rows = conn.execute(
        """
        SELECT asset_name, market_value_idr, notes
        FROM holdings
        WHERE snapshot_date='2026-04-30' AND asset_class='gold'
        ORDER BY asset_name
        """
    ).fetchall()
    conn.close()

    assert result["carried"] == 1
    assert [(row["asset_name"], row["market_value_idr"], row["notes"]) for row in rows] == [
        ("Antam 100g", 151_000_000.0, "manually updated"),
        ("Antam 50g", 75_000_000.0, " [carried forward]"),
    ]


def test_cascade_holding_update_scopes_to_specific_institution_and_account(tmp_path, monkeypatch):
    db_path = str(tmp_path / "finance.db")
    conn = open_db(db_path)
    _seed_holding(
        conn,
        snapshot_date="2026-03-31",
        asset_class="gold",
        asset_name="Antam 100g",
        institution="Vault A",
        account="A1",
        market_value_idr=100_000_000,
        cost_basis_idr=80_000_000,
    )
    _seed_holding(
        conn,
        snapshot_date="2026-04-30",
        asset_class="gold",
        asset_name="Antam 100g",
        institution="Vault A",
        account="A1",
        market_value_idr=100_000_000,
        cost_basis_idr=80_000_000,
    )
    _seed_holding(
        conn,
        snapshot_date="2026-04-30",
        asset_class="gold",
        asset_name="Antam 100g",
        institution="Vault B",
        account="B1",
        market_value_idr=200_000_000,
        cost_basis_idr=150_000_000,
    )
    conn.execute(
        """
        UPDATE holdings
        SET market_value=?, market_value_idr=?, cost_basis=?, cost_basis_idr=?, unit_price=?
        WHERE snapshot_date='2026-03-31' AND asset_class='gold' AND asset_name='Antam 100g'
          AND owner='Gandrik' AND institution='Vault A' AND account='A1'
        """,
        (130_000_000, 130_000_000, 90_000_000, 90_000_000, 2),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(wealth_api, "_db_path", db_path)
    monkeypatch.setattr(wealth_api, "_auto_snapshot", lambda snapshot_date: None)

    wealth_api._cascade_holding_update(
        "2026-03-31",
        "gold",
        "Antam 100g",
        "Gandrik",
        "Vault A",
        "A1",
    )

    conn = open_db(db_path)
    rows = conn.execute(
        """
        SELECT institution, account, market_value_idr
        FROM holdings
        WHERE snapshot_date='2026-04-30' AND asset_class='gold' AND asset_name='Antam 100g'
        ORDER BY institution
        """
    ).fetchall()
    conn.close()

    assert [(row["institution"], row["account"], row["market_value_idr"]) for row in rows] == [
        ("Vault A", "A1", 130_000_000.0),
        ("Vault B", "B1", 200_000_000.0),
    ]


def test_snapshot_keeps_distinct_liabilities_with_same_name(tmp_path, monkeypatch):
    db_path = str(tmp_path / "finance.db")
    conn = open_db(db_path)
    conn.execute(
        """
        INSERT INTO liabilities
            (snapshot_date, liability_type, liability_name, institution, account,
             owner, currency, balance, balance_idr, due_date, notes, import_date)
        VALUES ('2026-03-15', 'credit_card', 'Visa Platinum', 'BCA', '1111',
                'Gandrik', 'IDR', 5000000, 5000000, '', '', '2026-04-11')
        """
    )
    conn.execute(
        """
        INSERT INTO liabilities
            (snapshot_date, liability_type, liability_name, institution, account,
             owner, currency, balance, balance_idr, due_date, notes, import_date)
        VALUES ('2026-03-20', 'credit_card', 'Visa Platinum', 'Mandiri', '2222',
                'Gandrik', 'IDR', 7000000, 7000000, '', '', '2026-04-11')
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(wealth_api, "_db_path", db_path)
    result = wealth_api.create_snapshot(
        wealth_api.SnapshotRequest(snapshot_date="2026-03-31")
    )

    assert result["snapshot"]["credit_card_debt_idr"] == 12_000_000.0
