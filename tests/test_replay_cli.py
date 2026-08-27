"""CLI tests for the read-only Replay commands.

Prior art: tests/test_cli.py -- same CliRunner + isolated temp DB shape.
"""

import pandas as pd
import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.replay import create_session


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    create_tables(path)
    set_default_db_path(path)
    monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", path)
    monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "test.log"))

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-08-01", periods=20)]
    closes = [100.0 + i for i in range(20)]
    opens = [100.0] + [closes[i - 1] + 3.0 for i in range(1, 20)]
    upsert("daily_prices", pd.DataFrame({
        "stock_id": ["2317"] * 20,
        "date": dates,
        "open": opens,
        "high": [c + 2 for c in closes],
        "low": [c - 2 for c in closes],
        "close": closes,
        "volume": [1_000_000] * 20,
        "adj_close": closes,
        "fetched_at": ["2025-09-01"] * 20,
    }), path)
    return path


@pytest.fixture()
def traded_session(db_file):
    session = create_session("2317", "2025-08-08", db_path=db_file)
    session.place_order("buy", lots=1)
    session.advance()
    session.place_order("sell", lots=1)
    session.advance()
    return session


@pytest.fixture()
def runner():
    return CliRunner()


class TestReplayList:
    def test_listing_shows_each_session(self, runner, traded_session):
        result = runner.invoke(app, ["replay", "list"])

        assert result.exit_code == 0
        assert "2317" in result.stdout
        assert str(traded_session.id) in result.stdout

    def test_listing_with_no_sessions_is_not_an_error(self, runner, db_file):
        result = runner.invoke(app, ["replay", "list"])

        assert result.exit_code == 0


class TestReplayShow:
    def test_showing_a_session_reports_its_performance(self, runner, traded_session):
        result = runner.invoke(app, ["replay", "show", str(traded_session.id)])

        assert result.exit_code == 0
        assert "Buy & Hold" in result.stdout

    def test_showing_an_unknown_session_fails_clearly(self, runner, db_file):
        result = runner.invoke(app, ["replay", "show", "999"])

        assert result.exit_code == 1
        assert "999" in result.stdout


class TestReplayExport:
    def test_exporting_writes_the_trade_detail(self, runner, traded_session, tmp_path):
        target = tmp_path / "trades.csv"

        result = runner.invoke(
            app, ["replay", "export", str(traded_session.id), "--output", str(target)]
        )

        assert result.exit_code == 0
        exported = pd.read_csv(target)
        assert len(exported) == 2
        for column in ("placed_on", "filled_on", "side", "lots", "fill_price", "fee", "tax"):
            assert column in exported.columns

    def test_exporting_an_unknown_session_fails_clearly(self, runner, db_file, tmp_path):
        result = runner.invoke(
            app, ["replay", "export", "999", "--output", str(tmp_path / "x.csv")]
        )

        assert result.exit_code == 1


class TestReadOnly:
    def test_the_commands_do_not_move_the_cursor(self, runner, traded_session, db_file):
        from twstock_analyzer.replay import load_session

        before = load_session(traded_session.id, db_path=db_file).cursor

        runner.invoke(app, ["replay", "list"])
        runner.invoke(app, ["replay", "show", str(traded_session.id)])

        assert load_session(traded_session.id, db_path=db_file).cursor == before
