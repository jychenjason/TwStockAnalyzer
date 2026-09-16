"""資料來源此刻最多能給到哪一天。

更新指令要先回答一個問題：現在去抓，有沒有可能拿到比資料庫更新的東西？

以前的答案是「日曆上的今天」。於是週六跑一次 update，1000 多檔股票每一檔都
為了確認「星期六沒有新資料」各發一次請求、抓回一張空表、印一行
``already up to date``，最後一起撞上 TWSE 的 428 限流——log 看起來很忙，實際上
一個位元組都沒有更新。

正確的比較對象是**可得最新交易日**：以現在的時間推算，來源已經公布到哪一天。
三件事決定它：

1. **公布時間差**——當日收盤資料要等盤後彙整，約下午四點才出得來。四點以前
   問，來源手上最新的仍然是前一個交易日。
2. **週末**——沒有交易，往前退到最近的平日。
3. **國定假日**——沒有內建行事曆可查（TWSE 的假日表得另外抓）。改用觀察到的
   事實：整個市場都交不出某一天的資料時，把那天記進 ``market_calendar``，
   之後就不必再問第二遍。這個觀察只在**那一天已經過完**時才算數，否則
   「四點剛過、來源還沒公布」會被誤記成假日，當天就再也抓不到當日行情。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from twstock_analyzer.screening.freshness import market_reference_date

#: 盤後資料大約幾點公布。TWSE 的日收盤行情在收盤後彙整，約 16:00 才會出現；
#: 在這之前問，來源手上最新的仍是前一個交易日。
PUBLISH_HOUR = 16

#: 往回找交易日最多退幾天。春節連假可以連休九天，取 15 天留餘裕；超過就停手
#: ——寧可多抓一次，也不要在行事曆被寫壞時無止境地往前走。
MAX_LOOKBACK_DAYS = 15


def non_trading_days(db_path: str) -> set[str]:
    """觀察到「全市場都沒有資料」的那些日子。"""
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return set()
    try:
        return {
            row[0]
            for row in conn.execute("SELECT date FROM market_calendar WHERE has_trading = 0")
        }
    except sqlite3.OperationalError:
        return set()  # 舊資料庫還沒有這張表
    finally:
        conn.close()


def available_trading_date(now: datetime | None = None, db_path: str | None = None) -> str:
    """以現在時間推算，來源此刻最多能給到哪一個交易日（YYYY-MM-DD）。

    ``db_path`` 是選用的：給了就會一併避開已知的非交易日，並在資料庫已經有更
    新的資料時以資料庫為準。不給就只做「時間差 + 週末」的推算。
    """
    now = now or datetime.now()

    day = now.date()
    if now.hour < PUBLISH_HOUR:
        day -= timedelta(days=1)

    holidays: set[str] = set()
    reference: str | None = None
    if db_path:
        holidays = non_trading_days(db_path)
        reference = _market_reference_date(db_path)

    for _ in range(MAX_LOOKBACK_DAYS):
        if day.weekday() < 5 and day.isoformat() not in holidays:
            break
        day -= timedelta(days=1)

    target = day.isoformat()

    # 資料庫裡已經有比推算更新的資料（來源提前公布、或這台機器的時鐘慢了）：
    # 以資料庫為準。否則會為了抓「比手上還舊的東西」把全市場再掃一遍。
    if reference and reference > target:
        return reference
    return target


def record_non_trading_day(target: str, db_path: str, now: datetime | None = None) -> bool:
    """記下「整個市場都沒有這一天的資料」，回傳是否真的寫入。

    兩道門檻，都是實測踩出來的：

    * **只記已經過完的日子。** 當天剛過四點、來源還沒公布時，全市場一樣交不出
      資料，但那是時間差、不是假日；記下去會讓當天再也抓不到當日行情。
    * **手上已經有那天的行情就不准記。** 呼叫端的「沒抓到任何資料」是從它問過
      的那幾檔推論出來的，而它問的往往正是**落後或停止交易的那幾檔**——那種樣
      本交不出資料是常態，跟市場有沒有開盤無關。2026-08-21 實測：1089 檔裡
      1086 檔快取命中，只有 3 檔落後的被問到、都回空表，於是 08-20 這個
      1086 檔都有資料的正常交易日被記成了假日。資料庫自己就是反證。
    """
    now = now or datetime.now()
    if target >= now.date().isoformat():
        return False
    if _has_prices_on(target, db_path):
        return False

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO market_calendar (date, has_trading, checked_at) VALUES (?, 0, ?)",
            (target, now.isoformat()),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return False  # 舊資料庫還沒有這張表——不記就是了，行為退回原本的樣子
    finally:
        conn.close()
    return True


def forget_non_trading_day(target: str, db_path: str) -> None:
    """來源後來真的給了這一天的資料——把先前的觀察撤掉。

    自我修復用。一次全網路異常若讓所有來源都回空表（而不是拋例外），那天會被
    誤記成假日；只要之後真的抓到那天的資料，這個誤記就會自己消失。
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM market_calendar WHERE date = ?", (target,))
        conn.commit()
    except sqlite3.OperationalError:
        return
    finally:
        conn.close()


def _has_prices_on(target: str, db_path: str) -> bool:
    """資料庫裡有沒有任何一檔在這一天的日線。有，就證明那天有開盤。"""
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM daily_prices WHERE date = ? LIMIT 1", (target,)
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    return row is not None


def _market_reference_date(db_path: str) -> str | None:
    """資料庫裡多數股票最後成交的那一天；讀不到就當作沒有。"""
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        return None
    try:
        return market_reference_date(conn)
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
