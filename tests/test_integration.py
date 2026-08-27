"""Integration tests: full pipeline with mocked sources."""

import json
import os

import pandas as pd
import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.analysis.technical import TechnicalAnalyzer
from twstock_analyzer.analysis.fundamental import FundamentalAnalyzer
from twstock_analyzer.analysis.institutional import InstitutionalAnalyzer
from twstock_analyzer.db.repository import upsert, has_data, set_default_db_path


def _patch_logger(monkeypatch, tmp_path):
    """Redirect the log file to a temp path to avoid PermissionError
    from Docker-owned data/twstock.log."""
    monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "test.log"))


@pytest.fixture()
def runner():
    return CliRunner()


class TestFullPipeline:
    """End-to-end: mock source → DataLoader → DB → Analysis → Report."""

    def test_full_pipeline_mocked(self, db_path, sample_daily_prices, sample_fundamentals, sample_quarterly_financials, sample_monthly_revenue, sample_institutional_trading):
        """Simulate full flow: fetch → store → analyze → report."""
        # 1. Store data
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)
        upsert("fundamentals", sample_fundamentals, db_path)
        upsert("quarterly_financials", sample_quarterly_financials, db_path)
        upsert("monthly_revenue", sample_monthly_revenue, db_path)
        upsert("institutional_trading", sample_institutional_trading, db_path)

        # 2. Verify data exists
        coverage = has_data("daily_prices", "2330", "2025-07-01", "2026-12-31", db_path)
        assert coverage["has_data"] is True
        assert coverage["row_count"] == 250

        # 3. Technical analysis
        conn = __import__("sqlite3").connect(db_path)
        df = pd.read_sql_query(
            "SELECT * FROM daily_prices WHERE stock_id='2330' ORDER BY date", conn
        )
        conn.close()

        analyzer = TechnicalAnalyzer()
        result = analyzer.calculate_all(df)
        assert "sma_5" in result.columns
        assert "rsi_14" in result.columns
        assert "kd_k" in result.columns

        # 4. Fundamental analysis
        fa = FundamentalAnalyzer(db_path)
        pe = fa.pe_ratio("2330", "2026-12-31")
        eps_trend = fa.eps_trend("2330", quarters=4)
        roe = fa.roe_analysis("2330", quarters=4)
        revenue = fa.monthly_revenue("2330", months=12)

        assert pe is None or pe > 0
        assert not eps_trend.empty
        assert not revenue.empty
        # ROE 沒有任何來源會填，所以這一步本來就該是空的。
        assert roe.empty

        # 5. Institutional analysis
        ia = InstitutionalAnalyzer(db_path)
        foreign = ia.foreign_investors("2330", days=10)
        funds = ia.mutual_funds("2330", days=10)
        trend = ia.net_buy_trend("2330", days=30)

        assert not foreign.empty
        assert not funds.empty
        assert trend in ("accumulating", "distributing", "neutral", None)

    def test_cli_analyze_with_data(self, db_path, sample_daily_prices, monkeypatch, tmp_path):
        """CLI analyze command with populated DB."""
        _patch_logger(monkeypatch, tmp_path)
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_path)

        runner = CliRunner()
        result = runner.invoke(app, ["analyze", "2330", "--indicators", "ma,rsi"])
        assert result.exit_code == 0
        assert "2330" in result.stdout

    def test_cli_report_json_with_data(self, db_path, sample_daily_prices, sample_fundamentals, sample_quarterly_financials, sample_monthly_revenue, monkeypatch, tmp_path):
        """CLI report JSON with populated DB."""
        _patch_logger(monkeypatch, tmp_path)
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)
        upsert("fundamentals", sample_fundamentals, db_path)
        upsert("quarterly_financials", sample_quarterly_financials, db_path)
        upsert("monthly_revenue", sample_monthly_revenue, db_path)
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_path)

        runner = CliRunner()
        result = runner.invoke(app, ["report", "2330", "--format", "json", "--output", "-"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["stock_id"] == "2330"
        assert data["prices_count"] == 250  # all 250 rows should be found

    def test_cli_list_with_data(self, db_path, sample_daily_prices, monkeypatch, tmp_path):
        """CLI list command with populated DB."""
        _patch_logger(monkeypatch, tmp_path)
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_path)

        runner = CliRunner()
        result = runner.invoke(app, ["list", "--top", "10"])
        assert result.exit_code == 0
        assert "2330" in result.stdout

    def test_technical_analysis_all_indicators(self, db_path, sample_daily_prices):
        """Verify all technical indicators compute without NaN issues."""
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)

        conn = __import__("sqlite3").connect(db_path)
        df = pd.read_sql_query(
            "SELECT * FROM daily_prices WHERE stock_id='2330' ORDER BY date", conn
        )
        conn.close()

        analyzer = TechnicalAnalyzer()
        result = analyzer.calculate_all(df)

        # Check all indicator columns exist
        expected_cols = [
            "sma_5", "sma_10", "sma_20", "sma_60", "sma_120", "sma_240",
            "ema_5", "ema_10", "ema_20", "ema_60",
            "rsi_14",
            "kd_k", "kd_d",
        ]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

        # Some early rows may have NaN (insufficient data for indicators)
        # But later rows should have values
        for col in expected_cols:
            non_null = result[col].dropna()
            assert len(non_null) > 0, f"Column {col} has all NaN values"

    def test_fundamental_eps_trend_detection(self, db_path, sample_quarterly_financials):
        """EPS trend should detect upward/downward movement."""
        set_default_db_path(db_path)
        upsert("quarterly_financials", sample_quarterly_financials, db_path)

        fa = FundamentalAnalyzer(db_path)
        trend = fa.eps_trend("2330", quarters=8)

        assert not trend.empty
        assert "trend_direction" in trend.columns
        # Sample data has increasing EPS, so latest trend should be "up"
        assert trend.iloc[0]["trend_direction"] == "up"

    def test_institutional_net_buy_trend(self, db_path, sample_institutional_trading):
        """Net buy trend should correctly classify accumulating/distributing."""
        set_default_db_path(db_path)
        upsert("institutional_trading", sample_institutional_trading, db_path)

        ia = InstitutionalAnalyzer(db_path)
        trend = ia.net_buy_trend("2330", days=30)
        assert trend == "accumulating"  # Positive foreign_net throughout

    def test_upsert_overwrites_existing(self, db_path, sample_daily_prices):
        """Upsert with same PK should overwrite, not duplicate."""
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)

        conn = __import__("sqlite3").connect(db_path)
        count1 = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE stock_id='2330'"
        ).fetchone()[0]
        conn.close()
        assert count1 == 250

        # Upsert same data again
        upsert("daily_prices", sample_daily_prices, db_path)

        conn = __import__("sqlite3").connect(db_path)
        count2 = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE stock_id='2330'"
        ).fetchone()[0]
        conn.close()
        assert count2 == 250  # Same count, not doubled

    def test_cross_module_workflow(self, db_path, sample_daily_prices, sample_fundamentals, sample_quarterly_financials, sample_monthly_revenue, sample_institutional_trading):
        """Test realistic workflow: update → analyze → report."""
        set_default_db_path(db_path)

        # Step 1: "Update" - store data
        upsert("daily_prices", sample_daily_prices, db_path)
        upsert("fundamentals", sample_fundamentals, db_path)
        upsert("quarterly_financials", sample_quarterly_financials, db_path)
        upsert("monthly_revenue", sample_monthly_revenue, db_path)
        upsert("institutional_trading", sample_institutional_trading, db_path)

        # Step 2: "Analyze" - run technical analysis
        conn = __import__("sqlite3").connect(db_path)
        df = pd.read_sql_query(
            "SELECT * FROM daily_prices WHERE stock_id='2330' ORDER BY date", conn
        )
        conn.close()

        assert not df.empty

        tech = TechnicalAnalyzer()
        analyzed = tech.calculate_all(df)
        assert "sma_5" in analyzed.columns

        # Step 3: "Report" - generate fundamental report
        fa = FundamentalAnalyzer(db_path)
        pe = fa.pe_ratio("2330", "2026-12-31")
        eps_trend = fa.eps_trend("2330")

        assert not eps_trend.empty

        # Step 4: "List" - verify stock appears
        ia = InstitutionalAnalyzer(db_path)
        foreign = ia.foreign_investors("2330")
        assert not foreign.empty

    def test_cli_update_existing_empty_db(self, runner, monkeypatch, tmp_path):
        """update existing on empty DB shows suggestion."""
        _patch_logger(monkeypatch, tmp_path)
        db_file = str(tmp_path / "test_empty.db")
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_file)
        runner = CliRunner()
        result = runner.invoke(app, ["update", "--stock", "existing"])
        assert result.exit_code == 0
        assert "No stocks in database" in result.stdout

    def test_cli_update_existing_with_data(self, db_path, sample_daily_prices, monkeypatch, tmp_path):
        """update existing iterates over stocks and shows progress."""
        _patch_logger(monkeypatch, tmp_path)
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_path)

        runner = CliRunner()
        result = runner.invoke(app, ["update", "--stock", "existing"])
        assert result.exit_code == 0
        assert "Updating 1/1:" in result.stdout
        assert "2330" in result.stdout
        assert "Updated 1/1 stocks" in result.stdout

    def test_cli_update_existing_multiple_stocks(self, db_path, sample_daily_prices, monkeypatch, tmp_path):
        """update existing with multiple distinct stock_ids."""
        _patch_logger(monkeypatch, tmp_path)
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_path)

        # Add a second stock directly
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO daily_prices "
            "(stock_id, date, open, high, low, close, volume, adj_close, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2317", "2025-07-02", 100, 105, 98, 102, 500000, 102, "2025-07-02"),
        )
        conn.commit()
        conn.close()

        runner = CliRunner()
        result = runner.invoke(app, ["update", "--stock", "existing"])
        assert result.exit_code == 0
        assert "2317" in result.stdout
        assert "2330" in result.stdout
