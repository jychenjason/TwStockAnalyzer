"""Replay Session 與 Cursor 的服務層。

此模組是 Replay 的唯一接縫：建立 Session、推進 Cursor、讀取 Cursor 當日可見的資料。
它不匯入任何介面框架，也不發出任何網路請求——回放只讀本地資料。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pandas as pd

DEFAULT_INITIAL_CAPITAL = 1_000_000.0

#: 一張 = 1000 股。模擬交易只以整張為單位。
SHARES_PER_LOT = 1000

#: 每個指標需要多少根 K 棒才算得準。Warm-up 短於此值時該指標不可靠。
INDICATOR_LOOKBACK: dict[str, int] = {
    "sma_5": 5,
    "sma_10": 10,
    "sma_20": 20,
    "sma_60": 60,
    "sma_120": 120,
    "sma_240": 240,
    "ema_5": 5,
    "ema_10": 10,
    "ema_20": 20,
    "ema_60": 60,
    "macd": 35,  # slow 26 + signal 9
    "rsi_14": 14,
    "kd": 8,  # k 5 + d 3
    "bb": 20,
}


class CoverageError(Exception):
    """本地資料的涵蓋範圍不足以支撐這條時間軸。"""


class SessionNotFoundError(Exception):
    """指定的 Replay Session 不存在。"""


def _resolve_db_path(db_path: str | None) -> str:
    from ..db import repository

    return db_path or repository._DEFAULT_DB_PATH


def ensure_replay_schema(db_path: str | None = None) -> None:
    """確保 Replay 需要的資料表存在。

    既有的資料庫是在 Replay 之前建立的，不會有這些表；使用者不該為了開始練習
    而先去跑一次資料更新指令。DDL 都是 IF NOT EXISTS，重複呼叫無害。
    """
    from ..db.schema import TABLE_DEFS

    conn = sqlite3.connect(_resolve_db_path(db_path))
    try:
        for table in ("replay_sessions", "replay_orders", "dividends"):
            conn.execute(TABLE_DEFS[table])
        conn.commit()
    finally:
        conn.close()


def _backfill_hint(stock_id: str, start_date: str) -> str:
    return (
        "請先回補： python -m twstock_analyzer.cli.main update"
        f" --stock {stock_id} --start {start_date}"
    )


def _trading_days(stock_id: str, db_path: str | None) -> list[str]:
    """該股票自己有資料的交易日，由早到晚。停牌日不存在於此序列中。"""
    conn = sqlite3.connect(_resolve_db_path(db_path))
    try:
        rows = conn.execute(
            "SELECT date FROM daily_prices WHERE stock_id = ? ORDER BY date",
            (stock_id,),
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


@dataclass
class ReplaySession:
    """一次回放練習：一檔股票、一個起始日，以及當下的 Cursor。"""

    id: int
    stock_id: str
    start_date: str
    cursor: str
    initial_capital: float = DEFAULT_INITIAL_CAPITAL
    fee_discount: float = 1.0
    end_date: str | None = None
    name: str | None = None
    _trading_days: list[str] = field(default_factory=list, repr=False)
    _db_path: str | None = field(default=None, repr=False)

    @property
    def last_playable_day(self) -> str:
        """可播放的最後一個交易日：結束日（若有設定）或本地資料的末端。"""
        if not self.end_date:
            return self._trading_days[-1]
        within = [d for d in self._trading_days if d <= self.end_date]
        return within[-1] if within else self._trading_days[-1]

    @property
    def at_end(self) -> bool:
        """Cursor 是否已在可播放的最後一個交易日。"""
        return self.cursor >= self.last_playable_day

    def advance(self) -> str:
        """把 Cursor 推進到下一個交易日，並結算前一日掛著的 Order。

        走的是這檔股票自己的交易日序列，停牌日不佔一格。已在末端時原地不動，
        不拋出例外——播到盡頭是正常結局，不是錯誤。
        """
        if self.at_end:
            return self.cursor
        position = self._trading_days.index(self.cursor)
        self.cursor = self._trading_days[position + 1]
        self._save_cursor()
        self._settle_pending()
        return self.cursor

    def jump_to(self, date: str) -> str:
        """直接跳到指定日期。

        往前跳等於連按數次「下一日」——中途每一天照常結算，掛著的委託不會被跳過去
        而永遠不成交。往回跳等於 Rewind，該日之後的一切作廢。
        """
        if date > self._trading_days[-1]:
            raise ValueError(
                f"{date} 超出可播放範圍（本地資料只到 {self._trading_days[-1]}）"
            )
        target = self._resolve_trading_day(date)
        if target <= self.cursor:
            return self.rewind(target)
        while self.cursor < target and not self.at_end:
            self.advance()
        return self.cursor

    def step_back(self) -> str:
        """退回前一個交易日。與 Rewind 同義，只是固定退一格。"""
        position = self._trading_days.index(self.cursor)
        if position == 0:
            return self.cursor
        return self.rewind(self._trading_days[position - 1])

    # ------------------------------------------------------------------
    # 績效
    # ------------------------------------------------------------------

    def equity_curve(self) -> pd.DataFrame:
        """自起始日至 Cursor 的每日權益（現金 + 部位市值）。"""
        prices = self.visible_prices()
        prices = prices[prices["date"] >= self.start_date]
        fills = self.fills()

        rows = []
        for _, bar in prices.iterrows():
            day = bar["date"]
            cash = float(self.initial_capital)
            shares = 0
            cost_basis = 0.0
            for order in fills:
                if order["filled_on"] > day:
                    continue
                if order["side"] == "buy":
                    cash -= order["amount"] + order["fee"]
                    shares += order["shares"]
                    cost_basis += order["amount"] + order["fee"]
                else:
                    average = cost_basis / shares if shares else 0.0
                    cash += order["amount"] - order["fee"] - order["tax"]
                    cost_basis -= average * order["shares"]
                    shares -= order["shares"]
            rows.append({"date": day, "equity": cash + shares * float(bar["close"])})

        return pd.DataFrame(rows)

    def buy_and_hold(self) -> dict:
        """對照組：在同樣的規則下，起始日之後的第一個開盤全押、抱到最後一天收盤。

        對照組受的限制與使用者相同——看得到起始日收盤才能行動，所以進場價是
        次一交易日的開盤，而且一樣扣手續費與證交稅。
        """
        from .costs import buy_cost, sell_proceeds

        start_index = self._trading_days.index(
            next(d for d in self._trading_days if d >= self.start_date)
        )
        entry_index = min(start_index + 1, len(self._trading_days) - 1)
        entry_day = self._trading_days[entry_index]
        exit_day = self.cursor

        if exit_day <= entry_day:
            return {"lots": 0, "final_equity": float(self.initial_capital), "return": 0.0}

        entry_price = self._open_price(entry_day)
        exit_price = float(
            self.visible_prices().set_index("date").loc[exit_day, "close"]
        )

        lots = 0
        while buy_cost(entry_price, (lots + 1) * SHARES_PER_LOT, self.fee_discount)["total"] <= self.initial_capital:
            lots += 1

        if lots == 0:
            return {"lots": 0, "final_equity": float(self.initial_capital), "return": 0.0}

        entry = buy_cost(entry_price, lots * SHARES_PER_LOT, self.fee_discount)
        exit_ = sell_proceeds(exit_price, lots * SHARES_PER_LOT, self.fee_discount)
        final = self.initial_capital - entry["total"] + exit_["total"]
        return {
            "lots": lots,
            "final_equity": final,
            "return": final / self.initial_capital - 1,
        }

    def summary(self) -> dict:
        """本次練習的績效摘要，含 Buy & Hold 對照。

        沒有這個對照，使用者無法分辨自己是選股高手，還是只是搭上了順風車。
        """
        account = self._account()
        prices = self.visible_prices()
        close = float(prices["close"].iloc[-1]) if not prices.empty else 0.0
        final_equity = account["cash"] + account["shares"] * close

        sells = [o for o in self.fills() if o["side"] == "sell"]
        wins = 0
        shares = 0
        cost_basis = 0.0
        for order in self.fills():
            if order["side"] == "buy":
                shares += order["shares"]
                cost_basis += order["amount"] + order["fee"]
            else:
                average = cost_basis / shares if shares else 0.0
                proceeds = order["amount"] - order["fee"] - order["tax"]
                if proceeds > average * order["shares"]:
                    wins += 1
                cost_basis -= average * order["shares"]
                shares -= order["shares"]

        curve = self.equity_curve()
        if curve.empty:
            max_drawdown = 0.0
        else:
            peak = curve["equity"].cummax()
            max_drawdown = float((curve["equity"] / peak - 1).min())

        buy_hold = self.buy_and_hold()
        return {
            "final_equity": final_equity,
            "total_return": final_equity / self.initial_capital - 1,
            "realized_pnl": account["realized_pnl"],
            "unrealized_pnl": self.unrealized_pnl(),
            "trades": len(sells),
            "win_rate": (wins / len(sells)) if sells else None,
            "max_drawdown": max_drawdown,
            "buy_hold_return": buy_hold["return"],
            "buy_hold_final_equity": buy_hold["final_equity"],
        }

    def rename(self, name: str) -> None:
        """替這次練習改個認得出意圖的名字。"""
        self.name = name
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            conn.execute("UPDATE replay_sessions SET name = ? WHERE id = ?", (name, self.id))
            conn.commit()
        finally:
            conn.close()

    def rewind_preview(self, date: str) -> dict:
        """倒退到指定日會失去什麼——供介面在執行前提示使用者。"""
        target = self._resolve_trading_day(date)
        orders = self.orders()
        discarded = [o for o in orders if o["placed_on"] >= target]
        return {
            "target": target,
            "orders_discarded": len(discarded),
            "fills_undone": sum(1 for o in discarded if o["status"] == "filled"),
        }

    def rewind(self, date: str) -> str:
        """把 Cursor 退回較早的交易日，並作廢那天以後的一切。

        倒退到 D 的語意是「回到 D 這一天重新決定」：D 當天（含）之後送出的 Order
        一律消失。D 之前送出、在 D 開盤成交的 Order 保留——那筆成交發生在 D 的
        任何決定之前。否則會出現「已退回三月卻持有五月成交部位」的時間悖論。
        """
        target = self._resolve_trading_day(date)
        if target > self.cursor:
            raise ValueError(f"倒退目標 {target} 晚於目前的 {self.cursor}——倒退只能往回走")

        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            conn.execute(
                "DELETE FROM replay_orders WHERE session_id = ? AND placed_on >= ?",
                (self.id, target),
            )
            conn.commit()
        finally:
            conn.close()

        self.cursor = target
        self._save_cursor()
        return self.cursor

    def _resolve_trading_day(self, date: str) -> str:
        """把任意日期對應到不晚於它的最後一個交易日。"""
        earlier = [d for d in self._trading_days if d <= date]
        if not earlier:
            raise ValueError(f"{date} 早於這檔股票的第一個交易日 {self._trading_days[0]}")
        return earlier[-1]

    # ------------------------------------------------------------------
    # 模擬交易
    # ------------------------------------------------------------------

    def place_order(self, side: str, lots: int) -> int:
        """在 Cursor 當日送出一筆買賣意圖，於次一交易日開盤成交。

        沒有限價單也沒有委託有效期——只有四個價位的日 K 無法支撐觸價判定。
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"未知的買賣別：{side}")
        if lots <= 0:
            raise ValueError("張數必須大於 0")

        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            cur = conn.execute(
                "INSERT INTO replay_orders"
                " (session_id, side, lots, shares, placed_on, status)"
                " VALUES (?, ?, ?, ?, ?, 'pending')",
                (self.id, side, lots, lots * SHARES_PER_LOT, self.cursor),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def orders(self, status: str | None = None) -> list[dict]:
        """這個 Session 的委託紀錄，由早到晚。"""
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            sql = "SELECT * FROM replay_orders WHERE session_id = ?"
            params: tuple = (self.id,)
            if status:
                sql += " AND status = ?"
                params += (status,)
            sql += " ORDER BY id"
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def pending_orders(self) -> list[dict]:
        return self.orders("pending")

    def fills(self) -> list[dict]:
        return self.orders("filled")

    def voided_orders(self) -> list[dict]:
        return self.orders("voided")

    def cash(self) -> float:
        """目前現金餘額。由成交紀錄推導，不另存一份。"""
        return self._account()["cash"]

    def position(self) -> dict:
        """目前部位：股數與加權平均成本（含買進手續費）。"""
        account = self._account()
        return {"shares": account["shares"], "average_cost": account["average_cost"]}

    def realized_pnl(self) -> float:
        """已實現損益：賣出淨額扣掉該批部位的加權平均成本。"""
        return self._account()["realized_pnl"]

    def unrealized_pnl(self) -> float:
        """未實現損益：目前部位以 Cursor 當日收盤價評價後與成本的差額。"""
        account = self._account()
        if not account["shares"]:
            return 0.0
        visible = self.visible_prices()
        close = float(visible["close"].iloc[-1])
        return close * account["shares"] - account["cost_basis"]

    def _account(self) -> dict:
        """把成交紀錄重播一次，推導出現金、部位與已實現損益。"""
        cash = float(self.initial_capital)
        shares = 0
        cost_basis = 0.0
        realized = 0.0

        for order in self.fills():
            if order["side"] == "buy":
                paid = order["amount"] + order["fee"]
                cash -= paid
                shares += order["shares"]
                cost_basis += paid
            else:
                proceeds = order["amount"] - order["fee"] - order["tax"]
                average = cost_basis / shares if shares else 0.0
                released = average * order["shares"]
                cash += proceeds
                shares -= order["shares"]
                cost_basis -= released
                realized += proceeds - released

        return {
            "cash": cash,
            "shares": shares,
            "cost_basis": cost_basis,
            "average_cost": (cost_basis / shares) if shares else 0.0,
            "realized_pnl": realized,
        }

    def _open_price(self, date: str) -> float:
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            row = conn.execute(
                "SELECT open FROM daily_prices WHERE stock_id = ? AND date = ?",
                (self.stock_id, date),
            ).fetchone()
        finally:
            conn.close()
        return float(row[0])

    def _settle_pending(self) -> None:
        """以 Cursor 當日的開盤價結算所有掛著的 Order。"""
        from .costs import buy_cost, sell_proceeds

        pending = [o for o in self.pending_orders() if o["placed_on"] < self.cursor]
        if not pending:
            return

        price = self._open_price(self.cursor)
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            for order in pending:
                account = self._account()
                if order["side"] == "buy":
                    costs = buy_cost(price, order["shares"], self.fee_discount)
                    if costs["total"] > account["cash"]:
                        conn.execute(
                            "UPDATE replay_orders SET status = 'voided', void_reason = ?"
                            " WHERE id = ?",
                            (
                                (
                                    f"資金不足：需要 {costs['total']:,.0f} 元，"
                                    f"帳上只有 {account['cash']:,.0f} 元"
                                ),
                                order["id"],
                            ),
                        )
                        conn.commit()
                        continue
                else:
                    if order["shares"] > account["shares"]:
                        conn.execute(
                            "UPDATE replay_orders SET status = 'voided', void_reason = ?"
                            " WHERE id = ?",
                            (
                                (
                                    f"持股不足：欲賣出 {order['shares']} 股，"
                                    f"帳上只有 {account['shares']} 股"
                                ),
                                order["id"],
                            ),
                        )
                        conn.commit()
                        continue
                    costs = sell_proceeds(price, order["shares"], self.fee_discount)

                conn.execute(
                    "UPDATE replay_orders SET status = 'filled', filled_on = ?,"
                    " fill_price = ?, amount = ?, fee = ?, tax = ? WHERE id = ?",
                    (
                        self.cursor,
                        price,
                        costs["amount"],
                        costs["fee"],
                        costs["tax"],
                        order["id"],
                    ),
                )
                conn.commit()
        finally:
            conn.close()

    def visible_prices(self) -> pd.DataFrame:
        """Cursor 當日及之前的日 K，含起始日之前的 Warm-up 區間。

        回傳結果永遠不含晚於 Cursor 的任何一天。
        """
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            return pd.read_sql_query(
                "SELECT date, open, high, low, close, volume FROM daily_prices"
                " WHERE stock_id = ? AND date <= ? ORDER BY date",
                conn,
                params=(self.stock_id, self.cursor),
            )
        finally:
            conn.close()

    def visible_institutional(self) -> pd.DataFrame:
        """Cursor 當日及之前的三大法人資料。只回傳真正存在的日子。

        沒有資料的日子不會被補成 0——把缺口畫成 0 會被誤讀成法人當天沒有進出。
        """
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            return pd.read_sql_query(
                "SELECT date, foreign_buy, foreign_sell, foreign_net,"
                " fund_net, dealer_net, total_net FROM institutional_trading"
                " WHERE stock_id = ? AND date <= ? ORDER BY date",
                conn,
                params=(self.stock_id, self.cursor),
            )
        finally:
            conn.close()

    def institutional_at(self, date: str) -> dict | None:
        """某一天的三大法人資料；該天沒有資料時回傳 None，而不是一列 0。"""
        rows = self.visible_institutional()
        match = rows[rows["date"] == date]
        if match.empty:
            return None
        return match.iloc[-1].to_dict()

    def institutional_coverage(self) -> dict:
        """本地三大法人資料的涵蓋範圍；完全沒有資料時兩端皆為 None。"""
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            row = conn.execute(
                "SELECT MIN(date), MAX(date), COUNT(*) FROM institutional_trading"
                " WHERE stock_id = ?",
                (self.stock_id,),
            ).fetchone()
        finally:
            conn.close()
        return {"date_min": row[0], "date_max": row[1], "row_count": row[2] or 0}

    def visible_dividends(self) -> pd.DataFrame:
        """Cursor 當日及之前的除權息日。

        價格一律用原始成交價、不做還原（ADR 0002），所以持股跨過除權息日時帳面會
        憑空少一段；這些標記就是讓那根跳空不被誤讀成崩盤的東西。
        """
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            return pd.read_sql_query(
                "SELECT date, cash_dividend, stock_dividend, kind FROM dividends"
                " WHERE stock_id = ? AND date <= ? ORDER BY date",
                conn,
                params=(self.stock_id, self.cursor),
            )
        finally:
            conn.close()

    def visible_indicators(self) -> pd.DataFrame:
        """Cursor 當日及之前的日 K，附上技術指標欄位。

        指標是對可見區間（含 Warm-up）整段計算後截斷的結果——對滾動與遞迴指標
        而言，這與逐格重算等價，但避免了自動播放時的重複運算。
        """
        from ..analysis.technical import TechnicalAnalyzer

        return TechnicalAnalyzer().calculate_all(self.visible_prices())

    def unreliable_indicators(self) -> list[str]:
        """Warm-up 不足以支撐的指標名稱。畫面應把它們標示為不可靠而非照畫。"""
        available = len(self.visible_prices())
        return sorted(name for name, needed in INDICATOR_LOOKBACK.items() if needed > available)

    def _save_cursor(self) -> None:
        conn = sqlite3.connect(_resolve_db_path(self._db_path))
        try:
            conn.execute(
                "UPDATE replay_sessions SET cursor = ? WHERE id = ?", (self.cursor, self.id)
            )
            conn.commit()
        finally:
            conn.close()


def create_session(
    stock_id: str,
    start_date: str,
    *,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    fee_discount: float = 1.0,
    end_date: str | None = None,
    name: str | None = None,
    db_path: str | None = None,
) -> ReplaySession:
    """以一檔股票與一個起始日開啟一條 Replay Session 的時間軸。

    起始日若不是該股票的交易日（週末、假日、停牌），Cursor 落在其後的第一個交易日。
    本地資料不足以支撐這條時間軸時拒絕建立，並在訊息中指出該執行的回補指令。
    """
    ensure_replay_schema(db_path)
    days = _trading_days(stock_id, db_path)
    if not days:
        raise CoverageError(f"{stock_id} 沒有任何本地日 K 資料。{_backfill_hint(stock_id, start_date)}")

    cursor = next((d for d in days if d >= start_date), None)
    if cursor is None:
        raise CoverageError(
            f"{stock_id} 的本地資料只到 {days[-1]}，起始日 {start_date} 在其之後。"
            f"{_backfill_hint(stock_id, start_date)}"
        )

    name = name or f"{stock_id} @ {cursor}"

    conn = sqlite3.connect(_resolve_db_path(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO replay_sessions"
            " (stock_id, start_date, end_date, cursor, initial_capital, fee_discount, name)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (stock_id, start_date, end_date, cursor, initial_capital, fee_discount, name),
        )
        conn.commit()
        session_id = int(cur.lastrowid)
    finally:
        conn.close()

    return ReplaySession(
        id=session_id,
        stock_id=stock_id,
        start_date=start_date,
        cursor=cursor,
        initial_capital=initial_capital,
        fee_discount=fee_discount,
        end_date=end_date,
        name=name,
        _trading_days=days,
        _db_path=db_path,
    )


def list_sessions(db_path: str | None = None) -> list[dict]:
    """所有 Replay Session，最新的在前。"""
    ensure_replay_schema(db_path)
    conn = sqlite3.connect(_resolve_db_path(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM replay_sessions ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def delete_session(session_id: int, db_path: str | None = None) -> None:
    """刪除一個 Session 及其所有委託紀錄。"""
    conn = sqlite3.connect(_resolve_db_path(db_path))
    try:
        conn.execute("DELETE FROM replay_orders WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM replay_sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def load_session(session_id: int, db_path: str | None = None) -> ReplaySession:
    """載入既有的 Replay Session，Cursor 停在上次離開的位置。"""
    ensure_replay_schema(db_path)
    conn = sqlite3.connect(_resolve_db_path(db_path))
    try:
        row = conn.execute(
            "SELECT id, stock_id, start_date, end_date, cursor, initial_capital, fee_discount, name"
            " FROM replay_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise SessionNotFoundError(f"找不到 Replay Session {session_id}")

    return ReplaySession(
        id=row[0],
        stock_id=row[1],
        start_date=row[2],
        end_date=row[3],
        cursor=row[4],
        initial_capital=row[5],
        fee_discount=row[6],
        name=row[7],
        _trading_days=_trading_days(row[1], db_path),
        _db_path=db_path,
    )
