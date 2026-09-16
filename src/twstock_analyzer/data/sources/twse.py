"""TWSE (Taiwan Stock Exchange) data source.

Uses two API surfaces:
- ``openapi.twse.com.tw/v1`` — free OpenAPI for latest-day snapshots.
- ``www.twse.com.tw/exchangeReport/STOCK_DAY`` — per-month historical data.

Inherits retry logic from ``BaseDataSource`` — transient errors are
retried up to 3 times with exponential backoff.  ``DataFetchError``
is propagated immediately without retry.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, ClassVar

import pandas as pd
import requests

from ..loader import BaseDataSource, DataFetchError

if TYPE_CHECKING:
    from twstock_analyzer.utils.logger import get_logger  # noqa: F401


logger = logging.getLogger(__name__)


class TWSESource(BaseDataSource):
    """Fetch data from TWSE OpenAPI (https://openapi.twse.com.tw/v1/) and
    TWSE historical endpoint (https://www.twse.com.tw/exchangeReport/STOCK_DAY).
    """

    name = "twse"
    BASE_URL = "https://openapi.twse.com.tw/v1"
    STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
    TRANSIENT_HTTP_STATUSES = frozenset({428, 429})
    HTTP_MAX_ATTEMPTS = 5
    HTTP_BACKOFF_BASE = 2.0
    HTTP_BACKOFF_CAP = 30.0
    # 每筆請求之間的最小間隔，涵蓋月迴圈、股票迴圈與重試三層，
    # 防止一批連發請求在拉開距離前就把 TWSE 的限流窗打爆。
    HTTP_MIN_INTERVAL = 0.5
    # 暫時性錯誤之後的全域冷卻：COOLDOWN_STEP 逐次加倍（5→10→20→40→60），
    # 不只擋當下這一檔，同一支 source 的下一個請求也會先等這段再發。
    COOLDOWN_STEP = 5.0
    COOLDOWN_CAP = 60.0
    # 連續暫時性錯誤超過這個次數就改用長退避，避免短連發一直餵給限流窗。
    TRANSIENT_STORM_THRESHOLD = 3
    # 批次開跑前先 GET 網站根拿 WAF cookie，之後的資料請求才不會被當成 bot。
    WARMUP_ENABLED = True
    WARMUP_URL = "https://www.twse.com.tw/"
    BROWSER_HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.twse.com.tw/",
    }

    def __init__(self) -> None:
        super().__init__()
        self._session = requests.Session()
        self._session.headers.update(self.BROWSER_HEADERS)
        self._warmed_up = False
        self._consecutive_transient = 0
        self._last_request_at: float | None = None
        self._cooldown_until = 0.0

    def _maybe_warmup(self) -> None:
        """One-shot, best-effort warm-up GET so the WAF issues its cookies.

        ``_warmed_up`` is set before requesting on purpose: even a failed
        warm-up must not turn every later request into another attempt.
        """
        if self._warmed_up or not self.WARMUP_ENABLED or not self.WARMUP_URL:
            return
        self._warmed_up = True
        try:
            self._session.get(self.WARMUP_URL, timeout=10)
        except Exception as exc:
            logger.warning("TWSE warmup GET %s failed: %s", self.WARMUP_URL, exc)

    def _wait_for_global_quota(self) -> None:
        """Pause until the source-wide rate quota allows another request.

        Combines the steady ``HTTP_MIN_INTERVAL`` spacing with the cooldown
        accumulated from recent 428/429 responses — the cooldown belongs to
        this source, so a storm on one stock also backs off the next one.
        """
        now = time.monotonic()
        if self._last_request_at is None:
            min_gap = now
        else:
            min_gap = self._last_request_at + self.HTTP_MIN_INTERVAL
        target = max(min_gap, self._cooldown_until)
        if now < target:
            time.sleep(target - now)

    def _get(self, url: str, **kwargs) -> requests.Response:
        """GET through a shared browser-like session with rate limiting.

        HTTP 428 formally means ``Precondition Required``, while 429 is the
        standard rate-limit response.  TWSE's edge returns 428 to throttle
        and to screen out automated clients, and the identical request later
        works.  Treat both as transient here, with three defenses:

        - a persistent ``Session`` sending browser-like headers (and keeping
          cookies), so the request isn't mistaken for an anonymous script;
        - ``HTTP_MIN_INTERVAL`` spacing plus a source-wide cooldown that
          grows while failures repeat, so a whole batch stops hammering
          instead of each stock retrying on its own;
        - ``Retry-After`` kept above all of that when the server says how
          long to wait.
        """
        self._maybe_warmup()
        for attempt in range(self.HTTP_MAX_ATTEMPTS):
            self._wait_for_global_quota()
            response = self._session.get(url, **kwargs)
            now = time.monotonic()
            self._last_request_at = now
            status_code = getattr(response, "status_code", 200)
            if status_code not in self.TRANSIENT_HTTP_STATUSES:
                self._consecutive_transient = 0
                response.raise_for_status()
                return response

            self._consecutive_transient += 1
            delay = self._retry_delay(response, attempt)
            self._cooldown_until = max(self._cooldown_until, now + delay)
            headers = response.headers if isinstance(response.headers, Mapping) else {}
            response_text = response.text
            if not isinstance(response_text, str):
                response_text = ""
            body = response_text.replace("\n", " ")[:500]
            logger.warning(
                "TWSE HTTP %s attempt %s/%s; backoff %.2fs; "
                "server=%r retry-after=%r x-cache=%r x-request-id=%r body=%r",
                status_code,
                attempt + 1,
                self.HTTP_MAX_ATTEMPTS,
                delay,
                headers.get("Server"),
                headers.get("Retry-After"),
                headers.get("X-Cache"),
                headers.get("X-Request-ID"),
                body,
            )

        raise DataFetchError(
            f"TWSE HTTP {status_code} after {self.HTTP_MAX_ATTEMPTS} attempts "
            f"for {getattr(response, 'url', None) or url}"
        )

    def _retry_delay(self, response: requests.Response, attempt: int) -> float:
        """Wait before the next request once a transient error has arrived.

        ``Retry-After`` (numeric seconds or HTTP date) wins outright.  When
        the server stays silent, the wait follows the cooldown schedule —
        ``COOLDOWN_STEP`` doubling, capped by ``COOLDOWN_CAP`` — which is
        the same number that feeds ``_cooldown_until`` for the *next*
        request, whether it belongs to this stock or the next one.  Past
        ``TRANSIENT_STORM_THRESHOLD`` consecutive failures, escalate to the
        capped backoff so a neighborhood storm stops being fed.
        """
        headers = response.headers if isinstance(response.headers, Mapping) else {}
        retry_after = headers.get("Retry-After")
        if isinstance(retry_after, str) and retry_after:
            try:
                return min(max(float(retry_after), 0.0), self.HTTP_BACKOFF_CAP)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    seconds = (retry_at - datetime.now(UTC)).total_seconds()
                    return min(max(seconds, 0.0), self.HTTP_BACKOFF_CAP)
                except (TypeError, ValueError, OverflowError):
                    pass

        cooldown = min(
            self.COOLDOWN_STEP * (2 ** (self._consecutive_transient - 1)),
            self.COOLDOWN_CAP,
        )
        if self._consecutive_transient >= self.TRANSIENT_STORM_THRESHOLD:
            return max(self.HTTP_BACKOFF_CAP, cooldown)
        base = min(self.HTTP_BACKOFF_BASE * (2 ** attempt), self.HTTP_BACKOFF_CAP)
        backoff = base + random.uniform(0.0, 0.5)
        return min(max(backoff, cooldown), self.COOLDOWN_CAP)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_stock_list(self) -> list[dict]:
        """Fetch all listed stocks from MOPS CSV endpoint."""
        import csv
        import io

        try:
            resp = self._get(
                "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv",
                timeout=30,
            )
            resp.raise_for_status()
            # Force encoding to big5 or cp950 (traditional Chinese) — if plain UTF-8 fails
            resp.encoding = "utf-8"
            content = resp.text
        except Exception:
            return []

        stocks: list[dict] = []
        reader = csv.reader(io.StringIO(content))

        for row in reader:
            # Skip header row and empty rows
            if not row or len(row) < 4:
                continue
            # Header starts with 出表日期; skip it
            if row[0].strip() == "出表日期":
                continue

            stock_code = str(row[1]).strip()
            stock_name = str(row[3]).strip()  # Column 3 = 公司簡稱 (short name)

            # Handle quoted values that might contain extra spaces
            if len(stock_code) == 4 and stock_code.isdigit():
                stocks.append({"stock_id": stock_code, "name": stock_name})

        return stocks

    def fetch_daily(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch daily stock prices by iterating month-by-month via STOCK_DAY.

        Falls back to ``STOCK_DAY_ALL`` (latest day snapshot) when the
        per-month endpoint fails.
        """
        frames: list[pd.DataFrame] = []
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        cursor = start.replace(day=1)
        transport_error: Exception | None = None
        while cursor <= end:
            date_param = f"{cursor.year}{cursor.month:02d}01"
            url = f"{self.STOCK_DAY_URL}?response=json&date={date_param}&stockNo={stock_id}"
            try:
                resp = self._get(url, timeout=30)
                resp.raise_for_status()
                raw = resp.json()
                if raw.get("stat") != "OK":
                    # 「查無資料」是答案，不是故障——不記進 transport_error。
                    logger.warning("STOCK_DAY stat=%s for %s %s", raw.get("stat"), stock_id, date_param)
                    cursor = self._next_month(cursor)
                    continue
                frame = self._parse_stock_day_response(raw, stock_id, start_date, end_date)
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                logger.warning("STOCK_DAY failed for %s %s: %s", stock_id, date_param, exc)
                transport_error = exc
            cursor = self._next_month(cursor)

        if frames:
            combined = pd.concat(frames, ignore_index=True)
            combined = combined.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
            return combined

        snapshot = pd.DataFrame()
        try:
            resp = self._get(
                f"{self.BASE_URL}/exchangeReport/STOCK_DAY_ALL",
                params={"slipName": stock_id},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            snapshot = self._parse_stock_day_all_response(data, stock_id, start_date, end_date)
        except DataFetchError:
            raise
        except Exception as e:
            if transport_error is None:
                raise DataFetchError(f"TWSE fetch failed for {stock_id}: {e}")
            logger.warning("STOCK_DAY_ALL fallback failed for %s: %s", stock_id, e)

        if not snapshot.empty:
            return snapshot

        if transport_error is not None:
            # 每一次 STOCK_DAY 都失敗，而快照補不上這段區間（它只有最新一天，
            # 而且常落後一天）。這時回傳空表會被呼叫端讀成「今天沒有新資料」，
            # 於是股票被標成已更新、實際上悄悄落後。往外拋，讓 retry 裝飾器
            # 退避重試；真的救不回來就變成一次看得見的更新失敗。
            raise transport_error

        return snapshot

    def fetch_fundamentals(self, stock_id: str, period: str) -> pd.DataFrame:
        """Fetch fundamental data from TWSE OpenAPI."""
        try:
            resp = self._get(
                f"{self.BASE_URL}/exchangeReport/BWIBBU_d",
                params={"slipName": stock_id},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_fundamental_data(data, stock_id)
        except DataFetchError:
            raise
        except Exception as e:
            raise DataFetchError(f"TWSE fundamentals failed for {stock_id}: {e}")

    def fetch_all_fundamentals(self) -> pd.DataFrame:
        """Fetch fundamental data for ALL stocks in a single API call.

        BWIBBU_d returns the full list regardless of the ``slipName`` param,
        so this method fetches once and returns rows for every stock.
        """
        try:
            resp = self._get(
                f"{self.BASE_URL}/exchangeReport/BWIBBU_d",
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_batch_fundamental_data(data)
        except DataFetchError:
            raise
        except Exception as e:
            raise DataFetchError(f"TWSE all-fundamentals fetch failed: {e}")

    #: 營益分析彙總表——BWIBBU_d 沒有的每股盈餘與營業收入在這裡。
    QUARTERLY_ENDPOINT = "opendata/t187ap14_L"
    #: 月營收彙總表——去年同月增減(%) 就是營收年增率。
    MONTHLY_REVENUE_ENDPOINT = "opendata/t187ap05_L"

    def fetch_quarterly_financials(self) -> pd.DataFrame:
        """全上市公司最新一季的每股盈餘與營業收入（單次呼叫）。

        數字是**累計至該季**，不是單季：拿 1101 的 2026Q2 營業收入
        71,289,957 與月營收表前六個月的累計相減核對過，兩者相符。
        """
        try:
            resp = self._get(f"{self.BASE_URL}/{self.QUARTERLY_ENDPOINT}", timeout=60)
            resp.raise_for_status()
            return self._parse_quarterly_financials(resp.json())
        except DataFetchError:
            raise
        except Exception as e:
            raise DataFetchError(f"TWSE quarterly financials fetch failed: {e}")

    def fetch_monthly_revenue(self) -> pd.DataFrame:
        """全上市公司最新一個月的營收與年增率（單次呼叫）。"""
        try:
            resp = self._get(f"{self.BASE_URL}/{self.MONTHLY_REVENUE_ENDPOINT}", timeout=60)
            resp.raise_for_status()
            return self._parse_monthly_revenue(resp.json())
        except DataFetchError:
            raise
        except Exception as e:
            raise DataFetchError(f"TWSE monthly revenue fetch failed: {e}")

    T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"

    def fetch_institutional(self, stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch institutional trading data from TWSE (三大法人買賣超日報).

        Iterates day-by-day between start_date and end_date because T86
        returns all stocks for a single date.  Weekends are skipped, and a
        short delay between requests avoids TWSE rate-limiting.
        """
        import time
        from datetime import datetime, timedelta

        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        # Weekdays only — the market is closed on Sat/Sun.
        weekdays = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                weekdays.append(cursor)
            cursor += timedelta(days=1)

        frames: list[pd.DataFrame] = []
        transport_error: Exception | None = None
        for i, day in enumerate(weekdays):
            api_date = day.strftime("%Y%m%d")
            try:
                resp = self._get(
                    self.T86_URL,
                    params={"date": api_date, "selectType": "ALL", "response": "json"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                frame = self._parse_institutional_data(data, stock_id, api_date)
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                # 空表有兩種來源，意義相反：假日（答案）與 428 限流（故障）。
                # 這裡走到的一定是後者——請求本身壞了。混為一談的代價是呼叫端
                # 把「沒抓到」記成「這天沒開盤」，那一天就再也不會被重抓。
                logger.warning("T86 fetch failed for %s: %s", api_date, exc)
                transport_error = exc
            # Throttle between requests, but not after the last one.
            if i < len(weekdays) - 1:
                time.sleep(0.5)

        if frames:
            return pd.concat(frames, ignore_index=True)

        if transport_error is not None:
            raise transport_error

        return pd.DataFrame()

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_stock_day_response(
        self, raw: dict, stock_id: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Parse ``STOCK_DAY`` response (www.twse.com.tw).

        Response format::

            {"stat":"OK","fields":["日期","成交股數",...],
             "data":[["115/07/01","964,696","77,943,452",
                      "80.00","81.30","80.00","81.30","+1.30","918",""]]}

        Field index: [日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌差, 成交筆數, 註記]
        """
        rows_data = raw.get("data")
        if not rows_data:
            return pd.DataFrame()

        rows: list[dict] = []
        for row in rows_data:
            if not row or len(row) < 7:
                continue
            date_str = self._normalize_date(str(row[0]))
            if not (start_date <= date_str <= end_date):
                continue
            rows.append({
                "date": date_str,
                "open": self._parse_float(row[3]),
                "high": self._parse_float(row[4]),
                "low": self._parse_float(row[5]),
                "close": self._parse_float(row[6]),
                "volume": self._parse_volume(row[1]),
            })

        return pd.DataFrame(rows)

    def _parse_stock_day_all_response(
        self, data: list | dict, stock_id: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Parse ``STOCK_DAY_ALL`` response (openapi.twse.com.tw).

        New format (bare array of objects)::

            [{"Date":"1150701","Code":"9941","Name":"裕融",
              "TradeVolume":"...","OpeningPrice":"80.00",
              "HighestPrice":"81.30","LowestPrice":"80.00",
              "ClosingPrice":"81.30","Transaction":"918"}]
        """
        if isinstance(data, dict):
            if "data" not in data:
                return pd.DataFrame()
            rows_data = data["data"]
            # Legacy STOCK_DAY_ALL returns positional arrays (not objects).
            if rows_data and isinstance(rows_data[0], (list, tuple)):
                return self._parse_daily_data(data, stock_id, start_date, end_date)
            data = rows_data

        if not isinstance(data, list):
            return pd.DataFrame()

        rows: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("Code") != stock_id:
                continue
            date_str = self._normalize_date(str(item.get("Date", "")))
            if not (start_date <= date_str <= end_date):
                continue
            rows.append({
                "date": date_str,
                "open": self._parse_float(item.get("OpeningPrice")),
                "high": self._parse_float(item.get("HighestPrice")),
                "low": self._parse_float(item.get("LowestPrice")),
                "close": self._parse_float(item.get("ClosingPrice")),
                "volume": self._parse_volume(item.get("TradeVolume")),
            })

        return pd.DataFrame(rows)

    def _parse_daily_data(
        self, data: dict, stock_id: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """Parse legacy ``STOCK_DAY_ALL`` positional-array response.

        Response shape::

            {'fields': [...], 'data': [[...]]}

        Each row follows the field order:
        [securitiesTradingDate, stockCode, stockName, tradePrice,
         tradeVolume, buyPriceA, sellPriceA, openPrice, highPrice,
         lowPrice, changePrice, tradeVolumeNT]
        """
        if not isinstance(data, dict) or "data" not in data:
            return pd.DataFrame()

        rows: list[dict] = []
        for row in data["data"]:
            if not row or len(row) < 5:
                continue
            if row[1] != stock_id:  # stockCode is the second field
                continue
            rows.append({
                "date": self._normalize_date(str(row[0])),
                "open": self._parse_float(row[7]) if len(row) > 7 else 0.0,
                "high": self._parse_float(row[8]) if len(row) > 8 else 0.0,
                "low": self._parse_float(row[9]) if len(row) > 9 else 0.0,
                "close": self._parse_float(row[3]),
                "volume": self._parse_volume(row[4]),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
            df = df.reset_index(drop=True)
        return df

    def _parse_fundamental_data(self, data: dict | list, stock_id: str) -> pd.DataFrame:
        """Parse PE / dividend fundamental data.

        Handles both old format (dict with ``"data"`` key containing arrays)
        and new format (bare list of objects with named fields).
        """
        # New format: bare list of objects with 'Code', 'Date', 'PEratio', 'DividendYield' keys
        if isinstance(data, list):
            rows: list[dict] = []
            for item in data:
                if not isinstance(item, dict) or item.get("Code") != stock_id:
                    continue
                rows.append({
                    "date": f"{item['Date'][:4]}-{item['Date'][4:6]}-{item['Date'][6:8]}" if item.get("Date") else "",
                    "pe_ratio": float(item["PEratio"]) if item.get("PEratio") else None,
                    "dividend_yield": float(item["DividendYield"]) if item.get("DividendYield") else None,
                })
            return pd.DataFrame(rows)

        # Legacy format: dict with "data" key
        if "data" not in data:
            return pd.DataFrame()

        rows = []
        for row in data["data"]:
            if not row or row[0] != stock_id:
                continue
            rows.append({
                "date": row[1],
                "pe_ratio": float(row[2]) if row[2] else None,
                "dividend_yield": float(row[3]) if row[3] else None,
            })
        return pd.DataFrame(rows)

    def _parse_batch_fundamental_data(self, data: dict | list) -> pd.DataFrame:
        """Parse BWIBBU_d response into a batch DataFrame for ALL stocks.

        Handles both new format (bare list of objects with ``Code``, ``Date``,
        ``PEratio``, ``DividendYield``) and legacy format (dict with ``"data"``
        key containing positional arrays).
        """
        if isinstance(data, list):
            rows = []
            for item in data:
                if not isinstance(item, dict) or not item.get("Code"):
                    continue
                rows.append({
                    "stock_id": item["Code"],
                    "date": f"{item['Date'][:4]}-{item['Date'][4:6]}-{item['Date'][6:8]}" if item.get("Date") else "",
                    "pe_ratio": float(item["PEratio"]) if item.get("PEratio") else None,
                    "dividend_yield": float(item["DividendYield"]) if item.get("DividendYield") else None,
                })
            return pd.DataFrame(rows)

        if "data" not in data:
            return pd.DataFrame()

        rows = []
        for row in data["data"]:
            if not row or len(row) < 4:
                continue
            rows.append({
                "stock_id": row[0],
                "date": row[1],
                "pe_ratio": float(row[2]) if row[2] else None,
                "dividend_yield": float(row[3]) if row[3] else None,
            })
        return pd.DataFrame(rows)

    def _parse_institutional_data(
        self, data: dict, stock_id: str, api_date: str | None = None
    ) -> pd.DataFrame:
        """Parse T86 response (三大法人買賣超日報).

        T86 returns one row per stock.  Column 0 is 證券代號 (stock code).
        """
        if "data" not in data:
            return pd.DataFrame()

        if not api_date:
            return pd.DataFrame()

        date_str = f"{api_date[:4]}-{api_date[4:6]}-{api_date[6:8]}"
        rows: list[dict] = []
        for row in data["data"]:
            if not row or len(row) < 5:
                continue
            code = str(row[0])
            rows.append({
                "stock_id": code,
                "date": date_str,
                "foreign_buy": self._parse_volume(row[2]),
                "foreign_sell": self._parse_volume(row[3]),
                "foreign_net": self._parse_volume(row[4]),
                "fund_buy": self._parse_volume(row[8]),
                "fund_sell": self._parse_volume(row[9]),
                "fund_net": self._parse_volume(row[10]),
                "dealer_buy": self._parse_volume(row[12])
                             + self._parse_volume(row[15]),
                "dealer_sell": self._parse_volume(row[13])
                              + self._parse_volume(row[16]),
                "dealer_net": self._parse_volume(row[11]),
                "total_net": self._parse_volume(row[18]),
            })
        result = pd.DataFrame(rows)
        if stock_id != "0000":
            result = result[result["stock_id"] == stock_id]
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_date(roc_date: str) -> str:
        """Convert ROC date to ``YYYY-MM-DD``.

        Handles two formats:
        - ``'115/07/01'`` (slash-separated, from STOCK_DAY)
        - ``'1150701'`` (compact, from STOCK_DAY_ALL OpenAPI)
        """
        if "/" in roc_date:
            parts = roc_date.split("/")
        elif len(roc_date) == 7 and roc_date.isdigit():
            parts = [roc_date[:3], roc_date[3:5], roc_date[5:7]]
        else:
            return roc_date

        try:
            year = int(parts[0]) + 1911
            month = parts[1].zfill(2)
            day = parts[2].zfill(2)
            return f"{year}-{month}-{day}"
        except (IndexError, ValueError):
            return roc_date

    def _parse_quarterly_financials(self, data: Any) -> pd.DataFrame:
        """把營益分析彙總表整理成每檔一列。

        年度是民國年、季別是 1..4，合成 ``2026Q2`` 這種字串——字典序剛好等於
        時間序，之後取「最新一季」不必再解析日期。
        """
        if not isinstance(data, list):
            return pd.DataFrame()

        rows: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            stock_id = str(item.get("公司代號", "")).strip()
            period = self._roc_quarter(item.get("年度"), item.get("季別"))
            if not stock_id or not period:
                continue
            rows.append({
                "stock_id": stock_id,
                "period": period,
                "eps": self._parse_optional_float(item.get("基本每股盈餘(元)")),
                "revenue": self._parse_optional_float(item.get("營業收入")),
                "operating_income": self._parse_optional_float(item.get("營業利益")),
                "net_income": self._parse_optional_float(item.get("稅後淨利")),
            })
        return pd.DataFrame(rows)

    def _parse_monthly_revenue(self, data: Any) -> pd.DataFrame:
        """把月營收彙總表整理成每檔一列（最新一個月）。"""
        if not isinstance(data, list):
            return pd.DataFrame()

        rows: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            stock_id = str(item.get("公司代號", "")).strip()
            month = self._roc_month(item.get("資料年月"))
            if not stock_id or not month:
                continue
            rows.append({
                "stock_id": stock_id,
                "month": month,
                "revenue": self._parse_optional_float(item.get("營業收入-當月營收")),
                "revenue_yoy": self._parse_optional_float(item.get("營業收入-去年同月增減(%)")),
                "revenue_mom": self._parse_optional_float(item.get("營業收入-上月比較增減(%)")),
                "revenue_ytd": self._parse_optional_float(item.get("累計營業收入-當月累計營收")),
                "revenue_ytd_yoy": self._parse_optional_float(item.get("累計營業收入-前期比較增減(%)")),
            })
        return pd.DataFrame(rows)

    @staticmethod
    def _roc_quarter(year: Any, quarter: Any) -> str | None:
        """``("115", "2")`` → ``"2026Q2"``；看不懂就回 None 而不是猜。"""
        try:
            gregorian = int(str(year).strip()) + 1911
            q = int(str(quarter).strip())
        except (TypeError, ValueError):
            return None
        if not 1 <= q <= 4:
            return None
        return f"{gregorian}Q{q}"

    @staticmethod
    def _roc_month(roc_year_month: Any) -> str | None:
        """``"11507"`` → ``"2026-07"``。"""
        text = str(roc_year_month or "").strip()
        if len(text) != 5 or not text.isdigit():
            return None
        month = int(text[3:])
        if not 1 <= month <= 12:
            return None
        return f"{int(text[:3]) + 1911}-{month:02d}"

    @staticmethod
    def _parse_optional_float(value: Any) -> float | None:
        """數字或 None——空值必須是 None。

        ``_parse_float`` 把缺值當 0.0，用在 EPS 或年增率上會讓「未申報」和
        「不賺不賠 / 零成長」變成同一個值，之後再也分不出來。
        """
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if text in ("", "--", "-"):
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_float(value: Any) -> float:
        """Safely parse a numeric string or number to float; returns 0.0 on failure."""
        if value is None or value == "" or value == "--":
            return 0.0
        try:
            return float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _parse_volume(value: Any) -> int:
        """Safely parse a volume string (possibly comma-formatted) to int."""
        if value is None or value == "" or value == "--":
            return 0
        try:
            return int(float(str(value).replace(",", "")))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _next_month(dt: datetime) -> datetime:
        """Return the first day of the month following *dt*."""
        month = dt.month + 1
        year = dt.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        return dt.replace(year=year, month=month, day=1)
