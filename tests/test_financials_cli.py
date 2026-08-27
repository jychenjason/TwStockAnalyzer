"""`update --type financials` / `--type revenue`，以及 screen 對缺資料的反應。"""

import sqlite3

import pandas as pd
import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables

QUARTERLY = pd.DataFrame({
    "stock_id": ["1101", "2330"],
    "period": ["2026Q2", "2026Q2"],
    "eps": [0.38, 49.33],
    "revenue": [71_289_957.0, 2_404_484_000.0],
    "operating_income": [5_170_177.0, 1_425_569_000.0],
    "net_income": [4_569_799.0, 1_279_582_000.0],
})

MONTHLY = pd.DataFrame({
    "stock_id": ["1101", "2330"],
    "month": ["2026-07", "2026-07"],
    "revenue": [13_744_103.0, 467_580_548.0],
    "revenue_yoy": [1.54, 44.69],
    "revenue_mom": [2.70, 5.62],
    "revenue_ytd": [85_211_435.0, 2_872_064_000.0],
    "revenue_ytd_yoy": [1.54, 37.01],
})


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "cli.db")
    log = str(tmp_path / "cli.log")
    create_tables(path)
    set_default_db_path(path)
    monkeypatch.setattr("twstock_analyzer.cli.main.DEFAULT_DB", path)
    monkeypatch.setattr("twstock_analyzer.utils.logger.LOG_FILE", log)

    upsert("daily_prices", pd.DataFrame({
        "stock_id": ["1101", "2330"], "date": ["2026-08-17", "2026-08-17"],
        "open": [24.0, 1500.0], "high": [24.5, 1510.0], "low": [23.9, 1490.0],
        "close": [24.2, 1505.0], "volume": [1e7, 2e7], "adj_close": [24.2, 1505.0],
        "fetched_at": ["2026-08-17"] * 2,
    }), path)
    return path


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """`--stock all` 會去 TWSE 要股票清單；測試不該碰網路。"""
    monkeypatch.setattr(
        "twstock_analyzer.data.loader.DataLoader.get_stock_list",
        lambda self, logger=None: [{"stock_id": "1101", "name": "台泥"},
                                   {"stock_id": "2330", "name": "台積電"}],
    )


@pytest.fixture()
def offline(monkeypatch):
    """把兩支 TWSE 端點換成固定資料，測試不該碰網路。"""
    monkeypatch.setattr(
        "twstock_analyzer.data.sources.twse.TWSESource.fetch_quarterly_financials",
        lambda self: QUARTERLY.copy(),
    )
    monkeypatch.setattr(
        "twstock_analyzer.data.sources.twse.TWSESource.fetch_monthly_revenue",
        lambda self: MONTHLY.copy(),
    )


class TestUpdateFinancials:
    def test_financials_are_stored(self, runner, db, offline):
        result = runner.invoke(app, ["update", "--stock", "all", "--type", "financials"])

        assert result.exit_code == 0, result.stdout
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT stock_id, period, eps FROM quarterly_financials ORDER BY stock_id").fetchall()
        conn.close()
        assert rows == [("1101", "2026Q2", 0.38), ("2330", "2026Q2", 49.33)]

    def test_monthly_revenue_is_stored(self, runner, db, offline):
        result = runner.invoke(app, ["update", "--stock", "all", "--type", "revenue"])

        assert result.exit_code == 0, result.stdout
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT stock_id, month, revenue_yoy FROM monthly_revenue ORDER BY stock_id").fetchall()
        conn.close()
        assert rows == [("1101", "2026-07", 1.54), ("2330", "2026-07", 44.69)]

    def test_fetched_at_is_recorded(self, runner, db, offline):
        runner.invoke(app, ["update", "--stock", "all", "--type", "financials"])

        conn = sqlite3.connect(db)
        fetched = conn.execute("SELECT fetched_at FROM quarterly_financials LIMIT 1").fetchone()[0]
        conn.close()
        assert fetched, "沒有 fetched_at 就分不出資料是今天抓的還是半年前的"

    def test_a_single_stock_only_stores_that_stock(self, runner, db, offline):
        runner.invoke(app, ["update", "--stock", "1101", "--type", "financials"])

        conn = sqlite3.connect(db)
        ids = [r[0] for r in conn.execute("SELECT stock_id FROM quarterly_financials")]
        conn.close()
        assert ids == ["1101"]

    def test_rerunning_does_not_duplicate(self, runner, db, offline):
        runner.invoke(app, ["update", "--stock", "all", "--type", "financials"])
        runner.invoke(app, ["update", "--stock", "all", "--type", "financials"])

        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM quarterly_financials").fetchone()[0]
        conn.close()
        assert count == 2


class TestScreenOutput:
    def test_eps_and_revenue_yoy_appear_in_the_table(self, runner, db, offline):
        runner.invoke(app, ["update", "--stock", "all", "--type", "financials"])
        runner.invoke(app, ["update", "--stock", "all", "--type", "revenue"])

        result = runner.invoke(app, ["screen", "run"])

        assert result.exit_code == 0, result.stdout
        assert "0.38" in result.stdout, "EPS 應該印出來而不是 '-'"
        assert "44.69" in result.stdout, "營收年增率應該印出來而不是 '-'"

    def test_a_dead_filter_reports_missing_data_instead_of_no_matches(self, runner, db):
        """沒抓過財報時，--eps-min 不該安靜地回「沒有符合的股票」。"""
        result = runner.invoke(app, ["screen", "run", "--eps-min", "1"])

        assert "update" in result.stdout, result.stdout
        assert "No stocks matched" not in result.stdout

    def test_the_missing_data_message_is_not_a_crash(self, runner, db):
        result = runner.invoke(app, ["screen", "run", "--eps-min", "1"])

        assert result.exit_code == 1
        assert "Traceback" not in result.stdout

    def test_the_output_states_which_period_each_column_is_from(self, runner, db, offline):
        """欄位分屬四個不同時間，不標出來就會被讀成同一天的資料。"""
        runner.invoke(app, ["update", "--stock", "all", "--type", "financials"])
        runner.invoke(app, ["update", "--stock", "all", "--type", "revenue"])

        result = runner.invoke(app, ["screen", "run"])

        assert "2026Q2" in result.stdout, "EPS 的季別要標出來"
        assert "2026-07" in result.stdout, "營收年增率的月份要標出來"
        assert "2026-08-17" in result.stdout, "價格的交易日要標出來"

    def test_eps_min_filters(self, runner, db, offline):
        runner.invoke(app, ["update", "--stock", "all", "--type", "financials"])

        result = runner.invoke(app, ["screen", "run", "--eps-min", "10"])

        assert "2330" in result.stdout
        assert "1101" not in result.stdout
