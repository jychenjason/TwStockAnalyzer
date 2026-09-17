"""Unified logging module for TwStockAnalyzer."""

import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime
from logging.handlers import RotatingFileHandler


# ---------------------------------------------------------------------------
# Custom rotating file handler with datetime backup naming
# ---------------------------------------------------------------------------

class _DatetimeRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that names backups with ``YYYYMMDD-HHMM`` suffix.

    Files roll over at *maxBytes*; old backups beyond *backupCount* are
    pruned automatically.
    """

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        timestamp = datetime.now().strftime('%Y%m%d-%H%M')
        backup_name = f'{self.baseFilename}.{timestamp}'

        if os.path.exists(self.baseFilename):
            self.rotate(self.baseFilename, backup_name)

        # Prune backups beyond backupCount
        if self.backupCount > 0:
            base_dir = os.path.dirname(self.baseFilename)
            base_name = os.path.basename(self.baseFilename)
            pattern = re.compile(
                rf'^{re.escape(base_name)}\.\d{{8}}-\d{{4}}$'
            )
            backups = sorted(
                f for f in os.listdir(base_dir) if pattern.match(f)
            )
            while len(backups) > self.backupCount:
                oldest = os.path.join(base_dir, backups.pop(0))
                os.remove(oldest)

        if not self.delay:
            self.stream = self._open()


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

LOG_FORMAT = (
    '%(asctime)s | %(levelname)-5s | %(module)-15s | '
    '%(status)-8s | %(message)s | %(context)s | %(metrics)s'
)

DATE_FORMAT = '%Y-%m-%d %H:%M'

#: Record attribute marking a record the caller already printed to the terminal
#: itself (e.g. a Rich-coloured variant). The console handler skips those so the
#: same event is not shown twice.
CONSOLE_ECHOED_ATTR = 'console_echoed'

def _build_metrics_string(record: logging.LogRecord) -> str:
    """Build the metrics string from non-standard record attributes."""
    excluded = frozenset((
        'name', 'msg', 'args', 'created', 'filename', 'funcName',
        'levelname', 'levelno', 'lineno', 'module', 'msecs',
        'message', 'pathname', 'relativeCreated', 'exc_info',
        'exc_text', 'stack_info', 'context', 'status', 'metrics',
        'getMessage', 'thread', 'threadName', 'processName',
        'process', 'taskName', 'asctime', CONSOLE_ECHOED_ATTR,
        # loguru > logging forwarding (see FinMind source) stashes the
        # original loguru extras under this key; it is bookkeeping only.
        'extra',
    ))
    parts = []
    for k, v in record.__dict__.items():
        if k in excluded:
            continue
        parts.append(f'{k}={v}')
    return ' '.join(parts)


class _Formatter(logging.Formatter):
    """Formatter that computes *metrics* dynamically from extras."""

    def format(self, record: logging.LogRecord) -> str:
        record.__dict__.setdefault('context', '')
        record.__dict__.setdefault('status', '')
        record.metrics = _build_metrics_string(record)
        record.__dict__['module'] = record.name
        return super().format(record)


# ---------------------------------------------------------------------------
# Default log file path
# ---------------------------------------------------------------------------

LOG_FILE: str = 'data/twstock.log'


class _SeverityCounter(logging.Handler):
    """Process-local counter used for one CLI invocation at a time."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.counts: Counter[str] = Counter()
        self.active = False

    def emit(self, record: logging.LogRecord) -> None:
        if self.active:
            self.counts[record.levelname] += 1


_SEVERITY_COUNTER = _SeverityCounter()
_SUMMARY_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _attach_severity_counter(logger: logging.Logger) -> None:
    if _SEVERITY_COUNTER not in logger.handlers:
        logger.addHandler(_SEVERITY_COUNTER)


def begin_severity_summary() -> None:
    """Reset and start counting records for the current CLI invocation."""
    _SEVERITY_COUNTER.counts.clear()
    _SEVERITY_COUNTER.active = True
    _attach_severity_counter(logging.getLogger())
    # Tests and embedded callers can reuse logger instances across invocations.
    for candidate in logging.Logger.manager.loggerDict.values():
        if isinstance(candidate, logging.Logger):
            _attach_severity_counter(candidate)


def finish_severity_summary() -> dict[str, int]:
    """Stop counting, print the standard-level totals to stderr, and return them."""
    _SEVERITY_COUNTER.active = False
    counts = {level: _SEVERITY_COUNTER.counts[level] for level in _SUMMARY_LEVELS}
    rendered = " | ".join(f"{level}: {count}" for level, count in counts.items())
    print(f"Log severity summary | {rendered}", file=sys.stderr)
    return counts

#: Formatter used by :func:`format_console_line`; identical to the one the
#: handlers get, so a hand-printed line and a handler-printed line line up.
_CONSOLE_LINE_FORMATTER = _Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _setup_root_handler(path: str, formatter: _Formatter, debug: bool) -> None:
    """Add a file handler to the root logger so module-level loggers
    (e.g. ``twstock_analyzer.data.sources.twse``) also write to the log file."""
    root = logging.getLogger()
    _attach_severity_counter(root)
    if any(isinstance(h, _DatetimeRotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(logging.DEBUG)
    try:
        handler = _DatetimeRotatingFileHandler(
            filename=path,
            encoding='utf-8',
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
        )
    except OSError:
        # Cannot open the log file (e.g. permission denied) — skip the
        # shared file handler; module loggers still reach the console.
        return
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(formatter)
    root.addHandler(handler)


class _SkipRenderedTables(logging.Filter):
    """讓終端機 handler 略過已渲染的表格。

    表格會被寫進 log（status=TABLE），好讓 log 檔保留使用者看到的內容；但畫面上
    那份是 Rich 印的，若終端機 handler 再吐一次，同一張表就會出現兩遍——一份在
    stdout、一份在 stderr。檔案 handler 不套用這個過濾器，log 檔仍然完整。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "status", "") != "TABLE"


class _SkipConsoleEchoed(logging.Filter):
    """讓終端機 handler 略過呼叫端已經自行印過的紀錄。

    ``_clog`` 之類的輔助函式會把同一則訊息用 Rich 上色後印到 stdout；那份輸出
    已經套用 :data:`LOG_FORMAT`（見 :func:`format_console_line`），所以終端機
    handler 不能再吐一次，否則同一件事會出現兩遍。檔案 handler 不套用這個過濾
    器，log 檔仍然完整。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, CONSOLE_ECHOED_ATTR, False)


def format_console_line(
    logger: logging.Logger,
    level: int,
    message: str,
    status: str = '',
    context: str = '',
    **metrics,
) -> str:
    """Render *message* through :data:`LOG_FORMAT` without emitting it.

    Callers that print to the terminal themselves (to add Rich colour, or to
    show a longer variant of the message) use this so their line carries the
    same ``timestamp | level | module | status | ...`` columns as every line the
    handlers write. Pair it with ``extra={CONSOLE_ECHOED_ATTR: True}`` on the
    matching log call so the console handler does not print the event twice.
    """
    record = logger.makeRecord(
        logger.name, level, '(unknown file)', 0, message, (), None,
    )
    record.__dict__.update(
        {'context': context, 'status': status, **metrics},
    )
    return _CONSOLE_LINE_FORMATTER.format(record)


def get_logger(
    name: str,
    log_file: str | None = None,
    debug: bool = False,
) -> logging.Logger:
    """Return a configured *TwStockAnalyzer* logger.

    Parameters
    ----------
    name : str
        Logger name (typically ``__name__``).
    log_file : str | None
        Path to the rotating log file. Defaults to *LOG_FILE*.
    debug : bool
        When *True* the file handler is set to DEBUG; otherwise INFO.

    Auto-creates the parent directory of *log_file* if it does not exist.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # let handlers decide
    logger.propagate = False
    _attach_severity_counter(logger)

    # Avoid adding duplicate output handlers on repeated calls.  The severity
    # counter is bookkeeping, not evidence that console/file setup is done.
    if any(handler is not _SEVERITY_COUNTER for handler in logger.handlers):
        return logger

    formatter = _Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # --- Console handler (INFO+) -----------------------------------------
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    console.addFilter(_SkipRenderedTables())
    console.addFilter(_SkipConsoleEchoed())
    logger.addHandler(console)

    # --- Rotating file handler (INFO+) -----------------------------------
    # If the log file/directory is not writable (e.g. permission denied),
    # fall back to console-only logging with a warning instead of crashing.
    path = log_file if log_file is not None else LOG_FILE
    try:
        log_dir = os.path.dirname(path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # Write column header as the first line of a new log file
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(
                    '# Fields: timestamp(YYYY-MM-DD HH:MM) | level | module | '
                    'status | message | context | metrics\n'
                )

        file_handler = _DatetimeRotatingFileHandler(
            filename=path,
            encoding='utf-8',
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
        )
        file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Also capture module-level loggers (twse.py etc.) via the root logger
        _setup_root_handler(path, formatter, debug)
    except OSError as exc:
        logger.warning(
            'Could not open log file %s (%s); logging to console only.',
            path, exc, extra={'context': '', 'status': 'WARN'},
        )

    return logger


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def log_fetch(
    logger: logging.Logger,
    stock_id: str,
    source: str,
    status: str,
    message: str,
    **metrics,
) -> None:
    """Log a data-fetch event."""
    ctx = f'stock={stock_id} source={source}'
    logger.info(message, extra={'context': ctx, 'status': status, **metrics})


def log_cache(
    logger: logging.Logger,
    stock_id: str,
    status: str,
    message: str,
    **metrics,
) -> None:
    """Log a cache event."""
    ctx = f'stock={stock_id} cache={status.lower()}'
    logger.info(message, extra={'context': ctx, 'status': status, **metrics})


def log_analysis(
    logger: logging.Logger,
    stock_id: str,
    status: str,
    message: str,
    **metrics,
) -> None:
    """Log an analysis event."""
    ctx = f'stock={stock_id} analysis={status.lower()}'
    logger.info(message, extra={'context': ctx, 'status': status, **metrics})


def log_error(
    logger: logging.Logger,
    stock_id: str,
    status: str,
    message: str,
    **metrics,
) -> None:
    """Log an error event."""
    ctx = f'stock={stock_id}'
    logger.error(message, extra={'context': ctx, 'status': status, **metrics})
