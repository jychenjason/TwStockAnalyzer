"""Streamlit dashboard for TwStockAnalyzer."""

import subprocess
import sys
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from twstock_analyzer.analysis.technical import TechnicalAnalyzer
from twstock_analyzer.db.schema import TABLE_DEFS
from twstock_analyzer.screening.financials import ensure_financial_tables
from twstock_analyzer.streamlit_app.replay_page import render_replay


def _get_db_path() -> Path:
    """Return the database path, resolving relative to project root."""
    db = Path("data/twstock.db")
    if db.exists():
        return db
    # Try project root siblings
    for candidate in [
        Path(__file__).parent.parent.parent.parent / "data" / "twstock.db",
        Path.cwd() / "data" / "twstock.db",
    ]:
        if candidate.exists():
            return candidate
    return db


#: 價格表格的欄位中文對照。
PRICE_COLUMN_LABELS = {
    "stock_id": "股票代號",
    "date": "日期",
    "Date": "日期",
    "open": "開盤",
    "high": "最高",
    "low": "最低",
    "close": "收盤",
    "volume": "成交量",
    "adj_close": "還原收盤",
    "fetched_at": "抓取時間",
}

PAGE_ICONS = {
    "dashboard": "📈",
    "detail": "🔍",
    "data_manager": "📦",
}


def render_overview(db_path: Path) -> None:
    """Dashboard: stock overview with latest dates and basic stats."""
    st.header("📈 股票總覽")

    if not db_path.exists():
        st.warning(
            "📭 資料庫尚未建立。\n\n"
            "請先執行：\n"
            "```bash\n"
            "python -m twstock_analyzer.cli.main update --all\n"
            "```"
        )
        return

    conn = sqlite3.connect(str(db_path))
    try:
        # Stock list with latest date
        stocks_df = pd.read_sql(
            """
            SELECT stock_id,
                   MAX(date) AS latest_date,
                   MIN(date) AS earliest_date
              FROM daily_prices
             GROUP BY stock_id
             ORDER BY stock_id
            """,
            conn,
        )

        if stocks_df.empty:
            st.info("⚠️ 資料庫中尚無股票資料。請先執行資料更新。")
            return

        # Show summary cards
        total_stocks = len(stocks_df)
        st.metric("總股票數", total_stocks)

        # Full table
        st.subheader("股票清單")
        listing = stocks_df.rename(columns={
            "stock_id": "股票代號",
            "latest_date": "最新資料日",
            "earliest_date": "最早資料日",
        })
        st.dataframe(listing.reset_index(drop=True), use_container_width=True)

        # Quick stats per stock
        st.subheader("各股最新資料摘要")
        latest_ids = stocks_df["stock_id"].tolist()
        if latest_ids:
            placeholders = ",".join("?" for _ in latest_ids)
            stats_query = f"""
                SELECT dp.stock_id, dp.date, dp.open, dp.high, dp.low,
                       dp.close, dp.volume
                  FROM daily_prices dp
                  INNER JOIN (
                      SELECT stock_id, MAX(date) AS max_date
                        FROM daily_prices
                       WHERE stock_id IN ({placeholders})
                       GROUP BY stock_id
                  ) latest
                    ON dp.stock_id = latest.stock_id AND dp.date = latest.max_date
            """
            stats_df = pd.read_sql(stats_query, conn, params=latest_ids)
            display_cols = ["stock_id", "date", "open", "high", "low", "close", "volume"]
            available_cols = [c for c in display_cols if c in stats_df.columns]
            summary = stats_df[available_cols].rename(columns=PRICE_COLUMN_LABELS)
            st.dataframe(summary.reset_index(drop=True), use_container_width=True)
    finally:
        conn.close()


def render_detail(db_path: Path) -> None:
    """Stock detail: chart + technical indicators."""
    st.header("🔍 個股分析")

    stock_id = st.text_input("股票代碼", value="2330", max_chars=4)

    if not stock_id or not stock_id.isdigit() or len(stock_id) != 4:
        st.error("⛔ 請輸入有效的 4 位數股票代碼（例如：2330）")
        return

    if st.button("🔄 更新資料"):
        with st.spinner("更新中…"):
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "twstock_analyzer.cli.main",
                     "update", "--stock", stock_id],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    st.success("資料已更新！")
                    st.rerun()
                else:
                    st.error(f"更新失敗：{result.stderr}")
            except subprocess.TimeoutExpired:
                st.error("更新逾時")
            except Exception as e:
                st.error(f"錯誤：{e}")

    if not db_path.exists():
        st.warning("📭 資料庫尚未建立。")
        return

    conn = sqlite3.connect(str(db_path))
    try:
        df = pd.read_sql(
            "SELECT * FROM daily_prices WHERE stock_id = ? ORDER BY date ASC",
            conn,
            params=(stock_id,),
        )
    finally:
        conn.close()

    if df.empty:
        st.info(f"📭 找不到股票 {stock_id} 的資料。")
        return

    # Rename columns for TechnicalAnalyzer
    df.rename(columns={"date": "Date"}, inplace=True)
    required = ["open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in required):
        st.error(f"⛔ 缺少必要欄位: {[c for c in required if c not in df.columns]}")
        return

    # Technical indicators
    analyzer = TechnicalAnalyzer()
    analyzed_df = analyzer.calculate_all(df)

    tab_chart, tab_indicators, tab_data = st.tabs([
        "📊 K線圖",
        "📐 技術指標",
        "📋 原始資料",
    ])

    with tab_chart:
        st.subheader(f"{stock_id} 收盤價走勢")
        st.line_chart(analyzed_df.set_index("Date")[["close"]], use_container_width=True)

        st.subheader("均線 (SMA)")
        sma_cols = [c for c in analyzed_df.columns if c.startswith("sma_")]
        if sma_cols:
            sma_df = analyzed_df.set_index("Date")[sma_cols]
            st.line_chart(sma_df, use_container_width=True)

        st.subheader("布林帶")
        bb_cols = [c for c in analyzed_df.columns if c.startswith("bb_")]
        if bb_cols:
            st.line_chart(analyzed_df.set_index("Date")[bb_cols], use_container_width=True)

    with tab_indicators:
        indicator_groups = {
            "RSI 相對強弱指標": [c for c in analyzed_df.columns if c.startswith("rsi_")],
            "MACD 指數平滑異同移動平均": [c for c in analyzed_df.columns if c.startswith("macd_")],
            "KD 隨機指標": ["kd_k", "kd_d"] if "kd_k" in analyzed_df.columns else [],
        }
        for group_name, cols in indicator_groups.items():
            if cols:
                st.subheader(group_name)
                st.dataframe(analyzed_df[["Date"] + cols].tail(30), use_container_width=True)
                st.line_chart(analyzed_df.set_index("Date")[cols], use_container_width=True)

    with tab_data:
        st.subheader(f"{stock_id} 每日行情")
        raw = analyzed_df.tail(100).rename(columns=PRICE_COLUMN_LABELS)
        st.dataframe(raw, use_container_width=True)

    # 基本面
    # 三種頻率、三張表：本益比／殖利率是每日（fundamentals）、每股盈餘是每季
    # （quarterly_financials）、營收年增率是每月（monthly_revenue）。合成一列會
    # 讓不同時間的數字並排成同一天的資料。
    with st.expander("📊 基本面", expanded=False):
        fund_conn = sqlite3.connect(str(db_path))
        try:
            ensure_financial_tables(fund_conn)
            valuation_df = pd.read_sql_query(
                "SELECT report_date, pe_ratio, dividend_yield"
                " FROM fundamentals WHERE stock_id = ? ORDER BY report_date",
                fund_conn, params=(stock_id,)
            )
            eps_df = pd.read_sql_query(
                "SELECT period, eps, revenue AS quarter_revenue"
                " FROM quarterly_financials WHERE stock_id = ? ORDER BY period",
                fund_conn, params=(stock_id,)
            )
            revenue_df = pd.read_sql_query(
                "SELECT month, revenue, revenue_yoy"
                " FROM monthly_revenue WHERE stock_id = ? ORDER BY month",
                fund_conn, params=(stock_id,)
            )
        except Exception as exc:
            st.error(f"⛔ 讀取基本面資料失敗：{exc}")
            valuation_df = eps_df = revenue_df = pd.DataFrame()
        finally:
            fund_conn.close()

        if valuation_df.empty and eps_df.empty and revenue_df.empty:
            st.info(
                f"📭 尚無 {stock_id} 的基本面資料。請先執行：\n\n"
                "```bash\n"
                "python -m twstock_analyzer.cli.main update --stock all --type fundamental\n"
                "python -m twstock_analyzer.cli.main update --stock all --type financials\n"
                "python -m twstock_analyzer.cli.main update --stock all --type revenue\n"
                "```"
            )
        else:
            def _last(frame: pd.DataFrame, column: str):
                if frame.empty or column not in frame.columns:
                    return None
                values = frame[column].dropna()
                return values.iloc[-1] if len(values) else None

            def _num(value, fmt: str, suffix: str = "") -> str:
                return "—" if value is None or pd.isna(value) else format(value, fmt) + suffix

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("本益比", _num(_last(valuation_df, "pe_ratio"), ".2f"))
            col2.metric("每股盈餘", _num(_last(eps_df, "eps"), ".2f"))
            col3.metric("營收年增率", _num(_last(revenue_df, "revenue_yoy"), ".2f", "%"))
            col4.metric("殖利率", _num(_last(valuation_df, "dividend_yield"), ".2f", "%"))

            # 每個數字標明自己是哪一期的——這是三個不同的時間點。
            stamps = []
            if not valuation_df.empty:
                stamps.append(f"本益比／殖利率 {valuation_df['report_date'].iloc[-1]}")
            if not eps_df.empty:
                stamps.append(f"每股盈餘 {eps_df['period'].iloc[-1]}（累計）")
            if not revenue_df.empty:
                stamps.append(f"營收 {revenue_df['month'].iloc[-1]}")
            if stamps:
                st.caption("　|　".join(stamps))

            eps_chart = eps_df[["period", "eps"]].dropna() if not eps_df.empty else pd.DataFrame()
            if len(eps_chart) > 1:
                st.markdown("**每股盈餘走勢（累計至各季）**")
                eps_chart = eps_chart.set_index("period")
                eps_chart.columns = ["每股盈餘"]
                st.line_chart(eps_chart)

            yoy_chart = (
                revenue_df[["month", "revenue_yoy"]].dropna()
                if not revenue_df.empty else pd.DataFrame()
            )
            if len(yoy_chart) > 1:
                st.markdown("**月營收年增率走勢**")
                yoy_chart = yoy_chart.set_index("month")
                yoy_chart.columns = ["年增率 %"]
                st.line_chart(yoy_chart)

    # 三大法人
    with st.expander("🏛️ 三大法人", expanded=True):
        inst_conn = sqlite3.connect(str(db_path))
        try:
            inst_df = pd.read_sql_query(
                "SELECT date, foreign_buy, foreign_sell, foreign_net,"
                " fund_net, dealer_net, total_net"
                " FROM institutional_trading WHERE stock_id = ? ORDER BY date",
                inst_conn, params=(stock_id,)
            )
        except Exception as exc:
            # 讀取失敗與「這檔沒有資料」是兩件事，不能都說成沒有資料——
            # 之前那個 except 把 no such table 吞掉，害每一檔都顯示沒資料。
            st.error(f"⛔ 讀取三大法人資料失敗：{exc}")
            inst_df = pd.DataFrame()
        finally:
            inst_conn.close()

        if inst_df.empty:
            st.info(
                f"📭 尚無 {stock_id} 的三大法人資料。請先執行：\n\n"
                "```bash\n"
                f"python -m twstock_analyzer.cli.main update --stock {stock_id} --type institutional\n"
                "```"
            )
        else:
            latest_inst = inst_df.iloc[-1]
            st.caption(
                f"最新資料日期 {latest_inst['date']}　"
                f"（{inst_df['date'].min()} 起共 {len(inst_df)} 筆；單位：張）"
            )

            def _lots(value) -> str:
                return "—" if pd.isna(value) else f"{value / 1000:,.0f}"

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("外資買賣超", _lots(latest_inst.get("foreign_net")))
            col2.metric("投信買賣超", _lots(latest_inst.get("fund_net")))
            col3.metric("自營商買賣超", _lots(latest_inst.get("dealer_net")))
            col4.metric("三大法人合計", _lots(latest_inst.get("total_net")))

            st.markdown("**外資買賣張數**")
            buy_sell = inst_df[["date", "foreign_buy", "foreign_sell"]].dropna().tail(60)
            if not buy_sell.empty:
                buy_sell = buy_sell.set_index("date") / 1000
                buy_sell.columns = ["外資買進", "外資賣出"]
                st.line_chart(buy_sell)

            st.markdown("**三大法人買賣超走勢（近 60 個交易日）**")
            trend = inst_df[["date", "foreign_net", "fund_net", "dealer_net"]].dropna().tail(60)
            if not trend.empty:
                trend = trend.set_index("date") / 1000
                trend.columns = ["外資", "投信", "自營商"]
                st.bar_chart(trend)

            st.markdown("**明細（近 30 個交易日）**")
            detail = inst_df.tail(30).iloc[::-1].copy()
            for column in ("foreign_buy", "foreign_sell", "foreign_net", "fund_net", "dealer_net", "total_net"):
                detail[column] = detail[column] / 1000
            detail.columns = ["日期", "外資買進", "外資賣出", "外資買賣超", "投信買賣超", "自營商買賣超", "合計"]
            st.dataframe(detail.reset_index(drop=True), use_container_width=True)


def render_data_manager(db_path: Path) -> None:
    """Data management: cache metadata and table info."""
    st.header("📦 資料管理")

    if not db_path.exists():
        st.warning("📭 資料庫尚未建立。")
        return

    conn = sqlite3.connect(str(db_path))
    try:
        # Cache metadata
        st.subheader("快取資訊")
        cache_df = pd.read_sql("SELECT * FROM cache_metadata ORDER BY fetched_at DESC", conn)
        if cache_df.empty:
            st.info("⚠️ 暫無快取資料。")
        else:
            st.dataframe(cache_df, use_container_width=True)

        # Table info
        st.subheader("資料表結構")
        table_rows = []
        for table_name, ddl in TABLE_DEFS.items():
            try:
                count = pd.read_sql(f"SELECT COUNT(*) AS cnt FROM [{table_name}]", conn)["cnt"].iloc[0]
            except Exception:
                count = 0
            table_rows.append({"資料表": table_name, "筆數": count})
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True)

        # Stock list
        st.subheader("各股快取狀態")
        latest = pd.read_sql(
            """
            SELECT stock_id, MIN(date) AS earliest, MAX(date) AS latest
              FROM daily_prices
             GROUP BY stock_id
             ORDER BY stock_id
            """,
            conn,
        )
        if latest.empty:
            st.info("⚠️ 尚無股票資料。")
        else:
            coverage = latest.rename(columns={
                "stock_id": "股票代號",
                "earliest": "最早資料日",
                "latest": "最新資料日",
            })
            st.dataframe(coverage, use_container_width=True)

        # Refresh button
        if st.button("🔄 重新整理"):
            st.rerun()
    finally:
        conn.close()


def main() -> None:
    """Entry point for the Streamlit app."""
    st.set_page_config(page_title="TwStockAnalyzer", layout="wide", page_icon="📊")
    st.title("📊 臺股分析儀表板")

    db_path = _get_db_path()

    page = st.sidebar.radio(
        "導航",
        ["📈 總覽", "🔍 個股分析", "🎬 回放練習", "📦 資料管理"],
    )

    if page == "📈 總覽":
        render_overview(db_path)
    elif page == "🔍 個股分析":
        render_detail(db_path)
    elif page == "🎬 回放練習":
        render_replay(db_path)
    elif page == "📦 資料管理":
        render_data_manager(db_path)


if __name__ == "__main__":
    main()
