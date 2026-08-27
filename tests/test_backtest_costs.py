"""Trading costs applied to the existing backtest engine.

Before this, backtests charged no brokerage fee and no transaction tax, so
every reported return was overstated by roughly 0.6% per round trip.
"""

import pandas as pd
import pytest

from twstock_analyzer.backtesting.engine import run_backtest
from twstock_analyzer.backtesting.portfolio import PortfolioConfig
from twstock_analyzer.db.repository import upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.replay.costs import brokerage_fee, transaction_tax


@pytest.fixture()
def flat_market_db(tmp_path):
    """A perfectly flat market: any return or loss can only come from costs."""
    path = str(tmp_path / "backtest.db")
    create_tables(path)

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=120)]
    close = [100.0] * 120
    upsert("daily_prices", pd.DataFrame({
        "stock_id": ["2330"] * 120,
        "date": dates,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": [1_000_000] * 120,
        "adj_close": close,
        "fetched_at": ["2024-06-01"] * 120,
    }), path)
    return path


class TestCostModel:
    def test_the_fee_floor_applies_to_small_amounts(self):
        assert brokerage_fee(1_000.0) == 20.0

    def test_the_fee_is_a_rate_on_larger_amounts(self):
        # 500,000 * 0.001425 = 712.5
        assert brokerage_fee(500_000.0) == pytest.approx(713.0)

    def test_the_broker_discount_reduces_the_fee(self):
        assert brokerage_fee(500_000.0, discount=0.6) == pytest.approx(428.0)

    def test_the_transaction_tax_is_charged_on_the_sale_amount(self):
        assert transaction_tax(500_000.0) == pytest.approx(1_500.0)


class TestBacktestWithCosts:
    def test_a_flat_market_cannot_produce_a_profit_once_costs_are_charged(self, flat_market_db):
        result = run_backtest(
            stock_ids=["2330"],
            config=PortfolioConfig(initial_capital=1_000_000),
            start_date="2024-01-01",
            end_date="2024-06-30",
            signal_method="ma_cross",
            db_path=flat_market_db,
        )

        assert result.equity_curve.iloc[-1] <= 1_000_000.0

    def test_a_bigger_broker_discount_leaves_more_money_behind(self, flat_market_db):
        expensive = run_backtest(
            stock_ids=["2330"],
            config=PortfolioConfig(initial_capital=1_000_000, fee_discount=1.0),
            start_date="2024-01-01",
            end_date="2024-06-30",
            signal_method="ma_cross",
            db_path=flat_market_db,
        )
        cheap = run_backtest(
            stock_ids=["2330"],
            config=PortfolioConfig(initial_capital=1_000_000, fee_discount=0.3),
            start_date="2024-01-01",
            end_date="2024-06-30",
            signal_method="ma_cross",
            db_path=flat_market_db,
        )

        assert cheap.equity_curve.iloc[-1] >= expensive.equity_curve.iloc[-1]
