"""Fundamental analysis module for TwStockAnalyzer."""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import sqlite3

logger = logging.getLogger(__name__)


class FundamentalAnalyzer:
    """Compute fundamental metrics from DB data."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or "data/twstock.db"

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # pe_ratio
    # ------------------------------------------------------------------

    def pe_ratio(self, stock_id: str, date: str) -> Optional[float]:
        """TWSE 於 *date* 當日（或之前最近一日）公布的本益比。

        回傳 ``None`` 代表當日沒有公布——虧損的公司 TWSE 本來就不給本益比。

        這裡不自己用 close / EPS 算。舊版是那樣做的，但它讀的
        ``fundamentals.eps`` 沒有任何來源會填，所以永遠回 None；而改讀季報的每股
        盈餘也不對——那是**累計至該季**的數字，拿第一季的去除股價會得到約四倍的
        本益比。TWSE 公布的本益比才是完整的近四季計算結果。
        """
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT pe_ratio
                     FROM fundamentals
                    WHERE stock_id = ? AND report_date <= ?
                      AND pe_ratio IS NOT NULL
                    ORDER BY report_date DESC
                       LIMIT 1""",
                (stock_id, date),
            ).fetchone()
        finally:
            conn.close()

        if row is None or row["pe_ratio"] is None:
            return None
        return round(float(row["pe_ratio"]), 2)

    # ------------------------------------------------------------------
    # dividend_yield
    # ------------------------------------------------------------------

    def dividend_yield(self, stock_id: str, year: int) -> Optional[float]:
        """Pre-computed dividend yield for the given year.

        Falls back to computing from stored fundamentals if the
        ``dividend_yield`` column is NULL.
        """
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT dividend_yield
                     FROM fundamentals
                    WHERE stock_id = ?
                      AND strftime('%Y', report_date) = ?
                    ORDER BY report_date DESC
                       LIMIT 1""",
                (stock_id, str(year)),
            ).fetchone()
            if row and row["dividend_yield"] is not None:
                return round(float(row["dividend_yield"]), 2)
            return None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # monthly_revenue
    # ------------------------------------------------------------------

    def monthly_revenue(
        self,
        stock_id: str,
        months: int = 12,
    ) -> pd.DataFrame:
        """Revenue history with YoY growth, newest first.

        Returns a DataFrame with columns ``date`` (``YYYY-MM``), ``revenue``,
        ``revenue_yoy`` and ``qoq_growth``.

        資料來自 TWSE 月營收彙總表。舊版讀 ``fundamentals.revenue_yoy``，那一欄
        從來沒有來源會填，所以在真實資料庫上一直回傳空表；順帶一提，它是拿季報
        去估月營收，一年只有 4 個點。
        """
        conn = self._conn()
        try:
            df = pd.read_sql_query(
                """SELECT month AS date, revenue, revenue_yoy
                     FROM monthly_revenue
                    WHERE stock_id = ?
                    ORDER BY month DESC""",
                conn,
                params=(stock_id,),
            )
        except pd.errors.DatabaseError:
            # 資料庫建立於這張表之前。
            return pd.DataFrame(columns=["date", "revenue", "revenue_yoy"])
        finally:
            conn.close()

        if df.empty:
            return pd.DataFrame(columns=["date", "revenue", "revenue_yoy"])

        df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
        df["revenue_yoy"] = pd.to_numeric(df["revenue_yoy"], errors="coerce")
        df = df.head(max(months, 1)).reset_index(drop=True)

        # 逐月環比。df 是新到舊排序，前一個月在**下一列**，所以要對 shift(-1)
        # 相除；直接用 pct_change 會拿新的月份當分母，得到反過來的成長率。
        if df["revenue"].notna().sum() >= 2:
            previous = df["revenue"].shift(-1)
            df["qoq_growth"] = ((df["revenue"] / previous - 1) * 100).round(2)
        else:
            df["qoq_growth"] = None

        return df[["date", "revenue", "revenue_yoy", "qoq_growth"]]

    # ------------------------------------------------------------------
    # eps_trend
    # ------------------------------------------------------------------

    def eps_trend(
        self,
        stock_id: str,
        quarters: int = 8,
    ) -> pd.DataFrame:
        """EPS trend across the most recent *quarters* reporting periods.

        Columns: ``quarter``, ``eps``, ``trend_direction``.

        ``trend_direction`` compares the most recent quarter EPS to the
        immediately preceding quarter: ``up`` (>5 %), ``down`` (<-5 %),
        ``flat`` (within ±5 %), or ``insufficient_data``.
        """
        conn = self._conn()
        try:
            df = pd.read_sql_query(
                """SELECT period AS quarter, eps
                     FROM quarterly_financials
                    WHERE stock_id = ?
                    ORDER BY period DESC""",
                conn,
                params=(stock_id,),
            )
        except pd.errors.DatabaseError:
            # 資料庫建立於這張表之前。
            return pd.DataFrame(columns=["quarter", "eps", "trend_direction"])
        finally:
            conn.close()

        if df.empty:
            return pd.DataFrame(columns=["quarter", "eps", "trend_direction"])

        df["eps"] = pd.to_numeric(df["eps"], errors="coerce")
        df = df.head(quarters).reset_index(drop=True)

        # Determine trend direction
        eps_vals = df["eps"].dropna()
        if len(eps_vals) >= 2:
            current = eps_vals.iloc[0]
            previous = eps_vals.iloc[1]
            if previous != 0:
                change_pct = (current - previous) / abs(previous)
                if change_pct > 0.05:
                    trend = "up"
                elif change_pct < -0.05:
                    trend = "down"
                else:
                    trend = "flat"
            else:
                trend = "insufficient_data"
        else:
            trend = "insufficient_data"

        df["trend_direction"] = trend
        return df

    # ------------------------------------------------------------------
    # roe_analysis
    # ------------------------------------------------------------------

    def roe_analysis(
        self,
        stock_id: str,
        quarters: int = 8,
    ) -> pd.DataFrame:
        """ROE history with moving average.

        Columns: ``quarter``, ``roe``, ``avg_roe``.
        """
        conn = self._conn()
        try:
            df = pd.read_sql_query(
                """SELECT report_date, roe
                     FROM fundamentals
                    WHERE stock_id = ?
                    ORDER BY report_date DESC""",
                conn,
                params=(stock_id,),
            )
            if df.empty:
                return pd.DataFrame(
                    columns=["quarter", "roe", "avg_roe"]
                )

            df = df.rename(columns={"report_date": "quarter"})
            df["roe"] = pd.to_numeric(df["roe"], errors="coerce")

            # ROE 目前沒有任何免費來源會填這一欄。整欄皆空時回傳空表，否則呼叫端
            # 的 `if not df.empty` 會判定有資料，畫面上就會出現一張全是空值的表。
            if df["roe"].notna().sum() == 0:
                return pd.DataFrame(columns=["quarter", "roe", "avg_roe"])

            df = df.head(quarters).reset_index(drop=True)
            df["avg_roe"] = round(df["roe"].mean(), 2)
            return df
        finally:
            conn.close()
