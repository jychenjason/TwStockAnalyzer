"""爆量：最新交易日的成交量相對於前 20 日均量的倍數。

門檻取自實際市場分布（1,081 檔滿 61 個交易日的上市股）：當日量 ÷ 前 20 日均量的
中位數是 0.69、90 百分位 1.61、95 百分位 2.22。2.0 倍約落在第 93 百分位，選出約
6.9% 的股票——統計上確實異常，數量也還看得完。1.5 倍會選出 12.1%（只是「高於
平均」），3.0 倍只剩 3.1%（漏掉太多真實進貨）。

窗口取 20 日而非 5 日：5 日均量會被連續放量自己墊高，且尾部更亂（99 百分位 8.64
對 20 日的 5.15）。也不取 60 日：60 日量比的中位數只有 0.45，代表基準被舊的量能
水位污染，會把「量能回到正常」誤判成爆量。
"""

import pandas as pd
import pytest

from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import (
    VOLUME_SPIKE_MULTIPLE,
    VOLUME_SPIKE_WINDOW,
    ScreenCriteria,
    run_screen,
)


def _prices(stock_id: str, volumes: list[float], closes: list[float] | None = None) -> pd.DataFrame:
    """由早到晚的一段日線；最後一筆就是「最新交易日」。"""
    n = len(volumes)
    closes = closes or [10.0] * n
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", periods=n)]
    return pd.DataFrame({
        "stock_id": [stock_id] * n, "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes, "adj_close": closes,
        "fetched_at": ["2026-08-17"] * n,
    })


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "spike.db")
    create_tables(path)
    set_default_db_path(path)
    return path


class TestTheDefaults:
    def test_the_window_is_twenty_trading_days(self):
        assert VOLUME_SPIKE_WINDOW == 20

    def test_the_multiple_is_two(self):
        assert VOLUME_SPIKE_MULTIPLE == 2.0


class TestTheBaselineExcludesToday:
    """把當日算進均量，會讓爆得越兇的股票被稀釋得越多。"""

    def test_the_ratio_is_measured_against_the_prior_days_only(self, db):
        # 前 20 日各 1000，今日 3000。
        # 不含當日：3000 / 1000 = 3.0
        # 含當日  ：3000 / ((20*1000+3000)/21) = 2.74  ← 明顯低估
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [3000.0]), db)

        row = run_screen(ScreenCriteria(), db_path=db).iloc[0]

        assert row["volume_ratio"] == pytest.approx(3.0)

    def test_a_bigger_spike_is_not_damped_more_than_a_smaller_one(self, db):
        """含當日的稀釋是非線性的：10 倍會被壓成 6.8 倍，3 倍只壓成 2.7 倍。"""
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [10_000.0]), db)

        row = run_screen(ScreenCriteria(), db_path=db).iloc[0]

        assert row["volume_ratio"] == pytest.approx(10.0)


class TestTheFilter:
    def test_a_three_times_spike_passes_a_two_times_threshold(self, db):
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [3000.0]), db)

        assert not run_screen(ScreenCriteria(volume_spike=2.0), db_path=db).empty

    def test_one_and_a_half_times_does_not_pass(self, db):
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [1500.0]), db)

        assert run_screen(ScreenCriteria(volume_spike=2.0), db_path=db).empty

    def test_exactly_the_threshold_counts_as_a_spike(self, db):
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [2000.0]), db)

        assert not run_screen(ScreenCriteria(volume_spike=2.0), db_path=db).empty

    def test_a_quiet_day_is_not_a_spike(self, db):
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [400.0]), db)

        assert run_screen(ScreenCriteria(volume_spike=2.0), db_path=db).empty

    def test_only_the_spiking_stock_is_returned(self, db):
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [5000.0]), db)
        upsert("daily_prices", _prices("2222", [1000.0] * 20 + [1000.0]), db)

        result = run_screen(ScreenCriteria(volume_spike=2.0), db_path=db)

        assert set(result["stock_id"]) == {"1101"}


class TestTheWindowIsConfigurable:
    def test_a_five_day_window_uses_only_the_last_five_prior_days(self, db):
        # 近 5 日已放大到 4000，更早的 16 日只有 1000。
        # 5 日窗口 → 8000/4000 = 2.0（不算爆量的邊緣）
        # 20 日窗口 → 8000/((16*1000+5*4000)/21) 遠大於 2
        upsert("daily_prices", _prices("1101", [1000.0] * 16 + [4000.0] * 5 + [8000.0]), db)

        five = run_screen(ScreenCriteria(volume_spike_window=5), db_path=db).iloc[0]
        twenty = run_screen(ScreenCriteria(volume_spike_window=20), db_path=db).iloc[0]

        assert five["volume_ratio"] == pytest.approx(2.0)
        assert twenty["volume_ratio"] > 4.0

    def test_a_short_window_is_more_easily_fooled_by_a_rising_baseline(self, db):
        """5 日窗口被連續放量墊高後就測不出爆量了——這是不選它的理由。"""
        upsert("daily_prices", _prices("1101", [1000.0] * 16 + [4000.0] * 5 + [8000.0]), db)

        assert run_screen(ScreenCriteria(volume_spike=2.5, volume_spike_window=5), db_path=db).empty
        assert not run_screen(ScreenCriteria(volume_spike=2.5, volume_spike_window=20), db_path=db).empty


class TestInsufficientHistory:
    def test_a_stock_without_a_full_window_has_no_ratio(self, db):
        """新上市的股票沒有足夠歷史，略過而不是拿 3 天硬算。"""
        upsert("daily_prices", _prices("1101", [1000.0] * 3 + [9000.0]), db)

        row = run_screen(ScreenCriteria(), db_path=db).iloc[0]

        assert pd.isna(row["volume_ratio"])

    def test_it_is_excluded_from_a_spike_screen(self, db):
        upsert("daily_prices", _prices("1101", [1000.0] * 3 + [9000.0]), db)

        assert run_screen(ScreenCriteria(volume_spike=2.0), db_path=db).empty

    def test_exactly_enough_history_is_enough(self, db):
        # 20 日基準 + 當日 = 21 筆，剛好足夠。
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [3000.0]), db)

        assert not pd.isna(run_screen(ScreenCriteria(), db_path=db).iloc[0]["volume_ratio"])


class TestTheOutputIsInterpretable:
    def test_the_ratio_is_reported_even_without_the_filter(self, db):
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [3000.0]), db)

        assert "volume_ratio" in run_screen(ScreenCriteria(), db_path=db).columns

    def test_the_daily_change_is_reported(self, db):
        """爆量沒有方向：爆量上漲和爆量下跌是相反的訊號，必須看得出來。"""
        closes = [10.0] * 20 + [11.0]
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [3000.0], closes), db)

        row = run_screen(ScreenCriteria(), db_path=db).iloc[0]

        assert row["change_pct"] == pytest.approx(10.0)

    def test_a_falling_spike_has_a_negative_change(self, db):
        closes = [10.0] * 20 + [9.0]
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [3000.0], closes), db)

        assert run_screen(ScreenCriteria(), db_path=db).iloc[0]["change_pct"] == pytest.approx(-10.0)

    def test_the_only_bar_in_the_database_has_no_change(self, db):
        upsert("daily_prices", _prices("1101", [1000.0]), db)

        assert pd.isna(run_screen(ScreenCriteria(), db_path=db).iloc[0]["change_pct"])


class TestIlliquidStocksAreStillMeasured:
    """低量股的倍數是真的，只是沒有意義——由 --volume-min 過濾，不偷偷藏起來。"""

    def test_a_tiny_stock_still_gets_a_ratio(self, db):
        # 均量 6 張、當日 37 張 = 6.2 倍。數字沒錯，但那是 37 張。
        upsert("daily_prices", _prices("8101", [6000.0] * 20 + [37_000.0]), db)

        assert run_screen(ScreenCriteria(volume_spike=2.0), db_path=db).iloc[0][
            "volume_ratio"
        ] == pytest.approx(37_000 / 6000)

    def test_a_liquidity_floor_removes_it(self, db):
        upsert("daily_prices", _prices("8101", [6000.0] * 20 + [37_000.0]), db)
        upsert("daily_prices", _prices("2615", [13_970_000.0] * 20 + [77_405_000.0]), db)

        result = run_screen(
            ScreenCriteria(volume_spike=2.0, volume_min=500_000), db_path=db
        )

        assert set(result["stock_id"]) == {"2615"}


class TestCombinedWithOtherCriteria:
    def test_a_spike_screen_still_reports_the_other_columns(self, db):
        upsert("daily_prices", _prices("1101", [1000.0] * 20 + [3000.0]), db)

        row = run_screen(ScreenCriteria(volume_spike=2.0), db_path=db).iloc[0]

        assert row["latest_date"] and not pd.isna(row["latest_close"])


class TestStaleStocksAreNotTodaysSpike:
    """停止交易的股票，它「最新的一根 K 棒」不是市場的最新交易日。

    實際資料庫裡 1589 永冠-KY 的最後一筆是 2026-04-02，卻出現在 2026-08-18 的
    爆量清單中——四個月前的爆量被當成今天的訊號。這正是「數字看起來是今天的、
    其實不是」那一類錯誤。
    """

    def test_a_spike_on_the_market_latest_day_still_counts(self, db):
        """基準情境：兩檔都交易到最後一天，爆量的那檔要被選出來。"""
        upsert("daily_prices", _prices("1101", [1000.0] * 21), db)
        upsert("daily_prices", _prices("1589", [1000.0] * 20 + [9000.0]), db)

        result = run_screen(ScreenCriteria(volume_spike=2.0), db_path=db)

        assert set(result["stock_id"]) == {"1589"}

    def test_it_is_excluded_when_the_market_has_traded_since(self, db):
        # 1101 交易到第 30 天；1589 在第 21 天爆量後就沒資料了。
        upsert("daily_prices", _prices("1101", [1000.0] * 30), db)
        upsert("daily_prices", _prices("1589", [1000.0] * 20 + [9000.0]), db)

        result = run_screen(ScreenCriteria(volume_spike=2.0), db_path=db)

        assert result.empty, "1589 的爆量發生在市場最新交易日之前，不是今天的訊號"

    def test_the_unfiltered_screen_still_shows_its_real_ratio(self, db):
        """不是把資料藏起來——沒下爆量條件時，它的量比與真實日期都還在。"""
        upsert("daily_prices", _prices("1101", [1000.0] * 30), db)
        upsert("daily_prices", _prices("1589", [1000.0] * 20 + [9000.0]), db)

        row = run_screen(ScreenCriteria(), db_path=db).set_index("stock_id").loc["1589"]

        assert row["volume_ratio"] == pytest.approx(9.0)
        assert row["latest_date"] < "2026-02-11"

    def test_stale_stocks_are_reported_separately(self, db):
        from twstock_analyzer.screening.screener import stale_stock_ids

        upsert("daily_prices", _prices("1101", [1000.0] * 30), db)
        upsert("daily_prices", _prices("1589", [1000.0] * 20 + [9000.0]), db)

        import sqlite3
        conn = sqlite3.connect(db)
        stale = stale_stock_ids(conn)
        conn.close()

        assert stale == ["1589"]

    def test_nothing_is_stale_when_every_stock_traded(self, db):
        from twstock_analyzer.screening.screener import stale_stock_ids

        upsert("daily_prices", _prices("1101", [1000.0] * 21), db)
        upsert("daily_prices", _prices("2222", [1000.0] * 21), db)

        import sqlite3
        conn = sqlite3.connect(db)
        stale = stale_stock_ids(conn)
        conn.close()

        assert stale == []
