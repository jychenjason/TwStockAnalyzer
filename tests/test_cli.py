"""CLI invocation tests for TwStockAnalyzer."""

import json

import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.db.repository import set_default_db_path, upsert


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Every test gets its own isolated temp DB and log file."""
    db_file = str(tmp_path / "test.db")
    log_file = str(tmp_path / "test.log")
    create_tables(db_file)
    set_default_db_path(db_file)
    monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_file)
    monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", log_file)
    yield db_file


@pytest.fixture()
def runner():
    return CliRunner()


class TestCLIHelp:
    def test_help_shows_commands(self, runner):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "update" in result.stdout
        assert "analyze" in result.stdout
        assert "report" in result.stdout
        assert "list" in result.stdout

    def test_update_help(self, runner):
        result = runner.invoke(app, ["update", "--help"])
        assert result.exit_code == 0
        assert "--stock" in result.stdout
        assert "--all" not in result.stdout

    def test_analyze_help(self, runner):
        result = runner.invoke(app, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "--indicators" in result.stdout

    def test_report_help(self, runner):
        result = runner.invoke(app, ["report", "--help"])
        assert result.exit_code == 0
        assert "--format" in result.stdout


class TestCLIUpdate:
    def test_update_no_stock(self, runner):
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        assert "--stock" in result.stdout

    def test_update_invalid_stock_id(self, runner):
        result = runner.invoke(app, ["update", "--stock", "abc"])
        assert result.exit_code != 0

    def test_update_invalid_stock_id_too_short(self, runner):
        result = runner.invoke(app, ["update", "--stock", "123"])
        assert result.exit_code != 0

    def test_update_valid_stock_id(self, runner):
        result = runner.invoke(app, ["update", "--stock", "2330"])
        assert result.exit_code in (0, 1)


class TestCLIAnalyze:
    def test_analyze_no_data(self, runner):
        result = runner.invoke(app, ["analyze", "2330"])
        assert result.exit_code == 0
        assert "No data found" in result.stdout

    def test_analyze_invalid_stock_id(self, runner):
        result = runner.invoke(app, ["analyze", "abc"])
        assert result.exit_code != 0

    def test_analyze_with_indicators(self, runner):
        result = runner.invoke(app, ["analyze", "2330", "--indicators", "macd,rsi"])
        assert result.exit_code == 0

    def test_analyze_json_output(self, runner):
        result = runner.invoke(app, ["analyze", "2330", "--output", "json"])
        assert result.exit_code == 0


class TestCLIReport:
    def test_report_json_empty(self, runner):
        result = runner.invoke(app, ["report", "2330", "--format", "json", "--output", "-"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["stock_id"] == "2330"
        assert "generated_at" in data

    def test_report_html_no_data(self, runner):
        """HTML report with no data should exit with error."""
        result = runner.invoke(app, ["report", "2330", "--format", "html"])
        assert result.exit_code != 0
        assert "No price data found" in result.stdout

    def test_report_html_with_data(self, populated_db, sample_daily_prices, runner, monkeypatch):
        """HTML report should generate a Plotly HTML file with technical chart."""
        import twstock_analyzer.cli.main as cli_main
        original_db = cli_main.DEFAULT_DB
        cli_main.DEFAULT_DB = populated_db
        try:
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
                tmp_html = f.name
            try:
                result = runner.invoke(app, ["report", "2330", "--format", "html", "--output", tmp_html])
                assert result.exit_code == 0
                assert "Report saved to" in result.stdout
                # Verify file exists and is non-empty
                assert os.path.exists(tmp_html)
                assert os.path.getsize(tmp_html) > 0
                # Verify it contains Plotly chart content
                with open(tmp_html, "r") as f:
                    content = f.read()
                assert "Plotly" in content or "plotly" in content.lower() or "candlestick" in content.lower()
            finally:
                if os.path.exists(tmp_html):
                    os.unlink(tmp_html)
        finally:
            cli_main.DEFAULT_DB = original_db

    def test_report_html_custom_output(self, populated_db, sample_daily_prices, runner, monkeypatch):
        """HTML report should respect custom --output path."""
        import twstock_analyzer.cli.main as cli_main
        import os
        import tempfile
        original_db = cli_main.DEFAULT_DB
        cli_main.DEFAULT_DB = populated_db
        try:
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
                tmp_html = f.name
            try:
                result = runner.invoke(app, ["report", "2330", "--format", "html", "--output", tmp_html])
                assert result.exit_code == 0
                assert os.path.exists(tmp_html)
            finally:
                if os.path.exists(tmp_html):
                    os.unlink(tmp_html)
        finally:
            cli_main.DEFAULT_DB = original_db

    def test_report_html_default_filename(self, populated_db, sample_daily_prices, runner, monkeypatch):
        """Default HTML output should be report-{stock_id}.html, not report.html."""
        import twstock_analyzer.cli.main as cli_main
        import os
        original_db = cli_main.DEFAULT_DB
        cli_main.DEFAULT_DB = populated_db
        try:
            result = runner.invoke(app, ["report", "2330", "--format", "html"])
            assert result.exit_code == 0
            # The default should be report-2330.html, not report.html
            assert os.path.exists("report-2330.html")
            os.unlink("report-2330.html")
        finally:
            cli_main.DEFAULT_DB = original_db


class TestCLIList:
    def test_list_empty(self, runner):
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0

    def test_list_with_top(self, runner):
        result = runner.invoke(app, ["list", "--top", "10"])
        assert result.exit_code == 0

    def test_list_with_populated_db(self, populated_db, sample_daily_prices):
        """List should show stocks when data exists."""
        upsert("daily_prices", sample_daily_prices, populated_db)
        import twstock_analyzer.cli.main as cli_main
        original_db = cli_main.DEFAULT_DB
        cli_main.DEFAULT_DB = populated_db
        runner = CliRunner()
        try:
            result = runner.invoke(app, ["list", "--top", "10"])
            assert result.exit_code == 0
            assert "2330" in result.stdout
        finally:
            cli_main.DEFAULT_DB = original_db


class TestCLIUpdateExisting:
    def test_update_existing_empty_db(self, runner):
        """--stock existing on empty DB should show suggestion."""
        result = runner.invoke(app, ["update", "--stock", "existing"])
        assert result.exit_code == 0
        assert "No stocks in database" in result.stdout

    def test_update_existing_with_populated_db(self, populated_db, sample_daily_prices, runner, monkeypatch):
        """--stock existing iterates over stocks in DB."""
        import twstock_analyzer.cli.main as cli_main
        original_db = cli_main.DEFAULT_DB
        cli_main.DEFAULT_DB = populated_db
        upsert("daily_prices", sample_daily_prices, populated_db)
        runner = CliRunner()
        try:
            result = runner.invoke(app, ["update", "--stock", "existing"])
            assert result.exit_code == 0
            assert "Updating 1/1:" in result.stdout
            assert "2330" in result.stdout
            assert "Updated 1/1 stocks" in result.stdout
        finally:
            cli_main.DEFAULT_DB = original_db

    def test_update_existing_multiple_stocks(self, populated_db, monkeypatch):
        """--stock existing with multiple distinct stock_ids."""
        import sqlite3
        import twstock_analyzer.cli.main as cli_main
        original_db = cli_main.DEFAULT_DB
        cli_main.DEFAULT_DB = populated_db
        # Insert a second stock
        conn = sqlite3.connect(populated_db)
        conn.execute(
            "INSERT OR REPLACE INTO daily_prices "
            "(stock_id, date, open, high, low, close, volume, adj_close, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2317", "2025-07-02", 100, 105, 98, 102, 500000, 102, "2025-07-02"),
        )
        conn.commit()
        conn.close()
        runner = CliRunner()
        try:
            result = runner.invoke(app, ["update", "--stock", "existing"])
            assert result.exit_code == 0
            assert "2317" in result.stdout
            assert "2330" in result.stdout
        finally:
            cli_main.DEFAULT_DB = original_db
