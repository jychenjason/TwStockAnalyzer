"""更新要先問「現在該有多新」，而不是「今天是幾號」。

實測起點：資料庫已經是最新的，跑 update 卻仍然一檔一檔去抓。原因是判斷式拿
``latest >= today`` 比對——``today`` 是日曆上的今天，而 ``latest`` 是最後一個
交易日。兩者只有在「今天是交易日、而且當日行情已經入庫」時才會相等，其餘時間
（週末、假日、每天下午四點以前）全市場都會為了確認「沒有新資料」各發一次請求，
抓回空表，再一起撞上 TWSE 的 428 限流。

這裡的測試釘住三件事：時間差（四點）、週末推算、以及觀察到的非交易日只需要被
發現一次。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.data.availability import (
    PUBLISH_HOUR,
    available_trading_date,
    forget_non_trading_day,
    non_trading_days,
    record_non_trading_day,
)
from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables


def _at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    create_tables(db_file)
    set_default_db_path(db_file)
    monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", db_file)
    monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "test.log"))
    return db_file


@pytest.fixture()
def runner():
    return CliRunner()


class RecordingLoader:
    """記下被問了什麼，並照 ``rows`` 的設定回答。"""

    def __init__(self, rows: bool = True):
        self.calls: list[tuple[str, str, str, str | None]] = []
        self.rows = rows

    def get_data(self, stock_id, data_type, start_date, end_date=None, logger=None):
        self.calls.append((stock_id, data_type, start_date, end_date))
        if not self.rows:
            return pd.DataFrame()
        dates = pd.bdate_range(start_date, periods=1).strftime("%Y-%m-%d").tolist()
        return pd.DataFrame({
            "date": dates,
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1_000_000],
        })

    def get_stock_list(self, logger=None):
        return [{"stock_id": "2330"}]


def _seed_daily(db_path: str, stock_id: str, last_date: str, days: int = 120) -> None:
    dates = pd.bdate_range(end=last_date, periods=days).strftime("%Y-%m-%d").tolist()
    upsert(
        "daily_prices",
        pd.DataFrame({
            "stock_id": [stock_id] * len(dates),
            "date": dates,
            "open": [100.0] * len(dates),
            "high": [102.0] * len(dates),
            "low": [99.0] * len(dates),
            "close": [101.0] * len(dates),
            "volume": [1_000_000] * len(dates),
            "adj_close": [101.0] * len(dates),
            "fetched_at": [last_date] * len(dates),
        }),
        db_path,
    )


class TestPublishLag:
    """當日行情要等盤後彙整，約下午四點才出得來。"""

    def test_before_the_publish_hour_the_latest_available_day_is_yesterday(self):
        # 2026-08-21 是星期五。
        assert available_trading_date(_at("2026-08-21 09:00")) == "2026-08-20"
        assert available_trading_date(_at("2026-08-21 15:59")) == "2026-08-20"

    def test_from_the_publish_hour_onwards_today_counts(self):
        assert available_trading_date(_at("2026-08-21 16:00")) == "2026-08-21"
        assert available_trading_date(_at("2026-08-21 23:30")) == "2026-08-21"

    def test_the_publish_hour_is_four_in_the_afternoon(self):
        assert PUBLISH_HOUR == 16


class TestWeekends:
    """週末沒有交易——最新的仍是星期五。"""

    @pytest.mark.parametrize(
        "moment",
        [
            "2026-08-22 10:00",  # 週六上午
            "2026-08-22 20:00",  # 週六盤後時段
            "2026-08-23 20:00",  # 週日
            "2026-08-24 09:00",  # 週一早上，當日行情還沒公布
        ],
    )
    def test_the_weekend_never_moves_past_friday(self, moment):
        assert available_trading_date(_at(moment)) == "2026-08-21"

    def test_monday_afternoon_is_monday(self):
        assert available_trading_date(_at("2026-08-24 17:00")) == "2026-08-24"


class TestObservedNonTradingDays:
    """假日沒有內建行事曆，只能從「全市場都交不出資料」觀察出來。"""

    def test_a_recorded_holiday_is_stepped_over(self, _isolated_db):
        # 週五 08-21 記成非交易日 → 週六問，答案要退到週四。
        record_non_trading_day("2026-08-21", _isolated_db, now=_at("2026-08-22 10:00"))

        assert available_trading_date(_at("2026-08-22 10:00"), _isolated_db) == "2026-08-20"

    def test_consecutive_holidays_are_stepped_over_together(self, _isolated_db):
        for day in ("2026-08-19", "2026-08-20", "2026-08-21"):
            record_non_trading_day(day, _isolated_db, now=_at("2026-08-22 10:00"))

        assert available_trading_date(_at("2026-08-22 10:00"), _isolated_db) == "2026-08-18"

    def test_today_is_never_recorded_as_a_holiday(self, _isolated_db):
        # 下午四點剛過、來源還沒公布，全市場一樣交不出資料——那是時間差，不是
        # 假日。記下去，當天就再也抓不到當日行情。
        recorded = record_non_trading_day("2026-08-21", _isolated_db, now=_at("2026-08-21 16:05"))

        assert recorded is False
        assert non_trading_days(_isolated_db) == set()

    def test_a_day_the_database_proves_traded_is_never_recorded(self, _isolated_db):
        """實測 2026-08-21：1089 檔裡 1086 檔快取命中，只有 3 檔落後的被問到。

        那 3 檔都回空表——它們本來就停在舊日期，交不出東西是常態。舊的推論把
        這個樣本讀成「全市場都沒有資料」，於是把 1086 檔都有行情的 08-20 記成
        了假日。資料庫自己就是反證。
        """
        _seed_daily(_isolated_db, "2330", "2026-08-20")

        recorded = record_non_trading_day("2026-08-20", _isolated_db, now=_at("2026-08-21 10:40"))

        assert recorded is False
        assert non_trading_days(_isolated_db) == set()

    def test_a_day_that_later_yields_data_is_forgotten(self, _isolated_db):
        record_non_trading_day("2026-08-21", _isolated_db, now=_at("2026-08-22 10:00"))

        forget_non_trading_day("2026-08-21", _isolated_db)

        assert non_trading_days(_isolated_db) == set()

    def test_a_database_without_the_table_degrades_to_the_plain_calculation(self, tmp_path):
        bare = str(tmp_path / "bare.db")
        sqlite3.connect(bare).close()

        assert non_trading_days(bare) == set()
        assert available_trading_date(_at("2026-08-21 17:00"), bare) == "2026-08-21"


class TestDatabaseAheadOfTheClock:
    """資料庫已經比推算更新時，以資料庫為準——不為了抓更舊的東西掃全市場。"""

    def test_a_newer_reference_date_wins(self, _isolated_db):
        _seed_daily(_isolated_db, "2330", "2026-08-21")

        # 推算說只到 08-20（四點前），但資料庫已經有 08-21。
        assert available_trading_date(_at("2026-08-21 09:00"), _isolated_db) == "2026-08-21"

    def test_an_older_reference_date_does_not_hold_the_target_back(self, _isolated_db):
        _seed_daily(_isolated_db, "2330", "2026-08-14")

        assert available_trading_date(_at("2026-08-21 17:00"), _isolated_db) == "2026-08-21"


class TestUpdateExistingDoesNotAskWhenItAlreadyKnows:
    """這一組是最初那個問題的迴歸測試：已經最新就不該發請求。"""

    @pytest.fixture()
    def loader(self, monkeypatch):
        recording = RecordingLoader()
        monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: recording)
        return recording

    def test_a_database_current_to_the_available_day_fetches_nothing(self, runner, loader, _isolated_db):
        target = available_trading_date(datetime.now(), _isolated_db)
        _seed_daily(_isolated_db, "2330", target)
        _seed_daily(_isolated_db, "2317", target)

        result = runner.invoke(app, ["update", "--stock", "existing"])

        assert result.exit_code == 0
        assert loader.calls == [], "資料庫已經追上可得最新交易日，不該再問來源"
        assert "Updated 2/2" in result.stdout

    def test_it_holds_regardless_of_the_hour_the_test_runs(self, runner, loader, _isolated_db):
        """把今天釘成非交易日，讓 target 必定早於今天。

        上一個測試在下午四點以後跑會退化成「target 剛好等於今天」，舊行為也會
        通過。這一個在任何時刻都咬得住：資料庫停在昨天、日曆說今天，而今天沒有
        開盤——舊的 ``latest >= today`` 必定去抓，正確的行為是一個請求都不發。
        """
        conn = sqlite3.connect(_isolated_db)
        conn.execute(
            "INSERT OR REPLACE INTO market_calendar (date, has_trading, checked_at) VALUES (?, 0, ?)",
            (datetime.now().strftime("%Y-%m-%d"), datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

        target = available_trading_date(datetime.now(), _isolated_db)
        assert target < datetime.now().strftime("%Y-%m-%d")
        _seed_daily(_isolated_db, "2330", target)

        result = runner.invoke(app, ["update", "--stock", "existing"])

        assert result.exit_code == 0
        assert loader.calls == []

    def test_a_stale_database_still_fetches(self, runner, loader, _isolated_db):
        target = datetime.strptime(available_trading_date(datetime.now(), _isolated_db), "%Y-%m-%d")
        _seed_daily(_isolated_db, "2330", (target - timedelta(days=10)).strftime("%Y-%m-%d"))

        result = runner.invoke(app, ["update", "--stock", "existing"])

        assert result.exit_code == 0
        assert len(loader.calls) == 1, "落後的股票還是要抓"

    def test_force_asks_the_source_even_when_current(self, runner, loader, _isolated_db):
        target = available_trading_date(datetime.now(), _isolated_db)
        _seed_daily(_isolated_db, "2330", target)

        result = runner.invoke(app, ["update", "--stock", "existing", "--force"])

        assert result.exit_code == 0
        assert len(loader.calls) == 1


class TestHolidaySweepHappensOnce:
    """全市場都交不出資料時記下來，下一次不必再問一遍。"""

    def test_an_empty_market_on_a_finished_day_is_recorded(self, _isolated_db):
        from twstock_analyzer.cli.main import _remember_market_holiday
        from twstock_analyzer.utils.logger import get_logger

        _remember_market_holiday(
            "2026-08-21", asked_source=900, rows_fetched=0, failed=[],
            now=_at("2026-08-22 10:00"), db_path=_isolated_db, logger=get_logger("test"),
        )

        assert non_trading_days(_isolated_db) == {"2026-08-21"}

    def test_a_stale_sample_returning_nothing_is_not_a_holiday(self, _isolated_db):
        """整段流程的迴歸：只有落後的那幾檔被問到、全部回空表。"""
        from twstock_analyzer.cli.main import _remember_market_holiday
        from twstock_analyzer.utils.logger import get_logger

        # 市場在 08-20 有開盤——1086 檔的行情就躺在資料庫裡。
        _seed_daily(_isolated_db, "2330", "2026-08-20")
        _seed_daily(_isolated_db, "2317", "2026-08-20")
        # 被問到的只有停在更早日期的那 3 檔，而它們什麼都交不出來。
        _seed_daily(_isolated_db, "8105", "2026-08-13")

        _remember_market_holiday(
            "2026-08-20", asked_source=3, rows_fetched=0, failed=[],
            now=_at("2026-08-21 10:40"), db_path=_isolated_db, logger=get_logger("test"),
        )

        assert non_trading_days(_isolated_db) == set()

    def test_a_single_failed_stock_blocks_the_conclusion(self, _isolated_db):
        from twstock_analyzer.cli.main import _remember_market_holiday
        from twstock_analyzer.utils.logger import get_logger

        # 限流回的空表是故障，不是答案。一檔失敗就足以讓「全市場都沒有」失效。
        _remember_market_holiday(
            "2026-08-21", asked_source=900, rows_fetched=0, failed=["2330"],
            now=_at("2026-08-22 10:00"), db_path=_isolated_db, logger=get_logger("test"),
        )

        assert non_trading_days(_isolated_db) == set()

    def test_an_all_cache_hit_run_concludes_nothing(self, _isolated_db):
        from twstock_analyzer.cli.main import _remember_market_holiday
        from twstock_analyzer.utils.logger import get_logger

        # 一檔都沒問過，當然不能推論市場沒開。
        _remember_market_holiday(
            "2026-08-21", asked_source=0, rows_fetched=0, failed=[],
            now=_at("2026-08-22 10:00"), db_path=_isolated_db, logger=get_logger("test"),
        )

        assert non_trading_days(_isolated_db) == set()

    def test_any_fetched_row_clears_a_previous_misrecording(self, _isolated_db):
        from twstock_analyzer.cli.main import _remember_market_holiday
        from twstock_analyzer.utils.logger import get_logger

        record_non_trading_day("2026-08-21", _isolated_db, now=_at("2026-08-22 10:00"))

        _remember_market_holiday(
            "2026-08-21", asked_source=900, rows_fetched=12, failed=[],
            now=_at("2026-08-22 10:00"), db_path=_isolated_db, logger=get_logger("test"),
        )

        assert non_trading_days(_isolated_db) == set()


class TestInstitutionalBackfillHonoursTheCalendar:
    @pytest.fixture(autouse=True)
    def _no_throttle(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *_: None)

    def test_known_non_trading_days_are_not_requested_again(self, runner, monkeypatch, _isolated_db):
        asked: list[str] = []

        class Empty(RecordingLoader):
            def get_data(self, stock_id, data_type, start_date, end_date=None, logger=None):
                asked.append(start_date)
                return pd.DataFrame()

        monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: Empty())

        start_dt = datetime.now() - timedelta(days=10)
        while start_dt.weekday() >= 5:
            start_dt += timedelta(days=1)
        closed = start_dt.strftime("%Y-%m-%d")
        record_non_trading_day(closed, _isolated_db)

        result = runner.invoke(
            app, ["update", "--stock", "2330", "--type", "institutional", "--start", closed]
        )

        assert result.exit_code == 0
        assert closed not in asked

    def test_an_empty_day_is_remembered_for_next_time(self, runner, monkeypatch, _isolated_db):
        class Empty(RecordingLoader):
            def get_data(self, stock_id, data_type, start_date, end_date=None, logger=None):
                return pd.DataFrame()

        monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: Empty())

        start_dt = datetime.now() - timedelta(days=6)
        while start_dt.weekday() >= 5:
            start_dt += timedelta(days=1)
        start = start_dt.strftime("%Y-%m-%d")

        runner.invoke(app, ["update", "--stock", "2330", "--type", "institutional", "--start", start])

        assert start in non_trading_days(_isolated_db)
