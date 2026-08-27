from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

import pandas as pd

#: 計算 RSI 的期數，與 TechnicalAnalyzer 的預設一致。
RSI_PERIOD = 14

#: 均量的取樣期間（交易日）。成交量條件與輸出的均量欄都以此為準——
#: 用全歷史平均會讓多年前的一根爆量把現在很冷清的股票拉進來。
VOLUME_WINDOW = 20

#: 爆量基準的取樣期間（交易日，**不含當日**）。
#:
#: 取 20 日而非 5 日：5 日均量會被連續放量自己墊高，真正的爆量反而測不出來，
#: 且尾部更亂（實測 1,081 檔上市股，5 日量比的 99 百分位是 8.64，20 日只有
#: 5.15）。也不取 60 日：60 日量比的中位數僅 0.45，基準被舊的量能水位污染，
#: 會把「量能回到正常」誤判成爆量。
VOLUME_SPIKE_WINDOW = 20

#: 判定爆量的倍數門檻。
#:
#: 實測全市場當日量 ÷ 前 20 日均量的分布：中位數 0.69、90 百分位 1.61、
#: 95 百分位 2.22。2.0 倍約在第 93 百分位，選出約 6.9% 的股票——統計上確實
#: 異常，數量也還看得完。1.5 倍會選出 12.1%，那只是「高於平均」不是爆量。
VOLUME_SPIKE_MULTIPLE = 2.0

#: 判定爆量方向時的中性帶（%）。漲跌幅落在 ±此值之內視為「平盤」，不算上漲
#: 也不算下跌。
#:
#: 取 1.0：全市場當日 |漲跌幅| 的中位數是 1.42%，1% 落在典型單日波動之下，
#: 所以被歸為中性的確實是「沒走到哪裡去」的那些。實測 2 倍以上的 74 檔裡有
#: 16 檔（22%）落在這條帶內——最具代表性的是 2455 全新，3.79 倍量卻只有
#: +0.12%，那是換手攻防不是承接；用純正負號會把它算成爆量上漲。
SPIKE_DIRECTION_BAND = 1.0

#: 判定均線「趨勢方向」時回看的交易日數。方向 = 該均線今日的值與 N 日前相比。
#:
#: 取 3 而非 1：MA20 的單日斜率等於 (今日收盤 - 20 日前收盤) ÷ 20，只要一根 K 棒
#: 進出視窗就會翻面——實測 1,089 檔上市股，MA20 單日斜率的正負號有 12.3% 的日子
#: 隔天就反轉，回看 3 日降到 6.9%。也不取 5：翻面率只再降到 5.3%，卻讓通過交叉
#: 的股票從 43% 掉到 29%，穩定度換得的太少。
MA_TREND_WINDOW = 3

_MA_SPEC = re.compile(r"^\s*(\d+)\s*[xX/,\-]\s*(\d+)\s*$")


class MissingDataError(RuntimeError):
    """條件用到的欄位在資料庫裡完全沒有值。

    這種情況若靜靜回傳空表，使用者只會看到「沒有符合的股票」——把「篩不到」和
    「還沒抓資料」混為一談，是最容易讓人做出錯誤結論的失敗方式。
    """


@dataclass
class ScreenCriteria:
    pe_min: float | None = None
    pe_max: float | None = None
    rsi_min: float | None = None
    rsi_max: float | None = None
    volume_min: int | None = None
    #: 爆量門檻（倍數）。當日成交量 ÷ 前 N 日均量 >= 此值才算爆量。
    volume_spike: float | None = None
    #: 爆量基準的取樣天數，不含當日。
    volume_spike_window: int = VOLUME_SPIKE_WINDOW
    #: 爆量方向："up" = 爆量上漲（承接）、"down" = 爆量下跌（出貨）。
    spike_direction: str | None = None
    #: 方向判定的中性帶（%）。0 表示只看漲跌的正負號。
    spike_direction_band: float = SPIKE_DIRECTION_BAND
    #: 均線交叉，例如 "5x10"（MA5 與 MA10）。也接受 5/10、5-10、5,10。
    ma_crossover: str | None = None
    #: "up" = 黃金交叉（快線由下而上穿越慢線）；"down" = 死亡交叉。
    ma_crossover_direction: str = "up"
    #: 交叉必須發生在最近幾個交易日內。1 表示「今天剛穿越」。
    ma_crossover_within: int = 1
    #: 交叉當日兩條均線是否必須同時朝交叉的方向走。True = 黃金交叉還要求快慢線
    #: 都在上彎（死亡交叉則都下彎）。
    ma_trend_align: bool = True
    #: 判定趨勢方向時回看的交易日數。
    ma_trend_window: int = MA_TREND_WINDOW
    #: 最低每股盈餘（最新一季，累計至該季）。
    eps_min: float | None = None
    #: 舊名。實際篩的一直是 EPS 本身而不是成長率，保留是為了不讓存過的條件失效。
    eps_growth_min: float | None = None
    #: 最低月營收年增率（最新一個月）。
    revenue_yoy_min: float | None = None
    dividend_yield_min: float | None = None

    def __post_init__(self) -> None:
        if self.eps_min is None and self.eps_growth_min is not None:
            self.eps_min = self.eps_growth_min


def parse_ma_spec(spec: str) -> tuple[int, int]:
    """把 "5x10" 這類寫法拆成 (快線期數, 慢線期數)。"""
    match = _MA_SPEC.match(spec)
    if not match:
        raise ValueError(
            f"看不懂的均線交叉條件：{spec!r}。格式為「快線x慢線」，例如 5x10 或 5x20。"
        )
    fast, slow = int(match.group(1)), int(match.group(2))
    if fast <= 0 or slow <= 0:
        raise ValueError(f"均線期數必須大於 0：{spec!r}")
    if fast >= slow:
        raise ValueError(f"快線期數必須小於慢線：{spec!r}")
    return fast, slow


def _recent_closes(
    conn: sqlite3.Connection, bars: int, stock_ids: list[str] | None
) -> pd.DataFrame:
    """每檔股票最近 ``bars`` 個交易日的收盤價，由早到晚。"""
    where = ""
    params: list[Any] = []
    if stock_ids:
        placeholders = ",".join("?" for _ in stock_ids)
        where = f"WHERE stock_id IN ({placeholders})"
        params.extend(stock_ids)

    query = f"""
        SELECT stock_id, date, close FROM (
            SELECT stock_id, date, close,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
              FROM daily_prices
              {where}
        )
        WHERE rn <= ?
        ORDER BY stock_id, date
    """
    params.append(bars)
    return pd.read_sql_query(query, conn, params=params)


def _crossover_matches(
    conn: sqlite3.Connection,
    spec: str,
    direction: str,
    within: int,
    stock_ids: list[str] | None,
    trend_align: bool = False,
    trend_window: int = MA_TREND_WINDOW,
) -> set[str]:
    """發生指定均線交叉的股票代號。

    交叉的定義是「前一日快線不在慢線之上、當日在其之上」（向下交叉則相反），
    所以持續處於多頭排列的股票不會被算成交叉——那是狀態，不是事件。

    ``trend_align`` 另外要求**交叉當日**兩條均線都朝交叉的方向走：黃金交叉時
    快線與慢線都比 ``trend_window`` 日前高，死亡交叉則都比之前低。快線幾乎是
    免費的條件（實測 97.6% 的黃金交叉本來就滿足——快線要穿上去多半得自己上彎），
    真正在篩的是慢線：只有約半數的黃金交叉發生在 MA20 也在上彎的時候，其餘是
    慢線還在下墜、快線只是反彈得比它快而撞上去的那種交叉。

    趨勢一律看**交叉當日**而不是最新一日。這樣一筆交叉是否合格由事件本身決定，
    昨天篩得到的今天不會因為 ``within`` 往後挪一格就消失。
    """
    fast, slow = parse_ma_spec(spec)
    if direction not in ("up", "down"):
        raise ValueError(f"未知的交叉方向：{direction!r}（只接受 'up' 或 'down'）")
    within = max(int(within), 1)
    trend_window = int(trend_window)
    if trend_align and trend_window < 1:
        raise ValueError(f"趨勢回看天數必須大於 0：{trend_window!r}")

    # 判斷一筆交叉需要：慢線的 slow 根、「前一日」的 1 根，趨勢再往前 trend_window 根。
    needed = max(slow + 1, slow + trend_window) if trend_align else slow + 1
    frame = _recent_closes(conn, needed + within, stock_ids)
    if frame.empty:
        return set()

    matched: set[str] = set()
    for stock_id, group in frame.groupby("stock_id", sort=False):
        closes = group["close"].astype(float).reset_index(drop=True)
        if len(closes) < needed:
            continue  # 資料不足以判斷，略過而不是猜

        ma_fast = closes.rolling(fast).mean()
        ma_slow = closes.rolling(slow).mean()
        spread = ma_fast - ma_slow
        previous = spread.shift(1)
        crossed = (
            (spread > 0) & (previous <= 0) if direction == "up"
            else (spread < 0) & (previous >= 0)
        )

        if trend_align:
            fast_slope = ma_fast - ma_fast.shift(trend_window)
            slow_slope = ma_slow - ma_slow.shift(trend_window)
            # 持平（斜率恰為 0）不算「同方向」——那是走平不是趨勢。
            aligned = (
                (fast_slope > 0) & (slow_slope > 0) if direction == "up"
                else (fast_slope < 0) & (slow_slope < 0)
            )
            crossed = crossed & aligned

        if crossed.tail(within).any():
            matched.add(str(stock_id))

    return matched


def _rsi_matches(
    conn: sqlite3.Connection,
    rsi_min: float | None,
    rsi_max: float | None,
    stock_ids: list[str] | None,
) -> set[str]:
    """最新一根 K 棒的 RSI 落在指定區間的股票代號。"""
    import pandas_ta as ta

    frame = _recent_closes(conn, RSI_PERIOD * 4, stock_ids)
    if frame.empty:
        return set()

    matched: set[str] = set()
    for stock_id, group in frame.groupby("stock_id", sort=False):
        closes = group["close"].astype(float).reset_index(drop=True)
        if len(closes) <= RSI_PERIOD:
            continue

        rsi = ta.rsi(closes, length=RSI_PERIOD)
        if rsi is None or rsi.dropna().empty:
            continue

        latest = float(rsi.dropna().iloc[-1])
        if rsi_min is not None and latest < rsi_min:
            continue
        if rsi_max is not None and latest > rsi_max:
            continue
        matched.add(str(stock_id))

    return matched


def stale_stock_ids(conn: sqlite3.Connection) -> list[str]:
    """沒跟上最新交易日、因此無法判斷當日爆量的股票代號。

    要區分「update 沒跑完」與「已停止交易」請改用
    :func:`~twstock_analyzer.screening.freshness.market_freshness`——兩者的
    後果完全不同，混在一起會讓「你的資料少了三分之一」被讀成「這些股票有問題」。
    """
    from .freshness import market_freshness

    return market_freshness(conn).excluded


def run_screen(
    criteria: ScreenCriteria,
    stock_ids: list[str] | None = None,
    db_path: str = "data/twstock.db",
) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)

    from .financials import (
        LATEST_MONTH_CTE,
        LATEST_QUARTER_CTE,
        ensure_financial_tables,
        has_any,
    )
    from .stock_names import _ensure_table

    _ensure_table(conn)
    ensure_financial_tables(conn)

    # 條件用到的欄位若整欄都沒資料，回空表會被讀成「市場上沒有符合的股票」。
    for value, table, column, hint in (
        (criteria.eps_min, "quarterly_financials", "eps",
         "python -m twstock_analyzer.cli.main update --type financials"),
        (criteria.revenue_yoy_min, "monthly_revenue", "revenue_yoy",
         "python -m twstock_analyzer.cli.main update --type revenue"),
    ):
        if value is not None and not has_any(conn, table, column):
            conn.close()
            raise MissingDataError(
                f"資料庫裡的 {column} 沒有任何資料，這個條件篩不出東西也不代表沒有符合的股票。"
                f"請先執行：{hint}"
            )

    # 均線交叉與 RSI 是逐檔的時間序列判斷，SQL 做不動，先算出通過的代號再收斂。
    try:
        if criteria.ma_crossover:
            passing = _crossover_matches(
                conn,
                criteria.ma_crossover,
                criteria.ma_crossover_direction,
                criteria.ma_crossover_within,
                stock_ids,
                criteria.ma_trend_align,
                criteria.ma_trend_window,
            )
            stock_ids = sorted(passing & set(stock_ids)) if stock_ids else sorted(passing)
            if not stock_ids:
                conn.close()
                return pd.DataFrame()

        if criteria.rsi_min is not None or criteria.rsi_max is not None:
            passing = _rsi_matches(conn, criteria.rsi_min, criteria.rsi_max, stock_ids)
            stock_ids = sorted(passing & set(stock_ids)) if stock_ids else sorted(passing)
            if not stock_ids:
                conn.close()
                return pd.DataFrame()
    except Exception:
        conn.close()
        raise

    # 窗口會被插進 SQL 字串，只接受正整數。
    spike_window = int(criteria.volume_spike_window)
    if spike_window < 1:
        conn.close()
        raise ValueError(f"爆量基準天數必須大於 0：{criteria.volume_spike_window!r}")

    conditions: list[str] = []
    params: list[Any] = []

    if criteria.volume_spike is not None:
        # 直接比乘積而不是先相除：除法會在 spike_base 為 0 時出事，而且
        # base_bars 的檢查確保基準真的取滿了整個窗口，不是拿 3 天硬算。
        #
        # 最後一個條件把沒跟上最新交易日的股票排除在外——它們最後一根 K 棒的
        # 爆量不是「當日」的爆量。沒下爆量條件時它們照樣列出，量比與真實日期
        # 都在，不是把資料藏起來。
        #
        # 基準日由多數股票決定（見 freshness），不是全表 MAX(date)：後者只要
        # 有 1 檔多抓了一天，其餘 1088 檔就全部被排除。用 >= 是為了不把那檔
        # 跑在前面的也一起丟掉，它反而是資料最新的。
        from .freshness import market_reference_date

        reference = market_reference_date(conn)
        conditions.append(
            "vs.base_bars = ? AND vs.spike_base > 0"
            " AND vs.spike_volume >= ? * vs.spike_base"
            " AND p.date >= ?"
        )
        params.extend([spike_window, float(criteria.volume_spike), reference or ""])

    if criteria.spike_direction is not None:
        if criteria.volume_spike is None:
            conn.close()
            raise ValueError(
                "spike_direction 只用來區分爆量的方向，必須搭配 volume_spike"
                "（CLI 是 --volume-spike）一起使用。"
            )
        if criteria.spike_direction not in ("up", "down"):
            conn.close()
            raise ValueError(
                f"未知的爆量方向：{criteria.spike_direction!r}（只接受 'up' 或 'down'）"
            )
        band = float(criteria.spike_direction_band)
        if band < 0:
            conn.close()
            raise ValueError(f"中性帶不能是負的：{criteria.spike_direction_band!r}")

        # 沒有前一日收盤就判斷不出方向，略過而不是猜。
        comparison = ">" if criteria.spike_direction == "up" else "<"
        conditions.append(
            "p.prev_close > 0"
            f" AND (p.close / p.prev_close - 1) * 100 {comparison} ?"
        )
        params.append(band if criteria.spike_direction == "up" else -band)

    if criteria.pe_min is not None:
        conditions.append("f.pe_ratio >= ?")
        params.append(criteria.pe_min)
    if criteria.pe_max is not None:
        conditions.append("f.pe_ratio <= ?")
        params.append(criteria.pe_max)
    if criteria.volume_min is not None:
        conditions.append("v.avg_volume >= ?")
        params.append(criteria.volume_min)
    if criteria.eps_min is not None:
        conditions.append("q.eps >= ?")
        params.append(criteria.eps_min)
    if criteria.revenue_yoy_min is not None:
        conditions.append("m.revenue_yoy >= ?")
        params.append(criteria.revenue_yoy_min)
    if criteria.dividend_yield_min is not None:
        conditions.append("f.dividend_yield >= ?")
        params.append(criteria.dividend_yield_min)

    if stock_ids:
        placeholders = ",".join("?" for _ in stock_ids)
        conditions.append(f"p.stock_id IN ({placeholders})")
        params.extend(stock_ids)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # 每一欄都必須來自「最新的那一列」，不是歷史上的最大值——否則日期是今天、
    # 價格卻是半年前的天價，看起來完全像一筆合理的資料。
    query = f"""
        WITH latest_price AS (
            SELECT stock_id, date, close, prev_close FROM (
                SELECT stock_id, date, close,
                       LAG(close) OVER (PARTITION BY stock_id ORDER BY date) AS prev_close,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                  FROM daily_prices
            ) WHERE rn = 1
        ),
        -- 爆量：當日量對「前 N 日」的均量。基準**不含當日**——含了的話，爆得
        -- 越兇被自己稀釋得越多（19 天 1.0 + 今天 3.0 只會量到 2.74 倍）。
        -- rn = 1 在每檔股票裡唯一，所以 MAX(CASE WHEN rn = 1 ...) 取到的必定
        -- 是最新那一天的量，不是歷史最大量。
        volume_spike AS (
            SELECT stock_id,
                   MAX(CASE WHEN rn = 1 THEN volume END) AS spike_volume,
                   AVG(CASE WHEN rn > 1 THEN volume END) AS spike_base,
                   SUM(CASE WHEN rn > 1 THEN 1 ELSE 0 END) AS base_bars
              FROM (
                SELECT stock_id, volume,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                  FROM daily_prices
            ) WHERE rn <= {spike_window + 1}
            GROUP BY stock_id
        ),
        recent_volume AS (
            SELECT stock_id, AVG(volume) AS avg_volume FROM (
                SELECT stock_id, volume,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY date DESC) AS rn
                  FROM daily_prices
            ) WHERE rn <= {VOLUME_WINDOW}
            GROUP BY stock_id
        ),
        latest_fundamentals AS (
            SELECT stock_id, report_date, pe_ratio, dividend_yield FROM (
                SELECT stock_id, report_date, pe_ratio, dividend_yield,
                       ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY report_date DESC) AS rn
                  FROM fundamentals
            ) WHERE rn = 1
        ),
        {LATEST_QUARTER_CTE.strip()},
        {LATEST_MONTH_CTE.strip()}
        SELECT p.stock_id,
               s.name AS name,
               p.date AS latest_date,
               p.close AS latest_close,
               v.avg_volume AS avg_volume,
               CASE WHEN vs.base_bars = {spike_window} AND vs.spike_base > 0
                    THEN vs.spike_volume / vs.spike_base END AS volume_ratio,
               CASE WHEN p.prev_close > 0
                    THEN (p.close / p.prev_close - 1) * 100 END AS change_pct,
               f.report_date AS pe_date,
               f.pe_ratio AS pe_ratio,
               q.eps AS eps,
               q.period AS eps_period,
               m.revenue_yoy AS revenue_yoy,
               m.month AS revenue_month,
               f.dividend_yield AS dividend_yield
        FROM latest_price p
        LEFT JOIN recent_volume v ON p.stock_id = v.stock_id
        LEFT JOIN volume_spike vs ON p.stock_id = vs.stock_id
        LEFT JOIN latest_fundamentals f ON p.stock_id = f.stock_id
        LEFT JOIN latest_quarter q ON p.stock_id = q.stock_id
        LEFT JOIN latest_month m ON p.stock_id = m.stock_id
        LEFT JOIN stocks s ON p.stock_id = s.stock_id
        WHERE {where_clause}
        ORDER BY p.stock_id
    """

    try:
        df = pd.read_sql_query(query, conn, params=params)
    except pd.errors.DatabaseError:
        df = pd.DataFrame()
    finally:
        conn.close()

    if not df.empty:
        numeric_cols = [
            "latest_close", "avg_volume", "volume_ratio", "change_pct",
            "pe_ratio", "eps", "revenue_yoy", "dividend_yield",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

    return df
