"""Every SQL statement in the dashboard must run against the real schema.

The 個股分析 page spent its whole life querying a table named
`institutional_holdings`, which has never existed — the surrounding
`except Exception` turned "no such table" into the friendly-looking message
"No institutional data available", so every stock looked like it had no
institutional data.  These tests execute the page's SQL against a freshly
created schema so a wrong table or column name fails loudly instead.
"""

import ast
import inspect
import sqlite3

import pytest

from twstock_analyzer.db.schema import create_tables


def _sql_literals(module) -> list[str]:
    """Every static SELECT statement written in the module's source.

    f-string SQL is skipped: its literal parts are separate fragments that
    cannot be executed on their own.  Those queries are not covered here.
    """
    tree = ast.parse(inspect.getsource(module))

    interpolated = {
        id(part)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        for part in node.values
    }

    found = []
    for node in ast.walk(tree):
        if id(node) in interpolated:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if text.upper().startswith("SELECT"):
                found.append(" ".join(text.split()))
    return found


@pytest.fixture()
def schema_db(tmp_path):
    path = str(tmp_path / "schema.db")
    create_tables(path)
    return path


@pytest.fixture()
def dashboard_sql():
    from twstock_analyzer.streamlit_app import app

    statements = _sql_literals(app)
    assert statements, "dashboard should contain SQL to check"
    return statements


def test_every_dashboard_query_runs_against_the_real_schema(schema_db, dashboard_sql):
    conn = sqlite3.connect(schema_db)
    try:
        for sql in dashboard_sql:
            params = ["2330"] * sql.count("?")
            try:
                conn.execute(sql, params)
            except sqlite3.Error as exc:
                pytest.fail(f"dashboard SQL is broken: {exc}\n  {sql}")
    finally:
        conn.close()


def test_the_fundamentals_panel_reads_eps_from_the_table_that_has_it(dashboard_sql):
    """`fundamentals.eps` 沒有任何來源會填，讀它等於保證顯示 "—"。"""
    eps_queries = [s for s in dashboard_sql if " eps" in s.lower() or "eps," in s.lower()]

    assert eps_queries, "基本面面板必須查每股盈餘"
    for sql in eps_queries:
        assert "quarterly_financials" in sql, f"EPS 應該來自 quarterly_financials：{sql}"


def test_the_fundamentals_panel_reads_revenue_growth_from_the_monthly_table(dashboard_sql):
    yoy_queries = [s for s in dashboard_sql if "revenue_yoy" in s.lower()]

    assert yoy_queries, "基本面面板必須查營收年增率"
    for sql in yoy_queries:
        assert "monthly_revenue" in sql, f"營收年增率應該來自 monthly_revenue：{sql}"


def test_no_dashboard_query_reads_the_permanently_empty_columns(dashboard_sql):
    """`fundamentals` 的 eps／revenue／revenue_yoy／roe 沒有來源，不該再被讀。"""
    for sql in dashboard_sql:
        if "from fundamentals" not in sql.lower():
            continue
        for dead in ("eps", "revenue_yoy", "roe", "revenue"):
            assert dead not in sql.lower().split("from")[0], (
                f"{dead} 在 fundamentals 裡永遠是 NULL：{sql}"
            )


def test_the_institutional_panel_reads_the_table_that_exists(dashboard_sql):
    institutional = [s for s in dashboard_sql if "institutional" in s.lower()]

    assert institutional, "個股分析 page must query institutional data"
    for sql in institutional:
        assert "institutional_trading" in sql
        assert "institutional_holdings" not in sql
