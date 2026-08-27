"""臺股交易成本。

買賣各收券商手續費（可打折、有最低收費），賣出另加徵證券交易稅。
不模擬滑價——我們只有日 K 的四個價位，沒有真實買賣盤，任何滑價數字都是憑空捏造。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

FEE_RATE = 0.001425
MIN_FEE = 20.0
TAX_RATE = 0.003


def _to_dollars(value: float) -> float:
    """元以下四捨五入。

    刻意不用內建的 round()——它是銀行家捨入，會出現 712.5→712 但 713.5→714
    這種前後不一致，用在金額上是找麻煩。
    """
    return float(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def brokerage_fee(amount: float, discount: float = 1.0) -> float:
    """券商手續費：成交金額 × 0.1425% × 折數，未達最低收費者以最低收費計。"""
    return max(_to_dollars(amount * FEE_RATE * discount), MIN_FEE)


def transaction_tax(amount: float) -> float:
    """證券交易稅：成交金額 × 0.3%，僅賣出時課徵。"""
    return _to_dollars(amount * TAX_RATE)


def buy_cost(price: float, shares: int, discount: float = 1.0) -> dict:
    """買進一筆的成交金額、手續費與實際支付總額。"""
    amount = price * shares
    fee = brokerage_fee(amount, discount)
    return {"amount": amount, "fee": fee, "tax": 0.0, "total": amount + fee}


def sell_proceeds(price: float, shares: int, discount: float = 1.0) -> dict:
    """賣出一筆的成交金額、手續費、證交稅與實際入帳總額。"""
    amount = price * shares
    fee = brokerage_fee(amount, discount)
    tax = transaction_tax(amount)
    return {"amount": amount, "fee": fee, "tax": tax, "total": amount - fee - tax}
