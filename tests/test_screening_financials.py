"""screen 的 EPS 與營收年增率必須真的有值，且來自各自的最新一期。

`fundamentals.eps` / `.revenue_yoy` 從來沒有來源寫入，所以表格永遠印 "-"，而
`--eps-min` / `--revenue-yoy-min` 永遠回傳 0 檔——不報錯，看起來就像「市場上沒有
符合的股票」。這裡測的是改成讀 quarterly_financials / monthly_revenue 之後的行為。
"""

import pandas as pd
import pytest

from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import (
    MissingDataError,
    ScreenCriteria,
    run_screen,
)


@pytest.fixture()
def market(tmp_path):
    """兩檔股票：1101 有完整財報，2222 只有價格。

    兩張新表都刻意放**兩期**，且較舊的那期數字較大——若實作退回 MAX(...)，
    測試會抓到舊期的值。
    """
    path = str(tmp_path / "fin.db")
    create_tables(path)
    set_default_db_path(path)

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-07-01", periods=25)]
    for stock_id, close in (("1101", 24.2), ("2222", 10.5)):
        upsert("daily_prices", pd.DataFrame({
            "stock_id": [stock_id] * 25,
            "date": dates,
            "open": [close] * 25, "high": [close] * 25, "low": [close] * 25,
            "close": [close] * 25, "volume": [1_000_000.0] * 25,
            "adj_close": [close] * 25, "fetched_at": ["2026-08-17"] * 25,
        }), path)

    upsert("quarterly_financials", pd.DataFrame({
        "stock_id": ["1101", "1101"],
        "period": ["2026Q1", "2026Q2"],
        "eps": [9.9, 0.38],            # 舊的那期比較大
        "revenue": [30_000_000.0, 71_289_957.0],
        "operating_income": [1.0, 5_170_177.0],
        "net_income": [1.0, 4_569_799.0],
        "fetched_at": ["2026-08-17"] * 2,
    }), path)

    upsert("monthly_revenue", pd.DataFrame({
        "stock_id": ["1101", "1101"],
        "month": ["2026-06", "2026-07"],
        "revenue": [13_382_706.0, 13_744_103.0],
        "revenue_yoy": [88.8, 1.54],   # 舊的那月比較大
        "revenue_mom": [0.0, 2.70],
        "revenue_ytd": [1.0, 85_211_435.0],
        "revenue_ytd_yoy": [1.0, 1.54],
        "fetched_at": ["2026-08-17"] * 2,
    }), path)
    return path


@pytest.fixture()
def row(market):
    return run_screen(ScreenCriteria(), db_path=market).set_index("stock_id").loc["1101"]


class TestColumnsAreFilled:
    def test_eps_has_a_value(self, row):
        assert not pd.isna(row["eps"]), "EPS 欄不該再是空的"

    def test_revenue_yoy_has_a_value(self, row):
        assert not pd.isna(row["revenue_yoy"]), "營收年增率欄不該再是空的"

    def test_eps_comes_from_the_latest_quarter(self, row):
        # 2026Q2 是 0.38；較舊的 2026Q1 是 9.9。
        assert row["eps"] == pytest.approx(0.38)

    def test_revenue_yoy_comes_from_the_latest_month(self, row):
        # 2026-07 是 1.54；較舊的 2026-06 是 88.8。
        assert row["revenue_yoy"] == pytest.approx(1.54)

    def test_the_period_and_month_are_reported(self, row):
        """數字屬於哪一期必須看得到，否則又是一個「日期與數字來自不同時間」的表。"""
        assert row["eps_period"] == "2026Q2"
        assert row["revenue_month"] == "2026-07"

    def test_a_stock_without_financials_still_appears(self, market):
        result = run_screen(ScreenCriteria(), db_path=market).set_index("stock_id")
        assert "2222" in result.index
        assert pd.isna(result.loc["2222", "eps"])


class TestProvenanceIsVisible:
    """一列裡有三個不同的「最新」，使用者必須看得到各自是哪一天。

    價格是最新交易日、本益比／殖利率是 TWSE 最後一次公布日、每股盈餘是最新一季、
    營收年增率是最新一個月。不標出來，就會重演「日期寫今天、數字是半年前」。
    """

    def test_the_valuation_date_is_reported(self, market):
        upsert("fundamentals", pd.DataFrame({
            "stock_id": ["1101"], "report_date": ["2026-07-15"], "period": [None],
            "pe_ratio": [12.0], "dividend_yield": [3.4],
            "eps": [None], "revenue": [None], "revenue_yoy": [None], "roe": [None],
            "fetched_at": ["2026-08-17"],
        }), market)

        row = run_screen(ScreenCriteria(), db_path=market).set_index("stock_id").loc["1101"]

        assert row["pe_date"] == "2026-07-15"

    def test_the_four_dates_are_allowed_to_differ(self, market):
        upsert("fundamentals", pd.DataFrame({
            "stock_id": ["1101"], "report_date": ["2026-07-15"], "period": [None],
            "pe_ratio": [12.0], "dividend_yield": [3.4],
            "eps": [None], "revenue": [None], "revenue_yoy": [None], "roe": [None],
            "fetched_at": ["2026-08-17"],
        }), market)

        row = run_screen(ScreenCriteria(), db_path=market).set_index("stock_id").loc["1101"]

        assert {row["latest_date"], row["pe_date"], row["eps_period"], row["revenue_month"]} == {
            "2026-08-04", "2026-07-15", "2026Q2", "2026-07",
        }


class TestFiltersUseTheLatestFigures:
    def test_an_eps_floor_matches_on_the_latest_quarter(self, market):
        assert not run_screen(ScreenCriteria(eps_min=0.3), db_path=market).empty

    def test_an_eps_floor_is_not_satisfied_by_a_stale_quarter(self, market):
        # 只有讀到舊的 9.9 才會通過。
        assert run_screen(ScreenCriteria(eps_min=5.0), db_path=market).empty

    def test_a_revenue_yoy_floor_matches_on_the_latest_month(self, market):
        assert not run_screen(ScreenCriteria(revenue_yoy_min=1.0), db_path=market).empty

    def test_a_revenue_yoy_floor_is_not_satisfied_by_a_stale_month(self, market):
        # 只有讀到舊的 88.8 才會通過。
        assert run_screen(ScreenCriteria(revenue_yoy_min=50.0), db_path=market).empty

    def test_a_stock_without_financials_is_excluded_by_an_eps_floor(self, market):
        result = run_screen(ScreenCriteria(eps_min=0.3), db_path=market)
        assert set(result["stock_id"]) == {"1101"}


class TestMissingDataIsAnnounced:
    """「篩不到」和「這欄沒資料」必須分得出來。"""

    @pytest.fixture()
    def priceless(self, tmp_path):
        path = str(tmp_path / "empty.db")
        create_tables(path)
        set_default_db_path(path)
        upsert("daily_prices", pd.DataFrame({
            "stock_id": ["1101"], "date": ["2026-08-17"],
            "open": [24.0], "high": [24.0], "low": [24.0], "close": [24.2],
            "volume": [1_000_000.0], "adj_close": [24.2], "fetched_at": ["2026-08-17"],
        }), path)
        return path

    def test_an_eps_filter_without_any_eps_data_raises(self, priceless):
        with pytest.raises(MissingDataError) as excinfo:
            run_screen(ScreenCriteria(eps_min=1.0), db_path=priceless)
        assert "eps" in str(excinfo.value).lower()

    def test_the_error_names_the_command_that_fixes_it(self, priceless):
        with pytest.raises(MissingDataError) as excinfo:
            run_screen(ScreenCriteria(revenue_yoy_min=1.0), db_path=priceless)
        assert "financials" in str(excinfo.value) or "revenue" in str(excinfo.value)

    def test_a_filter_on_a_populated_column_does_not_raise(self, market):
        run_screen(ScreenCriteria(eps_min=999.0), db_path=market)  # 篩不到，但不該炸

    def test_screening_without_those_filters_still_works(self, priceless):
        assert not run_screen(ScreenCriteria(), db_path=priceless).empty


class TestLegacyDatabase:
    def test_screening_works_on_a_database_without_the_new_tables(self, tmp_path):
        """既有資料庫沒有這兩張表時，screen 仍要能跑，只是欄位是空的。"""
        import sqlite3

        path = str(tmp_path / "legacy.db")
        create_tables(path)
        set_default_db_path(path)
        upsert("daily_prices", pd.DataFrame({
            "stock_id": ["1101"], "date": ["2026-08-17"],
            "open": [24.0], "high": [24.0], "low": [24.0], "close": [24.2],
            "volume": [1_000_000.0], "adj_close": [24.2], "fetched_at": ["2026-08-17"],
        }), path)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE quarterly_financials")
        conn.execute("DROP TABLE monthly_revenue")
        conn.commit()
        conn.close()

        result = run_screen(ScreenCriteria(), db_path=path)

        assert list(result["stock_id"]) == ["1101"]
        assert pd.isna(result.iloc[0]["eps"])
