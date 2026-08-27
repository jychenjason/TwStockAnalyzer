"""Stock short names (公司簡稱) stored locally and shown in screening output.

Names were previously fetched live from TWSE during `update full` and thrown
away, so nothing offline could show them — screening results were a wall of
4-digit codes.
"""

import pandas as pd
import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import ScreenCriteria, run_screen
from twstock_analyzer.screening.stock_names import get_stock_name, save_stock_names


@pytest.fixture()
def market(tmp_path):
    path = str(tmp_path / "names.db")
    create_tables(path)
    set_default_db_path(path)

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-07-01", periods=30)]
    for stock_id in ("2330", "2317"):
        closes = [100.0 + i for i in range(30)]
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


class TestStoringNames:
    def test_a_saved_name_can_be_read_back(self, market):
        save_stock_names([{"stock_id": "2330", "name": "台積電"}], db_path=market)

        assert get_stock_name("2330", db_path=market) == "台積電"

    def test_an_unknown_stock_has_no_name(self, market):
        assert get_stock_name("9999", db_path=market) is None

    def test_saving_again_updates_rather_than_duplicates(self, market):
        save_stock_names([{"stock_id": "2330", "name": "台積電"}], db_path=market)
        save_stock_names([{"stock_id": "2330", "name": "台灣積體電路"}], db_path=market)

        assert get_stock_name("2330", db_path=market) == "台灣積體電路"

    def test_entries_without_a_name_are_ignored(self, market):
        saved = save_stock_names([
            {"stock_id": "2330", "name": "台積電"},
            {"stock_id": "2317", "name": ""},
            {"stock_id": "1101"},
        ], db_path=market)

        assert saved == 1
        assert get_stock_name("2317", db_path=market) is None


class TestScreeningOutput:
    def test_results_carry_the_stock_name(self, market):
        save_stock_names([
            {"stock_id": "2330", "name": "台積電"},
            {"stock_id": "2317", "name": "鴻海"},
        ], db_path=market)

        result = run_screen(ScreenCriteria(), db_path=market)

        names = dict(zip(result["stock_id"], result["name"]))
        assert names["2330"] == "台積電"
        assert names["2317"] == "鴻海"

    def test_a_stock_with_no_stored_name_still_appears(self, market):
        save_stock_names([{"stock_id": "2330", "name": "台積電"}], db_path=market)

        result = run_screen(ScreenCriteria(), db_path=market)

        assert set(result["stock_id"]) == {"2330", "2317"}
        assert pd.isna(result.set_index("stock_id").loc["2317", "name"])

    def test_the_name_column_exists_even_with_no_names_stored(self, market):
        result = run_screen(ScreenCriteria(), db_path=market)

        assert "name" in result.columns


class TestThroughTheCli:
    @pytest.fixture()
    def cli(self, market, monkeypatch, tmp_path):
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", market)
        monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "t.log"))
        save_stock_names([
            {"stock_id": "2330", "name": "台積電"},
            {"stock_id": "2317", "name": "鴻海"},
        ], db_path=market)
        return CliRunner()

    def test_screen_results_show_the_name_next_to_the_code(self, cli):
        result = cli.invoke(app, ["screen", "run"])

        assert result.exit_code == 0
        assert "台積電" in result.stdout
        assert "鴻海" in result.stdout

    def test_update_stocks_stores_the_list(self, market, monkeypatch, tmp_path):
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", market)
        monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "t.log"))

        class ListLoader:
            def get_stock_list(self, logger=None):
                return [{"stock_id": "1101", "name": "台泥"}]

        monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: ListLoader())

        result = CliRunner().invoke(app, ["update", "stocks"])

        assert result.exit_code == 0
        assert get_stock_name("1101", db_path=market) == "台泥"

    def test_update_stocks_reports_a_failed_fetch(self, market, monkeypatch, tmp_path):
        monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", market)
        monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", str(tmp_path / "t.log"))

        class EmptyLoader:
            def get_stock_list(self, logger=None):
                return []

        monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: EmptyLoader())

        result = CliRunner().invoke(app, ["update", "stocks"])

        assert result.exit_code == 1
