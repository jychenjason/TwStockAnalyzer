"""Taiwan Stock Analyzer CLI - Typer-based command line interface.

Commands:
    update      Fetch and cache stock data from online sources.
    analyze     Run technical/fundamental/institutional analysis on a stock.
    report      Generate analysis report (HTML/JSON).
    list        List stocks with basic info.

Stock ID validation: must be exactly 4 digits.
"""

from __future__ import annotations

import io
import json
import logging
import re as _re
from datetime import datetime, timedelta
from typing import Annotated, Optional

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from twstock_analyzer.analysis.fundamental import FundamentalAnalyzer
from twstock_analyzer.analysis.technical import TechnicalAnalyzer
from twstock_analyzer.backtesting.engine import run_backtest
from twstock_analyzer.backtesting.portfolio import PortfolioConfig
from twstock_analyzer.data.availability import (
    available_trading_date,
    forget_non_trading_day,
    non_trading_days,
    record_non_trading_day,
)
from twstock_analyzer.data.loader import DataLoader, validate_stock_id, validate_date
from twstock_analyzer.data.sources.finmind import FinMindSource
from twstock_analyzer.data.sources.twse import TWSESource
from twstock_analyzer.data.sources.yahoo import YahooSource
from twstock_analyzer.db.repository import has_data, upsert, get_table_columns
from twstock_analyzer.db.schema import create_tables
from twstock_analyzer.screening.screener import (
    MA_TREND_WINDOW,
    SPIKE_DIRECTION_BAND,
    VOLUME_SPIKE_WINDOW,
    MissingDataError,
    ScreenCriteria,
    run_screen,
)
from twstock_analyzer.screening.watchlist import create_watchlist, list_watchlists, delete_watchlist, get_watchlist
from twstock_analyzer.utils.logger import (
    CONSOLE_ECHOED_ATTR,
    begin_severity_summary,
    finish_severity_summary,
    format_console_line,
    get_logger,
)
from twstock_analyzer.visualization.report import generate_report

app = typer.Typer(
    help="Taiwan Stock Analyzer - Personal research tool",
    add_completion=False,
    invoke_without_command=True,
)
console = Console()


ERROR_STATUSES = frozenset({"FAIL", "NO_DATA", "NO_MATCH", "ERROR"})
WARN_STATUSES = frozenset({"WARN", "NOT_FOUND", "INVALID", "NO_STOCKS", "EMPTY"})


def _status_level(status: str) -> int:
    """Map a status tag to the logging level it should be emitted at.

    - FAIL / NO_DATA / NO_MATCH / ERROR  → ERROR
    - WARN / NOT_FOUND / INVALID / NO_STOCKS / EMPTY → WARNING
    - everything else                   → INFO
    """
    if status in ERROR_STATUSES:
        return logging.ERROR
    if status in WARN_STATUSES:
        return logging.WARNING
    return logging.INFO


def _clog(logger, message: str, status: str = "INFO", rich_message: str | None = None, **extra):
    """Print to terminal (with Rich markup) AND write to log file.

    The terminal line is rendered through the same ``LOG_FORMAT`` as every
    other line, so a Rich-coloured message still reads as
    ``timestamp | level | module | status | message | context | metrics``.
    The record is marked *console_echoed* so the logger's own console handler
    does not print the event a second time in plain text.

    Parameters
    ----------
    logger : logging.Logger
        Logger instance for file output.
    message : str
        Plain-text message (Rich markup stripped automatically).
    status : str
        Status tag (SUCCESS, FAIL, CACHE_HIT, etc).
    rich_message : str | None
        Optional Rich-formatted string for terminal. Falls back to *message*.
    """
    level = _status_level(status)
    terminal_msg = rich_message if rich_message is not None else message
    console.print(
        format_console_line(logger, level, terminal_msg, status, **extra),
        # soft_wrap keeps the record on one line: Rich's word-wrap would break
        # the pipe-delimited columns across rows, and highlight would recolour
        # the timestamp and numbers inside the prefix.
        highlight=False,
        soft_wrap=True,
    )
    clean_msg = _re.sub(r'\[/?\w+\]', '', message)
    _log_level(logger, clean_msg, status, **{CONSOLE_ECHOED_ATTR: True, **extra})


def _log_level(logger, message: str, status: str, **extra):
    """Dispatch to logger with level matching *status* (see _status_level)."""
    logger.log(
        _status_level(status),
        message,
        extra={'context': '', 'status': status, **extra},
    )

def _print_payload(payload: str, logger, description: str, status: str = "SUCCESS") -> None:
    """Write a machine-readable payload to stdout verbatim.

    ``report --format json --output -`` is meant to be piped into ``jq`` and the
    like, so the payload must be the *only* thing on stdout — no LOG_FORMAT
    prefix, no Rich markup interpretation, no word wrapping. The log file gets a
    one-line *description* of the event instead.
    """
    console.print(payload, markup=False, highlight=False, soft_wrap=True)
    _log_level(logger, description, status, **{CONSOLE_ECHOED_ATTR: True})


def _print_table(table: Table, logger) -> None:
    """Print a Rich table to the terminal AND record its rendered text in the log.

    The terminal gets the styled table; the log gets a plain-text render so
    everything shown to the user is also captured in the log file.
    """
    console.print(table)
    plain = Console(file=io.StringIO(), width=200)
    plain.print(table)
    rendered = plain.file.getvalue().rstrip()
    if rendered:
        _log_level(logger, "\n" + rendered, "TABLE")


DEFAULT_DB = "data/twstock.db"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_loader() -> DataLoader:
    """Return a DataLoader with all configured sources.

    Priority order: TWSE → FinMind → Yahoo.
    TWSE is always available (free, no token).  FinMind requires
    ``FINMIND_API_TOKEN``.  Yahoo requires ``yfinance`` installed.
    """
    sources = [TWSESource()]  # always available
    if not FinMindSource.should_skip():
        sources.append(FinMindSource())
    if not YahooSource.should_skip():
        sources.append(YahooSource())
    return DataLoader(sources=sources)


def _build_price_table(df: pd.DataFrame, title: str) -> Table:
    """Build a Rich table from a price/indicator DataFrame."""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    numeric_cols = [c for c in df.columns if c != "date"]
    table.add_column("date", justify="left", style="white")
    for col in numeric_cols:
        style = None
        if col == "close":
            style = "bold green"
        elif col.startswith("macd"):
            style = "cyan"
        elif col.startswith("rsi"):
            style = "yellow"
        table.add_column(col, justify="right", style=style)
    for _, row in df.tail(20).iterrows():
        row_date = str(row.get("date", "-"))
        values = [row_date]
        for col in numeric_cols:
            val = row.get(col)
            if isinstance(val, float):
                values.append(f"{val:.2f}" if val == val else "-")  # NaN check
            else:
                values.append(str(val) if val is not None else "-")
        table.add_row(*values)
    return table


def _log_action(logger, message: str, status: str, **extra):
    """Log with required context/status fields."""
    logger.info(message, extra={'context': '', 'status': status, **extra})


_VALID_DATA_TYPES = (
    "daily", "fundamental", "financials", "revenue", "institutional", "dividend",
)
_BATCH_KEYWORDS = ("all", "existing")


def _validate_data_type(
    ctx: typer.Context, param: typer.models.OptionInfo, value: str
) -> str:
    matched = [t for t in _VALID_DATA_TYPES if t.startswith(value)]
    if len(matched) == 1:
        return matched[0]
    if len(matched) > 1:
        raise typer.BadParameter(
            f"Ambiguous data type '{value}'. Matches: {', '.join(matched)}"
        )
    raise typer.BadParameter(
        f"Invalid data type '{value}'. Must be one of: {', '.join(_VALID_DATA_TYPES)}"
    )


def _format_stock_id(ctx: typer.Context, param: typer.models.OptionInfo, value: str | None) -> str | None:
    """Typer callback to validate stock ID before command body runs."""
    if value is None or value in _BATCH_KEYWORDS:
        return value
    try:
        validate_stock_id(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return value


# ------------------------------------------------------------------
# Replay App (read-only)
# ------------------------------------------------------------------

replay_app = typer.Typer(help="Review Replay Sessions (read-only)")


def _load_replay_session(session_id: int):
    """Load a session or exit with a clear message."""
    from twstock_analyzer.replay import load_session

    logger = get_logger("cli.replay", debug=True)
    try:
        return load_session(session_id, db_path=DEFAULT_DB)
    except Exception:
        _clog(
            logger,
            f"Replay session {session_id} not found",
            "NOT_FOUND",
            rich_message=f"[red]找不到 Replay Session {session_id}[/red]",
        )
        raise typer.Exit(code=1)


@replay_app.command(name="list")
def replay_list():
    """List every Replay Session, newest first."""
    from twstock_analyzer.replay import list_sessions

    logger = get_logger("cli.replay", debug=True)
    sessions = list_sessions(db_path=DEFAULT_DB)
    if not sessions:
        _clog(logger, "No replay sessions yet", "EMPTY", rich_message="[yellow]尚無任何回放紀錄。請在 Streamlit 的「回放練習」頁面建立。[/yellow]")
        return

    table = Table(title="Replay Sessions")
    for column in ("ID", "股票", "起始日", "結束日", "目前日期", "名稱"):
        table.add_column(column)
    for session in sessions:
        table.add_row(
            str(session["id"]),
            session["stock_id"],
            session["start_date"],
            session["end_date"] or "-",
            session["cursor"],
            session["name"] or "-",
        )
    _print_table(table, logger)


@replay_app.command(name="show")
def replay_show(session_id: int = typer.Argument(..., help="Replay session ID")):
    """Show one session's performance summary against Buy & Hold."""
    logger = get_logger("cli.replay", debug=True)
    session = _load_replay_session(session_id)
    summary = session.summary()

    table = Table(title=f"Replay #{session.id} — {session.stock_id} ({session.start_date} → {session.cursor})")
    table.add_column("項目")
    table.add_column("數值", justify="right")
    table.add_row("最終權益", f"{summary['final_equity']:,.0f}")
    table.add_row("總報酬", f"{summary['total_return'] * 100:.2f}%")
    table.add_row("已實現損益", f"{summary['realized_pnl']:,.0f}")
    table.add_row("未實現損益", f"{summary['unrealized_pnl']:,.0f}")
    table.add_row("交易次數", str(summary["trades"]))
    table.add_row("勝率", "-" if summary["win_rate"] is None else f"{summary['win_rate'] * 100:.1f}%")
    table.add_row("最大回撤", f"{summary['max_drawdown'] * 100:.2f}%")
    table.add_row("Buy & Hold 報酬", f"{summary['buy_hold_return'] * 100:.2f}%")
    _print_table(table, logger)


@replay_app.command(name="export")
def replay_export(
    session_id: int = typer.Argument(..., help="Replay session ID"),
    output: str = typer.Option(..., "--output", "-o", help="Destination CSV path"),
):
    """Export a session's trade detail as CSV."""
    logger = get_logger("cli.replay", debug=True)
    session = _load_replay_session(session_id)

    orders = session.orders()
    columns = [
        "id", "placed_on", "filled_on", "side", "lots", "shares",
        "fill_price", "amount", "fee", "tax", "status", "void_reason",
    ]
    frame = pd.DataFrame(orders, columns=columns) if orders else pd.DataFrame(columns=columns)
    frame.to_csv(output, index=False)
    _clog(logger, f"Exported {len(frame)} orders to {output}", "SUCCESS", rich_message=f"[green]已匯出 {len(frame)} 筆委託到 {output}[/green]")


# ------------------------------------------------------------------
# Backtest App
# ------------------------------------------------------------------

backtest_app = typer.Typer(help="Run and manage portfolio backtests")


@backtest_app.command(name="run")
def backtest_run(
    stocks: str = typer.Option(..., "--stocks", "-s", help="Comma-separated stock IDs"),
    capital: float = typer.Option(1000000.0, "--capital", "-c", help="Initial capital"),
    signal: str = typer.Option("ma_cross", "--signal", help="Signal method: ma_cross, rsi, macd"),
    start: str = typer.Option("2024-01-01", "--start", help="Start date YYYY-MM-DD"),
    end: str = typer.Option("", "--end", help="End date YYYY-MM-DD"),
    save: bool = typer.Option(False, "--save", help="Save result to DB"),
):
    """Run a portfolio backtest simulation."""
    logger = get_logger("cli.backtest.run", debug=True)
    _log_action(logger, f"stocks={stocks}, signal={signal}", "START")

    stock_ids = [s.strip() for s in stocks.split(",") if s.strip()]
    for sid in stock_ids:
        try:
            validate_stock_id(sid)
        except ValueError as exc:
            _clog(logger, f"Invalid stock ID '{sid}': {exc}", "INVALID", rich_message=f"[red]Invalid stock ID '{sid}': {exc}[/red]")
            raise typer.Exit(code=1)

    end = end or datetime.now().strftime("%Y-%m-%d")

    config = PortfolioConfig(initial_capital=capital)
    result = run_backtest(
        stock_ids=stock_ids,
        config=config,
        start_date=start,
        end_date=end,
        signal_method=signal,
        db_path=DEFAULT_DB,
        logger=logger,
    )

    if result.metrics.total_return == 0 and result.equity_curve.empty:
        _clog(logger, "No data available for backtest.", "NO_DATA", rich_message="[yellow]No data available for backtest. Ensure stocks have data in the date range.[/yellow]")
        return

    # Display metrics
    m = result.metrics
    table = Table(title="📈 Backtest Results", show_header=True, header_style="bold green")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Total Return", f"{m.total_return:.2%}")
    table.add_row("CAGR", f"{m.cagr:.2%}")
    table.add_row("Volatility", f"{m.volatility:.2%}")
    table.add_row("Sharpe Ratio", f"{m.sharpe_ratio:.2f}")
    table.add_row("Max Drawdown", f"{m.max_drawdown:.2%}")
    table.add_row("Win Rate", f"{m.win_rate:.2%}")
    table.add_row("Total Trades", str(m.total_trades))
    _print_table(table, logger)
    _clog(logger, f"Equity curve: {len(result.equity_curve)} data points", "SUCCESS", rich_message=f"[dim]Equity curve: {len(result.equity_curve)} data points[/dim]")

    _log_action(logger, "Backtest complete", "SUCCESS")

    if save:
        import sqlite3
        conn = sqlite3.connect(DEFAULT_DB)
        try:
            config_json = json.dumps({"stocks": stock_ids, "capital": capital, "signal": signal, "start": start, "end": end})
            result_json = json.dumps({"total_return": m.total_return, "cagr": m.cagr, "volatility": m.volatility, "sharpe_ratio": m.sharpe_ratio, "max_drawdown": m.max_drawdown, "win_rate": m.win_rate, "total_trades": m.total_trades})
            cur = conn.execute(
                "INSERT INTO backtest_results (name, config_json, result_json, metrics_json) VALUES (?, ?, ?, ?)",
                (f"{','.join(stock_ids)} {signal}", config_json, result_json, result_json),
            )
            result_id = cur.lastrowid
            # Save equity curve
            for idx, equity_val in result.equity_curve.items():
                date_str = str(idx)[:10] if hasattr(idx, '__str__') else str(idx)
                conn.execute(
                    "INSERT INTO backtest_equity_curves (result_id, date, equity) VALUES (?, ?, ?)",
                    (result_id, date_str, float(equity_val)),
                )
            conn.commit()
            _clog(logger, f"Backtest saved as result #{result_id}", "SUCCESS", rich_message=f"[green]Backtest saved as result #{result_id}[/green]")
        finally:
            conn.close()


@backtest_app.command(name="list")
def backtest_list():
    """List saved backtest results."""
    import sqlite3
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        rows = conn.execute(
            "SELECT id, name, metrics_json, created_at FROM backtest_results ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        logger = get_logger("cli.backtest.list")
        _clog(logger, "No saved backtest results.", "EMPTY", rich_message="[yellow]No saved backtest results.[/yellow]")
        return

    table = Table(title="📋 Saved Backtests", show_header=True, header_style="bold cyan")
    table.add_column("ID", justify="right", style="yellow")
    table.add_column("Name", justify="left")
    table.add_column("Return", justify="right")
    table.add_column("Sharpe", justify="right")
    table.add_column("Drawdown", justify="right")
    table.add_column("Created", justify="left")

    for row in rows:
        try:
            m = json.loads(row["metrics_json"])
            ret = f"{m.get('total_return', 0):.2%}"
            sharpe = f"{m.get('sharpe_ratio', 0):.2f}"
            dd = f"{m.get('max_drawdown', 0):.2%}"
        except (json.JSONDecodeError, TypeError):
            ret = sharpe = dd = "-"
        table.add_row(str(row["id"]), row["name"], ret, sharpe, dd, row["created_at"])

    _print_table(table, get_logger("cli.backtest.list"))


@backtest_app.command(name="show")
def backtest_show(
    result_id: int = typer.Argument(..., help="Backtest result ID"),
):
    """Show saved backtest details."""
    import sqlite3
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        row = conn.execute(
            "SELECT id, name, config_json, metrics_json, created_at FROM backtest_results WHERE id = ?",
            (result_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        _clog(get_logger("cli.backtest.show"), f"Backtest #{result_id} not found", "NOT_FOUND", rich_message=f"[red]Backtest #{result_id} not found[/red]")
        return

    try:
        m = json.loads(row["metrics_json"])
    except json.JSONDecodeError:
        m = {}

    table = Table(title=f"📈 Backtest #{row['id']}: {row['name']}", show_header=True, header_style="bold green")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("Total Return", f"{m.get('total_return', 0):.2%}")
    table.add_row("CAGR", f"{m.get('cagr', 0):.2%}")
    table.add_row("Volatility", f"{m.get('volatility', 0):.2%}")
    table.add_row("Sharpe Ratio", f"{m.get('sharpe_ratio', 0):.2f}")
    table.add_row("Max Drawdown", f"{m.get('max_drawdown', 0):.2%}")
    table.add_row("Win Rate", f"{m.get('win_rate', 0):.2%}")
    table.add_row("Created", row["created_at"])
    _print_table(table, get_logger("cli.backtest.show"))

    # Show config
    try:
        cfg = json.loads(row["config_json"])
        _clog(get_logger("cli.backtest.show"), f"Config: stocks={cfg.get('stocks')}, capital={cfg.get('capital')}, signal={cfg.get('signal')}", "INFO", rich_message=f"\n[bold]Config:[/bold] stocks={cfg.get('stocks')}, capital={cfg.get('capital')}, signal={cfg.get('signal')}")
    except (json.JSONDecodeError, TypeError):
        pass


@backtest_app.command(name="delete")
def backtest_delete(
    result_id: int = typer.Argument(..., help="Backtest result ID to delete"),
):
    """Delete a saved backtest result."""
    import sqlite3
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        conn.execute("DELETE FROM backtest_equity_curves WHERE result_id = ?", (result_id,))
        cur = conn.execute("DELETE FROM backtest_results WHERE id = ?", (result_id,))
        conn.commit()
        if cur.rowcount > 0:
            _clog(get_logger("cli.backtest.delete"), f"Backtest #{result_id} deleted", "DELETED", rich_message=f"[green]Backtest #{result_id} deleted[/green]")
        else:
            _clog(get_logger("cli.backtest.delete"), f"Backtest #{result_id} not found", "NOT_FOUND", rich_message=f"[red]Backtest #{result_id} not found[/red]")
    finally:
        conn.close()


# ------------------------------------------------------------------
# Screen App
# ------------------------------------------------------------------

screen_app = typer.Typer(help="Screen stocks by fundamental/technical criteria")


#: 低於這個 20 日均量（股）的爆量在實務上沒有意義——「均量 6 張、當日 37 張」
#: 也是 6 倍，但那是 37 張。
QUIET_VOLUME = 500_000


def _illiquid_spike_hint(df: pd.DataFrame) -> str | None:
    """爆量清單裡有多少檔其實根本沒量。

    實測全市場：2 倍以上的 75 檔中有 35 檔均量不到 500 張，倍數榜前排幾乎都是
    這種雜訊。這裡只提醒不過濾——偷偷濾掉才是更糟的失敗方式。
    """
    if "avg_volume" not in df.columns:
        return None
    quiet = int((df["avg_volume"].fillna(0) < QUIET_VOLUME).sum())
    if not quiet:
        return None
    return (
        f"注意：其中 {quiet} 檔的 20 日均量不到 {QUIET_VOLUME // 1000} 張，"
        f"倍數再高也只是幾十張的成交。要排除請加上 --volume-min {QUIET_VOLUME}"
    )


def _warn_about_stale_stocks(logger) -> None:
    """講出哪些股票沒參與當日爆量比對，以及那是為什麼。

    分兩種講：「update 沒跑完」是使用者可以馬上修好的，而且會讓篩選結果少掉
    一大塊市場；「停止交易」則是市場常態，講一聲就好。混在一起會讓前者被讀成
    「這些股票有問題」，而真正的問題——結果不完整——就被漏掉了。
    """
    import sqlite3

    from twstock_analyzer.screening.freshness import market_freshness

    conn = sqlite3.connect(DEFAULT_DB)
    try:
        fresh = market_freshness(conn)
    finally:
        conn.close()

    def _sample(ids: list[str]) -> str:
        return ", ".join(ids[:5]) + ("…" if len(ids) > 5 else "")

    if fresh.inactive:
        note = (
            f"已排除 {len(fresh.inactive)} 檔停止交易的股票（{_sample(fresh.inactive)}）"
            "——它們最後一根 K 棒的爆量不是當日訊號。"
        )
        _clog(logger, note, "INACTIVE", rich_message=f"[dim]{note}[/dim]")

    if fresh.behind:
        # 指令不寫死 docker/模組路徑——使用者怎麼叫這支 CLI 我們並不知道。
        warning = (
            f"有 {len(fresh.behind)} 檔的日線尚未更新到 {fresh.reference_date}，"
            f"無法判斷當日爆量而被排除（{_sample(fresh.behind)}）。"
        )
        _clog(logger, warning, "OUT_OF_DATE", rich_message=f"[yellow]⚠ {warning}[/yellow]")
        fix = "請先跑完 `update existing` 再篩一次。"
        _clog(logger, fix, "OUT_OF_DATE", rich_message=f"[yellow]  {fix}[/yellow]")

    if fresh.is_incomplete:
        alert = (
            f"本次爆量篩選僅涵蓋 {fresh.covered}/{fresh.total} 檔"
            f"（{fresh.covered / fresh.total * 100:.0f}%），結果並不完整——"
            "沒涵蓋到的股票是完全沒參與比對，不是比了沒中。"
        )
        _clog(logger, alert, "INCOMPLETE", rich_message=f"[bold yellow]⚠ {alert}[/bold yellow]")


def _as_of_caption(df: pd.DataFrame) -> str:
    """一行說明每一組欄位的資料日期。

    這張表的欄位分屬四種頻率：價格每日、本益比／殖利率由 TWSE 每日公布（但取決於
    你上次 update 的時間）、每股盈餘每季、營收年增率每月。不標出來，並排的數字會
    被當成同一天的資料。
    """
    def _span(column: str) -> str:
        if column not in df.columns:
            return ""
        values = sorted(set(df[column].dropna().astype(str)))
        if not values:
            return ""
        return values[-1] if len(values) == 1 else f"{values[0]}~{values[-1]}"

    parts = [
        (f"價格 {_span('latest_date')}", _span("latest_date")),
        (f"本益比/殖利率 {_span('pe_date')}", _span("pe_date")),
        (f"EPS {_span('eps_period')}", _span("eps_period")),
        (f"營收 {_span('revenue_month')}", _span("revenue_month")),
    ]
    return "資料日期： " + "　|　".join(text for text, present in parts if present)


@screen_app.command(name="run")
def screen_run(
    pe_min: Optional[float] = typer.Option(None, "--pe-min", help="Minimum PE ratio"),
    pe_max: Optional[float] = typer.Option(None, "--pe-max", help="Maximum PE ratio"),
    rsi_min: Optional[float] = typer.Option(None, "--rsi-min", help="Minimum RSI"),
    rsi_max: Optional[float] = typer.Option(None, "--rsi-max", help="Maximum RSI"),
    volume_min: Optional[int] = typer.Option(None, "--volume-min", help="最低 20 日均量（不是單日成交量）"),
    volume_spike: Optional[float] = typer.Option(None, "--volume-spike", help="爆量倍數：當日量 >= 前 N 日均量的幾倍（建議 2.0）"),
    volume_spike_window: int = typer.Option(VOLUME_SPIKE_WINDOW, "--volume-spike-window", help="爆量基準的取樣天數，不含當日"),
    spike_direction: Optional[str] = typer.Option(None, "--spike-direction", help="爆量方向：up = 爆量上漲（承接）、down = 爆量下跌（出貨）"),
    spike_direction_band: float = typer.Option(SPIKE_DIRECTION_BAND, "--spike-direction-band", help="方向判定的中性帶 %，落在 ±此值內視為平盤（預設 1.0；0 = 只看正負號）"),
    ma_crossover: Optional[str] = typer.Option(None, "--ma-cross", help="MA crossover, e.g. 5x10 or 5x20"),
    ma_direction: str = typer.Option("up", "--ma-direction", help="up = golden cross, down = death cross"),
    ma_within: int = typer.Option(1, "--ma-within", help="Crossed within the last N trading days"),
    ma_trend_align: bool = typer.Option(True, "--ma-trend-align/--no-ma-trend-align", help="交叉當日兩條均線也要同方向（黃金交叉＝都上彎、死亡交叉＝都下彎）。--no- 可關掉，只看交叉本身"),
    ma_trend_window: int = typer.Option(MA_TREND_WINDOW, "--ma-trend-window", help="判斷均線方向時回看的交易日數"),
    eps_min: Optional[float] = typer.Option(None, "--eps-min", help="最低每股盈餘（最新一季，累計）"),
    eps_growth_min: Optional[float] = typer.Option(None, "--eps-growth-min", help="--eps-min 的舊名"),
    revenue_yoy_min: Optional[float] = typer.Option(None, "--revenue-yoy-min", help="最低月營收年增率 %"),
    dividend_yield_min: Optional[float] = typer.Option(None, "--dividend-yield-min", help="Minimum dividend yield %"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Save criteria with this name"),
):
    """Run a stock screen with the given criteria."""
    logger = get_logger("cli.screen.run", debug=True)
    _log_action(logger, "Running screen", "START")

    criteria = ScreenCriteria(
        pe_min=pe_min,
        pe_max=pe_max,
        rsi_min=rsi_min,
        rsi_max=rsi_max,
        volume_min=volume_min,
        volume_spike=volume_spike,
        volume_spike_window=volume_spike_window,
        spike_direction=spike_direction,
        spike_direction_band=spike_direction_band,
        ma_crossover=ma_crossover,
        ma_crossover_direction=ma_direction,
        ma_crossover_within=ma_within,
        ma_trend_align=ma_trend_align,
        ma_trend_window=ma_trend_window,
        eps_min=eps_min,
        eps_growth_min=eps_growth_min,
        revenue_yoy_min=revenue_yoy_min,
        dividend_yield_min=dividend_yield_min,
    )

    try:
        df = run_screen(criteria, db_path=DEFAULT_DB)
    except MissingDataError as exc:
        # 這不是「篩不到」，是資料還沒抓——兩者混在一起會讓人做出錯誤結論。
        _clog(logger, str(exc), "MISSING_DATA", rich_message=f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1)
    except ValueError as exc:
        _clog(logger, str(exc), "FAIL", rich_message=f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    # 這段必須在空表判斷之前：一檔都沒中的時候，最需要知道有沒有東西被排除掉。
    if volume_spike is not None:
        _warn_about_stale_stocks(logger)

    if df.empty:
        _clog(logger, "No stocks matched the screening criteria.", "NO_MATCH", rich_message="[yellow]No stocks matched the screening criteria.[/yellow]")
        return

    table = Table(title="🔍 Screening Results", show_header=True, header_style="bold magenta")
    table.add_column("Stock ID", justify="left", style="cyan")
    table.add_column("名稱", justify="left", style="white")
    table.add_column("Close", justify="right")
    table.add_column("20日均量", justify="right")
    # 這兩欄只在判讀爆量時有意義，平時加上去會讓 80 字元的終端機把「台積電」
    # 擠成「台積…」。爆量本身沒有方向，所以一旦要看倍數就必須同時看漲跌。
    if volume_spike is not None:
        table.add_column("量比", justify="right")
        table.add_column("漲跌%", justify="right")
    table.add_column("PE Ratio", justify="right")
    table.add_column("EPS(累計)", justify="right")
    table.add_column("營收年增率%", justify="right")
    table.add_column("Div Yield", justify="right")

    for _, row in df.iterrows():
        spike_cells = [
            f"{row['volume_ratio']:.2f}" if pd.notna(row.get("volume_ratio")) else "-",
            f"{row['change_pct']:+.2f}" if pd.notna(row.get("change_pct")) else "-",
        ] if volume_spike is not None else []
        table.add_row(
            str(row["stock_id"]),
            str(row["name"]) if pd.notna(row.get("name")) else "-",
            f"{row['latest_close']:.2f}" if pd.notna(row["latest_close"]) else "-",
            f"{int(row['avg_volume'])}" if pd.notna(row["avg_volume"]) else "-",
            *spike_cells,
            f"{row['pe_ratio']:.2f}" if pd.notna(row["pe_ratio"]) else "-",
            f"{row['eps']:.2f}" if pd.notna(row["eps"]) else "-",
            f"{row['revenue_yoy']:.2f}" if pd.notna(row["revenue_yoy"]) else "-",
            f"{row['dividend_yield']:.2f}" if pd.notna(row["dividend_yield"]) else "-",
        )

    _print_table(table, logger)
    _clog(logger, _as_of_caption(df), "AS_OF", rich_message=f"[dim]{_as_of_caption(df)}[/dim]")

    if volume_spike is not None and volume_min is None:
        hint = _illiquid_spike_hint(df)
        if hint:
            _clog(logger, hint, "ILLIQUID", rich_message=f"[yellow]{hint}[/yellow]")

    _log_action(logger, f"Matched {len(df)} stocks", "SUCCESS")

    # Save criteria if name provided
    if name:
        import sqlite3
        conn = sqlite3.connect(DEFAULT_DB)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO screening_criteria (name, criteria_json, updated_at) VALUES (?, ?, datetime('now'))",
                (name, json.dumps({
                    "pe_min": pe_min, "pe_max": pe_max,
                    "rsi_min": rsi_min, "rsi_max": rsi_max,
                    "ma_crossover": ma_crossover, "ma_direction": ma_direction, "ma_within": ma_within,
                    "ma_trend_align": ma_trend_align, "ma_trend_window": ma_trend_window,
                    "volume_min": volume_min,
                    "volume_spike": volume_spike, "volume_spike_window": volume_spike_window,
                    "spike_direction": spike_direction, "spike_direction_band": spike_direction_band,
                    "eps_growth_min": eps_growth_min,
                    "revenue_yoy_min": revenue_yoy_min,
                    "dividend_yield_min": dividend_yield_min,
                })),
            )
            conn.commit()
            _clog(logger, f"Criteria saved as '{name}'", "SUCCESS", rich_message=f"[dim]Criteria saved as '{name}'[/dim]")
        finally:
            conn.close()


@screen_app.command(name="list")
def screen_list():
    """List saved screening criteria."""
    import sqlite3
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        rows = conn.execute(
            "SELECT id, name, created_at FROM screening_criteria ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        _clog(get_logger("cli.screen.list"), "No saved screening criteria.", "EMPTY", rich_message="[yellow]No saved screening criteria.[/yellow]")
        return

    table = Table(title="📋 Saved Screening Criteria", show_header=True, header_style="bold cyan")
    table.add_column("ID", justify="right", style="yellow")
    table.add_column("Name", justify="left")
    table.add_column("Created At", justify="left")

    for row in rows:
        table.add_row(str(row["id"]), row["name"], row["created_at"])

    _print_table(table, get_logger("cli.screen.list"))


@screen_app.command(name="delete")
def screen_delete(
    criteria_id: int = typer.Argument(..., help="Criteria ID to delete"),
):
    """Delete a saved screening criteria by ID."""
    import sqlite3
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        cur = conn.execute("DELETE FROM screening_criteria WHERE id = ?", (criteria_id,))
        conn.commit()
        if cur.rowcount > 0:
            _clog(get_logger("cli.screen.delete"), f"Deleted criteria #{criteria_id}", "DELETED", rich_message=f"[green]Deleted criteria #{criteria_id}[/green]")
        else:
            _clog(get_logger("cli.screen.delete"), f"Criteria #{criteria_id} not found", "NOT_FOUND", rich_message=f"[red]Criteria #{criteria_id} not found[/red]")
    finally:
        conn.close()


@screen_app.command(name="show")
def screen_show(
    criteria_id: int = typer.Argument(..., help="Criteria ID to load and run"),
):
    """Load saved criteria and run the screen."""
    import sqlite3
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        row = conn.execute(
            "SELECT criteria_json FROM screening_criteria WHERE id = ?", (criteria_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        _clog(get_logger("cli.screen.show"), f"Criteria #{criteria_id} not found", "NOT_FOUND", rich_message=f"[red]Criteria #{criteria_id} not found[/red]")
        return

    criteria_data = json.loads(row["criteria_json"])
    criteria = ScreenCriteria(**criteria_data)
    df = run_screen(criteria, db_path=DEFAULT_DB)

    if df.empty:
        _clog(get_logger("cli.screen.show"), "No stocks matched the saved criteria.", "NO_MATCH", rich_message="[yellow]No stocks matched the saved criteria.[/yellow]")
        return

    table = Table(title="🔍 Screening Results", show_header=True, header_style="bold magenta")
    table.add_column("Stock ID", justify="left", style="cyan")
    table.add_column("Close", justify="right")
    table.add_column("20日均量", justify="right")
    table.add_column("PE Ratio", justify="right")
    table.add_column("EPS(累計)", justify="right")
    table.add_column("營收年增率%", justify="right")
    table.add_column("Div Yield", justify="right")

    for _, row in df.iterrows():
        table.add_row(
            str(row["stock_id"]),
            f"{row['latest_close']:.2f}" if pd.notna(row["latest_close"]) else "-",
            f"{int(row['avg_volume'])}" if pd.notna(row["avg_volume"]) else "-",
            f"{row['pe_ratio']:.2f}" if pd.notna(row["pe_ratio"]) else "-",
            f"{row['eps']:.2f}" if pd.notna(row["eps"]) else "-",
            f"{row['revenue_yoy']:.2f}" if pd.notna(row["revenue_yoy"]) else "-",
            f"{row['dividend_yield']:.2f}" if pd.notna(row["dividend_yield"]) else "-",
        )

    _print_table(table, get_logger("cli.screen.show"))


# ------------------------------------------------------------------
# Watchlist App
# ------------------------------------------------------------------

watchlist_app = typer.Typer(help="Manage stock watchlists")


@watchlist_app.command(name="create")
def watchlist_create(
    name: str = typer.Argument(..., help="Watchlist name"),
    stocks: Optional[str] = typer.Option(None, "--stocks", "-s", help="Comma-separated stock IDs"),
):
    """Create a new watchlist."""
    logger = get_logger("cli.watchlist.create", debug=True)

    if not stocks:
        _clog(logger, "Please provide --stocks with comma-separated stock IDs", "INVALID", rich_message="[red]Please provide --stocks with comma-separated stock IDs[/red]")
        raise typer.Exit(code=1)

    stock_ids = [s.strip() for s in stocks.split(",") if s.strip()]
    for sid in stock_ids:
        try:
            validate_stock_id(sid)
        except ValueError as exc:
            _clog(logger, f"Invalid stock ID '{sid}': {exc}", "INVALID", rich_message=f"[red]Invalid stock ID '{sid}': {exc}[/red]")
            raise typer.Exit(code=1)

    wid = create_watchlist(name, stock_ids, db_path=DEFAULT_DB)
    _clog(logger, f"Watchlist '{name}' created with {len(stock_ids)} stocks (id={wid})", "SUCCESS", rich_message=f"[green]Watchlist '{name}' created with {len(stock_ids)} stocks (id={wid})[/green]")


@watchlist_app.command(name="list")
def watchlist_list():
    """List all watchlists."""
    watchlists = list_watchlists(db_path=DEFAULT_DB)

    if not watchlists:
        _clog(get_logger("cli.watchlist.list"), "No watchlists found.", "EMPTY", rich_message="[yellow]No watchlists found.[/yellow]")
        return

    table = Table(title="📋 Watchlists", show_header=True, header_style="bold cyan")
    table.add_column("ID", justify="right", style="yellow")
    table.add_column("Name", justify="left")
    table.add_column("Stocks", justify="right")
    table.add_column("Created At", justify="left")

    for wl in watchlists:
        table.add_row(
            str(wl["id"]),
            wl["name"],
            str(len(wl["stock_ids"])),
            wl["created_at"],
        )

    _print_table(table, get_logger("cli.watchlist.list"))


@watchlist_app.command(name="delete")
def watchlist_delete(
    watchlist_id: int = typer.Argument(..., help="Watchlist ID to delete"),
):
    """Delete a watchlist by ID."""
    deleted = delete_watchlist(watchlist_id, db_path=DEFAULT_DB)
    if deleted:
        _clog(get_logger("cli.watchlist.delete"), f"Watchlist #{watchlist_id} deleted", "DELETED", rich_message=f"[green]Watchlist #{watchlist_id} deleted[/green]")
    else:
        _clog(get_logger("cli.watchlist.delete"), f"Watchlist #{watchlist_id} not found", "NOT_FOUND", rich_message=f"[red]Watchlist #{watchlist_id} not found[/red]")


@watchlist_app.command(name="screen")
def watchlist_screen(
    watchlist_id: int = typer.Argument(..., help="Watchlist ID to screen"),
    pe_min: Optional[float] = typer.Option(None, "--pe-min", help="Minimum PE ratio"),
    pe_max: Optional[float] = typer.Option(None, "--pe-max", help="Maximum PE ratio"),
    rsi_min: Optional[float] = typer.Option(None, "--rsi-min", help="Minimum RSI"),
    rsi_max: Optional[float] = typer.Option(None, "--rsi-max", help="Maximum RSI"),
    volume_min: Optional[int] = typer.Option(None, "--volume-min", help="Minimum volume"),
    eps_growth_min: Optional[float] = typer.Option(None, "--eps-growth-min", help="Minimum EPS growth"),
    revenue_yoy_min: Optional[float] = typer.Option(None, "--revenue-yoy-min", help="Minimum revenue YoY %"),
    dividend_yield_min: Optional[float] = typer.Option(None, "--dividend-yield-min", help="Minimum dividend yield %"),
):
    """Screen stocks in a watchlist using the given criteria."""
    logger = get_logger("cli.watchlist.screen", debug=True)
    _log_action(logger, f"watchlist_id={watchlist_id}", "START")

    wl = get_watchlist(watchlist_id, db_path=DEFAULT_DB)
    if wl is None:
        _clog(logger, f"Watchlist #{watchlist_id} not found", "NOT_FOUND", rich_message=f"[red]Watchlist #{watchlist_id} not found[/red]")
        raise typer.Exit(code=1)

    _clog(logger, f"Screening watchlist '{wl['name']}' ({len(wl['stock_ids'])} stocks)", "START", rich_message=f"[blue]Screening watchlist '{wl['name']}' ({len(wl['stock_ids'])} stocks)[/blue]")

    criteria = ScreenCriteria(
        pe_min=pe_min,
        pe_max=pe_max,
        rsi_min=rsi_min,
        rsi_max=rsi_max,
        volume_min=volume_min,
        eps_growth_min=eps_growth_min,
        revenue_yoy_min=revenue_yoy_min,
        dividend_yield_min=dividend_yield_min,
    )

    df = run_screen(criteria, stock_ids=wl["stock_ids"], db_path=DEFAULT_DB)

    if df.empty:
        _clog(logger, "No stocks in watchlist matched the criteria.", "NO_MATCH", rich_message="[yellow]No stocks in watchlist matched the criteria.[/yellow]")
        return

    table = Table(title=f"🔍 Watchlist '{wl['name']}' Screening Results", show_header=True, header_style="bold magenta")
    table.add_column("Stock ID", justify="left", style="cyan")
    table.add_column("名稱", justify="left", style="white")
    table.add_column("Close", justify="right")
    table.add_column("20日均量", justify="right")
    table.add_column("PE Ratio", justify="right")
    table.add_column("EPS(累計)", justify="right")
    table.add_column("營收年增率%", justify="right")
    table.add_column("Div Yield", justify="right")

    for _, row in df.iterrows():
        table.add_row(
            str(row["stock_id"]),
            str(row["name"]) if pd.notna(row.get("name")) else "-",
            f"{row['latest_close']:.2f}" if pd.notna(row["latest_close"]) else "-",
            f"{int(row['avg_volume'])}" if pd.notna(row["avg_volume"]) else "-",
            f"{row['pe_ratio']:.2f}" if pd.notna(row["pe_ratio"]) else "-",
            f"{row['eps']:.2f}" if pd.notna(row["eps"]) else "-",
            f"{row['revenue_yoy']:.2f}" if pd.notna(row["revenue_yoy"]) else "-",
            f"{row['dividend_yield']:.2f}" if pd.notna(row["dividend_yield"]) else "-",
        )

    _print_table(table, logger)
    _log_action(logger, f"Matched {len(df)} stocks", "SUCCESS")


# ------------------------------------------------------------------
# Update App (Typer group with subcommands)
# ------------------------------------------------------------------

update_app = typer.Typer(
    help="Fetch and cache stock data from online sources",
    invoke_without_command=True,
)


@update_app.command(name="stocks")
def update_stock_names():
    """Refresh the local stock-code → short-name table from TWSE."""
    from twstock_analyzer.screening.stock_names import save_stock_names

    logger = get_logger("cli.update.stocks", debug=True)
    _log_action(logger, "Refreshing stock names", "START")

    create_tables(DEFAULT_DB)
    loader = _make_loader()
    stock_list = loader.get_stock_list(logger=logger)

    if not stock_list:
        _clog(logger, "Could not fetch the stock list.", "FAIL", rich_message="[red]無法取得股票清單，請檢查網路連線。[/red]")
        raise typer.Exit(code=1)

    saved = save_stock_names(stock_list, db_path=DEFAULT_DB)
    _clog(logger, f"Stored {saved} stock names", "SUCCESS", rich_message=f"[green]已更新 {saved} 檔股票的名稱。[/green]")


#: `update existing` / `update full` 這兩個子指令與 `--stock existing` / `--stock all`
#: 共用的選項。文件與錯誤訊息從專案早期就寫成子指令的形式，兩種寫法都要成立。
_TYPE_OPTION = typer.Option("daily", "--type", "-t", callback=_validate_data_type, help="Data type: daily, fundamental, institutional")
_FORCE_OPTION = typer.Option(False, "--force", "-f", help="Force re-fetch (bypass cache)")
_START_OPTION = typer.Option(None, "--start", help="Backfill from this date (YYYY-MM-DD). Defaults to one year ago.")


@update_app.callback()
def update_main(
    ctx: typer.Context,
    stock: Annotated[str, typer.Option("--stock", "-s", callback=_format_stock_id, help="Stock ID, or 'all' for all TWSE stocks, 'existing' for stocks already in DB")] = None,
    data_type: str = _TYPE_OPTION,
    force: bool = _FORCE_OPTION,
    start: str = _START_OPTION,
):
    """Fetch and cache stock data from online sources."""
    if ctx.invoked_subcommand is not None:
        return

    _run_update(stock, data_type, force, start)


@update_app.command(name="existing")
def update_existing(
    data_type: str = _TYPE_OPTION,
    force: bool = _FORCE_OPTION,
    start: str = _START_OPTION,
):
    """Update every stock already present in the local database."""
    _run_update("existing", data_type, force, start)


@update_app.command(name="full")
def update_full(
    data_type: str = _TYPE_OPTION,
    force: bool = _FORCE_OPTION,
    start: str = _START_OPTION,
):
    """Fetch the full TWSE listing, then update every stock in it."""
    _run_update("all", data_type, force, start)


def _run_update(stock: str | None, data_type: str, force: bool, start: str | None) -> None:
    """`update` 的本體。四個入口共用：``--stock X``、``--stock existing``、
    ``update existing``、``update full``。"""
    logger = get_logger("cli.update", debug=True)
    _log_action(logger, f"stock={stock}, type={data_type}, force={force}, start={start}", "START")

    if start:
        try:
            start = datetime.strptime(start, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            _clog(logger, f"Invalid --start date: {start}", "FAIL", rich_message=f"[red]Invalid --start date: {start}. Expected YYYY-MM-DD.[/red]")
            raise typer.Exit(code=1)

    create_tables(DEFAULT_DB)
    loader = _make_loader()

    if stock == "all":
        _clog(logger, "Fetching stock list from TWSE...", "START", rich_message="[yellow]Fetching stock list from TWSE...[/yellow]")
        stock_list = loader.get_stock_list(logger=logger)
        if not stock_list:
            _clog(logger, "Could not fetch stock list from TWSE.", "FAIL", rich_message="[red]Could not fetch stock list from TWSE. Check your network connection.[/red]")
            raise typer.Exit(code=1)
        stock_ids = [s["stock_id"] for s in stock_list]
        from twstock_analyzer.screening.stock_names import save_stock_names
        save_stock_names(stock_list, db_path=DEFAULT_DB)
        _clog(logger, f"Fetched {len(stock_ids)} stocks from TWSE. Starting update...", "STOCK_LIST_OK", rich_message=f"[green]Fetched {len(stock_ids)} stocks from TWSE. Starting update...[/green]")
    elif stock == "existing":
        import sqlite3
        conn = sqlite3.connect(DEFAULT_DB)
        try:
            cursor = conn.execute("SELECT DISTINCT stock_id FROM daily_prices ORDER BY stock_id")
            stock_ids = [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()
        if not stock_ids:
            _clog(logger, "No stocks in database.", "NO_STOCKS", rich_message="[yellow]No stocks in database.[/yellow]")
            return
    else:
        if not stock:
            _clog(logger, "Please specify --stock.", "WARN", rich_message="[yellow]Please specify --stock.[/yellow]")
            raise typer.Exit(code=1)
        stock_ids = [stock]

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    #「資料該有多新」不是日曆上的今天。下午四點以前來源只公布到前一個交易日，
    # 週末與假日根本沒有新資料——拿今天當基準，每一檔股票都會為了確認「沒有
    # 新資料」各發一次請求。
    target = available_trading_date(now, DEFAULT_DB)

    if data_type == "fundamental":
        _batch_update_fundamentals(loader, stock_ids, DEFAULT_DB, logger)
        return

    if data_type in ("financials", "revenue"):
        _batch_update_financials(data_type, stock_ids, DEFAULT_DB, logger)
        return

    if data_type == "institutional":
        _batch_update_institutional(loader, stock_ids, DEFAULT_DB, logger, force=force, start=start)
        return

    fetch_from = start or (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    total = len(stock_ids)
    success = 0
    failed: list[str] = []
    asked_source = 0  # 真的問過來源幾檔（快取命中的不算）
    rows_fetched = 0
    for n, stock_id in enumerate(stock_ids, 1):
        _clog(logger, f"Updating {n}/{total}: {stock_id}", "PROGRESS", rich_message=f"[blue]Updating {n}/{total}: {stock_id}[/blue]")
        sid, status, rows = _update_stock(stock_id, loader, data_type, force, fetch_from, today, target, DEFAULT_DB, logger, explicit_start=bool(start))
        if status != "SKIPPED":
            asked_source += 1
        if status in ("SUCCESS", "SKIPPED", "UP_TO_DATE"):
            success += 1
        elif status in ("FAIL", "NO_DATA"):
            failed.append(sid)
        if status == "SUCCESS":
            rows_fetched += rows or 0

    if stock in _BATCH_KEYWORDS:
        _remember_market_holiday(target, asked_source, rows_fetched, failed, now, DEFAULT_DB, logger)

    if stock == "all":
        _clog(logger, f"Full update complete: {success}/{total} stocks", "SUCCESS", rich_message=f"[green]Full update complete: {success}/{total} stocks[/green]")
        _warn_about_failed_updates(failed, logger)
    elif stock == "existing":
        _clog(logger, f"Existing update complete: {success}/{total} stocks", "SUCCESS", rich_message=f"[green]Updated {success}/{total} stocks[/green]")
        _warn_about_failed_updates(failed, logger)
    else:
        if status in ("NO_DATA", "FAIL"):
            raise typer.Exit(code=1)


# ------------------------------------------------------------------
# Main App Registration
# ------------------------------------------------------------------

app.add_typer(screen_app, name="screen")
app.add_typer(watchlist_app, name="watchlist")
app.add_typer(backtest_app, name="backtest")
app.add_typer(update_app, name="update")
app.add_typer(replay_app, name="replay")


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------

def _batch_update_fundamentals(
    loader: DataLoader,
    stock_ids: list[str],
    db_path: str,
    logger,
) -> None:
    """Fetch fundamentals for ALL stocks in a single API call and upsert."""
    all_fund = loader.get_all_fundamentals(logger=logger)
    if all_fund is None or all_fund.empty:
        _clog(logger, "No fundamental data returned from source.", "NO_DATA", rich_message="[red]No fundamental data returned.[/red]")
        return

    # Filter to only the stock_ids we care about
    all_fund = all_fund[all_fund["stock_id"].isin(stock_ids)]
    if all_fund.empty:
        _clog(logger, "No matching stocks in fundamental data.", "NO_DATA", rich_message="[yellow]No matching stocks in fundamental data.[/yellow]")
        return

    now_iso = datetime.now().isoformat()
    required_cols = get_table_columns("fundamentals")

    total = 0
    success = 0
    groups = all_fund.groupby("stock_id", sort=True)
    for stock_id, group in groups:
        total += 1
        df = group.copy()
        df = df.rename(columns={"date": "report_date"})
        df["fetched_at"] = now_iso
        for col in required_cols:
            if col not in df.columns:
                df[col] = None
        upsert("fundamentals", df, db_path)
        success += 1

    _clog(logger, f"Fundamentals complete: {success}/{total} stocks", "SUCCESS", rich_message=f"[green]Updated fundamentals for {success}/{total} stocks[/green]")


#: `update --type X` → (資料表, 抓取方法, 中文名)。兩支端點都是一次回全市場，
#: 所以不論指定幾檔都只打一次網路，再依 stock_ids 收斂。
_FINANCIAL_SOURCES = {
    "financials": ("quarterly_financials", "fetch_quarterly_financials", "季度財報（EPS／營收）"),
    "revenue": ("monthly_revenue", "fetch_monthly_revenue", "月營收（年增率）"),
}


def _batch_update_financials(
    data_type: str,
    stock_ids: list[str],
    db_path: str,
    logger,
) -> None:
    """抓 TWSE 的營益分析／月營收彙總表並寫入對應的資料表。"""
    from twstock_analyzer.screening.financials import ensure_financial_tables

    table, method_name, label = _FINANCIAL_SOURCES[data_type]
    ensure_financial_tables(db_path)

    _clog(logger, f"Fetching {label} from TWSE...", "START",
          rich_message=f"[yellow]抓取{label}…[/yellow]")
    try:
        frame = getattr(TWSESource(), method_name)()
    except Exception as exc:
        _clog(logger, f"{label} fetch failed: {exc}", "FAIL",
              rich_message=f"[red]{label}抓取失敗：{exc}[/red]")
        raise typer.Exit(code=1)

    if frame is None or frame.empty:
        _clog(logger, f"No {label} returned from TWSE.", "NO_DATA",
              rich_message=f"[red]TWSE 沒有回傳{label}。[/red]")
        raise typer.Exit(code=1)

    frame = frame[frame["stock_id"].isin(stock_ids)].copy()
    if frame.empty:
        _clog(logger, f"No matching stocks in {label}.", "NO_DATA",
              rich_message=f"[yellow]{label}裡沒有指定的股票。[/yellow]")
        return

    frame["fetched_at"] = datetime.now().isoformat()
    for col in get_table_columns(table):
        if col not in frame.columns:
            frame[col] = None
    upsert(table, frame, db_path)

    _clog(logger, f"{label} complete: {len(frame)} stocks", "SUCCESS",
          rich_message=f"[green]{label}更新完成：{len(frame)} 檔[/green]")


def _update_stock(
    stock: str,
    loader: DataLoader,
    data_type: str,
    force: bool,
    year_ago: str,
    today: str,
    target: str,
    db_path: str,
    logger,
    explicit_start: bool = False,
) -> tuple[str, str, int | None]:
    """Fetch and upsert a single stock.

    For institutional data: scans the last 6 months, finds gaps, fetches only
    missing dates.  For daily/fundamental: uses the existing incremental logic.

    ``target`` 是**可得最新交易日**——以現在時間推算，來源此刻最多能給到哪一
    天（見 ``data.availability``）。資料庫追上它就不必問來源。拿 ``today`` 來
    比會讓週末、假日、以及每天下午四點以前的每一次更新，都對每一檔股票發出
    一次注定抓回空表的請求。

    Returns (stock_id, status, row_count_or_None).
    Status is one of: "SUCCESS", "SKIPPED", "UP_TO_DATE", "NO_DATA", "FAIL".
    "SKIPPED" 是連問都沒問（資料庫已經追上 target）；"UP_TO_DATE" 是問了、
    來源沒有更新的東西。兩者對使用者都是「已是最新」，但只有前者省下請求。
    """
    _TABLE_MAP = {
        "daily": "daily_prices",
        "fundamental": "fundamentals",
        "institutional": "institutional_trading",
        "dividend": "dividends",
    }
    _DATE_COL_MAP = {
        "daily": "date",
        "fundamental": "report_date",
        "institutional": "date",
        "dividend": "date",
    }
    table = _TABLE_MAP.get(data_type, "daily_prices")
    date_col = _DATE_COL_MAP.get(data_type, "date")

    had_cache = False
    if force:
        _log_action(logger, "Force re-fetch enabled", "MISS_FORCED", stock_id=stock)
    else:
        coverage = has_data(table, stock, year_ago, today, date_column=date_col)
        if coverage["has_data"]:
            had_cache = True
            latest = coverage["date_max"]
            earliest = coverage["date_min"]
            if explicit_start and earliest and earliest > year_ago:
                # An explicit --start reaching further back than the cache: the
                # incremental tail logic below would silently skip the older
                # range, so fetch the whole requested window instead.
                _log_action(logger, f"Backfilling from {year_ago} (cache starts {earliest})", "BACKFILL", stock_id=stock)
            elif latest >= target:
                _log_action(logger, f"Already have {coverage['row_count']} rows", "CACHE_HIT", stock_id=stock)
                _clog(logger, f"Cache hit for {stock}: {coverage['row_count']} rows", "CACHE_HIT", rich_message=f"[green]Cache hit for {stock}: {coverage['row_count']} rows ({coverage['date_min']} to {latest})[/green]")
                return (stock, "SKIPPED", coverage["row_count"])
            else:
                # Incremental: only fetch days after the latest DB entry
                next_day = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                year_ago = next_day
                _log_action(logger, f"Incremental update from {next_day}", "INC_HIT", stock_id=stock)

    try:
        df = loader.get_data(stock, data_type, year_ago, today, logger=logger)
        if df.empty:
            # An empty incremental result just means no new rows since the last
            # trading day the stock had data (didn't trade today, suspended, or
            # today's data not published yet) — the cache is still current, so
            # this is "up to date", not a real failure.
            if had_cache:
                _clog(logger, f"{stock} already up to date (no new data)", "UP_TO_DATE", rich_message=f"[green]{stock} already up to date (no new data since {latest})[/green]")
                return (stock, "UP_TO_DATE", 0)
            _clog(logger, f"No data returned for {stock}", "NO_DATA", rich_message=f"[red]No data returned for {stock}[/red]")
            return (stock, "NO_DATA", 0)

        # Normalize DataFrame for DB storage
        df['stock_id'] = stock
        if 'fetched_at' not in df.columns:
            df['fetched_at'] = datetime.now().isoformat()

        if data_type == "daily" and 'adj_close' not in df.columns:
            df['adj_close'] = df['close'] if 'close' in df.columns else 0.0

        if data_type == "fundamental" and 'date' in df.columns and 'report_date' not in df.columns:
            df = df.rename(columns={'date': 'report_date'})

        # Fill missing columns with None so upsert doesn't fail validation
        required_cols = get_table_columns(table)
        for col in required_cols:
            if col not in df.columns:
                df[col] = None

        upsert(table, df, db_path)
        _clog(logger, f"Updated {stock}: {len(df)} rows stored", "SUCCESS", rich_message=f"[green]Updated {stock}: {len(df)} rows stored[/green]")
        return (stock, "SUCCESS", len(df))
    except Exception as exc:
        _clog(logger, f"Error updating {stock}: {exc}", "FAIL", rich_message=f"[red]Error updating {stock}: {exc}[/red]")
        return (stock, "FAIL", None)


def _remember_market_holiday(
    target: str,
    asked_source: int,
    rows_fetched: int,
    failed: list[str],
    now: datetime,
    db_path: str,
    logger,
) -> None:
    """整批更新過後，若全市場都交不出 target 當天的資料，就把那天記成非交易日。

    國定假日沒有內建行事曆可查，只能從觀察得知。但「交不出資料」有三種原因，
    只有一種是假日，所以三個條件缺一不可：

    * 真的問過來源（``asked_source``）——全部快取命中時我們根本沒問，不能當
      作市場沒開。
    * 沒有任何一檔抓失敗——限流回的空表是故障，不是答案。一檔失敗就足以讓
      「全市場都沒有」這個推論失效。
    * 完全沒有抓到新資料——只要有人給了資料，那天就是有開盤。

    但這三個條件仍然不夠，因為樣本有偏：問得到的往往正是落後或停止交易的那幾
    檔，它們交不出資料是常態。最後一道門檻在 record_non_trading_day——資料庫
    裡只要有任何一檔在那天有行情，就直接否決。它也順便擋掉「今天剛過四點、來
    源還沒公布」：那裡只記已經過完的日子。
    """
    if rows_fetched:
        forget_non_trading_day(target, db_path)
        return
    if not asked_source or failed:
        return
    if record_non_trading_day(target, db_path, now=now):
        _clog(
            logger,
            f"{target}: whole market returned nothing; recorded as a non-trading day",
            "NON_TRADING",
            rich_message=f"[dim]{target} 全市場都沒有資料，已記為非交易日；之後的更新會直接跳過這一天。[/dim]",
        )


def _warn_about_failed_updates(failed: list[str], logger) -> None:
    """把抓失敗講在結尾那一行。

    使用者只會讀最後一行。抓失敗的股票不會進分子，分母卻沒變，於是
    「Updated 1085/1089」看起來像完成——少掉的四檔是誰、下一步做什麼，
    都得跟著那一行一起說，否則就會在選股時才以「被排除」的形式冒出來。
    """
    if not failed:
        return
    shown = "、".join(failed[:5])
    if len(failed) > 5:
        shown += "…"
    _clog(
        logger,
        f"{len(failed)} 檔更新失敗（{shown}）——這些股票的資料仍停在更新前的日期。",
        "FAIL",
        rich_message=f"[yellow]⚠ {len(failed)} 檔更新失敗（{shown}）——這些股票的資料仍停在更新前的日期。[/yellow]",
    )
    _clog(
        logger,
        "多半是 TWSE 限流（HTTP 428）；再跑一次同樣的指令通常就會補齊。",
        "FAIL",
        rich_message="[dim]  多半是 TWSE 限流（HTTP 428）；再跑一次同樣的指令通常就會補齊。[/dim]",
    )


def _batch_update_institutional(
    loader: DataLoader,
    stock_ids: list[str],
    db_path: str,
    logger,
    force: bool = False,
    start: str | None = None,
) -> None:
    """Batch-update institutional data by fetching T86 per date for the last 6 months.

    Skips weekends, known non-trading days, and any date already present in the
    DB (unless ``force``), so re-runs only fetch the missing tail.  A short
    delay between requests avoids TWSE rate-limiting.

    掃描的上界是**可得最新交易日**，不是今天：T86 也要等盤後彙整，下午四點
    以前問今天只會拿到空表。假日同理——但假日不會留下任何一列資料，所以
    「已經有的日期」永遠涵蓋不到它們，每跑一次就會把區間內每一個假日重問一
    遍。把觀察到的空日記進 market_calendar，才不會每次都重蹈覆轍。
    """
    import sqlite3
    import time

    stock_set = set(stock_ids)
    now = datetime.now()
    today = datetime.strptime(available_trading_date(now, db_path), "%Y-%m-%d")
    if start:
        six_months_ago = datetime.strptime(start, "%Y-%m-%d")
    else:
        six_months_ago = now - timedelta(days=180)

    # Skip dates we already have (incremental), unless force re-fetch.
    existing_dates: set[str] = set()
    if not force:
        conn = sqlite3.connect(db_path)
        try:
            existing_dates = {
                row[0]
                for row in conn.execute("SELECT DISTINCT date FROM institutional_trading")
            }
        finally:
            conn.close()

    # Collect candidate trading dates (weekdays only) in range.
    known_closed = set() if force else non_trading_days(db_path)
    all_dates: list[str] = []
    cursor = six_months_ago
    while cursor <= today:
        if cursor.weekday() < 5:  # 0-4 = Mon-Fri; skip Sat/Sun
            date_str = cursor.strftime("%Y-%m-%d")
            if force or (date_str not in existing_dates and date_str not in known_closed):
                all_dates.append(date_str)
        cursor += timedelta(days=1)

    total_days = len(all_dates)
    if total_days == 0:
        _clog(logger, "Institutional data already up to date; nothing to fetch.", "SUCCESS", rich_message="[green]Institutional data already up to date.[/green]")
        return

    total_rows = 0
    days_with_data = 0

    for n, date_str in enumerate(all_dates, 1):
        _clog(logger, f"Day {n}/{total_days}: {date_str}", "PROGRESS", rich_message=f"[blue]{n}/{total_days}: {date_str}[/blue]")

        try:
            df = loader.get_data("0000", "institutional", date_str, date_str, logger=logger)
        except Exception as exc:
            _clog(logger, f"Fetch failed for {date_str}: {exc}", "WARN", rich_message=f"[yellow]Fetch failed for {date_str}: {exc}[/yellow]")
            time.sleep(0.5)
            continue

        # Throttle regardless of outcome to stay under TWSE rate limits.
        time.sleep(0.5)

        if df.empty:
            # 沒有一檔在這天交易——T86 是全市場端點，所以這就是「市場沒開」
            # 的直接證據。記下來，下一次不必再問一遍。
            record_non_trading_day(date_str, db_path, now=now)
            continue

        days_with_data += 1
        filtered = df[df["stock_id"].isin(stock_set)].copy()
        if filtered.empty:
            continue

        if "fetched_at" not in filtered.columns:
            filtered["fetched_at"] = datetime.now().isoformat()

        # Fill any missing schema columns so upsert() validation passes.
        for col in get_table_columns("institutional_trading"):
            if col not in filtered.columns:
                filtered[col] = None

        upsert("institutional_trading", filtered, db_path)
        total_rows += len(filtered)

    _clog(logger, f"Institutional complete: {days_with_data} days, {total_rows} rows upserted", "SUCCESS", rich_message=f"[green]Updated {total_rows} rows across {days_with_data} days[/green]")


@app.callback()
def _callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose/debug logging"),
):
    """Global callback - sets up logging."""
    begin_severity_summary()
    ctx.call_on_close(finish_severity_summary)
    if verbose:
        get_logger("cli", debug=True)
    # Store context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@app.command(name="analyze")
def analyze(
    stock_id: Annotated[str, typer.Argument(callback=_format_stock_id, help="Stock ID to analyze")],
    indicators: str = typer.Option("ma,rsi", "--indicators", "-i", help="Comma-separated indicators: ma,macd,rsi,kd,bb"),
    start_date: str = typer.Option("2024-01-01", "--start", help="Start date (YYYY-MM-DD)"),
    end_date: str = typer.Option("", "--end", help="End date (YYYY-MM-DD)"),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table, json"),
):
    """Run technical/fundamental/institutional analysis on a stock."""
    logger = get_logger("cli.analyze", debug=True)
    _log_action(logger, f"indicators={indicators}", "START", stock_id=stock_id)

    try:
        validate_stock_id(stock_id)
    except ValueError as exc:
        _clog(logger, f"Validation error: {exc}", "INVALID", rich_message=f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)

    end_date = end_date or datetime.now().strftime("%Y-%m-%d")
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)

    # Parse indicator list
    indicator_list = [i.strip().lower() for i in indicators.split(",")]

    # Load price data from DB
    import sqlite3
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM daily_prices WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
            conn, params=(stock_id, start_date, end_date),
        )
    except pd.errors.DatabaseError:
        df = pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        _clog(logger, f"No data found for {stock_id}", "NO_DATA", rich_message=f"[yellow]No data found for {stock_id} in [{start_date}, {end_date}][/yellow]")
        _clog(logger, "Run 'update --stock 2330' first to fetch data.", "NO_DATA", rich_message="[dim]Run 'update --stock 2330' first to fetch data.[/dim]")
        raise typer.Exit(code=0)

    # Run technical analysis
    analyzer = TechnicalAnalyzer(logger=logger)
    for ind in indicator_list:
        if ind == "ma":
            df = analyzer.sma(df)
        elif ind == "macd":
            df = analyzer.macd(df)
        elif ind == "rsi":
            df = analyzer.rsi(df)
        elif ind == "kd":
            df = analyzer.kd(df)
        elif ind == "bb":
            df = analyzer.bollinger_bands(df)
        else:
            _clog(logger, f"Unknown indicator: {ind}, skipping", "WARN", rich_message=f"[yellow]Unknown indicator: {ind}, skipping[/yellow]")

    # Output
    if output == "json":
        # Convert to serializable format
        result = df.to_dict(orient="records")
        _print_payload(
            json.dumps(result, indent=2, default=str),
            logger,
            f"JSON output for {stock_id}",
        )
    else:
        # Default: Rich table
        table = _build_price_table(df, f"📊 {stock_id} Analysis ({indicators})")
        _print_table(table, logger)

    _log_action(logger, f"Generated {len(df)} rows with indicators={indicator_list}", "SUCCESS", stock_id=stock_id)


@app.command(name="report")
def report(
    stock_id: Annotated[str, typer.Argument(callback=_format_stock_id, help="Stock ID for report")],
    format: str = typer.Option("html", "--format", "-f", help="Report format: html, json"),
    output_file: str = typer.Option("report.html", "--output", "-o", help="Output file path"),
):
    """Generate analysis report."""
    logger = get_logger("cli.report", debug=True)
    _log_action(logger, f"format={format}", "START", stock_id=stock_id)

    try:
        validate_stock_id(stock_id)
    except ValueError as exc:
        _clog(logger, f"Validation error: {exc}", "INVALID", rich_message=f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)

    if format == "json":
        # Gather all analysis data
        today = datetime.now().strftime("%Y-%m-%d")

        # Price data — all cached rows for the stock
        import sqlite3
        conn = sqlite3.connect(DEFAULT_DB)
        try:
            prices = pd.read_sql_query(
                "SELECT * FROM daily_prices WHERE stock_id=? ORDER BY date",
                conn, params=(stock_id,),
            )
        except pd.errors.DatabaseError:
            prices = pd.DataFrame()
        conn.close()

        # Technical indicators
        if not prices.empty:
            tech = TechnicalAnalyzer()
            prices = tech.calculate_all(prices)

        # Fundamental data
        fa = FundamentalAnalyzer(DEFAULT_DB)
        eps_trend = fa.eps_trend(stock_id)
        roe = fa.roe_analysis(stock_id)
        revenue = fa.monthly_revenue(stock_id)
        pe = fa.pe_ratio(stock_id, today)

        report_data = {
            "stock_id": stock_id,
            "generated_at": datetime.now().isoformat(),
            "prices_count": len(prices),
            "pe_ratio": pe,
            "eps_trend": eps_trend.to_dict(orient="records") if not eps_trend.empty else [],
            "roe_analysis": roe.to_dict(orient="records") if not roe.empty else [],
            "monthly_revenue": revenue.to_dict(orient="records") if not revenue.empty else [],
        }

        if output_file == "-":
            _print_payload(
                json.dumps(report_data, indent=2, default=str),
                logger,
                f"JSON report for {stock_id}",
            )
        else:
            with open(output_file, "w") as f:
                json.dump(report_data, f, indent=2, default=str)
            _clog(logger, f"Report saved to {output_file}", "SUCCESS", rich_message=f"[green]Report saved to {output_file}[/green]")
    else:
        # HTML report — default to report-{stock_id}.html in the CWD
        if output_file == "report.html":
            output_file = f"report-{stock_id}.html"

        # Price data — all cached rows for the stock
        import sqlite3
        conn = sqlite3.connect(DEFAULT_DB)
        try:
            prices = pd.read_sql_query(
                "SELECT * FROM daily_prices WHERE stock_id=? ORDER BY date",
                conn, params=(stock_id,),
            )
        except pd.errors.DatabaseError:
            prices = pd.DataFrame()
        conn.close()

        if prices.empty:
            _clog(logger, f"No price data found for {stock_id}", "NO_DATA", rich_message=f"[red]No price data found for {stock_id}[/red]")
            _clog(logger, "Run 'update --stock 2330' first to fetch data.", "NO_DATA", rich_message="[dim]Run 'update --stock 2330' first to fetch data.[/dim]")
            _log_action(logger, "No data available", "NO_DATA", stock_id=stock_id)
            raise typer.Exit(code=1)

        # Clean numeric columns that may be stored as bytes in SQLite
        for col in ("open", "high", "low", "close", "volume", "adj_close"):
            if col in prices.columns:
                prices[col] = pd.to_numeric(prices[col], errors="coerce")

        # Calculate technical indicators
        tech = TechnicalAnalyzer()
        prices = tech.calculate_all(prices)

        # Build data dict for generate_report()
        data_dict = {
            "prices": prices,
            "technical": prices,
            "stock_id": stock_id,
            "generated_at": datetime.now().isoformat(),
        }

        try:
            generate_report(stock_id, data_dict, output_file, format="html")
            _clog(logger, f"HTML report saved to {output_file}", "SUCCESS", rich_message=f"[green]Report saved to {output_file}[/green]")
        except Exception as exc:
            _clog(logger, f"Error generating HTML report: {exc}", "FAIL", rich_message=f"[red]Error generating HTML report: {exc}[/red]")
            raise typer.Exit(code=1)

    _log_action(logger, f"Report generated in {format} format", "SUCCESS", stock_id=stock_id)


@app.command(name="list")
def list_stocks(
    top: int = typer.Option(20, "--top", "-t", help="Show top N stocks"),
):
    """List stocks with basic info."""
    logger = get_logger("cli.list", debug=True)
    _log_action(logger, f"top={top}", "START")

    import sqlite3
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        # Get distinct stock IDs with latest price
        df = pd.read_sql_query(
            """SELECT stock_id,
                      MAX(date) as latest_date,
                      MAX(close) as latest_close,
                      COUNT(*) as data_points
               FROM daily_prices
               GROUP BY stock_id
               ORDER BY latest_close DESC
               LIMIT ?""",
            conn, params=(top,),
        )
    except pd.errors.DatabaseError:
        df = pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        _clog(logger, "No stocks in database.", "EMPTY", rich_message="[yellow]No stocks in database. Run 'update full' first.[/yellow]")
        return

    table = Table(title="📋 Stock Overview", show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", justify="center")
    table.add_column("Stock ID", justify="left")
    table.add_column("Latest Date", justify="center")
    table.add_column("Close", justify="right")
    table.add_column("Data Points", justify="right")

    for i, row in df.iterrows():
        table.add_row(
            str(i + 1),
            str(row["stock_id"]),
            str(row["latest_date"]),
            f"{row['latest_close']:.2f}" if pd.notna(row["latest_close"]) else "-",
            str(int(row["data_points"])) if pd.notna(row["data_points"]) else "0",
        )

    _print_table(table, logger)
    _log_action(logger, f"Displayed {len(df)} stocks", "SUCCESS")


@app.command(name="serve")
def serve(
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind"),
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind"),
):
    """Start the FastAPI REST API server."""
    import uvicorn
    from twstock_analyzer.api.server import create_app

    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    app()
