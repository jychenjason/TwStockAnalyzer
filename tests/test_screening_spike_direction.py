"""爆量的方向：爆量上漲（承接）與爆量下跌（出貨）是相反的訊號。

實測 2 倍以上的 74 檔中，上漲 35、中性 16、下跌 23——只看倍數等於把三種相反的
情況混在一起。

中性帶預設 ±1%，取自全市場當日 |漲跌幅| 的分布（中位數 1.42%）：1% 落在典型
單日波動之下，所以被歸為中性的確實是「沒走到哪裡去」的那些。最具代表性的是
2455 全新——3.79 倍量卻只有 +0.12%，那是換手攻防不是承接；用純正負號會把它
算成爆量上漲。
"""

import pandas as pd
import pytest

from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import (
    SPIKE_DIRECTION_BAND,
    ScreenCriteria,
    run_screen,
)


def _prices(stock_id: str, volumes: list[float], closes: list[float]) -> pd.DataFrame:
    n = len(volumes)
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", periods=n)]
    return pd.DataFrame({
        "stock_id": [stock_id] * n, "date": dates,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": volumes, "adj_close": closes, "fetched_at": ["2026-08-19"] * n,
    })


def _spike(stock_id: str, change_pct: float) -> pd.DataFrame:
    """一檔 3 倍爆量、當日漲跌幅為 change_pct 的股票。"""
    return _prices(
        stock_id,
        [1_000_000.0] * 20 + [3_000_000.0],
        [100.0] * 20 + [100.0 * (1 + change_pct / 100)],
    )


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "dir.db")
    create_tables(path)
    set_default_db_path(path)
    return path


@pytest.fixture()
def market(db):
    """五檔都是 3 倍爆量，只有當日漲跌幅不同。"""
    upsert("daily_prices", _spike("1111", 8.0), db)    # 爆量大漲
    upsert("daily_prices", _spike("2222", 1.5), db)    # 爆量小漲
    upsert("daily_prices", _spike("3333", 0.12), db)   # 爆量平盤（2455 全新那種）
    upsert("daily_prices", _spike("4444", -1.5), db)   # 爆量小跌
    upsert("daily_prices", _spike("5555", -8.0), db)   # 爆量大跌
    return db


class TestTheDefaultBand:
    def test_the_neutral_band_is_one_percent(self):
        assert SPIKE_DIRECTION_BAND == 1.0


class TestUp:
    def test_it_keeps_the_rising_spikes(self, market):
        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="up"), db_path=market
        )
        assert set(result["stock_id"]) == {"1111", "2222"}

    def test_a_flat_close_is_not_a_rising_spike(self, market):
        """3 倍量而股價只動 0.12%，是換手不是承接。"""
        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="up"), db_path=market
        )
        assert "3333" not in set(result["stock_id"])

    def test_falling_spikes_are_excluded(self, market):
        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="up"), db_path=market
        )
        assert {"4444", "5555"}.isdisjoint(set(result["stock_id"]))


class TestDown:
    def test_it_keeps_the_falling_spikes(self, market):
        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="down"), db_path=market
        )
        assert set(result["stock_id"]) == {"4444", "5555"}

    def test_a_flat_close_is_not_a_falling_spike(self, market):
        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="down"), db_path=market
        )
        assert "3333" not in set(result["stock_id"])


class TestTheBandIsConfigurable:
    def test_a_zero_band_falls_back_to_the_plain_sign(self, market):
        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="up", spike_direction_band=0.0),
            db_path=market,
        )
        assert set(result["stock_id"]) == {"1111", "2222", "3333"}

    def test_a_wider_band_demands_a_bigger_move(self, market):
        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="up", spike_direction_band=5.0),
            db_path=market,
        )
        assert set(result["stock_id"]) == {"1111"}

    def test_the_band_is_symmetric(self, market):
        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="down", spike_direction_band=5.0),
            db_path=market,
        )
        assert set(result["stock_id"]) == {"5555"}


class TestItOnlyAppliesToSpikes:
    def test_a_rising_stock_without_a_spike_is_not_selected(self, db):
        upsert("daily_prices", _prices("1111", [1_000_000.0] * 21,
                                       [100.0] * 20 + [108.0]), db)   # 大漲但沒爆量

        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="up"), db_path=db
        )

        assert result.empty


class TestBadInput:
    def test_an_unknown_direction_is_rejected(self, market):
        with pytest.raises(ValueError, match="sideways"):
            run_screen(
                ScreenCriteria(volume_spike=2.0, spike_direction="sideways"), db_path=market
            )

    def test_a_direction_without_a_spike_threshold_is_rejected(self, market):
        """單獨用方向不是這個參數的用途，講清楚比默默當成漲跌篩選好。"""
        with pytest.raises(ValueError, match="volume-spike|volume_spike"):
            run_screen(ScreenCriteria(spike_direction="up"), db_path=market)

    def test_a_negative_band_is_rejected(self, market):
        with pytest.raises(ValueError):
            run_screen(
                ScreenCriteria(volume_spike=2.0, spike_direction="up", spike_direction_band=-1.0),
                db_path=market,
            )


class TestUnknownDirection:
    def test_a_stock_with_no_previous_close_is_excluded(self, db):
        """只有一根 K 棒就判斷不出方向，略過而不是猜。"""
        upsert("daily_prices", _prices("1111", [1_000_000.0], [100.0]), db)

        result = run_screen(
            ScreenCriteria(volume_spike=2.0, spike_direction="up"), db_path=db
        )

        assert result.empty
