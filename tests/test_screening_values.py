"""Screening output must report the *latest* figures, not the largest ones.

`run_screen` built its columns with `MAX(...)` over every row a stock ever had:
`latest_close` was the highest close in history while `latest_date` was the
real latest date, so the two came from different days.  For 1101 that showed
26.55 (2026-02-25) against a latest date of 2026-08-17, whose close was 24.20.
`avg_volume` averaged the entire history, and the fundamental columns took the
largest value across all report periods.
"""

import pandas as pd
import pytest

from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import VOLUME_WINDOW, ScreenCriteria, run_screen


@pytest.fixture()
def market(tmp_path):
    """1101 peaks in the middle and drifts down; the last close is NOT the highest.

    Closes: 20, 21 … peak 40 at index 20, then falls to 30 at index 30.
    Volumes: 1,000,000 every day except an ancient 99,000,000 spike at index 0.
    """
    path = str(tmp_path / "values.db")
    create_tables(path)
    set_default_db_path(path)

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-07-01", periods=31)]
    closes = [20.0 + i for i in range(21)] + [40.0 - i for i in range(1, 11)]
    volumes = [99_000_000.0] + [1_000_000.0] * 30

    upsert("daily_prices", pd.DataFrame({
        "stock_id": ["1101"] * 31,
        "date": dates,
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": volumes,
        "adj_close": closes,
        "fetched_at": ["2025-08-20"] * 31,
    }), path)

    upsert("fundamentals", pd.DataFrame({
        "stock_id": ["1101", "1101"],
        "report_date": ["2024-12-31", "2025-06-30"],
        "period": ["2024Q4", "2025Q2"],
        "eps": [9.0, 3.0],
        "pe_ratio": [50.0, 12.0],
        "dividend_yield": [1.0, 6.0],
        "revenue": [1000.0, 1200.0],
        "revenue_yoy": [30.0, 5.0],
        "roe": [20.0, 8.0],
        "fetched_at": ["2025-08-20", "2025-08-20"],
    }), path)
    return path


@pytest.fixture()
def row(market):
    result = run_screen(ScreenCriteria(), db_path=market)
    assert not result.empty
    return result.iloc[0]


class TestPriceColumns:
    def test_the_close_is_the_one_from_the_latest_date(self, row):
        # Last bar is 30.0; the historical high is 40.0.
        assert row["latest_close"] == pytest.approx(30.0)

    def test_the_date_is_the_latest_trading_day(self, row):
        assert row["latest_date"] == "2025-08-12"

    def test_price_and_date_come_from_the_same_day(self, market, row):
        import sqlite3

        conn = sqlite3.connect(market)
        close_on_that_day = conn.execute(
            "SELECT close FROM daily_prices WHERE stock_id = '1101' AND date = ?",
            (row["latest_date"],),
        ).fetchone()[0]
        conn.close()

        assert row["latest_close"] == pytest.approx(close_on_that_day)


class TestVolumeColumn:
    def test_volume_averages_a_recent_window_not_all_history(self, row):
        # An ancient 99M spike must not drag the average of a quiet stock up.
        assert row["avg_volume"] == pytest.approx(1_000_000.0)

    def test_the_window_is_the_documented_length(self, market):
        import sqlite3

        conn = sqlite3.connect(market)
        recent = conn.execute(
            "SELECT COUNT(*) FROM (SELECT date FROM daily_prices WHERE stock_id='1101'"
            " ORDER BY date DESC LIMIT ?)",
            (VOLUME_WINDOW,),
        ).fetchone()[0]
        conn.close()

        assert recent == VOLUME_WINDOW


class TestFundamentalColumns:
    """`fundamentals` 現在只剩每日的本益比與殖利率。

    eps 與 revenue_yoy 已改由 quarterly_financials / monthly_revenue 供應（那兩欄
    在 `fundamentals` 裡從來沒有來源會填），同樣的「必須取最新一期」要求改在
    tests/test_screening_financials.py 驗證。
    """

    def test_the_pe_ratio_is_from_the_latest_report(self, row):
        # 2025-06-30 reports 12.0; the older period reported 50.0.
        assert row["pe_ratio"] == pytest.approx(12.0)

    def test_dividend_yield_is_from_the_latest_report(self, row):
        assert row["dividend_yield"] == pytest.approx(6.0)


class TestFiltersUseTheSameFigures:
    def test_a_pe_ceiling_judges_the_latest_report(self, market):
        # Latest PE is 12; the stale one is 50.  A ceiling of 20 must match.
        assert not run_screen(ScreenCriteria(pe_max=20.0), db_path=market).empty

    def test_a_pe_floor_is_not_satisfied_by_a_stale_report(self, market):
        # A floor of 40 would pass only if the stale 50.0 were used.
        assert run_screen(ScreenCriteria(pe_min=40.0), db_path=market).empty

    def test_a_volume_floor_judges_the_recent_average(self, market):
        # One ancient 99M day must not qualify a stock that now trades 1M.
        assert run_screen(ScreenCriteria(volume_min=50_000_000), db_path=market).empty

    def test_a_reachable_volume_floor_still_matches(self, market):
        assert not run_screen(ScreenCriteria(volume_min=500_000), db_path=market).empty


class TestStocksWithoutFundamentals:
    def test_a_stock_with_no_fundamentals_still_appears(self, market):
        upsert("daily_prices", pd.DataFrame({
            "stock_id": ["2222"],
            "date": ["2025-08-12"],
            "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
            "volume": [500_000.0], "adj_close": [10.5], "fetched_at": ["2025-08-20"],
        }), market)

        result = run_screen(ScreenCriteria(), db_path=market)

        assert "2222" in set(result["stock_id"])
        assert pd.isna(result.set_index("stock_id").loc["2222", "pe_ratio"])
