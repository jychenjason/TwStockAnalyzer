"""Tests for the screening module."""

import json
import tempfile
from pathlib import Path

import pytest

from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import ScreenCriteria, run_screen
from twstock_analyzer.screening.watchlist import (
    create_watchlist,
    delete_watchlist,
    get_watchlist,
    list_watchlists,
)


class TestScreenCriteria:
    def test_defaults(self):
        c = ScreenCriteria()
        assert c.pe_min is None
        assert c.pe_max is None
        assert c.rsi_min is None
        assert c.volume_min is None

    def test_with_values(self):
        c = ScreenCriteria(pe_min=10.0, pe_max=20.0, volume_min=1_000_000)
        assert c.pe_min == 10.0
        assert c.pe_max == 20.0
        assert c.volume_min == 1_000_000

    def test_partial(self):
        c = ScreenCriteria(rsi_max=30.0)
        assert c.rsi_max == 30.0
        assert c.pe_min is None


class TestWatchlistCRUD:
    @pytest.fixture
    def db_path(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        create_tables(tmp.name)
        yield tmp.name
        Path(tmp.name).unlink(missing_ok=True)

    def test_create(self, db_path):
        wid = create_watchlist("growth", ["2330", "2454", "2317"], db_path)
        assert wid > 0

    def test_list(self, db_path):
        create_watchlist("a", ["2330"], db_path)
        create_watchlist("b", ["2454"], db_path)
        wls = list_watchlists(db_path)
        assert len(wls) == 2

    def test_get(self, db_path):
        wid = create_watchlist("test", ["2330"], db_path)
        wl = get_watchlist(wid, db_path)
        assert wl is not None
        assert wl["name"] == "test"
        assert "2330" in wl["stock_ids"]

    def test_delete(self, db_path):
        wid = create_watchlist("del", ["2330"], db_path)
        assert delete_watchlist(wid, db_path) is True
        assert get_watchlist(wid, db_path) is None

    def test_delete_nonexistent(self, db_path):
        assert delete_watchlist(9999, db_path) is False


class TestRunScreen:
    @pytest.fixture
    def db_path(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        create_tables(tmp.name)
        conn = __import__("sqlite3").connect(tmp.name)
        # Insert sample daily price data
        conn.execute(
            "INSERT INTO daily_prices (stock_id, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2330", "2024-01-02", 100.0, 105.0, 99.0, 102.0, 10_000_000),
        )
        conn.execute(
            "INSERT INTO daily_prices (stock_id, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2454", "2024-01-02", 200.0, 210.0, 198.0, 205.0, 500_000),
        )
        # Insert sample fundamentals — schema uses report_date, not date
        conn.execute(
            "INSERT INTO fundamentals (stock_id, report_date, period, eps, pe_ratio, dividend_yield, revenue, revenue_yoy, roe, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2330", "2024-01-02", "2024Q1", 6.5, 15.0, 0.03, 5000.0, 0.15, 15.0, "2024-01-01"),
        )
        conn.execute(
            "INSERT INTO fundamentals (stock_id, report_date, period, eps, pe_ratio, dividend_yield, revenue, revenue_yoy, roe, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2454", "2024-01-02", "2024Q1", 8.0, 25.0, 0.02, 3000.0, 0.05, 12.0, "2024-01-01"),
        )
        conn.commit()
        conn.close()
        yield tmp.name
        Path(tmp.name).unlink(missing_ok=True)

    def test_run_screen_no_criteria(self, db_path):
        df = run_screen(ScreenCriteria(), db_path=db_path)
        assert len(df) >= 2

    def test_run_screen_pe_min(self, db_path):
        df = run_screen(ScreenCriteria(pe_min=20.0), db_path=db_path)
        assert len(df) == 1
        assert "2454" in df["stock_id"].values

    def test_run_screen_pe_max(self, db_path):
        df = run_screen(ScreenCriteria(pe_max=20.0), db_path=db_path)
        assert len(df) == 1
        assert "2330" in df["stock_id"].values

    def test_run_screen_pe_range(self, db_path):
        df = run_screen(ScreenCriteria(pe_min=10.0, pe_max=20.0), db_path=db_path)
        assert len(df) == 1
        assert "2330" in df["stock_id"].values

    def test_run_screen_volume_min(self, db_path):
        df = run_screen(ScreenCriteria(volume_min=1_000_000), db_path=db_path)
        assert len(df) == 1
        assert "2330" in df["stock_id"].values

    def test_run_screen_stock_ids_filter(self, db_path):
        df = run_screen(ScreenCriteria(), stock_ids=["2330"], db_path=db_path)
        assert len(df) == 1
        assert "2330" in df["stock_id"].values

    def test_run_screen_empty_result(self, db_path):
        df = run_screen(ScreenCriteria(pe_min=100.0), db_path=db_path)
        assert len(df) == 0

    def test_run_screen_no_db_table(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            # Don't create tables — run_screen should gracefully return empty DataFrame
            df = run_screen(ScreenCriteria(), db_path=tmp.name)
            assert len(df) == 0
        finally:
            Path(tmp.name).unlink(missing_ok=True)
