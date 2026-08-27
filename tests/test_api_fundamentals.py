"""`/stocks/{id}/fundamentals` 必須讀真的有資料的表。

這個端點是上一輪 repoint 時漏掉的最後一處：它仍從 `fundamentals` 取 eps／roe／
revenue／revenue_yoy，而那四欄沒有任何來源會填，於是回傳的是一串值全為 null 的
物件——比空陣列更糟，因為呼叫端會判定「有資料」。
"""


import pandas as pd
import pytest
from starlette.testclient import TestClient

from twstock_analyzer.api.server import create_app
from twstock_analyzer.db.repository import upsert
from twstock_analyzer.db.schema import create_tables


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    create_tables(path)
    monkeypatch.setenv("TWSTOCK_DB", path)

    upsert("fundamentals", pd.DataFrame({
        "stock_id": ["2330"], "report_date": ["2026-08-14"], "period": [None],
        "pe_ratio": [27.76], "dividend_yield": [0.9],
        "eps": [None], "revenue": [None], "revenue_yoy": [None], "roe": [None],
        "fetched_at": ["2026-08-19"],
    }), path)
    upsert("quarterly_financials", pd.DataFrame({
        "stock_id": ["2330", "2330"], "period": ["2026Q1", "2026Q2"],
        "eps": [20.0, 49.33], "revenue": [1.2e9, 2.4e9],
        "operating_income": [1e8, 2e8], "net_income": [1e8, 2e8],
        "fetched_at": ["2026-08-19"] * 2,
    }), path)
    upsert("monthly_revenue", pd.DataFrame({
        "stock_id": ["2330", "2330"], "month": ["2026-06", "2026-07"],
        "revenue": [4.4e8, 4.6e8], "revenue_yoy": [38.0, 44.69],
        "revenue_mom": [2.0, 5.62], "revenue_ytd": [2.4e9, 2.87e9],
        "revenue_ytd_yoy": [36.0, 37.01], "fetched_at": ["2026-08-19"] * 2,
    }), path)
    return TestClient(create_app())


@pytest.fixture()
def body(client):
    return client.get("/stocks/2330/fundamentals").json()


class TestEpsTrend:
    def test_it_carries_real_numbers(self, body):
        assert body["eps_trend"], "eps_trend 不該是空的"
        assert body["eps_trend"][0]["eps"] is not None

    def test_the_latest_quarter_is_first(self, body):
        assert body["eps_trend"][0]["period"] == "2026Q2"
        assert body["eps_trend"][0]["eps"] == pytest.approx(49.33)


class TestRevenue:
    def test_the_growth_rate_is_present(self, body):
        assert body["revenue"][0]["revenue_yoy"] == pytest.approx(44.69)

    def test_it_is_monthly(self, body):
        assert body["revenue"][0]["month"] == "2026-07"


class TestRoe:
    def test_it_is_empty_rather_than_a_list_of_nulls(self, body):
        """ROE 沒有來源。回一串 null 會讓呼叫端以為有資料。"""
        assert body["roe"] == []


class TestPeRatio:
    def test_the_published_ratio_is_returned(self, body):
        assert body["pe_ratio"] == pytest.approx(27.76)


class TestNoNullOnlyRecords:
    def test_no_returned_record_is_entirely_empty(self, body):
        for key in ("eps_trend", "roe", "revenue"):
            for record in body[key]:
                values = [v for k, v in record.items() if k not in ("period", "month", "report_date")]
                assert any(v is not None for v in values), f"{key} 回了一筆全空的紀錄"


class TestLegacyDatabase:
    def test_it_survives_a_database_without_the_new_tables(self, tmp_path, monkeypatch):
        import sqlite3

        path = str(tmp_path / "legacy.db")
        create_tables(path)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE quarterly_financials")
        conn.execute("DROP TABLE monthly_revenue")
        conn.commit()
        conn.close()
        monkeypatch.setenv("TWSTOCK_DB", path)

        body = TestClient(create_app()).get("/stocks/2330/fundamentals").json()

        assert body["eps_trend"] == []
        assert body["revenue"] == []
