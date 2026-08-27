from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class AllocationMethod(Enum):
    EQUAL = "equal"
    WEIGHTED = "weighted"
    FIXED = "fixed"


@dataclass
class PortfolioConfig:
    initial_capital: float = 1_000_000
    #: 券商手續費折數（1.0 = 不打折）。手續費與證交稅一律計入，
    #: 否則來回一趟約 0.6% 的成本會讓績效虛胖。
    fee_discount: float = 1.0
    allocation_method: AllocationMethod = AllocationMethod.EQUAL
    rebalance_frequency: Literal["monthly", "quarterly", "none"] = "none"
    benchmark_stock_id: str | None = None
