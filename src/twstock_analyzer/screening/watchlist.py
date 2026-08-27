from __future__ import annotations

import json
import sqlite3


def create_watchlist(name: str, stock_ids: list[str], db_path: str = "data/twstock.db") -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO watchlists (name, stock_ids_json) VALUES (?, ?)",
            (name, json.dumps(stock_ids)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_watchlists(db_path: str = "data/twstock.db") -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, name, stock_ids_json, created_at, updated_at FROM watchlists ORDER BY name").fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["stock_ids"] = json.loads(d.pop("stock_ids_json"))
            result.append(d)
        return result
    finally:
        conn.close()


def delete_watchlist(watchlist_id: int, db_path: str = "data/twstock.db") -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_watchlist(watchlist_id: int, db_path: str = "data/twstock.db") -> dict | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id, name, stock_ids_json, created_at, updated_at FROM watchlists WHERE id = ?", (watchlist_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["stock_ids"] = json.loads(d.pop("stock_ids_json"))
        return d
    finally:
        conn.close()
