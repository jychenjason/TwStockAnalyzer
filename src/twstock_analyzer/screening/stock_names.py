"""股票代號與公司簡稱的本地對照表。

名稱來自 TWSE 的公開清單（公司簡稱欄），抓下來就存起來——否則每次要顯示名稱
都得連外，離線的篩選結果就只剩一排四位數代號。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime


def _connect(db_path: str | None) -> sqlite3.Connection:
    from ..db import repository

    return sqlite3.connect(db_path or repository._DEFAULT_DB_PATH)


def _ensure_table(conn: sqlite3.Connection) -> None:
    """既有資料庫建立於本表之前，不該為了看名稱而要求使用者重建資料庫。"""
    from ..db.schema import TABLE_DEFS

    conn.execute(TABLE_DEFS["stocks"])


def save_stock_names(stocks: list[dict], db_path: str | None = None) -> int:
    """存入（或更新）股票簡稱，回傳實際寫入的筆數。

    沒有名稱的項目會被略過——寧可沒有名稱，也不要存一個空字串進去，
    之後分不清是「沒抓到」還是「真的叫這個」。
    """
    rows = [
        (str(entry["stock_id"]).strip(), str(entry.get("name", "")).strip())
        for entry in stocks
        if entry.get("stock_id")
    ]
    rows = [(stock_id, name) for stock_id, name in rows if name]
    if not rows:
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        _ensure_table(conn)
        conn.executemany(
            "INSERT INTO stocks (stock_id, name, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(stock_id) DO UPDATE SET name = excluded.name,"
            " updated_at = excluded.updated_at",
            [(stock_id, name, now) for stock_id, name in rows],
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def get_stock_name(stock_id: str, db_path: str | None = None) -> str | None:
    """單一股票的簡稱；沒有存過則回 None。"""
    conn = _connect(db_path)
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT name FROM stocks WHERE stock_id = ?", (stock_id,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def get_stock_names(db_path: str | None = None) -> dict[str, str]:
    """全部已知的代號→簡稱對照。"""
    conn = _connect(db_path)
    try:
        _ensure_table(conn)
        rows = conn.execute("SELECT stock_id, name FROM stocks").fetchall()
    finally:
        conn.close()
    return {stock_id: name for stock_id, name in rows}
