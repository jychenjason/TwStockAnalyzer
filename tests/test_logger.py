"""Logger tests for TwStockAnalyzer."""

import os
import re
import tempfile

import pytest

from twstock_analyzer.utils.logger import (
    LOG_FORMAT,
    _DatetimeRotatingFileHandler,
    begin_severity_summary,
    finish_severity_summary,
    get_logger,
    log_analysis,
    log_cache,
    log_error,
    log_fetch,
)


class TestSeveritySummary:
    def test_counts_every_standard_level_and_prints_to_stderr(self, tmp_path, capsys):
        logger = get_logger(
            "severity_summary_test", log_file=str(tmp_path / "summary.log"), debug=True
        )
        begin_severity_summary()
        logger.debug("debug")
        logger.info("info one")
        logger.info("info two")
        logger.warning("warning")
        logger.error("error")

        counts = finish_severity_summary()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "DEBUG: 1" in captured.err
        assert "INFO: 2" in captured.err
        assert "WARNING: 1" in captured.err
        assert "ERROR: 1" in captured.err
        assert "CRITICAL: 0" in captured.err
        assert counts == {
            "DEBUG": 1,
            "INFO": 2,
            "WARNING": 1,
            "ERROR": 1,
            "CRITICAL": 0,
        }


class TestLoggerFormat:
    def test_log_format_has_expected_fields(self):
        """LOG_FORMAT should contain all expected fields."""
        assert "%(asctime)s" in LOG_FORMAT
        assert "%(levelname)" in LOG_FORMAT or "%(levelname)-5s" in LOG_FORMAT
        assert "%(module)" in LOG_FORMAT or "%(module)-15s" in LOG_FORMAT
        assert "%(context)" in LOG_FORMAT
        assert "%(status)" in LOG_FORMAT
        assert "%(message)s" in LOG_FORMAT
        assert "%(metrics)s" in LOG_FORMAT

    def test_log_line_matches_pattern(self, tmp_path):
        """A logged line should match the expected format."""
        log_file = str(tmp_path / "test.log")
        logger = get_logger("test_module_pattern", log_file=log_file, debug=True)

        logger.info("Test message", extra={
            'context': 'stock=2330 source=finmind',
            'status': 'START',
        })

        with open(log_file) as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]

        assert len(lines) >= 1
        # Verify the line contains expected components
        assert "Test message" in lines[-1]
        assert "START" in lines[-1]
        assert "2330" in lines[-1]


class TestLoggerRotation:
    def test_rotating_file_handler_configured(self, tmp_path):
        """Logger should use _DatetimeRotatingFileHandler with 5MB max."""
        log_file = str(tmp_path / "rotate_test.log")
        logger = get_logger("rotation_test", log_file=log_file, debug=True)

        # Clear handlers for fresh setup
        logger.handlers.clear()

        logger = get_logger("rotation_test", log_file=log_file, debug=True)

        file_handlers = [
            h for h in logger.handlers
            if isinstance(h, _DatetimeRotatingFileHandler)
        ]
        assert len(file_handlers) >= 1
        handler = file_handlers[0]
        assert handler.maxBytes == 5 * 1024 * 1024  # 5 MB
        assert handler.backupCount == 5


class TestLoggerAutoDir:
    def test_auto_creates_log_directory(self, tmp_path):
        """get_logger should create parent directory of log file."""
        subdir = tmp_path / "nested" / "deep"
        log_file = str(subdir / "app.log")

        logger = get_logger("auto_dir_test", log_file=log_file, debug=True)

        assert subdir.exists()
        assert os.path.isdir(str(subdir))


class TestLoggerConvenience:
    def test_log_fetch(self, tmp_path):
        log_file = str(tmp_path / "fetch.log")
        logger = get_logger("log_fetch_test", log_file=log_file, debug=True)
        logger.handlers.clear()
        logger = get_logger("log_fetch_test", log_file=log_file, debug=True)

        log_fetch(logger, "2330", "finmind", "START", "Fetching data...")
        assert logger.hasHandlers()

    def test_log_cache(self, tmp_path):
        log_file = str(tmp_path / "cache.log")
        logger = get_logger("log_cache_test", log_file=log_file, debug=True)
        logger.handlers.clear()
        logger = get_logger("log_cache_test", log_file=log_file, debug=True)

        log_cache(logger, "2330", "HIT", "Cache hit")
        assert logger.hasHandlers()

    def test_log_analysis(self, tmp_path):
        log_file = str(tmp_path / "analysis.log")
        logger = get_logger("log_analysis_test", log_file=log_file, debug=True)
        logger.handlers.clear()
        logger = get_logger("log_analysis_test", log_file=log_file, debug=True)

        log_analysis(logger, "2330", "SUCCESS", "Analysis complete")
        assert logger.hasHandlers()

    def test_log_error(self, tmp_path):
        log_file = str(tmp_path / "error.log")
        logger = get_logger("log_error_test", log_file=log_file, debug=True)
        logger.handlers.clear()
        logger = get_logger("log_error_test", log_file=log_file, debug=True)

        log_error(logger, "2330", "FAIL", "Something broke")
        assert logger.hasHandlers()


class TestLoggerReentry:
    def test_get_logger_returns_same_instance(self, tmp_path):
        """Calling get_logger twice with same name should return same logger."""
        log_file = str(tmp_path / "reentry.log")
        logger1 = get_logger("reentry_test", log_file=log_file, debug=True)
        logger2 = get_logger("reentry_test", log_file=log_file, debug=True)
        assert logger1 is logger2


class TestConsoleLineFormat:
    """Every message printed to the terminal must carry the LOG_FORMAT columns."""

    LINE_RE = re.compile(
        r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2} \| '   # timestamp
        r'(INFO |WARNING|ERROR) \| '            # level
        r'\S.*?\s* \| '                         # module
        r'\S*\s* \| '                           # status
        r'.*$'                                  # message | context | metrics
    )

    def test_format_console_line_matches_handler_output(self, tmp_path):
        """format_console_line must render the same columns as the handler."""
        import logging

        from twstock_analyzer.utils.logger import format_console_line

        logger = get_logger(
            "console_fmt_test", log_file=str(tmp_path / "c.log"), debug=True
        )
        line = format_console_line(
            logger, logging.INFO, "Updating 368/1089: 2457", "PROGRESS"
        )
        assert self.LINE_RE.match(line), line
        assert "PROGRESS" in line
        assert "console_fmt_test" in line

    def test_clog_console_output_is_formatted_and_not_duplicated(self, tmp_path, capsys):
        """_clog prints exactly one formatted line, not a bare Rich message."""
        from twstock_analyzer.cli.main import _clog

        logger = get_logger(
            "clog_fmt_test", log_file=str(tmp_path / "d.log"), debug=True
        )
        _clog(
            logger,
            "2455 already up to date (no new data)",
            "UP_TO_DATE",
            rich_message="[green]2455 already up to date (no new data since 2026-08-20)[/green]",
        )

        captured = capsys.readouterr()
        lines = [l for l in (captured.out + captured.err).splitlines() if l.strip()]
        assert len(lines) == 1, lines
        assert self.LINE_RE.match(lines[0]), lines[0]
        assert "UP_TO_DATE" in lines[0]
        assert "2026-08-20" in lines[0]

    def test_clog_error_status_renders_error_level(self, tmp_path, capsys):
        from twstock_analyzer.cli.main import _clog

        logger = get_logger(
            "clog_err_test", log_file=str(tmp_path / "e.log"), debug=True
        )
        _clog(logger, "Fetch failed for 9999", "FAIL")

        captured = capsys.readouterr()
        lines = [l for l in (captured.out + captured.err).splitlines() if l.strip()]
        assert len(lines) == 1, lines
        assert "| ERROR |" in lines[0]

    def test_long_message_is_not_wrapped(self, tmp_path, capsys):
        """Rich word-wrap would split the pipe-delimited columns across rows."""
        from twstock_analyzer.cli.main import _clog

        logger = get_logger(
            "clog_wrap_test", log_file=str(tmp_path / "f.log"), debug=True
        )
        _clog(logger, "x" * 300, "INFO")

        captured = capsys.readouterr()
        lines = [l for l in (captured.out + captured.err).splitlines() if l.strip()]
        assert len(lines) == 1, lines

    def test_payload_output_is_not_prefixed(self, tmp_path, capsys):
        """Machine-readable payloads stay pipeable — no LOG_FORMAT prefix."""
        import json

        from twstock_analyzer.cli.main import _print_payload

        logger = get_logger(
            "payload_test", log_file=str(tmp_path / "g.log"), debug=True
        )
        _print_payload(json.dumps({"stock_id": "2330"}), logger, "JSON report for 2330")

        captured = capsys.readouterr()
        assert json.loads(captured.out)["stock_id"] == "2330"
        # The descriptive record still reaches the log file.
        assert "JSON report for 2330" in (tmp_path / "g.log").read_text()
