"""均線交叉的「趨勢同方向」條件。

只看交叉本身，會把兩種完全不同的事情算成同一件：MA20 已經翻上來、快線順勢
穿越（趨勢轉多），以及 MA20 還在下墜、快線只是反彈得比它快而撞上去（跌勢中
的反彈）。`ma_trend_align` 要求**交叉當日**兩條均線都朝交叉的方向走，把後者
排除。

實測 1,089 檔上市股一年份的 5x20 交叉：快線幾乎不篩掉東西（97.6% 的黃金交叉
本來就滿足），真正在篩的是慢線——只有約半數的黃金交叉發生在 MA20 也在上彎的
時候。

Fixture（每檔 30 個交易日，交叉點與斜率獨立於實作算出）：

  2222 先跌後漲   — MA5 於 index 23 上穿 MA10，此時 MA10 也在上彎（+0.30）
                    MA5 於 index 24 上穿 MA20，但 MA20 仍在下彎（-0.75）
  3333 先漲後跌   — MA5 於 index 24 下穿 MA10，但 MA10 仍在上彎（+0.60）
  4444 平盤後跳動 — 長期平盤後跳動。MA5 於 index 26 與 index 29 各上穿 MA20 一次；
                    index 29 那筆回看 3 日兩條線都恰好持平（0.00），回看 1 日則
                    都上彎（+4.00 / +1.00）。用 within=1 才能單獨看到那一筆。
"""

import pandas as pd
import pytest

from twstock_analyzer.db.repository import upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import ScreenCriteria, run_screen

DIP_THEN_RISE = [100 - i for i in range(20)] + [80 + 3 * i for i in range(1, 11)]
PEAK_THEN_FALL = [100 + 2 * i for i in range(20)] + [140 - 3 * i for i in range(1, 11)]
FLAT_THEN_JUMP = [100.0] * 26 + [120.0, 90.0, 90.0, 120.0]


@pytest.fixture()
def market(tmp_path):
    path = str(tmp_path / "trend.db")
    create_tables(path)

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-07-01", periods=30)]
    for stock_id, closes in (
        ("2222", DIP_THEN_RISE),
        ("3333", PEAK_THEN_FALL),
        ("4444", FLAT_THEN_JUMP),
    ):
        upsert("daily_prices", pd.DataFrame({
            "stock_id": [stock_id] * 30,
            "date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000_000] * 30,
            "adj_close": closes,
            "fetched_at": ["2025-08-15"] * 30,
        }), path)
    return path


def _matched(df) -> set[str]:
    return set() if df.empty else set(df["stock_id"])


class TestGoldenCrossNeedsBothLinesRising:
    def test_a_cross_into_a_still_falling_slow_line_is_rejected(self, market):
        # 2222 的 MA5 確實上穿了 MA20，但那是二十天跌勢後的反彈，MA20 還在下墜。
        result = run_screen(
            ScreenCriteria(ma_crossover="5x20", ma_crossover_within=10),
            db_path=market,
        )

        assert "2222" not in _matched(result)

    def test_the_same_cross_is_found_once_alignment_is_switched_off(self, market):
        # 證明上面篩掉它的是趨勢條件，不是這檔根本沒有交叉。
        result = run_screen(
            ScreenCriteria(
                ma_crossover="5x20", ma_crossover_within=10, ma_trend_align=False
            ),
            db_path=market,
        )

        assert "2222" in _matched(result)

    def test_a_cross_with_both_lines_rising_is_kept(self, market):
        # 同一檔對 MA10 的交叉發生得晚一步，那時 MA10 已經翻上來了。
        result = run_screen(
            ScreenCriteria(ma_crossover="5x10", ma_crossover_within=10),
            db_path=market,
        )

        assert "2222" in _matched(result)


class TestDeathCrossNeedsBothLinesFalling:
    def test_a_cross_into_a_still_rising_slow_line_is_rejected(self, market):
        # 3333 的 MA5 下穿 MA10，但 MA10 還在上彎——那是漲勢第一次被打斷，
        # 不是跌勢。
        result = run_screen(
            ScreenCriteria(
                ma_crossover="5x10", ma_crossover_direction="down", ma_crossover_within=10
            ),
            db_path=market,
        )

        assert "3333" not in _matched(result)

    def test_the_same_cross_is_found_once_alignment_is_switched_off(self, market):
        result = run_screen(
            ScreenCriteria(
                ma_crossover="5x10",
                ma_crossover_direction="down",
                ma_crossover_within=10,
                ma_trend_align=False,
            ),
            db_path=market,
        )

        assert "3333" in _matched(result)


class TestTheTrendWindow:
    def test_a_one_day_window_reads_the_slow_line_as_falling(self, market):
        # 3333 的 MA10 單日斜率是 -0.30，回看三日卻是 +0.60。回看幾天不是
        # 細節，它會改變答案。
        result = run_screen(
            ScreenCriteria(
                ma_crossover="5x10",
                ma_crossover_direction="down",
                ma_crossover_within=10,
                ma_trend_window=1,
            ),
            db_path=market,
        )

        assert "3333" in _matched(result)

    def test_a_flat_line_does_not_count_as_the_same_direction(self, market):
        # 4444 在交叉當日回看三日，兩條均線都恰好持平——走平不是趨勢。
        result = run_screen(
            ScreenCriteria(ma_crossover="5x20", ma_crossover_within=1),
            db_path=market,
        )

        assert "4444" not in _matched(result)

    def test_the_same_bar_qualifies_when_the_window_shortens_to_one_day(self, market):
        result = run_screen(
            ScreenCriteria(
                ma_crossover="5x20", ma_crossover_within=1, ma_trend_window=1
            ),
            db_path=market,
        )

        assert "4444" in _matched(result)

    def test_a_window_below_one_is_rejected_loudly(self, market):
        with pytest.raises(ValueError):
            run_screen(
                ScreenCriteria(ma_crossover="5x20", ma_trend_window=0), db_path=market
            )


class TestTheTrendIsReadAtTheCrossBarNotToday:
    def test_widening_the_window_does_not_change_whether_a_cross_qualifies(self, market):
        """把 --ma-within 放寬只該多納入更早的交叉，不該讓已合格的那筆消失。"""
        tight = run_screen(
            ScreenCriteria(ma_crossover="5x10", ma_crossover_within=7), db_path=market
        )
        loose = run_screen(
            ScreenCriteria(ma_crossover="5x10", ma_crossover_within=12), db_path=market
        )

        assert _matched(tight) <= _matched(loose)
        assert "2222" in _matched(tight)


class TestNotEnoughHistory:
    def test_a_stock_with_bars_enough_for_the_cross_but_not_the_trend_is_skipped(self, tmp_path):
        # 21 根 K 棒夠判斷 5x20 的交叉，卻不夠回看三日的趨勢。略過而不是把
        # NaN 當成「沒有上彎」。
        path = str(tmp_path / "short.db")
        create_tables(path)
        closes = [100 - i for i in range(15)] + [85 + 5 * i for i in range(1, 7)]
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-07-01", periods=21)]
        upsert("daily_prices", pd.DataFrame({
            "stock_id": ["9999"] * 21,
            "date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000_000] * 21,
            "adj_close": closes,
            "fetched_at": ["2025-08-15"] * 21,
        }), path)

        result = run_screen(
            ScreenCriteria(ma_crossover="5x20", ma_crossover_within=5), db_path=path
        )

        assert _matched(result) == set()


class TestThroughTheCli:
    @pytest.fixture()
    def cli(self, market, monkeypatch, tmp_path):
        from typer.testing import CliRunner

        from twstock_analyzer.cli.main import app
        from twstock_analyzer.db.repository import set_default_db_path

        set_default_db_path(market)
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", market)
        monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "t.log"))
        return CliRunner(), app

    def test_alignment_is_on_by_default(self, cli):
        runner, app = cli

        result = runner.invoke(app, ["screen", "run", "--ma-cross", "5x20", "--ma-within", "10"])

        assert result.exit_code == 0
        assert "2222" not in result.stdout

    def test_the_opt_out_flag_restores_the_plain_crossover_screen(self, cli):
        runner, app = cli

        result = runner.invoke(app, [
            "screen", "run", "--ma-cross", "5x20", "--ma-within", "10", "--no-ma-trend-align",
        ])

        assert result.exit_code == 0
        assert "2222" in result.stdout

    def test_a_bad_trend_window_exits_with_an_explanation(self, cli):
        runner, app = cli

        result = runner.invoke(app, [
            "screen", "run", "--ma-cross", "5x20", "--ma-trend-window", "0",
        ])

        assert result.exit_code == 1
        assert "趨勢回看天數" in result.stdout


class TestThroughTheApi:
    @pytest.fixture()
    def client(self, market, monkeypatch):
        from fastapi.testclient import TestClient

        from twstock_analyzer.api.server import create_app

        monkeypatch.setenv("TWSTOCK_DB", market)
        return TestClient(create_app())

    def test_the_endpoint_aligns_by_default(self, client):
        response = client.post("/screen", params={"ma_crossover": "5x20", "ma_within": 10})

        assert response.status_code == 200
        assert "2222" not in [r["stock_id"] for r in response.json()["results"]]

    def test_the_endpoint_can_switch_alignment_off(self, client):
        response = client.post(
            "/screen",
            params={"ma_crossover": "5x20", "ma_within": 10, "ma_trend_align": False},
        )

        assert response.status_code == 200
        assert "2222" in [r["stock_id"] for r in response.json()["results"]]

    def test_a_bad_trend_window_is_a_client_error(self, client):
        response = client.post(
            "/screen", params={"ma_crossover": "5x20", "ma_trend_window": 0}
        )

        assert response.status_code == 400
