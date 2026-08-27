"""Chart behaviour that users can see.

Weekends, public holidays and trading halts have no bars, so leaving them on
the time axis stretches the candles apart with blank space.  These tests pin
the gap removal down at the chart seam.
"""

import pandas as pd
import pytest

from twstock_analyzer.visualization.charts import (
    DOWN_COLOR,
    UP_COLOR,
    non_trading_days,
    plot_kline,
    plot_technical,
    volume_colors,
)


@pytest.fixture()
def prices_with_a_halt():
    """2025-09-01..05 and 15..16 traded; the week between is a halt."""
    dates = [
        "2025-09-01", "2025-09-02", "2025-09-03", "2025-09-04", "2025-09-05",
        "2025-09-15", "2025-09-16",
    ]
    return pd.DataFrame({
        "date": dates,
        "open": [100.0] * len(dates),
        "high": [102.0] * len(dates),
        "low": [99.0] * len(dates),
        "close": [101.0] * len(dates),
        "volume": [1_000_000] * len(dates),
    })


class TestNonTradingDays:
    def test_weekends_are_reported(self, prices_with_a_halt):
        missing = non_trading_days(prices_with_a_halt["date"])

        assert "2025-09-06" in missing  # Saturday
        assert "2025-09-07" in missing  # Sunday

    def test_a_trading_halt_is_reported_too(self, prices_with_a_halt):
        missing = non_trading_days(prices_with_a_halt["date"])

        # The halt runs Monday 09-08 through Friday 09-12.
        for day in ("2025-09-08", "2025-09-09", "2025-09-10", "2025-09-11", "2025-09-12"):
            assert day in missing

    def test_days_that_did_trade_are_never_reported(self, prices_with_a_halt):
        missing = set(non_trading_days(prices_with_a_halt["date"]))

        assert missing.isdisjoint(set(prices_with_a_halt["date"]))

    def test_the_range_is_bounded_by_the_data(self, prices_with_a_halt):
        missing = non_trading_days(prices_with_a_halt["date"])

        # 2025-09-01 .. 2025-09-16 is 16 calendar days, 7 of which traded.
        assert len(missing) == 9

    def test_an_empty_series_has_nothing_to_hide(self):
        assert non_trading_days([]) == []


class TestChartsHideThem:
    def test_the_kline_axis_skips_non_trading_days(self, prices_with_a_halt):
        figure = plot_kline(prices_with_a_halt)

        hidden = figure.layout.xaxis.rangebreaks[0]["values"]
        assert "2025-09-08" in hidden
        assert "2025-09-01" not in hidden

    def test_the_technical_chart_skips_them_as_well(self, prices_with_a_halt):
        figure = plot_technical(prices_with_a_halt)

        hidden = figure.layout.xaxis.rangebreaks[0]["values"]
        assert "2025-09-08" in hidden

    def test_a_gapless_series_needs_no_rangebreaks(self):
        dates = ["2025-09-01", "2025-09-02", "2025-09-03"]
        df = pd.DataFrame({
            "date": dates,
            "open": [100.0] * 3,
            "high": [102.0] * 3,
            "low": [99.0] * 3,
            "close": [101.0] * 3,
            "volume": [1_000_000] * 3,
        })

        figure = plot_kline(df)

        assert not figure.layout.xaxis.rangebreaks


@pytest.fixture()
def mixed_days():
    """Four days: up, down, doji (close == open), up."""
    return pd.DataFrame({
        "date": ["2025-09-01", "2025-09-02", "2025-09-03", "2025-09-04"],
        "open": [100.0, 105.0, 110.0, 108.0],
        "high": [106.0, 106.0, 111.0, 113.0],
        "low": [99.0, 101.0, 109.0, 107.0],
        "close": [105.0, 102.0, 110.0, 112.0],
        "volume": [10, 20, 30, 40],
    })


class TestVolumeMatchesTheCandles:
    def test_each_bar_takes_its_days_direction(self, mixed_days):
        assert volume_colors(mixed_days) == [UP_COLOR, DOWN_COLOR, UP_COLOR, UP_COLOR]

    def test_a_doji_is_coloured_the_same_way_the_candle_is(self, mixed_days):
        # close == open on 2025-09-03; plotly draws that candle as increasing.
        assert volume_colors(mixed_days)[2] == UP_COLOR

    def test_the_kline_volume_uses_the_candle_palette(self, mixed_days):
        figure = plot_kline(mixed_days)

        candle = figure.data[0]
        volume = figure.data[-1]

        assert candle.increasing.fillcolor == UP_COLOR
        assert candle.decreasing.fillcolor == DOWN_COLOR
        assert list(volume.marker.color) == volume_colors(mixed_days)

    def test_the_technical_chart_volume_matches_too(self, mixed_days):
        figure = plot_technical(mixed_days)

        volume = next(trace for trace in figure.data if trace.name == "成交量")

        assert list(volume.marker.color) == volume_colors(mixed_days)

    def test_no_bar_keeps_the_old_fixed_colour(self, mixed_days):
        figure = plot_kline(mixed_days)

        volume = figure.data[-1]

        assert set(volume.marker.color) <= {UP_COLOR, DOWN_COLOR}
