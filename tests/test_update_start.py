"""Backfill tests for the update command's start-date option.

The seam under test is the CLI.  A recording stand-in for the data loader
captures the date range actually requested, so the tests observe behaviour
without touching the network.
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.db.repository import set_default_db_path
from twstock_analyzer.db.schema import create_tables


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    create_tables(db_file)
    set_default_db_path(db_file)
    monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_file)
    monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "test.log"))
    return db_file


class RecordingLoader:
    """Stands in for the real DataLoader and records what was asked of it."""

    def __init__(self):
        self.calls: list[tuple[str, str, str, str | None]] = []

    def get_data(self, stock_id, data_type, start_date, end_date=None, logger=None):
        self.calls.append((stock_id, data_type, start_date, end_date))
        dates = pd.bdate_range(start_date, periods=3).strftime("%Y-%m-%d").tolist()
        return pd.DataFrame({
            "date": dates,
            "open": [100.0] * 3,
            "high": [102.0] * 3,
            "low": [99.0] * 3,
            "close": [101.0] * 3,
            "volume": [1_000_000] * 3,
        })

    def get_stock_list(self, logger=None):
        return [{"stock_id": "2330"}]


@pytest.fixture()
def loader(monkeypatch):
    recording = RecordingLoader()
    monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: recording)
    return recording


@pytest.fixture()
def runner():
    return CliRunner()


class TestDailyBackfill:
    def test_start_option_backfills_from_the_requested_year(self, runner, loader):
        result = runner.invoke(app, ["update", "--stock", "2330", "--start", "2015-01-01"])

        assert result.exit_code == 0
        assert loader.calls[0][2] == "2015-01-01"

    def test_without_start_the_existing_one_year_window_is_kept(self, runner, loader):
        result = runner.invoke(app, ["update", "--stock", "2330"])

        assert result.exit_code == 0
        requested = datetime.strptime(loader.calls[0][2], "%Y-%m-%d")
        one_year_ago = datetime.now() - timedelta(days=365)
        assert abs((requested - one_year_ago).days) <= 1

    def test_start_earlier_than_cached_data_still_reaches_back(self, runner, loader):
        runner.invoke(app, ["update", "--stock", "2330", "--start", "2024-01-01"])
        loader.calls.clear()

        result = runner.invoke(app, ["update", "--stock", "2330", "--start", "2015-01-01"])

        assert result.exit_code == 0
        assert loader.calls, "a backfill reaching further back must still fetch"
        assert loader.calls[0][2] == "2015-01-01"

    def test_backfilling_the_same_range_twice_does_not_duplicate_rows(self, runner, loader, _isolated_db):
        import sqlite3

        runner.invoke(app, ["update", "--stock", "2330", "--start", "2015-01-01"])
        conn = sqlite3.connect(_isolated_db)
        first = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]

        runner.invoke(app, ["update", "--stock", "2330", "--start", "2015-01-01", "--force"])
        second = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        conn.close()

        assert first > 0
        assert second == first


class TestDividendBackfill:
    def test_dividends_are_fetched_and_stored(self, runner, monkeypatch, _isolated_db):
        import sqlite3

        class DividendLoader(RecordingLoader):
            def get_data(self, stock_id, data_type, start_date, end_date=None, logger=None):
                self.calls.append((stock_id, data_type, start_date, end_date))
                return pd.DataFrame({
                    "date": ["2020-06-18"],
                    "cash_dividend": [2.5],
                    "stock_dividend": [0.0],
                    "kind": ["息"],
                })

        monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: DividendLoader())

        result = runner.invoke(
            app, ["update", "--stock", "2330", "--type", "dividend", "--start", "2020-01-01"]
        )

        conn = sqlite3.connect(_isolated_db)
        rows = conn.execute("SELECT date, cash_dividend, kind FROM dividends").fetchall()
        conn.close()

        assert result.exit_code == 0
        assert rows == [("2020-06-18", 2.5, "息")]


class TestInstitutionalBackfill:
    @pytest.fixture(autouse=True)
    def _no_throttle(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *_: None)

    def test_start_option_reaches_further_back_than_the_default_window(self, runner, monkeypatch):
        requested: list[str] = []

        class InstitutionalLoader(RecordingLoader):
            def get_data(self, stock_id, data_type, start_date, end_date=None, logger=None):
                requested.append(start_date)
                return pd.DataFrame()

        monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: InstitutionalLoader())

        # Roll the start onto a weekday: the backfill deliberately skips
        # weekends, so anchoring the assertion to a Saturday or Sunday would
        # make this test pass or fail depending on the day it is run.
        start_dt = datetime.now() - timedelta(days=400)
        while start_dt.weekday() >= 5:
            start_dt += timedelta(days=1)
        start = start_dt.strftime("%Y-%m-%d")

        result = runner.invoke(
            app, ["update", "--stock", "2330", "--type", "institutional", "--start", start]
        )

        assert result.exit_code == 0
        assert min(requested) == start

    def test_one_failing_date_does_not_abort_the_whole_backfill(self, runner, monkeypatch, _isolated_db):
        import sqlite3

        seen: list[str] = []

        class FlakyLoader(RecordingLoader):
            def get_data(self, stock_id, data_type, start_date, end_date=None, logger=None):
                seen.append(start_date)
                if len(seen) == 1:
                    raise RuntimeError("rate limited")
                return pd.DataFrame({
                    "stock_id": ["2330"],
                    "date": [start_date],
                    "foreign_buy": [1000.0],
                    "foreign_sell": [500.0],
                    "foreign_net": [500.0],
                })

        monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: FlakyLoader())
        start = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")

        result = runner.invoke(
            app, ["update", "--stock", "2330", "--type", "institutional", "--start", start]
        )

        conn = sqlite3.connect(_isolated_db)
        stored = conn.execute("SELECT COUNT(*) FROM institutional_trading").fetchone()[0]
        conn.close()

        assert result.exit_code == 0
        assert len(seen) > 1, "the backfill must continue past the failing date"
        assert stored > 0
