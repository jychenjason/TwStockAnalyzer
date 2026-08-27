"""季度財報與月營收各自存一張表。

三種資料的頻率不同：本益比／殖利率是**每日**、每股盈餘是**每季**、營收年增率是
**每月**。硬塞進 `fundamentals` 同一列會做出「一列裡三個欄位分屬三個不同時間」的
資料——正是先前 screen 表格把半年前的最高價標成今天收盤價的那種錯。
"""

import sqlite3

import pandas as pd
import pytest

from twstock_analyzer.db.repository import get_table_columns, upsert
from twstock_analyzer.db.schema import TABLE_DEFS, create_tables
from twstock_analyzer.screening.financials import ensure_financial_tables


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "fin.db")
    create_tables(path)
    return path


class TestSchema:
    def test_quarterly_table_exists(self, db):
        assert "quarterly_financials" in TABLE_DEFS
        conn = sqlite3.connect(db)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "quarterly_financials" in names

    def test_monthly_table_exists(self, db):
        conn = sqlite3.connect(db)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "monthly_revenue" in names

    def test_quarterly_is_keyed_by_stock_and_period(self, db):
        """同一檔同一季重抓一次不該變成兩列。"""
        for eps in (1.0, 2.0):
            upsert("quarterly_financials", pd.DataFrame({
                "stock_id": ["1101"], "period": ["2026Q2"], "eps": [eps],
                "revenue": [100.0], "operating_income": [10.0], "net_income": [5.0],
                "fetched_at": ["2026-08-17"],
            }), db)

        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT eps FROM quarterly_financials WHERE stock_id='1101'").fetchall()
        conn.close()
        assert rows == [(2.0,)], "重抓應該覆蓋，不是累積"

    def test_monthly_is_keyed_by_stock_and_month(self, db):
        for yoy in (1.0, 9.0):
            upsert("monthly_revenue", pd.DataFrame({
                "stock_id": ["1101"], "month": ["2026-07"], "revenue": [100.0],
                "revenue_yoy": [yoy], "revenue_mom": [0.0],
                "revenue_ytd": [700.0], "revenue_ytd_yoy": [1.0],
                "fetched_at": ["2026-08-17"],
            }), db)

        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT revenue_yoy FROM monthly_revenue WHERE stock_id='1101'").fetchall()
        conn.close()
        assert rows == [(9.0,)]

    def test_the_parsed_frame_matches_the_table_columns(self):
        """解析器的欄位要正好對得上資料表，否則 upsert 會在執行期才炸。"""
        quarterly = set(get_table_columns("quarterly_financials")) - {"fetched_at"}
        monthly = set(get_table_columns("monthly_revenue")) - {"fetched_at"}
        assert quarterly == {"stock_id", "period", "eps", "revenue", "operating_income", "net_income"}
        assert monthly == {
            "stock_id", "month", "revenue", "revenue_yoy",
            "revenue_mom", "revenue_ytd", "revenue_ytd_yoy",
        }


class TestLegacyDatabases:
    def test_the_tables_are_created_on_a_database_that_predates_them(self, tmp_path):
        """既有的 data/twstock.db 沒有這兩張表，不該為了看 EPS 就要重建資料庫。"""
        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE daily_prices (stock_id TEXT, date TEXT, close REAL)")
        conn.commit()
        conn.close()

        ensure_financial_tables(path)

        conn = sqlite3.connect(path)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert {"quarterly_financials", "monthly_revenue"} <= names
