from __future__ import annotations

from enum import Enum

import pandas as pd

from twstock_analyzer.analysis.technical import TechnicalAnalyzer


class SignalType(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0


class SignalGenerator:
    """Generate buy/sell signals from technical indicators."""

    def __init__(self, logger=None):
        self.tech = TechnicalAnalyzer(logger=logger)

    def ma_crossover(self, df: pd.DataFrame, fast: int = 20, slow: int = 60) -> pd.Series:
        """Generate signals on SMA crossover: BUY when fast crosses above slow, SELL on cross below."""
        df = df.copy()
        df = self.tech.sma(df, periods=[fast, slow])
        fast_col = f"sma_{fast}"
        slow_col = f"sma_{slow}"
        signals = pd.Series(SignalType.HOLD.value, index=df.index)
        signals[df[fast_col] > df[slow_col]] = SignalType.BUY.value
        # Only generate SELL if currently in position (handled by engine)
        signals[df[fast_col] < df[slow_col]] = SignalType.SELL.value
        return signals

    def rsi_threshold(self, df: pd.DataFrame, period: int = 14, oversold: float = 30, overbought: float = 70) -> pd.Series:
        """RSI-based signals: BUY when RSI crosses above oversold, SELL when crosses below overbought."""
        df = df.copy()
        df = self.tech.rsi(df, period=period)
        rsi_col = f"rsi_{period}"
        signals = pd.Series(SignalType.HOLD.value, index=df.index)
        signals[df[rsi_col] < oversold] = SignalType.BUY.value
        signals[df[rsi_col] > overbought] = SignalType.SELL.value
        return signals

    def macd_crossover(self, df: pd.DataFrame) -> pd.Series:
        """MACD signal line crossover: BUY when MACD crosses above signal, SELL on cross below."""
        df = df.copy()
        df = self.tech.macd(df)

        # TechnicalAnalyzer.macd() creates columns: macd_MACD, macd_SIGNAL, macd_HIST
        macd_col = [c for c in df.columns if c == "macd_MACD"]
        signal_col = [c for c in df.columns if c == "macd_SIGNAL"]
        if not macd_col or not signal_col:
            return pd.Series(SignalType.HOLD.value, index=df.index)
        macd = df[macd_col[0]]
        signal = df[signal_col[0]]
        signals = pd.Series(SignalType.HOLD.value, index=df.index)
        # Crossover detection
        prev_macd = macd.shift(1)
        prev_signal = signal.shift(1)
        signals[(prev_macd <= prev_signal) & (macd > signal)] = SignalType.BUY.value
        signals[(prev_macd >= prev_signal) & (macd < signal)] = SignalType.SELL.value
        return signals

    def generate(self, df: pd.DataFrame, method: str = "ma_cross", **kwargs) -> pd.Series:
        """Generate signals using the specified method."""
        if method == "ma_cross":
            return self.ma_crossover(df, **kwargs)
        elif method == "rsi":
            return self.rsi_threshold(df, **kwargs)
        elif method == "macd":
            return self.macd_crossover(df, **kwargs)
        else:
            raise ValueError(f"Unknown signal method: {method}")
