from finance.db import open_db
import finance.api as finance_api


def test_upsert_category_renames_and_propagates_references(tmp_path, monkeypatch):
    db_path = str(tmp_path / "finance.db")
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO categories (category, icon, sort_order, is_recurring, monthly_budget, category_group, subcategory, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Food", "🍜", 10, 0, None, "Living", "Dining", "2026-04-16 00:00:00"),
    )
    conn.execute(
        "INSERT INTO transactions (date, amount, raw_description, merchant, category, institution, account, owner, hash, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-16", -25000, "Lunch", "Warung", "Food", "BCA", "123", "Gandrik", "tx-1", "2026-04-16 00:00:00"),
    )
    conn.execute(
        "INSERT INTO category_overrides (hash, category, merchant, notes, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?)",
        ("tx-1", "Food", "Warung", "", "2026-04-16 00:00:00", "user"),
    )
    conn.execute(
        "INSERT INTO merchant_aliases (merchant, alias, category, match_type, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("Warung", "Lunch", "Food", "exact", "2026-04-16 00:00:00"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(finance_api, "_db_path", db_path)

    result = finance_api.post_category(
        finance_api.CategoryUpsertRequest(
            original_category="Food",
            category="Dining",
            icon="🍽️",
            sort_order=5,
            is_recurring=False,
            monthly_budget=500000,
            category_group="Living",
            subcategory="Meals",
        )
    )

    conn = open_db(db_path)
    category_row = conn.execute(
        "SELECT category, icon, sort_order, is_recurring, monthly_budget, category_group, subcategory FROM categories"
    ).fetchone()
    tx_category = conn.execute("SELECT category FROM transactions WHERE hash='tx-1'").fetchone()[0]
    override_category = conn.execute("SELECT category FROM category_overrides WHERE hash='tx-1'").fetchone()[0]
    alias_category = conn.execute("SELECT category FROM merchant_aliases WHERE alias='Lunch'").fetchone()[0]
    conn.close()

    assert result["category"] == "Dining"
    assert category_row["category"] == "Dining"
    assert category_row["icon"] == "🍽️"
    assert category_row["sort_order"] == 5
    assert category_row["is_recurring"] == 0
    assert category_row["monthly_budget"] == 500000
    assert category_row["category_group"] == "Living"
    assert category_row["subcategory"] == "Meals"
    assert tx_category == "Dining"
    assert override_category == "Dining"
    assert alias_category == "Dining"
