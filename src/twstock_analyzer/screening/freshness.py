"""資料庫裡「最新交易日」是哪一天，以及哪些股票沒跟上。

爆量比的是最新交易日的成交量，所以得先決定那一天。這個模組回答兩件事：

1. **基準日**由多數股票決定，不是全表 ``MAX(date)``。用最大值很脆弱——只要有
   1 檔多抓了一天，其餘 1088 檔就全部變成「落後」，篩選幾乎回傳空的，而訊息
   只會平靜地說「已排除 1088 檔」。
2. **落後的原因分兩種**，後果完全不同：update 沒跑完（健康的股票，跑完就好）
   對上真的停止交易（已下市或長期停牌）。混為一談會讓使用者把「你的資料少了
   三分之一」讀成「這些股票有問題」。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: 落後幾個**交易日**以內仍視為「資料未更新」，超過就當成停止交易。
#:
#: 取 2：日線一天一根，跑完 update 就會補上；連續兩天沒有新資料通常代表
#: 使用者的更新中斷了，而不是這檔股票下市了。
STALE_GRACE_DAYS = 2

#: 被排除的比例超過多少就算「這次篩選的涵蓋範圍不完整」。
#:
#: 取 5%：市場上長期停牌／等待下市的股票本來就有個位數檔，天天喊不完整只會
#: 讓人忽略這個警告；一旦超過 5%，幾乎必然是 update 沒跑完。
INCOMPLETE_COVERAGE = 0.05


@dataclass(frozen=True)
class MarketFreshness:
    """資料庫在「最新交易日」這件事上的狀態。"""

    #: 多數股票最後成交的那一天。資料庫是空的時為 None。
    reference_date: str | None
    #: 資料只落後幾個交易日的股票——update 沒跑完，不是股票有問題。
    behind: list[str]
    #: 長期沒有新資料的股票——已下市或長期停牌。
    inactive: list[str]
    #: 資料庫裡有日線的股票總數。
    total: int

    @property
    def excluded(self) -> list[str]:
        """無法判斷當日爆量的股票代號。"""
        return sorted(self.behind + self.inactive)

    @property
    def covered(self) -> int:
        """真正參與當日爆量比對的股票數。"""
        return self.total - len(self.behind) - len(self.inactive)

    @property
    def is_incomplete(self) -> bool:
        """被排除的比例高到足以讓篩選結果失去代表性。"""
        if not self.total:
            return False
        return (self.total - self.covered) / self.total > INCOMPLETE_COVERAGE


def market_reference_date(conn: sqlite3.Connection) -> str | None:
    """多數股票最後成交的那一天。

    以「各股票最後一筆日線的日期」做多數決；票數相同時取較晚的那天（此時
    沒有多數可言，退化成取最新，行為與直覺一致）。
    """
    row = conn.execute(
        """SELECT last_date FROM (
               SELECT MAX(date) AS last_date, COUNT(*) AS n FROM (
                   SELECT stock_id, MAX(date) AS date FROM daily_prices GROUP BY stock_id)
               GROUP BY date)
            ORDER BY n DESC, last_date DESC
            LIMIT 1"""
    ).fetchone()
    return row[0] if row else None


def market_freshness(conn: sqlite3.Connection) -> MarketFreshness:
    """基準日、沒跟上的股票，以及這次篩選的涵蓋範圍。"""
    reference = market_reference_date(conn)
    if reference is None:
        return MarketFreshness(None, [], [], 0)

    total = conn.execute(
        "SELECT COUNT(DISTINCT stock_id) FROM daily_prices"
    ).fetchone()[0]

    # missing 用「資料庫裡實際存在的交易日」來數，不是日曆天——否則週一跑
    # 篩選時，整個市場都會因為週末而被判成落後。
    rows = conn.execute(
        """WITH last AS (
               SELECT stock_id, MAX(date) AS last_date FROM daily_prices GROUP BY stock_id),
           trading_days AS (
               SELECT DISTINCT date FROM daily_prices WHERE date <= ?)
           SELECT l.stock_id,
                  (SELECT COUNT(*) FROM trading_days d WHERE d.date > l.last_date) AS missing
             FROM last l
            WHERE l.last_date < ?
            ORDER BY l.stock_id""",
        (reference, reference),
    ).fetchall()

    behind = [sid for sid, missing in rows if missing <= STALE_GRACE_DAYS]
    inactive = [sid for sid, missing in rows if missing > STALE_GRACE_DAYS]
    return MarketFreshness(reference, behind, inactive, total)
