"""資料庫裡「最新交易日」是誰、哪些股票沒跟上。

爆量比的是最新交易日的量，所以必須先決定那一天是哪一天。原本用全表
``MAX(date)``，那很脆弱：只要有 1 檔跑在前面，其餘 1088 檔就全部變成「落後」，
篩選會幾乎回傳空的，而訊息只會平靜地說「已排除 1088 檔」。

而且「落後」有兩種完全不同的原因，後果也不同：

* **資料未更新**——update 沒跑完。健康的股票，跑完就好。實測 2026-08-20 有
  378 檔屬於這種，佔全市場 35%，使用者的篩選結果因此少了三分之一的市場。
* **停止交易**——已下市或長期停牌。1589 永冠-KY 的資料停在 2026-04-02。
"""

import pandas as pd
import pytest

from twstock_analyzer.db.repository import upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.freshness import (
    STALE_GRACE_DAYS,
    market_freshness,
)

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-07-01", periods=40)]


def _bars(stock_id: str, upto: int) -> pd.DataFrame:
    """從第 1 天到第 ``upto`` 天（含）的日線。"""
    dates = DAYS[:upto]
    n = len(dates)
    return pd.DataFrame({
        "stock_id": [stock_id] * n, "date": dates,
        "open": [10.0] * n, "high": [10.0] * n, "low": [10.0] * n, "close": [10.0] * n,
        "volume": [1_000_000.0] * n, "adj_close": [10.0] * n,
        "fetched_at": ["2026-08-20"] * n,
    })


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "fresh.db")
    create_tables(path)
    return path


def _freshness(path):
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        return market_freshness(conn)
    finally:
        conn.close()


class TestTheReferenceDay:
    def test_it_is_the_day_most_stocks_last_traded(self, db):
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("2000", 29), db)

        assert _freshness(db).reference_date == DAYS[29]

    def test_a_single_stock_running_ahead_does_not_redefine_the_market(self, db):
        """關鍵情境：1 檔多抓了一天，不該讓其他所有股票都變成落後。"""
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("9999", 31), db)

        fresh = _freshness(db)

        assert fresh.reference_date == DAYS[29], "基準日應由多數決定，不是最大值"
        assert fresh.behind == [] and fresh.inactive == []

    def test_a_stock_ahead_of_the_market_still_participates(self, db):
        """跑在前面的那檔有最新資料，沒有理由把它排掉。"""
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("9999", 31), db)

        assert "9999" not in _freshness(db).excluded

    def test_a_tie_prefers_the_later_day(self, db):
        upsert("daily_prices", _bars("1101", 30), db)
        upsert("daily_prices", _bars("2222", 29), db)

        assert _freshness(db).reference_date == DAYS[29]

    def test_an_empty_database_has_no_reference_day(self, db):
        assert _freshness(db).reference_date is None


class TestBehindVersusInactive:
    def test_one_missing_trading_day_is_merely_out_of_date(self, db):
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("1477", 29), db)

        fresh = _freshness(db)

        assert fresh.behind == ["1477"]
        assert fresh.inactive == []

    def test_a_long_gap_counts_as_no_longer_trading(self, db):
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("1589", 10), db)

        fresh = _freshness(db)

        assert fresh.inactive == ["1589"]
        assert fresh.behind == []

    def test_the_boundary_is_the_documented_grace(self, db):
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("1111", 30 - STALE_GRACE_DAYS), db)      # 剛好在容許範圍
        upsert("daily_prices", _bars("2222", 30 - STALE_GRACE_DAYS - 1), db)  # 超過一天

        fresh = _freshness(db)

        assert fresh.behind == ["1111"]
        assert fresh.inactive == ["2222"]

    def test_the_gap_is_counted_in_trading_days_not_calendar_days(self, db):
        """週末不算，否則週一跑篩選時整個市場都會被判成落後。"""
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("1477", 29), db)

        # DAYS 是營業日序列，第 29 與第 30 天中間沒有交易日。
        assert _freshness(db).behind == ["1477"]

    def test_both_kinds_are_reported_together(self, db):
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("1477", 29), db)
        upsert("daily_prices", _bars("1589", 10), db)

        fresh = _freshness(db)

        assert fresh.behind == ["1477"]
        assert fresh.inactive == ["1589"]
        assert set(fresh.excluded) == {"1477", "1589"}


class TestCoverage:
    def test_it_counts_the_stocks_that_can_be_judged(self, db):
        for i in range(8):
            upsert("daily_prices", _bars(f"100{i}", 30), db)
        upsert("daily_prices", _bars("1477", 29), db)

        fresh = _freshness(db)

        assert fresh.total == 9
        assert fresh.covered == 8

    def test_a_small_number_of_dead_stocks_is_not_incomplete(self, db):
        for i in range(40):
            upsert("daily_prices", _bars(f"{1000 + i}", 30), db)
        upsert("daily_prices", _bars("1589", 10), db)

        assert not _freshness(db).is_incomplete, "1/41 檔停止交易是常態，不該喊不完整"

    def test_a_third_of_the_market_missing_is_incomplete(self, db):
        """實測情境：708 檔更新到 08-20、378 檔還停在 08-19。"""
        for i in range(7):
            upsert("daily_prices", _bars(f"200{i}", 30), db)
        for i in range(3):
            upsert("daily_prices", _bars(f"300{i}", 29), db)

        fresh = _freshness(db)

        assert fresh.is_incomplete
        assert fresh.covered == 7 and fresh.total == 10

    def test_an_empty_database_is_not_incomplete(self, db):
        assert not _freshness(db).is_incomplete
