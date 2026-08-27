import pandas as pd
import pandas_ta as ta


class TechnicalAnalyzer:
    """Compute technical indicators on price DataFrames."""

    def __init__(self, logger=None):
        self.logger = logger

    def validate_input(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate required columns exist."""
        required = ['open', 'high', 'low', 'close', 'volume']
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Required columns missing: {missing}")
        return df

    def sma(self, df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
        """Simple Moving Average. periods=[5,10,20,60,120,240]."""
        if periods is None:
            periods = [5, 10, 20, 60, 120, 240]
        for p in periods:
            df[f'sma_{p}'] = ta.sma(df['close'], length=p)
        return df

    def ema(self, df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
        """Exponential Moving Average. periods=[5,10,20,60]."""
        if periods is None:
            periods = [5, 10, 20, 60]
        for p in periods:
            df[f'ema_{p}'] = ta.ema(df['close'], length=p)
        return df

    def macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """MACD (12, 26, 9)."""
        result = ta.macd(df['close'], fast=12, slow=26, signal=9)
        if result is not None and not result.empty:
            for col in result.columns:
                df[f'macd_{col}'] = result[col]
        return df

    def rsi(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """RSI (14)."""
        df[f'rsi_{period}'] = ta.rsi(df['close'], length=period)
        return df

    def kd(self, df: pd.DataFrame) -> pd.DataFrame:
        """KD/Stochastic (5, 3, 3)."""
        result = ta.stoch(df['high'], df['low'], df['close'], k=5, d=3)
        if result is not None and not result.empty:
            df['kd_k'] = result.iloc[:, 0]
            df['kd_d'] = result.iloc[:, 1]
        return df

    def bollinger_bands(self, df: pd.DataFrame) -> pd.DataFrame:
        """Bollinger Bands (20, 2)."""
        result = ta.bbands(df['close'], length=20, std=2)
        if result is not None and not result.empty:
            for col in result.columns:
                df[f'bb_{col}'] = result[col]
        return df

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run all indicators on the DataFrame."""
        df = self.validate_input(df.copy())
        df = self.sma(df)
        df = self.ema(df)
        df = self.macd(df)
        df = self.rsi(df)
        df = self.kd(df)
        df = self.bollinger_bands(df)
        return df
