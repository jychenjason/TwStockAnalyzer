"""Rendered tables appear once on screen and once in the log file.

`_print_table` prints the table with Rich *and* writes the rendered text to the
log so the log file captures everything the user saw.  The logger's console
handler writes to stderr, so it echoed that same text straight back to the
terminal — every table showed up twice.

The assertions look at each sink directly (Rich's console, the logger's console
handler, the log file) rather than at pytest's captured output, so they测的是
真正送到各個出口的內容。
"""

import io
import logging

import pytest
from rich.console import Console
from rich.table import Table

from twstock_analyzer.cli import main as cli_main
from twstock_analyzer.utils.logger import get_logger

#: 放在資料格裡的唯一標記。標題會被 Rich 依表格寬度折行，不適合當比對對象。
MARKER = "ZZUNIQUEZZ"


@pytest.fixture()
def table():
    table = Table(title="測試表格")
    table.add_column("代號")
    table.add_row(MARKER)
    return table


@pytest.fixture()
def rich_output(monkeypatch):
    """Rich 的輸出改導到記憶體，方便計算表格出現幾次。"""
    stream = io.StringIO()
    monkeypatch.setattr(cli_main, "console", Console(file=stream, width=200))
    return stream


@pytest.fixture()
def logger(tmp_path, request):
    return get_logger(
        f"test.table.{request.node.name}", log_file=str(tmp_path / "t.log"), debug=True
    )


def _console_stream(logger) -> io.StringIO:
    """把 logger 的終端機 handler 導到記憶體，並回傳該串流。"""
    handler = next(
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    )
    stream = io.StringIO()
    handler.stream = stream
    return stream


def test_rich_prints_the_table_exactly_once(table, logger, rich_output):
    _print_table = cli_main._print_table

    _print_table(table, logger)

    assert rich_output.getvalue().count(MARKER) == 1


def test_the_log_console_handler_does_not_echo_the_table(table, logger, rich_output):
    echoed = _console_stream(logger)

    cli_main._print_table(table, logger)

    assert MARKER not in echoed.getvalue(), "表格已由 Rich 印出，log 的終端機 handler 不該再吐一份"


def test_the_log_file_still_records_the_table(tmp_path, table, request, rich_output):
    log_file = tmp_path / "t.log"
    logger = get_logger(f"test.table.file.{request.node.name}", log_file=str(log_file), debug=True)

    cli_main._print_table(table, logger)

    assert MARKER in log_file.read_text(), "log 檔仍必須保留使用者看到的內容"


def test_ordinary_messages_are_still_echoed(logger):
    echoed = _console_stream(logger)

    logger.info("plain message", extra={"context": "", "status": "SUCCESS"})

    assert "plain message" in echoed.getvalue(), "一般訊息本來就該顯示在終端機"
