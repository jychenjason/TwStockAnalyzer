"""季度財報與月營收的讀取輔助。

TWSE 免費的 BWIBBU_d 只給本益比與殖利率，所以 `fundamentals` 的 eps／revenue／
revenue_yoy 三欄從頭到尾都是 NULL。補上的兩支來源各有自己的頻率（季／月），
存在各自的表裡，這個模組負責把「每檔最新一筆」取出來給篩選與分析用。
"""

from __future__ import annotations

import sqlite3

_TABLES = ("quarterly_financials", "monthly_revenue")


def ensure_financial_tables(conn_or_path: sqlite3.Connection | str) -> None:
    """在既有資料庫上補建這兩張表。

    使用者的 data/twstock.db 建立於這兩張表之前，不該為了看 EPS 就要求重建。
    """
    from ..db.schema import TABLE_DEFS

    own = isinstance(conn_or_path, str)
    conn = sqlite3.connect(conn_or_path) if own else conn_or_path
    try:
        for table in _TABLES:
            conn.execute(TABLE_DEFS[table])
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


#: 每檔最新一季的每股盈餘與營收。period 是 ``2026Q2``，字典序即時間序。
LATEST_QUARTER_CTE = """
    latest_quarter AS (
        SELECT stock_id, period, eps, revenue AS quarter_revenue FROM (
            SELECT stock_id, period, eps, revenue,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY period DESC) AS rn
              FROM quarterly_financials
        ) WHERE rn = 1
    )
"""

#: 每檔最新一個月的營收與年增率。
LATEST_MONTH_CTE = """
    latest_month AS (
        SELECT stock_id, month, revenue AS month_revenue, revenue_yoy FROM (
            SELECT stock_id, month, revenue, revenue_yoy,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY month DESC) AS rn
              FROM monthly_revenue
        ) WHERE rn = 1
    )
"""


def has_any(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """該欄位在資料庫裡到底有沒有任何值。

    用來把「條件篩不到東西」和「這欄根本沒資料」分開——後者靜靜回傳空表，
    會被讀成「市場上沒有符合的股票」。
    """
    if table not in _TABLES:
        raise ValueError(f"未知的資料表：{table}")
    try:
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None
