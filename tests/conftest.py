"""Shared fixtures for TwStockAnalyzer tests."""

import os
import tempfile
from datetime import datetime, timedelta

import pandas as pd
import pytest
import sqlite3


@pytest.fixture()
def db_path():
    """Create a temp SQLite DB with full schema.  Returns the path."""
    from twstock_analyzer.db.schema import create_tables
    from twstock_analyzer.db.repository import set_default_db_path

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path_str = tmp.name

    create_tables(db_path_str)
    set_default_db_path(db_path_str)
    yield db_path_str

    # Cleanup
    try:
        os.unlink(db_path_str)
    except OSError:
        pass


@pytest.fixture()
def sample_daily_prices():
    """~1 year of daily price data for 2330, aligned with the report
    command's default lookback window (today - 365 days)."""
    dates = pd.bdate_range("2025-07-02", periods=250)
    return pd.DataFrame({
        "stock_id": ["2330"] * 250,
        "date": dates.strftime("%Y-%m-%d").tolist(),
        "open": [500.0 + i * 0.5 for i in range(250)],
        "high": [510.0 + i * 0.5 for i in range(250)],
        "low": [490.0 + i * 0.5 for i in range(250)],
        "close": [505.0 + i * 0.5 for i in range(250)],
        "volume": [1_000_000] * 250,
        "adj_close": [505.0 + i * 0.5 for i in range(250)],
        "fetched_at": ["2025-07-02"] * 250,
    })


@pytest.fixture()
def sample_fundamentals():
    """`fundamentals` 表的樣本：只有本益比與殖利率。

    eps / revenue / revenue_yoy / roe 刻意留空，因為唯一寫入這張表的來源
    （TWSE BWIBBU_d）就只給這兩個數字。fixture 若替它們編值，任何「讀 EPS」的
    程式都會在測試裡通過、在真實資料庫上回傳空白——這正是 screen 表格的 EPS 欄
    長期是 "-" 卻沒有測試抓到的原因。
    """
    rows = [
        ("2330", "2023Q1", "2023-03-31", 35.0, 0.03),
        ("2330", "2023Q2", "2023-06-30", 33.0, 0.032),
        ("2330", "2023Q3", "2023-09-30", 31.0, 0.035),
        ("2330", "2023Q4", "2023-12-31", 29.0, 0.04),
        ("2330", "2024Q1", "2024-03-31", 28.0, 0.042),
        ("2330", "2024Q2", "2024-06-30", 27.0, 0.045),
        ("2330", "2024Q3", "2024-09-30", 26.0, 0.048),
        ("2330", "2024Q4", "2024-12-31", 25.0, 0.05),
    ]
    df = pd.DataFrame(rows, columns=[
        "stock_id", "period", "report_date", "pe_ratio", "dividend_yield",
    ])
    df["fetched_at"] = "2024-01-01"
    for unfilled in ("eps", "revenue", "revenue_yoy", "roe"):
        df[unfilled] = None
    return df


@pytest.fixture()
def sample_quarterly_financials():
    """季報樣本（TWSE 營益分析彙總表）——EPS 真正的來源。"""
    rows = [
        ("2330", "2023Q1", 12.5, 5000.0),
        ("2330", "2023Q2", 13.0, 5200.0),
        ("2330", "2023Q3", 14.0, 5500.0),
        ("2330", "2023Q4", 15.0, 5800.0),
        ("2330", "2024Q1", 16.0, 6000.0),
        ("2330", "2024Q2", 17.5, 6300.0),
        ("2330", "2024Q3", 18.0, 6500.0),
        ("2330", "2024Q4", 19.0, 6800.0),
    ]
    df = pd.DataFrame(rows, columns=["stock_id", "period", "eps", "revenue"])
    df["operating_income"] = df["revenue"] * 0.4
    df["net_income"] = df["revenue"] * 0.3
    df["fetched_at"] = "2024-01-01"
    return df


@pytest.fixture()
def sample_monthly_revenue():
    """月營收樣本（TWSE 月營收彙總表）——營收年增率真正的來源。"""
    months = [f"2024-{m:02d}" for m in range(1, 13)]
    df = pd.DataFrame({
        "stock_id": ["2330"] * 12,
        "month": months,
        "revenue": [2000.0 + i * 50 for i in range(12)],
        "revenue_yoy": [10.0 + i for i in range(12)],
        "revenue_mom": [2.0] * 12,
        "revenue_ytd": [2000.0 * (i + 1) for i in range(12)],
        "revenue_ytd_yoy": [12.0] * 12,
        "fetched_at": ["2024-01-01"] * 12,
    })
    return df


@pytest.fixture()
def sample_institutional_trading():
    """Institutional trading data for 2330 (三大法人買賣超)."""
    dates = pd.bdate_range("2025-07-01", periods=30)
    return pd.DataFrame({
        "stock_id": ["2330"] * 30,
        "date": dates.strftime("%Y-%m-%d").tolist(),
        "foreign_buy": [10000.0 + i * 100 for i in range(30)],
        "foreign_sell": [9000.0 + i * 80 for i in range(30)],
        "foreign_net": [1000.0 + i * 20 for i in range(30)],
        "fund_buy": [3000.0 + i * 50 for i in range(30)],
        "fund_sell": [2500.0 + i * 40 for i in range(30)],
        "fund_net": [500.0 + i * 10 for i in range(30)],
        "dealer_buy": [500.0 + i * 20 for i in range(30)],
        "dealer_sell": [400.0 + i * 15 for i in range(30)],
        "dealer_net": [100.0 + i * 5 for i in range(30)],
        "total_net": [1500.0 + i * 30 for i in range(30)],
        "fetched_at": ["2025-07-01"] * 30,
    })


@pytest.fixture()
def populated_db(
    db_path,
    sample_daily_prices,
    sample_fundamentals,
    sample_quarterly_financials,
    sample_monthly_revenue,
    sample_institutional_trading,
):
    """DB fixture with sample data already inserted."""
    from twstock_analyzer.db.repository import upsert

    upsert("daily_prices", sample_daily_prices, db_path)
    upsert("fundamentals", sample_fundamentals, db_path)
    upsert("quarterly_financials", sample_quarterly_financials, db_path)
    upsert("monthly_revenue", sample_monthly_revenue, db_path)
    upsert("institutional_trading", sample_institutional_trading, db_path)
    return db_path


@pytest.fixture()
def mock_env_no_finmind(monkeypatch):
    """Ensure FINMIND_API_TOKEN is unset."""
    monkeypatch.delenv("FINMIND_API_TOKEN", raising=False)


@pytest.fixture()
def mock_env_with_finmind(monkeypatch):
    """Set a fake FinMind API token."""
    monkeypatch.setenv("FINMIND_API_TOKEN", "fake-token-for-testing")
