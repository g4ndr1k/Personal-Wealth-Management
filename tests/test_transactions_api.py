import finance.api as finance_api


def test_tx_where_filters_uncategorised_transactions_only():
    parts = finance_api._tx_where(
        year=2026,
        month=4,
        owner='Gandrik',
        category=None,
        category_group=None,
        uncategorised_only=True,
        q='lunch',
    )

    assert "(category IS NULL OR TRIM(category) = '')" in parts.clause
    assert parts.params == ['2026', '04', 'Gandrik', '%lunch%', '%lunch%']


def test_tx_where_filters_category_group():
    parts = finance_api._tx_where(
        year=None,
        month=None,
        owner=None,
        category=None,
        category_group='Health & Family',
        uncategorised_only=False,
        q=None,
    )

    assert 'EXISTS (' in parts.clause
    assert 'category_group = ?' in parts.clause
    assert parts.params == ['Health & Family']
