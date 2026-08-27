"""個股分析的 EPS 與月營收也必須讀真的有資料的表。

`FundamentalAnalyzer.eps_trend` / `.monthly_revenue` 讀的是 `fundamentals.eps` 與
`fundamentals.revenue_yoy`——那兩欄沒有任何來源會填，所以在真實資料庫上永遠回傳
空表。既有測試之所以綠燈，是因為 fixture 自己把值塞進了那兩欄；真實資料裡沒有。
"""

import pandas as pd
import pytest

from twstock_analyzer.analysis.fundamental import FundamentalAnalyzer
from twstock_analyzer.db.repository import upsert
from twstock_analyzer.db.schema import create_tables


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "fa.db")
    create_tables(path)

    upsert("daily_prices", pd.DataFrame({
        "stock_id": ["2330"], "date": ["2026-08-17"],
        "open": [1500.0], "high": [1510.0], "low": [1490.0], "close": [1505.0],
        "volume": [2e7], "adj_close": [1505.0], "fetched_at": ["2026-08-17"],
    }), path)

    # 只填新表，`fundamentals` 的 eps / revenue_yoy 保持 NULL——真實資料庫的樣子。
    upsert("quarterly_financials", pd.DataFrame({
        "stock_id": ["2330"] * 3,
        "period": ["2025Q4", "2026Q1", "2026Q2"],
        "eps": [40.0, 20.0, 49.33],
        "revenue": [1e9, 1.2e9, 2.4e9],
        "operating_income": [1e8] * 3,
        "net_income": [1e8] * 3,
        "fetched_at": ["2026-08-17"] * 3,
    }), path)

    upsert("monthly_revenue", pd.DataFrame({
        "stock_id": ["2330"] * 3,
        "month": ["2026-05", "2026-06", "2026-07"],
        "revenue": [4.0e8, 4.4e8, 4.6e8],
        "revenue_yoy": [30.0, 38.0, 44.69],
        "revenue_mom": [1.0, 2.0, 5.62],
        "revenue_ytd": [2.0e9, 2.4e9, 2.87e9],
        "revenue_ytd_yoy": [35.0, 36.0, 37.01],
        "fetched_at": ["2026-08-17"] * 3,
    }), path)
    return path


@pytest.fixture()
def analyzer(db):
    return FundamentalAnalyzer(db_path=db)


class TestEpsTrend:
    def test_it_is_not_empty_when_only_the_new_table_is_populated(self, analyzer):
        assert not analyzer.eps_trend("2330").empty

    def test_the_latest_quarter_comes_first(self, analyzer):
        result = analyzer.eps_trend("2330")
        assert result.iloc[0]["quarter"] == "2026Q2"
        assert result.iloc[0]["eps"] == pytest.approx(49.33)

    def test_it_respects_the_quarter_limit(self, analyzer):
        assert len(analyzer.eps_trend("2330", quarters=2)) == 2

    def test_the_trend_compares_the_two_most_recent_quarters(self, analyzer):
        # 20.0 → 49.33 是上升。
        assert analyzer.eps_trend("2330").iloc[0]["trend_direction"] == "up"

    def test_an_unknown_stock_gives_an_empty_frame(self, analyzer):
        assert analyzer.eps_trend("9999").empty


class TestMonthlyRevenue:
    def test_it_is_not_empty_when_only_the_new_table_is_populated(self, analyzer):
        assert not analyzer.monthly_revenue("2330").empty

    def test_the_yoy_is_the_published_one_not_a_derived_guess(self, analyzer):
        result = analyzer.monthly_revenue("2330")
        assert result.iloc[0]["revenue_yoy"] == pytest.approx(44.69)

    def test_it_is_monthly_not_quarterly(self, analyzer):
        # 舊版本用季報估月營收，一年只給 4 個點。
        assert analyzer.monthly_revenue("2330", months=12).iloc[0]["date"] == "2026-07"

    def test_it_respects_the_month_limit(self, analyzer):
        assert len(analyzer.monthly_revenue("2330", months=2)) == 2

    def test_an_unknown_stock_gives_an_empty_frame(self, analyzer):
        assert analyzer.monthly_revenue("9999").empty

    def test_month_over_month_growth_divides_by_the_earlier_month(self, analyzer):
        """4.4e8 → 4.6e8 是 +4.55%。

        表格是新到舊排序，若直接用 pct_change 會拿 4.6e8 當分母，得到 -4.35%
        ——方向和幅度都錯。
        """
        result = analyzer.monthly_revenue("2330")
        assert result.iloc[0]["qoq_growth"] == pytest.approx(4.55, abs=0.01)


class TestPeRatio:
    """本益比用 TWSE 公布的那個數字，不要自己算。

    舊版是 close / fundamentals.eps。那一欄永遠是 NULL，所以永遠回 None；就算補上
    新來源也不能這樣算——季報的每股盈餘是**累計至該季**，用它去除股價，第一季會
    得到約四倍的本益比。TWSE 在 BWIBBU_d 已經公布了正確的本益比。
    """

    @pytest.fixture()
    def with_published_pe(self, db):
        upsert("fundamentals", pd.DataFrame({
            "stock_id": ["2330", "2330"],
            "report_date": ["2026-08-16", "2026-08-17"],
            "period": [None, None],
            "pe_ratio": [20.0, 30.5],
            "dividend_yield": [1.5, 1.6],
            "eps": [None, None], "revenue": [None, None],
            "revenue_yoy": [None, None], "roe": [None, None],
            "fetched_at": ["2026-08-17"] * 2,
        }), db)
        return db

    def test_it_returns_the_published_ratio(self, with_published_pe):
        fa = FundamentalAnalyzer(db_path=with_published_pe)
        assert fa.pe_ratio("2330", "2026-08-17") == pytest.approx(30.5)

    def test_it_uses_the_value_as_of_the_requested_date(self, with_published_pe):
        fa = FundamentalAnalyzer(db_path=with_published_pe)
        assert fa.pe_ratio("2330", "2026-08-16") == pytest.approx(20.0)

    def test_it_is_none_when_twse_publishes_none(self, db):
        # 虧損的公司 TWSE 不給本益比，回傳空字串 → 存成 NULL。
        upsert("fundamentals", pd.DataFrame({
            "stock_id": ["2330"], "report_date": ["2026-08-17"], "period": [None],
            "pe_ratio": [None], "dividend_yield": [1.6],
            "eps": [None], "revenue": [None], "revenue_yoy": [None], "roe": [None],
            "fetched_at": ["2026-08-17"],
        }), db)

        assert FundamentalAnalyzer(db_path=db).pe_ratio("2330", "2026-08-17") is None

    def test_it_is_none_when_there_is_no_row_at_all(self, db):
        assert FundamentalAnalyzer(db_path=db).pe_ratio("9999", "2026-08-17") is None


class TestRoe:
    """ROE 目前沒有任何免費來源，就必須誠實地回傳「沒有」。

    `fundamentals.roe` 整欄是 NULL，舊版照樣回傳一列 roe=NaN 的表——呼叫端的
    `if not df.empty` 會判定有資料，於是畫面上出現一張全是空值的表格。
    """

    def test_it_is_empty_when_every_value_is_null(self, db):
        upsert("fundamentals", pd.DataFrame({
            "stock_id": ["2330"], "report_date": ["2026-08-17"], "period": [None],
            "pe_ratio": [30.5], "dividend_yield": [1.6],
            "eps": [None], "revenue": [None], "revenue_yoy": [None], "roe": [None],
            "fetched_at": ["2026-08-17"],
        }), db)

        assert FundamentalAnalyzer(db_path=db).roe_analysis("2330").empty

    def test_it_still_returns_rows_when_roe_is_present(self, db):
        upsert("fundamentals", pd.DataFrame({
            "stock_id": ["2330"], "report_date": ["2026-08-17"], "period": [None],
            "pe_ratio": [30.5], "dividend_yield": [1.6],
            "eps": [None], "revenue": [None], "revenue_yoy": [None], "roe": [18.0],
            "fetched_at": ["2026-08-17"],
        }), db)

        result = FundamentalAnalyzer(db_path=db).roe_analysis("2330")
        assert result.iloc[0]["roe"] == pytest.approx(18.0)


class TestLegacyDatabase:
    def test_the_analyzer_works_on_a_database_without_the_new_tables(self, tmp_path):
        import sqlite3

        path = str(tmp_path / "legacy.db")
        create_tables(path)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE quarterly_financials")
        conn.execute("DROP TABLE monthly_revenue")
        conn.commit()
        conn.close()

        fa = FundamentalAnalyzer(db_path=path)

        assert fa.eps_trend("2330").empty
        assert fa.monthly_revenue("2330").empty
