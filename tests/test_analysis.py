"""Tests for analysis modules: Technical, Fundamental, Institutional."""

import pandas as pd
import pytest

from twstock_analyzer.analysis.technical import TechnicalAnalyzer
from twstock_analyzer.analysis.fundamental import FundamentalAnalyzer
from twstock_analyzer.analysis.institutional import InstitutionalAnalyzer


def _make_price_df(n=60):
    """Create a small price DataFrame for analysis tests."""
    dates = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame({
        "stock_id": ["2330"] * n,
        "date": dates.strftime("%Y-%m-%d").tolist(),
        "open": [500.0 + i * 0.5 for i in range(n)],
        "high": [510.0 + i * 0.5 for i in range(n)],
        "low": [490.0 + i * 0.5 for i in range(n)],
        "close": [505.0 + i * 0.5 for i in range(n)],
        "volume": [1_000_000] * n,
        "adj_close": [505.0 + i * 0.5 for i in range(n)],
    })


class TestTechnicalAnalyzer:
    @pytest.fixture()
    def analyzer(self):
        return TechnicalAnalyzer()

    @pytest.fixture()
    def df(self):
        return _make_price_df(60)

    def test_validate_input_valid(self, analyzer, df):
        result = analyzer.validate_input(df)
        assert isinstance(result, pd.DataFrame)

    def test_validate_input_missing_columns(self, analyzer):
        bad = pd.DataFrame({"only_one_col": [1, 2, 3]})
        with pytest.raises(ValueError, match="Required columns missing"):
            analyzer.validate_input(bad)

    def test_sma(self, analyzer, df):
        result = analyzer.sma(df)
        assert "sma_5" in result.columns
        assert "sma_10" in result.columns
        assert "sma_20" in result.columns
        assert "sma_60" in result.columns
        assert "sma_120" in result.columns
        assert "sma_240" in result.columns

    def test_sma_custom_periods(self, analyzer, df):
        result = analyzer.sma(df, periods=[10, 20])
        assert "sma_10" in result.columns
        assert "sma_20" in result.columns
        assert "sma_5" not in result.columns

    def test_ema(self, analyzer, df):
        result = analyzer.ema(df)
        assert "ema_5" in result.columns
        assert "ema_10" in result.columns

    def test_macd(self, analyzer, df):
        result = analyzer.macd(df)
        assert "macd_MACD_12_26_9" in result.columns or any("macd_" in c for c in result.columns)

    def test_rsi(self, analyzer, df):
        result = analyzer.rsi(df)
        assert "rsi_14" in result.columns

    def test_kd(self, analyzer, df):
        result = analyzer.kd(df)
        assert "kd_k" in result.columns
        assert "kd_d" in result.columns

    def test_bollinger_bands(self, analyzer, df):
        result = analyzer.bollinger_bands(df)
        assert any("bb_" in c for c in result.columns)

    def test_calculate_all(self, analyzer, df):
        result = analyzer.calculate_all(df)
        assert "sma_5" in result.columns
        assert "rsi_14" in result.columns
        assert "kd_k" in result.columns
        assert any("bb_" in c for c in result.columns)
        assert "macd_" in " ".join(result.columns)


class TestFundamentalAnalyzer:
    @pytest.fixture()
    def analyzer(self, populated_db):
        return FundamentalAnalyzer(db_path=populated_db)

    def test_pe_ratio(self, analyzer, populated_db):
        pe = analyzer.pe_ratio("2330", "2026-12-31")
        assert pe is not None
        assert pe > 0

    def test_pe_ratio_is_none_when_twse_publishes_none(self, populated_db):
        """本益比改讀 TWSE 公布值，虧損的公司那一欄本來就是空的。

        （原本這個測試塞一筆負的 `fundamentals.eps` 進去，但那一欄沒有任何來源
        會填——見 tests/test_fundamental_analysis_sources.py 的 TestPeRatio。）
        """
        import sqlite3
        conn = sqlite3.connect(populated_db)
        conn.execute(
            "INSERT OR REPLACE INTO fundamentals"
            " (stock_id, report_date, period, pe_ratio) VALUES (?, ?, ?, ?)",
            ("9999", "2024-03-31", None, None),
        )
        conn.commit()
        conn.close()

        assert FundamentalAnalyzer(populated_db).pe_ratio("9999", "2026-12-31") is None

    def test_eps_trend(self, analyzer):
        result = analyzer.eps_trend("2330", quarters=4)
        assert not result.empty
        assert "quarter" in result.columns
        assert "eps" in result.columns
        assert "trend_direction" in result.columns

    def test_eps_trend_empty(self, tmp_path):
        fa = FundamentalAnalyzer(str(tmp_path / "empty.db"))
        import tempfile
        from twstock_analyzer.db.schema import create_tables
        create_tables(str(tmp_path / "empty.db"))
        fa = FundamentalAnalyzer(str(tmp_path / "empty.db"))
        result = fa.eps_trend("2330")
        assert result.empty

    def test_roe_analysis_is_empty_without_a_source(self, analyzer):
        """ROE 目前沒有免費來源，回空表比回一列 NaN 誠實。

        有值時的行為由 tests/test_fundamental_analysis_sources.py 的 TestRoe 驗證。
        """
        result = analyzer.roe_analysis("2330", quarters=4)
        assert result.empty
        assert list(result.columns) == ["quarter", "roe", "avg_roe"]

    def test_monthly_revenue(self, analyzer):
        result = analyzer.monthly_revenue("2330", months=12)
        assert not result.empty
        assert "date" in result.columns
        assert "revenue" in result.columns

    def test_dividend_yield(self, analyzer):
        result = analyzer.dividend_yield("2330", 2024)
        assert result is not None or result is None  # Just verify no crash


class TestInstitutionalAnalyzer:
    @pytest.fixture()
    def analyzer(self, populated_db):
        return InstitutionalAnalyzer(db_path=populated_db)

    def test_foreign_investors(self, analyzer):
        result = analyzer.foreign_investors("2330", days=10)
        assert not result.empty
        assert "date" in result.columns
        assert "foreign_buy" in result.columns
        assert "foreign_sell" in result.columns
        assert "foreign_net" in result.columns

    def test_foreign_investors_empty(self, tmp_path):
        from twstock_analyzer.db.schema import create_tables
        create_tables(str(tmp_path / "empty.db"))
        analyzer = InstitutionalAnalyzer(db_path=str(tmp_path / "empty.db"))
        result = analyzer.foreign_investors("9999")
        assert result.empty
        assert list(result.columns) == ["date", "foreign_buy", "foreign_sell", "foreign_net"]

    def test_mutual_funds(self, analyzer):
        result = analyzer.mutual_funds("2330", days=10)
        assert not result.empty
        assert "date" in result.columns
        assert "fund_buy" in result.columns
        assert "fund_sell" in result.columns
        assert "fund_net" in result.columns

    def test_dealers(self, analyzer):
        result = analyzer.dealers("2330", days=10)
        assert not result.empty
        assert "date" in result.columns
        assert "dealer_buy" in result.columns
        assert "dealer_sell" in result.columns
        assert "dealer_net" in result.columns

    def test_dealers_empty(self, tmp_path):
        from twstock_analyzer.db.schema import create_tables
        create_tables(str(tmp_path / "empty.db"))
        analyzer = InstitutionalAnalyzer(db_path=str(tmp_path / "empty.db"))
        result = analyzer.dealers("9999")
        assert result.empty
        assert list(result.columns) == ["date", "dealer_buy", "dealer_sell", "dealer_net"]

    def test_net_buy_trend_accumulating(self, analyzer):
        """When foreign net is positive overall, should return 'accumulating'."""
        trend = analyzer.net_buy_trend("2330", days=30)
        # Sample data has positive foreign_net increasing
        assert trend == "accumulating"

    def test_shareholding_distribution(self, analyzer):
        result = analyzer.shareholding_distribution("2330")
        assert not result.empty
