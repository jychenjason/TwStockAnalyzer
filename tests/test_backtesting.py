"""Tests for the backtesting module."""

import os
import tempfile

import numpy as np
import pandas as pd
import pytest
import sqlite3

from twstock_analyzer.backtesting.engine import BacktestResult, run_backtest
from twstock_analyzer.backtesting.metrics import BacktestMetrics, compute_metrics
from twstock_analyzer.backtesting.portfolio import AllocationMethod, PortfolioConfig
from twstock_analyzer.backtesting.signals import SignalGenerator, SignalType


# ---------------------------------------------------------------------------
# SignalGenerator tests
# ---------------------------------------------------------------------------


class TestSignalGenerator:
    @pytest.fixture
    def price_df(self):
        """Generate ~1 year of price data for signal testing."""
        dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(len(dates)) * 0.5)
        return pd.DataFrame({
            "date": dates,
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": np.random.randint(1_000_000, 10_000_000, len(dates)),
        })

    # -- direct method tests --

    def test_ma_crossover_returns_series(self, price_df):
        sg = SignalGenerator()
        signals = sg.ma_crossover(price_df)
        assert isinstance(signals, pd.Series)
        assert set(signals.dropna().unique()).issubset({-1, 0, 1})

    def test_rsi_threshold_returns_series(self, price_df):
        sg = SignalGenerator()
        signals = sg.rsi_threshold(price_df)
        assert isinstance(signals, pd.Series)
        assert set(signals.dropna().unique()).issubset({-1, 0, 1})

    def test_macd_crossover_returns_series(self, price_df):
        sg = SignalGenerator()
        signals = sg.macd_crossover(price_df)
        assert isinstance(signals, pd.Series)
        assert set(signals.dropna().unique()).issubset({-1, 0, 1})

    # -- factory dispatch tests --

    def test_generate_ma_cross(self, price_df):
        sg = SignalGenerator()
        signals = sg.generate(price_df, method="ma_cross")
        assert isinstance(signals, pd.Series)

    def test_generate_rsi(self, price_df):
        sg = SignalGenerator()
        signals = sg.generate(price_df, method="rsi")
        assert isinstance(signals, pd.Series)

    def test_generate_macd(self, price_df):
        sg = SignalGenerator()
        signals = sg.generate(price_df, method="macd")
        assert isinstance(signals, pd.Series)

    def test_generate_unknown_method(self, price_df):
        sg = SignalGenerator()
        with pytest.raises(ValueError, match="Unknown signal method"):
            sg.generate(price_df, method="unknown")

    # -- signal type values --

    def test_signal_type_values(self):
        assert SignalType.BUY.value == 1
        assert SignalType.SELL.value == -1
        assert SignalType.HOLD.value == 0


# ---------------------------------------------------------------------------
# PortfolioConfig tests
# ---------------------------------------------------------------------------


class TestPortfolioConfig:
    def test_defaults(self):
        config = PortfolioConfig()
        assert config.initial_capital == 1_000_000
        assert config.allocation_method == AllocationMethod.EQUAL
        assert config.rebalance_frequency == "none"
        assert config.benchmark_stock_id is None

    def test_custom_values(self):
        config = PortfolioConfig(
            initial_capital=500_000,
            allocation_method=AllocationMethod.FIXED,
            rebalance_frequency="monthly",
            benchmark_stock_id="0050",
        )
        assert config.initial_capital == 500_000
        assert config.allocation_method == AllocationMethod.FIXED
        assert config.rebalance_frequency == "monthly"
        assert config.benchmark_stock_id == "0050"


# ---------------------------------------------------------------------------
# BacktestMetrics tests
# ---------------------------------------------------------------------------


class TestBacktestMetrics:
    def test_defaults(self):
        m = BacktestMetrics()
        assert m.total_return == 0.0
        assert m.sharpe_ratio == 0.0
        assert m.max_drawdown == 0.0
        assert m.total_trades == 0

    def test_empty_equity_curve(self):
        m = compute_metrics(pd.Series(dtype=float))
        assert m.total_return == 0.0

    def test_single_value(self):
        m = compute_metrics(pd.Series([100.0]))
        assert m.total_return == 0.0

    def test_constant_growth(self):
        curve = pd.Series([100, 110, 121, 133.1])  # 10% each period
        m = compute_metrics(curve, periods_per_year=252)
        assert m.total_return > 0
        assert m.total_trades > 0

    def test_positive_sharpe(self):
        curve = pd.Series(np.linspace(100, 200, 252))  # steady uptrend
        m = compute_metrics(curve, periods_per_year=252)
        assert m.sharpe_ratio > 0

    def test_max_drawdown(self):
        curve = pd.Series([100, 120, 60, 110, 130])  # drop from 120 to 60
        m = compute_metrics(curve)
        assert m.max_drawdown < 0
        assert m.max_drawdown <= -0.4  # at least 40% drawdown

    def test_zero_volatility(self):
        """Constant equity curve → 0 vol, 0 sharpe."""
        curve = pd.Series([100.0] * 10)
        m = compute_metrics(curve)
        assert m.volatility == 0.0
        assert m.sharpe_ratio == 0.0
        assert m.max_drawdown == 0.0

    def test_negative_return(self):
        curve = pd.Series([100, 90, 80, 70])
        m = compute_metrics(curve)
        assert m.total_return < 0


# ---------------------------------------------------------------------------
# BacktestEngine tests
# ---------------------------------------------------------------------------


class TestBacktestEngine:
    @pytest.fixture
    def db_with_data(self):
        """Create a temp DB with sample price data for backtesting."""
        from twstock_analyzer.db.schema import create_tables

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        create_tables(tmp.name)
        conn = sqlite3.connect(tmp.name)

        # Insert 1 year of daily data for 2330
        dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(len(dates)) * 0.5)
        for i, date in enumerate(dates):
            conn.execute(
                "INSERT INTO daily_prices "
                "(stock_id, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "2330",
                    date.strftime("%Y-%m-%d"),
                    float(close[i] * 0.99),
                    float(close[i] * 1.02),
                    float(close[i] * 0.98),
                    float(close[i]),
                    int(np.random.randint(1_000_000, 10_000_000)),
                ),
            )
        conn.commit()
        conn.close()
        yield tmp.name
        os.unlink(tmp.name)

    # -- single-stock backtests --

    def test_run_backtest_single_stock(self, db_with_data):
        result = run_backtest(
            stock_ids=["2330"],
            config=PortfolioConfig(initial_capital=1_000_000),
            start_date="2024-01-01",
            end_date="2024-12-31",
            signal_method="ma_cross",
            db_path=db_with_data,
        )
        assert isinstance(result, BacktestResult)
        assert len(result.stock_ids) == 1
        assert "2330" in result.stock_ids
        assert len(result.equity_curve) > 0

    def test_backtest_default_config(self, db_with_data):
        result = run_backtest(
            stock_ids=["2330"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            db_path=db_with_data,
        )
        assert isinstance(result, BacktestResult)
        assert result.config is not None
        assert result.config.initial_capital == 1_000_000

    def test_backtest_empty_stock_list(self, db_with_data):
        result = run_backtest(
            stock_ids=[],
            start_date="2024-01-01",
            end_date="2024-12-31",
            db_path=db_with_data,
        )
        assert isinstance(result, BacktestResult)
        assert result.equity_curve.empty

    def test_backtest_nonexistent_stock(self, db_with_data):
        result = run_backtest(
            stock_ids=["9999"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            db_path=db_with_data,
        )
        assert isinstance(result, BacktestResult)
        assert result.equity_curve.empty

    def test_backtest_metrics_populated(self, db_with_data):
        result = run_backtest(
            stock_ids=["2330"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            signal_method="rsi",
            db_path=db_with_data,
        )
        m = result.metrics
        assert isinstance(m.total_return, float)
        assert isinstance(m.sharpe_ratio, float)
        assert isinstance(m.max_drawdown, float)

    def test_backtest_rsi_signal(self, db_with_data):
        result = run_backtest(
            stock_ids=["2330"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            signal_method="rsi",
            db_path=db_with_data,
        )
        assert len(result.equity_curve) > 0
        assert isinstance(result.metrics, BacktestMetrics)

    def test_backtest_macd_signal(self, db_with_data):
        result = run_backtest(
            stock_ids=["2330"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            signal_method="macd",
            db_path=db_with_data,
        )
        assert len(result.equity_curve) > 0

    # -- multi-stock backtests --

    def test_multi_stock_backtest(self, db_with_data):
        """Insert a second stock and run a multi-stock backtest."""
        conn = sqlite3.connect(db_with_data)
        dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
        np.random.seed(99)
        close = 50 + np.cumsum(np.random.randn(len(dates)) * 0.3)
        for i, date in enumerate(dates):
            conn.execute(
                "INSERT INTO daily_prices "
                "(stock_id, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "2454",
                    date.strftime("%Y-%m-%d"),
                    float(close[i] * 0.99),
                    float(close[i] * 1.02),
                    float(close[i] * 0.98),
                    float(close[i]),
                    int(np.random.randint(500_000, 5_000_000)),
                ),
            )
        conn.commit()
        conn.close()

        result = run_backtest(
            stock_ids=["2330", "2454"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            signal_method="ma_cross",
            db_path=db_with_data,
        )
        assert isinstance(result, BacktestResult)
        assert set(result.stock_ids) == {"2330", "2454"}
        assert len(result.equity_curve) > 0

    # -- config passthrough --

    def test_backtest_custom_initial_capital(self, db_with_data):
        result = run_backtest(
            stock_ids=["2330"],
            config=PortfolioConfig(initial_capital=500_000),
            start_date="2024-01-01",
            end_date="2024-12-31",
            signal_method="ma_cross",
            db_path=db_with_data,
        )
        assert result.equity_curve.iloc[0] == 500_000

    def test_backtest_result_fields(self, db_with_data):
        result = run_backtest(
            stock_ids=["2330"],
            start_date="2024-01-01",
            end_date="2024-12-31",
            signal_method="ma_cross",
            db_path=db_with_data,
        )
        assert result.start_date == "2024-01-01"
        assert result.end_date == "2024-12-31"
        assert isinstance(result.metrics, BacktestMetrics)
        assert isinstance(result.equity_curve, pd.Series)
        assert isinstance(result.signals, pd.DataFrame)
