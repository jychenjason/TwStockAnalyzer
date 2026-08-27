"""Screening on moving-average crossovers and RSI.

Both `ma_crossover` and `rsi_min`/`rsi_max` existed as ScreenCriteria fields
that `run_screen` never read — the CLI and the REST API accepted them and
returned a list that looked filtered but was not.  These tests pin the actual
filtering behaviour down.

Fixture series (30 trading days each), with the crossover index computed
independently of the implementation:

  1111 一路上漲   — MA5 above MA10 throughout, no crossing at all
  2222 先跌後漲   — MA5 crosses **up** through MA10 at index 23 (6 days from the end)
  3333 先漲後跌   — MA5 crosses **down** through MA10 at index 24 (5 days from the end)

These cases are about crossover *detection*, so they all pass
``ma_trend_align=False`` (CLI: ``--no-ma-trend-align``) to switch off the
trend-alignment filter that ``screen run`` applies by default.  Alignment has
its own file — tests/test_screening_ma_trend.py.
"""

import pandas as pd
import pytest

from twstock_analyzer.db.repository import upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import ScreenCriteria, run_screen

RISING = [100 + i for i in range(30)]
DIP_THEN_RISE = [100 - i for i in range(20)] + [80 + 3 * i for i in range(1, 11)]
PEAK_THEN_FALL = [100 + 2 * i for i in range(20)] + [140 - 3 * i for i in range(1, 11)]


@pytest.fixture()
def market(tmp_path):
    path = str(tmp_path / "screen.db")
    create_tables(path)

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-07-01", periods=30)]
    for stock_id, closes in (("1111", RISING), ("2222", DIP_THEN_RISE), ("3333", PEAK_THEN_FALL)):
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


class TestGoldenCross:
    def test_finds_the_stock_whose_ma5_crossed_up_through_ma10(self, market):
        result = run_screen(
            ScreenCriteria(ma_crossover="5x10", ma_crossover_within=10, ma_trend_align=False), db_path=market
        )

        assert _matched(result) == {"2222"}

    def test_a_stock_that_never_crossed_is_not_matched(self, market):
        result = run_screen(
            ScreenCriteria(ma_crossover="5x10", ma_crossover_within=30, ma_trend_align=False), db_path=market
        )

        assert "1111" not in _matched(result)

    def test_the_window_excludes_crossings_that_are_too_old(self, market):
        # 2222 crossed 6 trading days before the end.
        recent = run_screen(
            ScreenCriteria(ma_crossover="5x10", ma_crossover_within=3, ma_trend_align=False), db_path=market
        )

        assert "2222" not in _matched(recent)

    def test_a_one_day_window_means_it_crossed_today(self, market):
        result = run_screen(
            ScreenCriteria(ma_crossover="5x10", ma_crossover_within=1, ma_trend_align=False), db_path=market
        )

        assert _matched(result) == set()


class TestDeathCross:
    def test_finds_the_stock_whose_ma5_crossed_down_through_ma10(self, market):
        result = run_screen(
            ScreenCriteria(
                ma_crossover="5x10",
                ma_crossover_direction="down",
                ma_crossover_within=10,
                ma_trend_align=False,
            ),
            db_path=market,
        )

        assert _matched(result) == {"3333"}

    def test_direction_is_not_symmetric(self, market):
        up = run_screen(
            ScreenCriteria(ma_crossover="5x10", ma_crossover_within=10, ma_trend_align=False), db_path=market
        )
        down = run_screen(
            ScreenCriteria(
                ma_crossover="5x10",
                ma_crossover_direction="down",
                ma_crossover_within=10,
                ma_trend_align=False,
            ),
            db_path=market,
        )

        assert _matched(up).isdisjoint(_matched(down))


class TestSpecParsing:
    @pytest.mark.parametrize("spec", ["5x10", "5X10", "5/10", "5-10", "5,10"])
    def test_common_separators_are_accepted(self, market, spec):
        result = run_screen(
            ScreenCriteria(ma_crossover=spec, ma_crossover_within=10, ma_trend_align=False), db_path=market
        )

        assert _matched(result) == {"2222"}

    def test_ma5_against_ma20_is_supported(self, market):
        result = run_screen(
            ScreenCriteria(ma_crossover="5x20", ma_crossover_within=10, ma_trend_align=False), db_path=market
        )

        # 2222's rebound is steep enough to pull MA5 through MA20 as well —
        # MA20 itself is still falling there, which is why this same cross is
        # rejected once trend alignment is on (see test_screening_ma_trend.py).
        assert "2222" in _matched(result)

    def test_a_malformed_spec_is_rejected_loudly(self, market):
        with pytest.raises(ValueError):
            run_screen(ScreenCriteria(ma_crossover="fast-and-slow", ma_trend_align=False), db_path=market)

    def test_a_fast_window_must_be_shorter_than_the_slow_one(self, market):
        with pytest.raises(ValueError):
            run_screen(ScreenCriteria(ma_crossover="20x5", ma_trend_align=False), db_path=market)


class TestNotEnoughHistory:
    def test_a_stock_without_enough_bars_is_skipped_not_crashed(self, tmp_path):
        path = str(tmp_path / "short.db")
        create_tables(path)
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-07-01", periods=6)]
        upsert("daily_prices", pd.DataFrame({
            "stock_id": ["9999"] * 6,
            "date": dates,
            "open": [100.0] * 6,
            "high": [101.0] * 6,
            "low": [99.0] * 6,
            "close": [100.0 + i for i in range(6)],
            "volume": [1_000_000] * 6,
            "adj_close": [100.0 + i for i in range(6)],
            "fetched_at": ["2025-08-15"] * 6,
        }), path)

        result = run_screen(ScreenCriteria(ma_crossover="5x10", ma_trend_align=False), db_path=path)

        assert _matched(result) == set()


class TestRsiIsActuallyApplied:
    def test_an_impossible_rsi_ceiling_matches_nothing(self, market):
        # Every fixture stock ends on a strong move; none can sit below RSI 1.
        result = run_screen(ScreenCriteria(rsi_max=1.0), db_path=market)

        assert _matched(result) == set()

    def test_a_full_range_rsi_filter_matches_everything(self, market):
        result = run_screen(ScreenCriteria(rsi_min=0.0, rsi_max=100.0), db_path=market)

        assert _matched(result) == {"1111", "2222", "3333"}

    def test_the_falling_stock_is_the_weak_one(self, market):
        weak = run_screen(ScreenCriteria(rsi_max=40.0), db_path=market)

        assert "3333" in _matched(weak)
        assert "1111" not in _matched(weak)


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

    def test_a_golden_cross_screen_lists_the_matching_stock(self, cli):
        runner, app = cli

        result = runner.invoke(app, ["screen", "run", "--ma-cross", "5x10", "--ma-within", "10", "--no-ma-trend-align"])

        assert result.exit_code == 0
        assert "2222" in result.stdout
        assert "3333" not in result.stdout

    def test_a_death_cross_screen_lists_the_other_one(self, cli):
        runner, app = cli

        result = runner.invoke(app, [
            "screen", "run", "--ma-cross", "5x10", "--ma-direction", "down", "--ma-within", "10",
            "--no-ma-trend-align",
        ])

        assert result.exit_code == 0
        assert "3333" in result.stdout

    def test_a_malformed_spec_exits_with_an_explanation(self, cli):
        runner, app = cli

        result = runner.invoke(app, ["screen", "run", "--ma-cross", "五日線"])

        assert result.exit_code == 1
        assert "5x10" in result.stdout


class TestThroughTheApi:
    @pytest.fixture()
    def client(self, market, monkeypatch):
        from fastapi.testclient import TestClient

        from twstock_analyzer.api.server import create_app

        monkeypatch.setenv("TWSTOCK_DB", market)
        return TestClient(create_app())

    def test_the_endpoint_screens_on_crossovers(self, client):
        response = client.post("/screen", params={"ma_crossover": "5x10", "ma_within": 10, "ma_trend_align": False})

        assert response.status_code == 200
        assert [r["stock_id"] for r in response.json()["results"]] == ["2222"]

    def test_a_malformed_spec_is_a_client_error(self, client):
        response = client.post("/screen", params={"ma_crossover": "nope"})

        assert response.status_code == 400
