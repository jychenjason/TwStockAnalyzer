"""Repository layer for TwStockAnalyzer SQLite database."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .schema import TABLE_DEFS

_DEFAULT_DB_PATH = "data/twstock.db"


@contextmanager
def _connect(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _extract_column_names(ddl: str) -> list[str]:
    inner = ddl.strip()
    start = inner.index("(")
    end = inner.rindex(")")
    body = inner[start + 1 : end]
    cols: list[str] = []
    seen: set[str] = set()
    for line in body.split(","):
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if any(upper.startswith(kw + " ") or upper == kw for kw in (
            "PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "CHECK",
        )):
            continue
        name = line.split()[0].rstrip(")")
        if name.isidentifier() and name not in seen:
            cols.append(name)
            seen.add(name)
    return cols


def get_table_columns(table: str) -> list[str]:
    """Return the column names for a given table, as defined in TABLE_DEFS."""
    if table not in TABLE_DEFS:
        raise ValueError(f"Unknown table: {table}")
    return _extract_column_names(TABLE_DEFS[table])


def set_default_db_path(path: str) -> None:
    global _DEFAULT_DB_PATH
    _DEFAULT_DB_PATH = path


def get_latest_date(
    table: str,
    stock_id: str,
    db_path: str | None = None,
    date_column: str = "date",
) -> str | None:
    db_path = db_path or _DEFAULT_DB_PATH
    if table not in TABLE_DEFS:
        raise ValueError(f"Unknown table: {table}")
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT MAX({date_column}) FROM {table} WHERE stock_id = ?", (stock_id,)
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def has_data(
    table: str,
    stock_id: str,
    date_from: str,
    date_to: str,
    db_path: str | None = None,
    date_column: str = "date",
) -> dict[str, Any]:
    db_path = db_path or _DEFAULT_DB_PATH
    if table not in TABLE_DEFS:
        raise ValueError(f"Unknown table: {table}")
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"""SELECT MIN({date_column}), MAX({date_column}), COUNT(*)
  FROM {table}
 WHERE stock_id = ?
   AND {date_column} BETWEEN ? AND ?""",
            (stock_id, date_from, date_to),
        )
        min_d, max_d, cnt = cur.fetchone()
    return {
        "has_data": bool(cnt and cnt > 0),
        "date_min": min_d,
        "date_max": max_d,
        "row_count": int(cnt) if cnt else 0,
    }


def upsert(
    table: str,
    df: pd.DataFrame,
    db_path: str | None = None,
) -> int:
    db_path = db_path or _DEFAULT_DB_PATH
    if table not in TABLE_DEFS:
        raise ValueError(f"Unknown table: {table}")
    table_columns = set(_extract_column_names(TABLE_DEFS[table]))
    df_columns = set(df.columns)
    missing = table_columns - df_columns
    if missing:
        raise ValueError(
            f"Missing columns for table '{table}': {sorted(missing)}"
        )
    cols = [c for c in table_columns if c in df_columns]
    placeholders = ",".join(["?"] * len(cols))
    col_names = ",".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
    with _connect(db_path) as conn:
        rows = [
            tuple(_to_sqlite_scalar(df[c].iloc[i]) for c in cols)
            for i in range(len(df))
        ]
        if rows:
            conn.executemany(sql, rows)
        affected = conn.total_changes
    return affected


def _to_sqlite_scalar(value: Any) -> Any:
    """Coerce a pandas/numpy scalar to a native type sqlite3 can bind.

    numpy integers/floats support the buffer protocol, so sqlite3 would
    otherwise store them as raw BLOB bytes instead of numbers.  Convert them
    to native Python via ``.item()`` and map NaN/NA to ``None`` (SQL NULL).
    """
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # native NaN
        return None
    item = getattr(value, "item", None)
    if item is not None:  # numpy scalar
        try:
            value = item()
        except (ValueError, TypeError):
            return value
        if isinstance(value, float) and value != value:  # numpy NaN -> None
            return None
    return value


def get_cache_metadata(
    stock_id: str,
    data_type: str,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    db_path = db_path or _DEFAULT_DB_PATH
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT cache_key, stock_id, data_type, source, fetched_at,
       date_from, date_to, row_count
  FROM cache_metadata
 WHERE stock_id = ? AND data_type = ?
 ORDER BY fetched_at DESC
 LIMIT 1""",
            (stock_id, data_type),
        ).fetchone()
    if row is None:
        return None
    return {
        "cache_key": row[0],
        "stock_id": row[1],
        "data_type": row[2],
        "source": row[3],
        "fetched_at": row[4],
        "date_from": row[5],
        "date_to": row[6],
        "row_count": row[7],
    }
