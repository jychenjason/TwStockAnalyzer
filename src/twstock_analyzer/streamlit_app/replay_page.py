"""Replay 頁面：逐日回放與模擬交易的操作介面。

這一層刻意保持很薄——所有規則都在 Replay 服務層，這裡只負責收集輸入、
呼叫服務層、把結果畫出來。頁面不得自行查詢資料庫或計算任何業務規則。
"""

from __future__ import annotations

import time

import streamlit as st

from twstock_analyzer.replay import (
    CoverageError,
    create_session,
    delete_session,
    list_sessions,
    load_session,
)
from twstock_analyzer.visualization.charts import plot_kline

DISPLAY_BARS = 120

#: 疊在 K 線上的指標（價格尺度）與各自的顏色。
OVERLAY_INDICATORS = {
    "MA5": ("sma_5", "#e6550d"),
    "MA20": ("sma_20", "#3182bd"),
    "MA60": ("sma_60", "#31a354"),
    "MA240": ("sma_240", "#756bb1"),
}

#: 需要獨立面板的指標（非價格尺度）。
PANEL_INDICATORS = {
    "RSI": ["rsi_14"],
    "KD": ["kd_k", "kd_d"],
}


def _current_session(db_path: str):
    session_id = st.session_state.get("replay_session_id")
    if session_id is None:
        return None
    try:
        return load_session(session_id, db_path=db_path)
    except Exception:  # noqa: BLE001 - 任何載入失敗都該回到列表，而不是卡在壞掉的畫面
        st.session_state.pop("replay_session_id", None)
        return None


def _render_setup(db_path: str) -> None:
    existing = list_sessions(db_path=db_path)
    if existing:
        st.subheader("繼續之前的練習")
        labels = {
            f"#{s['id']} {s['name'] or s['stock_id']}（{s['start_date']} → {s['cursor']}）": s["id"]
            for s in existing
        }
        chosen = st.selectbox("選擇 Session", list(labels))
        cols = st.columns([1, 1, 6])
        if cols[0].button("繼續"):
            st.session_state["replay_session_id"] = labels[chosen]
            st.rerun()
        if cols[1].button("刪除", type="secondary"):
            delete_session(labels[chosen], db_path=db_path)
            st.rerun()
        st.divider()

    st.subheader("開始一段新的回放")
    col1, col2, col3 = st.columns(3)
    stock_id = col1.text_input("股票代號", value="2330")
    start_date = col2.date_input("起始日")
    initial_capital = col3.number_input(
        "初始資金", min_value=10_000, value=1_000_000, step=100_000
    )
    col4, col5 = st.columns(2)
    use_end = col4.checkbox("設定結束日")
    end_date = col5.date_input("結束日", disabled=not use_end)
    fee_discount = st.slider("券商手續費折數", 0.1, 1.0, 1.0, 0.05)

    if st.button("開始回放", type="primary"):
        try:
            session = create_session(
                stock_id.strip(),
                start_date.strftime("%Y-%m-%d"),
                initial_capital=float(initial_capital),
                fee_discount=float(fee_discount),
                end_date=end_date.strftime("%Y-%m-%d") if use_end else None,
                db_path=db_path,
            )
        except CoverageError as exc:
            st.error(str(exc))
            return
        st.session_state["replay_session_id"] = session.id
        st.rerun()


def _render_controls(session) -> float:
    """畫出播放控制列，回傳目前選定的播放速度。

    這裡**不做**自動播放的推進——推進要等整頁畫完才能做，見 `_autoplay_tick`。
    """
    cols = st.columns([1, 1, 1, 2, 2])

    if cols[0].button("◀ 上一日"):
        session.step_back()
        st.rerun()
    if cols[1].button("下一日 ▶", disabled=session.at_end):
        session.advance()
        st.rerun()

    playing = st.session_state.get("replay_playing", False)
    if cols[2].button("⏸ 暫停" if playing else "▶ 自動播放"):
        st.session_state["replay_playing"] = not playing
        st.rerun()

    speed = cols[3].select_slider("速度（秒／格）", [0.25, 0.5, 1.0, 2.0], value=1.0)
    target = cols[4].text_input("跳至日期", value="")
    if target:
        try:
            session.jump_to(target.strip())
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.rerun()

    return speed


def _autoplay_tick(session, speed: float) -> None:
    """自動播放時推進一格。必須在整頁畫完之後才呼叫。

    `st.rerun()` 會立刻中止這一輪腳本，所以若在畫圖之前推進，K 線與各面板永遠
    沒有機會被畫出來——畫面會卡在上一次完整執行的那一幀，直到使用者按下暫停，
    累積的日期才一次全部浮現。
    """
    if not st.session_state.get("replay_playing", False):
        return

    if session.at_end:
        st.session_state["replay_playing"] = False
        return

    time.sleep(speed)
    session.advance()
    st.rerun()


def _render_chart(session) -> None:
    visible = session.visible_indicators()
    chosen = st.multiselect("疊加指標", list(OVERLAY_INDICATORS), default=["MA5", "MA20"])
    unreliable = session.unreliable_indicators()

    shaky = [name for name in chosen if OVERLAY_INDICATORS[name][0] in unreliable]
    if shaky:
        st.warning(
            f"暖身資料不足，以下指標尚不可靠，畫面已略過：{'、'.join(shaky)}。"
            " 請以 update --start 回補更早的歷史資料。"
        )

    overlays = {
        OVERLAY_INDICATORS[name][0]: OVERLAY_INDICATORS[name][1]
        for name in chosen
        if OVERLAY_INDICATORS[name][0] not in unreliable
    }
    window = visible.tail(DISPLAY_BARS)
    figure = plot_kline(window, indicators=overlays or None)

    marks = session.visible_dividends()
    earliest = window["date"].min() if not window.empty else None
    for _, mark in marks.iterrows():
        if earliest and mark["date"] < earliest:
            continue
        amount = mark["cash_dividend"] or mark["stock_dividend"] or 0.0
        figure.add_vline(
            x=mark["date"],
            line_dash="dot",
            line_color="#d62728",
            annotation_text=f"除{mark['kind'] or '權息'} {amount:g}",
            annotation_position="top",
        )

    st.plotly_chart(figure, use_container_width=True)

    panel = st.selectbox("附加面板", ["（不顯示）", *PANEL_INDICATORS])
    if panel in PANEL_INDICATORS:
        columns = [c for c in PANEL_INDICATORS[panel] if c in window.columns]
        if columns:
            st.line_chart(window.set_index("date")[columns])


def _render_trading(session) -> None:
    st.markdown("#### 模擬交易")

    position = session.position()
    cols = st.columns(4)
    cols[0].metric("現金", f"{session.cash():,.0f}")
    cols[1].metric("持股", f"{position['shares']:,} 股")
    cols[2].metric("平均成本", f"{position['average_cost']:,.2f}")
    cols[3].metric("未實現損益", f"{session.unrealized_pnl():,.0f}")

    order_cols = st.columns([1, 1, 1, 3])
    lots = order_cols[0].number_input("張數", min_value=1, value=1, step=1)
    if order_cols[1].button("買進", disabled=session.at_end):
        session.place_order("buy", int(lots))
        st.rerun()
    if order_cols[2].button("賣出", disabled=session.at_end):
        session.place_order("sell", int(lots))
        st.rerun()
    order_cols[3].caption("委託於次一交易日開盤成交（ADR 0001）。無限價單。")

    pending = session.pending_orders()
    if pending:
        st.info(
            "掛著待成交："
            + "、".join(f"{o['side']} {o['lots']} 張（{o['placed_on']}）" for o in pending)
        )

    voided = session.voided_orders()
    if voided:
        st.warning("未成交（已作廢）：" + "；".join(o["void_reason"] for o in voided[-3:]))

    fills = session.fills()
    if fills:
        with st.expander(f"成交紀錄（{len(fills)} 筆）"):
            st.dataframe(
                [
                    {
                        "下單日": o["placed_on"],
                        "成交日": o["filled_on"],
                        "買賣": o["side"],
                        "張數": o["lots"],
                        "成交價": o["fill_price"],
                        "手續費": o["fee"],
                        "證交稅": o["tax"],
                    }
                    for o in fills
                ],
                use_container_width=True,
            )


def _render_institutional(session) -> None:
    st.markdown("#### 三大法人")

    coverage = session.institutional_coverage()
    if coverage["date_min"] is None:
        st.info("本地沒有這檔股票的籌碼資料。可用 update --type institutional --start 回補。")
        return
    if session.cursor < coverage["date_min"]:
        st.info(f"籌碼資料自 {coverage['date_min']} 起才有，目前的回放日期早於此。")
        return

    today = session.institutional_at(session.cursor)
    if today is None:
        st.warning(f"{session.cursor} 無籌碼資料（不是 0，是這一天沒有資料）。")
    else:
        cols = st.columns(3)
        cols[0].metric("外資買賣超", f"{today['foreign_net']:,.0f}")
        cols[1].metric("投信買賣超", f"{today['fund_net']:,.0f}")
        cols[2].metric("自營商買賣超", f"{today['dealer_net']:,.0f}")

    recent = session.visible_institutional().tail(30)
    if not recent.empty:
        st.bar_chart(recent.set_index("date")[["foreign_net"]])


def _render_summary(session) -> None:
    summary = session.summary()
    st.markdown("#### 績效摘要")

    cols = st.columns(4)
    cols[0].metric("總報酬", f"{summary['total_return'] * 100:.2f}%")
    cols[1].metric(
        "Buy & Hold",
        f"{summary['buy_hold_return'] * 100:.2f}%",
        delta=f"{(summary['total_return'] - summary['buy_hold_return']) * 100:.2f}%",
    )
    cols[2].metric(
        "勝率",
        "-" if summary["win_rate"] is None else f"{summary['win_rate'] * 100:.1f}%",
    )
    cols[3].metric("最大回撤", f"{summary['max_drawdown'] * 100:.2f}%")

    curve = session.equity_curve()
    if not curve.empty:
        st.line_chart(curve.set_index("date")[["equity"]])


def _render_admin(session, db_path: str) -> None:
    with st.expander("Session 管理"):
        name = st.text_input("名稱", value=session.name or "")
        if st.button("改名") and name.strip():
            session.rename(name.strip())
            st.rerun()

        target = st.text_input("倒退至（YYYY-MM-DD）", value="")
        if target:
            preview = session.rewind_preview(target.strip())
            st.warning(
                f"倒退至 {preview['target']} 會作廢 {preview['orders_discarded']} 筆委託"
                f"（其中 {preview['fills_undone']} 筆已成交）。此動作無法復原。"
            )
            if st.button("確認倒退", type="primary"):
                session.rewind(target.strip())
                st.rerun()

        if st.button("結束並回到列表"):
            st.session_state.pop("replay_session_id", None)
            st.session_state.pop("replay_playing", None)
            st.rerun()


def _render_session(session, db_path: str) -> None:
    header = st.columns([3, 1, 1])
    header[0].subheader(f"{session.stock_id} — {session.cursor}")
    header[1].metric("起始日", session.start_date)
    header[2].metric("結束日", session.end_date or "資料末端")

    speed = _render_controls(session)
    if session.at_end:
        st.info("已播到終點。下方為本次練習的績效摘要。")

    _render_chart(session)
    _render_trading(session)
    _render_institutional(session)
    if session.at_end or session.fills():
        _render_summary(session)
    _render_admin(session, db_path)

    # 整頁畫完之後才推進，否則 st.rerun() 會中止腳本、讓下方內容永遠畫不出來。
    _autoplay_tick(session, speed)


def render_replay(db_path) -> None:
    """Replay 頁面進入點。"""
    st.header("🎬 回放練習")

    path = str(db_path)
    session = _current_session(path)
    if session is None:
        _render_setup(path)
        st.caption("回放只讀本地資料。資料不足時請先以 update --start 回補。")
        return

    _render_session(session, path)
