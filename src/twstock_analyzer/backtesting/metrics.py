from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BacktestMetrics:
    total_return: float = 0.0
    cagr: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0


def compute_metrics(equity_curve: pd.Series, risk_free_rate: float = 0.02, periods_per_year: int = 252) -> BacktestMetrics:
    """Compute performance metrics from an equity curve (daily values)."""
    metrics = BacktestMetrics()

    if equity_curve.empty or len(equity_curve) < 2:
        return metrics

    initial = equity_curve.iloc[0]
    final = equity_curve.iloc[-1]

    if initial <= 0:
        return metrics

    metrics.total_return = (final - initial) / initial

    # CAGR
    n_years = len(equity_curve) / periods_per_year
    if n_years > 0:
        metrics.cagr = (final / initial) ** (1 / n_years) - 1
    else:
        metrics.cagr = metrics.total_return

    # Daily returns
    daily_returns = equity_curve.pct_change().dropna()
    if len(daily_returns) > 0:
        metrics.volatility = daily_returns.std() * np.sqrt(periods_per_year)
        excess_returns = daily_returns - risk_free_rate / periods_per_year
        if daily_returns.std() > 0:
            metrics.sharpe_ratio = np.sqrt(periods_per_year) * excess_returns.mean() / daily_returns.std()

    # Max drawdown
    rolling_max = equity_curve.expanding().max()
    drawdowns = (equity_curve - rolling_max) / rolling_max
    metrics.max_drawdown = drawdowns.min()

    # Win rate (estimate from daily returns)
    if len(daily_returns) > 0:
        positive_days = (daily_returns > 0).sum()
        metrics.win_rate = positive_days / len(daily_returns)
        metrics.total_trades = len(daily_returns)

    return metrics
