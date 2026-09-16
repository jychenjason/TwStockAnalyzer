"""Yahoo Finance data source for Taiwan stock data.

Uses ``yfinance`` to fetch OHLCV data.  Stock IDs are automatically
suffixed with ``.TW`` for Taiwan Exchange listings.

Should be used as a fallback when TWSE / FinMind are unavailable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

from ..loader import BaseDataSource, DataFetchError

if TYPE_CHECKING:
    from twstock_analyzer.utils.logger import get_logger  # noqa: F401


logger = logging.getLogger(__name__)


class YahooSource(BaseDataSource):
    """Fetch daily stock data from Yahoo Finance (via ``yfinance``).

    No API token required — rate limits apply (about 2 000 requests/hour).
    """

    name = "yahoo"

    @classmethod
    def should_skip(cls, env_check: dict | None = None) -> bool:
        """Skip only if ``yfinance`` is not installed."""
        try:
            import yfinance  # noqa: F401
        except ImportError:
            return True
        return False

    def fetch_daily(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch daily OHLCV from Yahoo Finance.

        Parameters
        ----------
        stock_id : str
            4-digit TWSE stock code (e.g. ``"2330"``).
        start_date : str
            Inclusive start date ``YYYY-MM-DD``.
        end_date : str
            Inclusive end date ``YYYY-MM-DD``.

        Returns
        -------
        pd.DataFrame
            Columns: ``date, open, high, low, close, volume``.
        """
        import yfinance as yf

        ticker = f"{stock_id}.TW"
        try:
            df = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=True)
        except Exception as exc:
            raise DataFetchError(f"Yahoo Finance fetch failed for {stock_id}: {exc}")

        if df is None or df.empty:
            return pd.DataFrame()

        # yfinance <1.0 returns MultiIndex columns; flatten
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        result = pd.DataFrame()
        result["date"] = df["Date"].dt.strftime("%Y-%m-%d")
        result["open"] = df["Open"].astype(float)
        result["high"] = df["High"].astype(float)
        result["low"] = df["Low"].astype(float)
        result["close"] = df["Close"].astype(float)
        result["volume"] = df["Volume"].astype(int)

        # Filter to requested date range (yfinance end is exclusive)
        result = result[(result["date"] >= start_date) & (result["date"] <= end_date)]
        return result.reset_index(drop=True)

    def fetch_fundamentals(self, stock_id: str, period: str) -> pd.DataFrame:
        """Not implemented for Yahoo — returns empty DataFrame."""
        return pd.DataFrame()

    def fetch_institutional(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Not implemented for Yahoo — returns empty DataFrame."""
        return pd.DataFrame()
