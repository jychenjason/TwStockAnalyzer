from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from twstock_analyzer.backtesting.metrics import BacktestMetrics, compute_metrics
from twstock_analyzer.backtesting.portfolio import AllocationMethod, PortfolioConfig
from twstock_analyzer.backtesting.signals import SignalGenerator, SignalType
from twstock_analyzer.replay.costs import brokerage_fee, transaction_tax


@dataclass
class BacktestResult:
    stock_ids: list[str] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    config: PortfolioConfig | None = None


def run_backtest(
    stock_ids: list[str],
    config: PortfolioConfig | None = None,
    start_date: str = "2024-01-01",
    end_date: str = "2026-07-01",
    signal_method: str = "ma_cross",
    db_path: str = "data/twstock.db",
    logger=None,
) -> BacktestResult:
    """Run a multi-asset backtest simulation.

    Args:
        stock_ids: List of stock IDs to include in the portfolio.
        config: Portfolio configuration (capital, allocation, rebalance).
        start_date: Simulation start date.
        end_date: Simulation end date.
        signal_method: Signal generation method ('ma_cross', 'rsi', 'macd').
        db_path: Path to the SQLite database.
        logger: Optional logger.

    Returns:
        BacktestResult with performance metrics and equity curve.
    """
    if config is None:
        config = PortfolioConfig()

    import sqlite3

    conn = sqlite3.connect(db_path)

    try:
        all_prices: dict[str, pd.DataFrame] = {}

        for sid in stock_ids:
            df = pd.read_sql_query(
                "SELECT date, close FROM daily_prices WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
                conn, params=(sid, start_date, end_date),
            )
            if df.empty:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            df = df.rename(columns={"close": sid})
            df[sid] = pd.to_numeric(df[sid], errors="coerce")
            all_prices[sid] = df[[sid]]

        if not all_prices:
            return BacktestResult(stock_ids=stock_ids, start_date=start_date, end_date=end_date)

        # Merge all price series on date index
        price_df = pd.concat(all_prices.values(), axis=1, join="inner").dropna()
        if price_df.empty:
            return BacktestResult(stock_ids=stock_ids, start_date=start_date, end_date=end_date)

        price_df.columns = stock_ids  # Align columns back to stock_ids
    finally:
        conn.close()

    # For single stock, use that stock's OHLCV for signal generation
    if len(stock_ids) == 1:
        sid = stock_ids[0]
        conn2 = sqlite3.connect(db_path)
        try:
            ohlcv = pd.read_sql_query(
                "SELECT date, open, high, low, close, volume FROM daily_prices WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
                conn2, params=(sid, start_date, end_date),
            )
        except Exception:
            ohlcv = pd.DataFrame()
        finally:
            conn2.close()

        if not ohlcv.empty:
            ohlcv["date"] = pd.to_datetime(ohlcv["date"])
            sg = SignalGenerator(logger=logger)
            signals = sg.generate(ohlcv, method=signal_method)
            signals.index = ohlcv["date"]
        else:
            signals = pd.Series(0, index=price_df.index)
    else:
        # Multi-stock: use equal-weighted average close for composite signal
        composite = price_df.mean(axis=1)
        composite_df = composite.reset_index()
        composite_df.columns = ["date", "close"]
        composite_df["open"] = composite_df["close"]
        composite_df["high"] = composite_df["close"]
        composite_df["low"] = composite_df["close"]
        composite_df["volume"] = 0
        sg = SignalGenerator(logger=logger)
        signals = sg.generate(composite_df, method=signal_method)
        signals.index = composite_df["date"]

    # Portfolio simulation
    n_stocks = len(stock_ids)
    capital = config.initial_capital
    equity_curve_list: list[float] = [capital]
    positions: dict[str, float] = {}

    if config.allocation_method == AllocationMethod.EQUAL:
        alloc_per_stock = 1.0 / n_stocks
    elif config.allocation_method == AllocationMethod.FIXED:
        alloc_per_stock = 1.0 / n_stocks  # simplified
    else:
        alloc_per_stock = 1.0 / n_stocks

    for date_idx in range(len(price_df)):
        current_prices = price_df.iloc[date_idx]
        sig = signals.iloc[date_idx] if date_idx < len(signals) else 0

        # Evaluate current portfolio value
        total_value = capital
        for sid in stock_ids:
            if sid in positions and sid in current_prices.index:
                total_value += positions[sid] * current_prices[sid]

        if sig == SignalType.BUY.value and not positions:
            # Deploy capital: buy each stock with allocated portion.  Brokerage
            # fee is deducted from the cash actually spent, so the position size
            # reflects what the money really buys.
            capital = 0
            invest_per_stock = total_value * alloc_per_stock
            for sid in stock_ids:
                if sid in current_prices.index and current_prices[sid] > 0:
                    fee = brokerage_fee(invest_per_stock, config.fee_discount)
                    positions[sid] = max(invest_per_stock - fee, 0.0) / current_prices[sid]

        elif sig == SignalType.SELL.value:
            # Liquidate all positions, paying brokerage fee and transaction tax.
            for sid in list(positions.keys()):
                if sid in current_prices.index:
                    proceeds = positions[sid] * current_prices[sid]
                    capital += (
                        proceeds
                        - brokerage_fee(proceeds, config.fee_discount)
                        - transaction_tax(proceeds)
                    )
                positions[sid] = 0
            positions = {}

        # Record total portfolio value
        portfolio_value = capital
        for sid in stock_ids:
            if sid in positions and sid in current_prices.index:
                portfolio_value += positions[sid] * current_prices[sid]
        equity_curve_list.append(portfolio_value)

    # Build equity curve Series
    equity_curve = pd.Series(equity_curve_list, index=[price_df.index[0]] + list(price_df.index))

    # Compute metrics
    metrics = compute_metrics(equity_curve)

    signal_df = pd.DataFrame({"signal": signals.values}, index=signals.index)

    return BacktestResult(
        stock_ids=stock_ids,
        start_date=start_date,
        end_date=end_date,
        metrics=metrics,
        equity_curve=equity_curve,
        signals=signal_df,
        config=config,
    )
