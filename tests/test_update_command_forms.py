"""README 與錯誤訊息裡的指令形式必須真的存在。

`update existing` / `update full` 從專案早期就寫在文件的十幾個地方，`screen run`
的修正建議也是這樣寫的——但 CLI 只認得 `update --stock existing`，照著文件打
會拿到 `No such command 'existing'`。錯誤訊息叫使用者去跑一個不存在的指令，比
不給建議更糟。這裡把兩種寫法都釘住。
"""

from __future__ import annotations

import pandas as pd
import pytest
from typer.testing import CliRunner

from twstock_analyzer.cli.main import app
from twstock_analyzer.db.repository import set_default_db_path, upsert
from twstock_analyzer.db.schema import create_tables


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


class StubLoader:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def get_data(self, stock_id, data_type, start_date, end_date=None, logger=None):
        self.calls.append((stock_id, data_type))
        return pd.DataFrame({
            "date": [start_date],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1_000_000],
        })

    def get_stock_list(self, logger=None):
        return [{"stock_id": "2330", "name": "台積電"}]


@pytest.fixture()
def loader(monkeypatch):
    stub = StubLoader()
    monkeypatch.setattr("twstock_analyzer.cli.main._make_loader", lambda: stub)
    return stub


def _seed(db_path: str, stock_id: str = "2330") -> None:
    upsert(
        "daily_prices",
        pd.DataFrame({
            "stock_id": [stock_id],
            "date": ["2025-07-02"],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "volume": [1_000_000],
            "adj_close": [101.0],
            "fetched_at": ["2025-07-02"],
        }),
        db_path,
    )


class TestDocumentedCommandForms:
    def test_update_existing_is_a_real_command(self, runner, loader, _isolated_db):
        _seed(_isolated_db)

        result = runner.invoke(app, ["update", "existing"])

        assert result.exit_code == 0
        assert "Updated 1/1 stocks" in result.stdout
        assert "Log severity summary" in result.stderr
        assert "INFO:" in result.stderr
        assert "WARNING:" in result.stderr
        assert "ERROR:" in result.stderr

    def test_update_existing_on_an_empty_database_says_so(self, runner, loader):
        result = runner.invoke(app, ["update", "existing"])

        assert result.exit_code == 0
        assert "No stocks in database" in result.stdout

    def test_update_full_is_a_real_command(self, runner, loader):
        result = runner.invoke(app, ["update", "full"])

        assert result.exit_code == 0
        assert "Fetched 1 stocks from TWSE" in result.stdout
        assert loader.calls == [("2330", "daily")]

    def test_the_subcommands_take_the_same_options(self, runner, loader, _isolated_db):
        _seed(_isolated_db)

        result = runner.invoke(app, ["update", "existing", "--force", "--start", "2024-01-01"])

        assert result.exit_code == 0
        assert loader.calls == [("2330", "daily")]

    def test_the_option_form_still_works(self, runner, loader, _isolated_db):
        _seed(_isolated_db)

        result = runner.invoke(app, ["update", "--stock", "existing"])

        assert result.exit_code == 0
        assert "Updated 1/1 stocks" in result.stdout


class TestAdviceNamesAWorkingCommand:
    """`screen run` 在資料落後時建議的指令，必須真的跑得起來。"""

    def test_the_command_the_freshness_warning_names_exists(self, runner):
        assert runner.invoke(app, ["update", "existing", "--help"]).exit_code == 0
