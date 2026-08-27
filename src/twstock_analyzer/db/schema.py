"""SQLite schema definitions for TwStockAnalyzer."""

from __future__ import annotations

TABLE_DEFS = {
    "daily_prices": """
        CREATE TABLE IF NOT EXISTS daily_prices (
            stock_id    TEXT NOT NULL,
            date        TEXT NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            adj_close   REAL,
            fetched_at  TEXT,
            PRIMARY KEY (stock_id, date)
        )
    """,
    "fundamentals": """
        CREATE TABLE IF NOT EXISTS fundamentals (
            stock_id        TEXT NOT NULL,
            report_date     TEXT NOT NULL,
            period          TEXT,
            eps             REAL,
            pe_ratio        REAL,
            dividend_yield  REAL,
            revenue         REAL,
            revenue_yoy     REAL,
            roe             REAL,
            fetched_at      TEXT,
            PRIMARY KEY (stock_id, report_date)
        )
    """,
    # 每股盈餘與營業收入來自季報（營益分析彙總表），數字是累計至該季。
    # 和 fundamentals 分開存，因為那張表是**每日**的本益比／殖利率——頻率不同的
    # 資料放同一列，就會做出「一列裡的欄位分屬不同時間」的假資料。
    "quarterly_financials": """
        CREATE TABLE IF NOT EXISTS quarterly_financials (
            stock_id          TEXT NOT NULL,
            period            TEXT NOT NULL,
            eps               REAL,
            revenue           REAL,
            operating_income  REAL,
            net_income        REAL,
            fetched_at        TEXT,
            PRIMARY KEY (stock_id, period)
        )
    """,
    # 營收年增率是**每月**公布的，季報給不了。
    "monthly_revenue": """
        CREATE TABLE IF NOT EXISTS monthly_revenue (
            stock_id         TEXT NOT NULL,
            month            TEXT NOT NULL,
            revenue          REAL,
            revenue_yoy      REAL,
            revenue_mom      REAL,
            revenue_ytd      REAL,
            revenue_ytd_yoy  REAL,
            fetched_at       TEXT,
            PRIMARY KEY (stock_id, month)
        )
    """,
    "institutional_trading": """
        CREATE TABLE IF NOT EXISTS institutional_trading (
            stock_id        TEXT NOT NULL,
            date            TEXT NOT NULL,
            foreign_buy     REAL,
            foreign_sell    REAL,
            foreign_net     REAL,
            fund_buy        REAL,
            fund_sell       REAL,
            fund_net        REAL,
            dealer_buy      REAL,
            dealer_sell     REAL,
            dealer_net      REAL,
            total_net       REAL,
            fetched_at      TEXT,
            PRIMARY KEY (stock_id, date)
        )
    """,
    "cache_metadata": """
        CREATE TABLE IF NOT EXISTS cache_metadata (
            cache_key   TEXT PRIMARY KEY,
            stock_id    TEXT,
            data_type   TEXT,
            source      TEXT,
            fetched_at  TEXT,
            date_from   TEXT,
            date_to     TEXT,
            row_count   INT
        )
    """,
    "screening_criteria": """
        CREATE TABLE IF NOT EXISTS screening_criteria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            criteria_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "watchlists": """
        CREATE TABLE IF NOT EXISTS watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            stock_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "screening_snapshots": """
        CREATE TABLE IF NOT EXISTS screening_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            criteria_id INTEGER,
            snapshot_json TEXT NOT NULL,
            result_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (criteria_id) REFERENCES screening_criteria(id)
        )
    """,
    "backtest_results": """
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            config_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "backtest_equity_curves": """
        CREATE TABLE IF NOT EXISTS backtest_equity_curves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            equity REAL NOT NULL,
            FOREIGN KEY (result_id) REFERENCES backtest_results(id) ON DELETE CASCADE
        )
    """,
    "stocks": """
        CREATE TABLE IF NOT EXISTS stocks (
            stock_id   TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "dividends": """
        CREATE TABLE IF NOT EXISTS dividends (
            stock_id       TEXT NOT NULL,
            date           TEXT NOT NULL,
            cash_dividend  REAL,
            stock_dividend REAL,
            kind           TEXT,
            fetched_at     TEXT,
            PRIMARY KEY (stock_id, date)
        )
    """,
    "replay_sessions": """
        CREATE TABLE IF NOT EXISTS replay_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id        TEXT NOT NULL,
            start_date      TEXT NOT NULL,
            end_date        TEXT,
            cursor          TEXT NOT NULL,
            initial_capital REAL NOT NULL,
            fee_discount    REAL NOT NULL,
            name            TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """,
    "replay_orders": """
        CREATE TABLE IF NOT EXISTS replay_orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            side        TEXT NOT NULL,
            lots        INTEGER NOT NULL,
            shares      INTEGER NOT NULL,
            placed_on   TEXT NOT NULL,
            filled_on   TEXT,
            fill_price  REAL,
            amount      REAL,
            fee         REAL,
            tax         REAL,
            status      TEXT NOT NULL,
            void_reason TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES replay_sessions(id) ON DELETE CASCADE
        )
    """,
    "market_calendar": """
        CREATE TABLE IF NOT EXISTS market_calendar (
            date        TEXT PRIMARY KEY,
            has_trading INTEGER NOT NULL,
            checked_at  TEXT NOT NULL
        )
    """,
    "_meta": """
        CREATE TABLE IF NOT EXISTS _meta (
            key           TEXT PRIMARY KEY,
            value         TEXT
        )
    """,
}

INDEX_DEFS = [
    "CREATE INDEX IF NOT EXISTS idx_daily_stock_date ON daily_prices(stock_id, date);",
    "CREATE INDEX IF NOT EXISTS idx_fund_stock_date ON fundamentals(stock_id, report_date);",
    "CREATE INDEX IF NOT EXISTS idx_inst_stock_date ON institutional_trading(stock_id, date);",
]


def create_tables(db_path: str) -> None:
    from .repository import set_default_db_path

    set_default_db_path(db_path)
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        for ddl in TABLE_DEFS.values():
            conn.execute(ddl)
        for idx in INDEX_DEFS:
            conn.execute(idx)
        for col in ("dealer_buy", "dealer_sell", "dealer_net"):
            try:
                conn.execute(
                    f"ALTER TABLE institutional_trading ADD COLUMN {col} REAL"
                )
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()
