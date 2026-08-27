"""`screen run --volume-spike` 的輸出。

低量股會讓爆量榜完全失真：實測資料庫裡 2 倍以上的 75 檔中有 35 檔均量不到
500 張，倍數榜前排是「均量 6 張、當日 37 張 = 5.7 倍」這種東西，真正有意義的
萬海（均量 13,970 張、當日 77,405 張）反而排在它們後面。

不預設幫使用者濾掉——這種隱形過濾正是本專案一路在修的毛病；改成明講有幾檔
低於門檻，並提示可用的參數。
"""

import pandas as pd
import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables

#: 低於這個 20 日均量（張）的爆量在實務上沒有意義。
QUIET_LOTS = 500


def _prices(stock_id, volumes, closes=None):
    n = len(volumes)
    closes = closes or [10.0] * n
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", periods=n)]
    return pd.DataFrame({
        "stock_id": [stock_id] * n, "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes, "adj_close": closes, "fetched_at": ["2026-08-17"] * n,
    })


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "spike_cli.db")
    create_tables(path)
    set_default_db_path(path)
    monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", path)
    monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "l.log"))
    return path


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def market(db):
    """一檔真爆量的大型股，一檔均量 6 張的雜訊，一檔沒爆量的。"""
    upsert("daily_prices", _prices("2615", [13_970_000.0] * 20 + [77_405_000.0],
                                   [10.0] * 20 + [10.54]), db)
    upsert("daily_prices", _prices("8101", [6_000.0] * 20 + [37_000.0],
                                   [10.0] * 20 + [9.17]), db)
    upsert("daily_prices", _prices("1101", [5_000_000.0] * 20 + [5_100_000.0]), db)
    return db


class TestTheSpikeScreen:
    def test_it_selects_the_spiking_stocks(self, runner, market):
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert result.exit_code == 0, result.stdout
        assert "2615" in result.stdout
        assert "1101" not in result.stdout

    def test_the_ratio_is_shown(self, runner, market):
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "5.54" in result.stdout, "量比要印出來才能排序判斷"

    def test_the_daily_change_is_shown_with_its_sign(self, runner, market):
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "+5.40" in result.stdout, "爆量上漲"
        assert "-8.30" in result.stdout, "爆量下跌"


class TestTheIlliquidityHint:
    def test_it_warns_when_no_liquidity_floor_was_given(self, runner, market):
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "--volume-min" in result.stdout, "應提示可用的參數"

    def test_the_warning_counts_the_quiet_stocks(self, runner, market):
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "1 檔" in result.stdout, "8101 均量 6 張，應被數出來"

    def test_the_quiet_stock_is_still_listed(self, runner, market):
        """只是提醒，不是偷偷過濾。"""
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "8101" in result.stdout

    def test_no_warning_once_a_floor_is_given(self, runner, market):
        result = runner.invoke(
            app, ["screen", "run", "--volume-spike", "2", "--volume-min", "500000"]
        )

        assert "--volume-min" not in result.stdout
        assert "8101" not in result.stdout

    def test_no_warning_when_nothing_is_illiquid(self, runner, db):
        upsert("daily_prices", _prices("2615", [13_970_000.0] * 20 + [77_405_000.0]), db)

        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "--volume-min" not in result.stdout

    def test_no_warning_without_a_spike_screen(self, runner, market):
        result = runner.invoke(app, ["screen", "run"])

        assert "--volume-min" not in result.stdout


class TestStaleStocksAreAnnounced:
    """排除已停止交易的股票，但要講出來——安靜地少掉幾檔才是更糟的失敗。"""

    @pytest.fixture()
    def with_stale(self, db):
        upsert("daily_prices", _prices("2615", [13_970_000.0] * 30), db)
        upsert("daily_prices", _prices("1589", [1_000_000.0] * 20 + [9_000_000.0]), db)
        return db

    def test_the_stale_spike_is_not_a_result(self, runner, with_stale):
        """1589 只出現在「已排除」的說明裡，不出現在結果表。"""
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "No stocks matched" in result.stdout
        assert "Screening Results" not in result.stdout

    def test_the_exclusion_is_reported(self, runner, with_stale):
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "1 檔" in result.stdout
        assert "停止交易" in result.stdout

    def test_nothing_is_said_when_no_stock_is_stale(self, runner, db):
        upsert("daily_prices", _prices("2615", [1_000_000.0] * 20 + [9_000_000.0]), db)

        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "停止交易" not in result.stdout

    def test_nothing_is_said_without_a_spike_screen(self, runner, with_stale):
        result = runner.invoke(app, ["screen", "run"])

        assert "停止交易" not in result.stdout


class TestOutOfDateDataIsDistinguished:
    """「update 沒跑完」和「已下市」是兩件事，後果也不同。

    2026-08-20 實測：708 檔更新到當日、378 檔還停在 08-19。舊訊息把這 378 檔
    和 1589 永冠-KY（資料停在 2026-04-02）一起說成「最後一根 K 棒的爆量不是
    當日訊號」，看起來像那些股票有問題——實際上是使用者的篩選少了 35% 的市場。
    """

    @pytest.fixture()
    def half_updated(self, db):
        """7 檔更新到最新交易日，3 檔落後一天。"""
        for i in range(7):
            upsert("daily_prices", _prices(f"200{i}", [1_000_000.0] * 21,
                                           [10.0] * 21), db)
        for i in range(3):
            upsert("daily_prices", _prices(f"300{i}", [1_000_000.0] * 20,
                                           [10.0] * 20), db)
        return db

    def test_it_says_the_data_is_out_of_date_not_that_trading_stopped(self, runner, half_updated):
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "尚未更新" in result.stdout
        assert "停止交易" not in result.stdout

    def test_it_names_the_command_that_fixes_it(self, runner, half_updated):
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "update existing" in result.stdout

    def test_it_warns_that_the_screen_is_incomplete(self, runner, half_updated):
        """3/10 檔沒參與比對，使用者必須知道結果不完整。"""
        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "不完整" in result.stdout
        assert "7/10" in result.stdout

    def test_a_lone_dead_stock_does_not_trigger_the_incomplete_warning(self, runner, db):
        """長期停牌的股票市場上本來就有幾檔，天天喊不完整會讓人忽略警告。"""
        for i in range(40):
            upsert("daily_prices", _prices(f"{2000 + i}", [1_000_000.0] * 21, [10.0] * 21), db)
        upsert("daily_prices", _prices("1589", [1_000_000.0] * 5, [10.0] * 5), db)

        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "停止交易" in result.stdout
        assert "不完整" not in result.stdout

    def test_nothing_is_said_when_everything_is_up_to_date(self, runner, db):
        for i in range(5):
            upsert("daily_prices", _prices(f"200{i}", [1_000_000.0] * 21, [10.0] * 21), db)

        result = runner.invoke(app, ["screen", "run", "--volume-spike", "2"])

        assert "尚未更新" not in result.stdout
        assert "不完整" not in result.stdout


class TestSpikeDirection:
    @pytest.fixture()
    def directional(self, db):
        def spike(sid, chg):
            return _prices(sid, [2_000_000.0] * 20 + [6_000_000.0],
                           [100.0] * 20 + [100.0 * (1 + chg / 100)])
        upsert("daily_prices", spike("1111", 8.0), db)     # 爆量大漲
        upsert("daily_prices", spike("3333", 0.12), db)    # 爆量平盤
        upsert("daily_prices", spike("5555", -8.0), db)    # 爆量大跌
        return db

    def test_up_keeps_only_the_rising_spike(self, runner, directional):
        result = runner.invoke(
            app, ["screen", "run", "--volume-spike", "2", "--spike-direction", "up"]
        )

        assert result.exit_code == 0, result.stdout
        assert "1111" in result.stdout
        assert "5555" not in result.stdout

    def test_up_excludes_the_flat_close(self, runner, directional):
        result = runner.invoke(
            app, ["screen", "run", "--volume-spike", "2", "--spike-direction", "up"]
        )

        assert "3333" not in result.stdout

    def test_down_keeps_only_the_falling_spike(self, runner, directional):
        result = runner.invoke(
            app, ["screen", "run", "--volume-spike", "2", "--spike-direction", "down"]
        )

        assert "5555" in result.stdout
        assert "1111" not in result.stdout

    def test_the_band_is_configurable(self, runner, directional):
        result = runner.invoke(
            app, ["screen", "run", "--volume-spike", "2",
                  "--spike-direction", "up", "--spike-direction-band", "0"]
        )

        assert "3333" in result.stdout, "帶寬 0 時平盤只看正負號"

    def test_a_bad_direction_is_a_clean_error(self, runner, directional):
        result = runner.invoke(
            app, ["screen", "run", "--volume-spike", "2", "--spike-direction", "sideways"]
        )

        assert result.exit_code == 1
        assert "Traceback" not in result.stdout

    def test_a_direction_without_a_spike_threshold_is_a_clean_error(self, runner, directional):
        result = runner.invoke(app, ["screen", "run", "--spike-direction", "up"])

        assert result.exit_code == 1
        assert "Traceback" not in result.stdout
        assert "--volume-spike" in result.stdout
