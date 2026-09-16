"""FinMind data source for Taiwan stock data."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pandas as pd
from dotenv import load_dotenv

from ..loader import BaseDataSource, DataFetchError

if TYPE_CHECKING:
    from twstock_analyzer.utils.logger import get_logger  # noqa: F401

load_dotenv()


class FinMindSource(BaseDataSource):
    """Fetch data from FinMind SDK.

    Inherits retry logic from ``BaseDataSource`` — transient errors are
    retried up to 3 times with exponential backoff.  ``DataFetchError``
    is propagated immediately without retry.
    """

    name = "finmind"

    def __init__(self) -> None:
        self._dl = None
        self._token = os.environ.get('FINMIND_API_TOKEN', '')

    @classmethod
    def should_skip(cls, env_check: dict | None = None) -> bool:
        """Skip if no API token configured."""
        return not os.environ.get('FINMIND_API_TOKEN', '').strip()

    def _get_dl(self):
        if self._dl is None:
            from FinMind.data import DataLoader
            self._dl = DataLoader()
        return self._dl

    def fetch_daily(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        dl = self._get_dl()
        try:
            df = dl.taiwan_stock_daily(
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
            if df is None or df.empty:
                raise DataFetchError(f"No data returned for {stock_id}")
            df = self._normalize_daily_df(df)
            return df
        except DataFetchError:
            raise
        except Exception as e:
            raise DataFetchError(f"FinMind fetch failed for {stock_id}: {e}")

    def fetch_fundamentals(self, stock_id: str, period: str) -> pd.DataFrame:
        dl = self._get_dl()
        try:
            df = dl.taiwan_stock_financial_statement(stock_id=stock_id)
            if df is not None and not df.empty:
                return df
            return pd.DataFrame()
        except DataFetchError:
            raise
        except Exception as e:
            raise DataFetchError(f"FinMind fundamentals failed for {stock_id}: {e}")

    def fetch_institutional(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        dl = self._get_dl()
        try:
            from datetime import datetime, timedelta
            frames: list[pd.DataFrame] = []
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            cursor = start
            while cursor <= end:
                date_str = cursor.strftime("%Y%m%d")
                try:
                    df = dl.taiwan_institutional_investors(stock_id=stock_id, date=date_str)
                    if df is not None and not df.empty:
                        frames.append(df)
                except Exception:
                    pass
                cursor += timedelta(days=1)
            if frames:
                return pd.concat(frames, ignore_index=True)
            return pd.DataFrame()
        except DataFetchError:
            raise
        except Exception as e:
            raise DataFetchError(f"FinMind institutional failed for {stock_id}: {e}")

    def fetch_dividends(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        """除權息交易日、金額與除權/除息別。

        用 taiwan_stock_dividend_result（除權息結果表），它直接給除權息交易日與
        金額，不需要從價格跳空反推。
        """
        dl = self._get_dl()
        try:
            df = dl.taiwan_stock_dividend_result(
                stock_id=stock_id,
                start_date=start_date,
                end_date=end_date,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            out = pd.DataFrame({
                "date": df["date"],
                "cash_dividend": df.get("stock_and_cache_dividend", 0.0),
                "stock_dividend": 0.0,
                "kind": df.get("stock_or_cache_dividend", ""),
            })
            is_stock = out["kind"].astype(str).str.contains("權")
            out.loc[is_stock, "stock_dividend"] = out.loc[is_stock, "cash_dividend"]
            out.loc[is_stock, "cash_dividend"] = 0.0
            return out
        except Exception as e:
            raise DataFetchError(f"FinMind dividends failed for {stock_id}: {e}")

    def _normalize_daily_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """Map FinMind column names to standard column names.

        FinMind TaiwanStockPrice uses ``max``/``min`` for high/low.
        """
        col_map = {}
        for col in df.columns:
            lower = col.lower()
            if 'date' in lower or 'trade_date' in lower:
                col_map[col] = 'date'
            elif 'open' in lower:
                col_map[col] = 'open'
            elif lower == 'max' or 'high' in lower:
                col_map[col] = 'high'
            elif lower == 'min' or 'low' in lower:
                col_map[col] = 'low'
            elif 'close' in lower:
                col_map[col] = 'close'
            elif 'volume' in lower:
                col_map[col] = 'volume'
            elif 'turnover' in lower or 'amount' in lower:
                col_map[col] = 'turnover'
        if col_map:
            df = df.rename(columns=col_map)
        return df
