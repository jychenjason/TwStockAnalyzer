"""更新失敗必須說出來，不能偽裝成「已是最新」。

TWSE 的 STOCK_DAY 會在連續請求時回 428（限流）。舊行為是把 428 吞掉、
退回 STOCK_DAY_ALL 快照，快照又落後一天而落在區間外，於是 fetch_daily
回傳空表——呼叫端讀成「今天沒有新資料」，把股票標成已更新，實際上它
悄悄落後了。這裡的測試釘住「抓失敗」與「真的沒資料」必須是兩種結果。
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from twstock_analyzer.data.sources.twse import TWSESource


@pytest.fixture(autouse=True)
def _no_twse_http_wait(monkeypatch):
    """428 retry tests exercise delays without making the suite wait."""
    monkeypatch.setattr("twstock_analyzer.data.sources.twse.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("twstock_analyzer.data.sources.twse.random.uniform", lambda _a, _b: 0.0)


def _response(status: int, payload: dict | list) -> Mock:
    resp = Mock()
    resp.status_code = status
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} Client Error")
    else:
        resp.raise_for_status.return_value = None
    return resp


# 快照落後一天：要 08-20，只給得出 08-19。
STALE_SNAPSHOT = [
    {
        "Date": "1150819",
        "Code": "2348",
        "Name": "海悅",
        "TradeVolume": "278036",
        "TradeValue": "19396497",
        "OpeningPrice": "69.00",
        "HighestPrice": "70.40",
        "LowestPrice": "68.90",
        "ClosingPrice": "70.00",
        "Change": "0.6000",
        "Transaction": "222",
    }
]


class TestRateLimitedFetchIsNotSilentSuccess:
    def test_all_requests_rate_limited_raises_instead_of_empty(self):
        """STOCK_DAY 全部 428、快照又補不到區間——必須拋錯，不能回空表。"""
        source = TWSESource()

        def fake_get(url, *args, **kwargs):
            if "STOCK_DAY_ALL" in url:
                return _response(200, STALE_SNAPSHOT)
            return _response(428, {})

        with (
            patch("twstock_analyzer.data.sources.twse.requests.get", side_effect=fake_get),
            patch("twstock_analyzer.data.loader.time.sleep"),
            pytest.raises(Exception) as excinfo,
        ):
            source.fetch_daily("2348", "2026-08-20", "2026-08-20")

        assert "428" in str(excinfo.value)

    def test_transient_failure_is_retried_not_reported_as_fetch_error(self):
        """限流是暫時性的——必須讓 retry 裝飾器有機會退避重試。"""
        source = TWSESource()
        attempts = {"n": 0}

        ok_payload = {
            "stat": "OK",
            "data": [
                ["115/08/20", "765,114", "52,000,000", "68.00", "70.50", "67.90", "70.00", "0.60", "500"],
            ],
        }

        def fake_get(url, *args, **kwargs):
            if "STOCK_DAY_ALL" in url:
                return _response(200, STALE_SNAPSHOT)
            attempts["n"] += 1
            if attempts["n"] < 3:
                return _response(428, {})
            return _response(200, ok_payload)

        with (
            patch("twstock_analyzer.data.sources.twse.requests.get", side_effect=fake_get),
            patch("twstock_analyzer.data.loader.time.sleep"),
        ):
            result = source.fetch_daily("2348", "2026-08-20", "2026-08-20")

        assert not result.empty
        assert result.iloc[0]["date"] == "2026-08-20"
        assert attempts["n"] == 3

    def test_genuinely_no_data_still_returns_empty(self):
        """真的沒資料（stat 不是 OK，沒有任何請求失敗）維持空表，不是錯誤。"""
        source = TWSESource()

        def fake_get(url, *args, **kwargs):
            if "STOCK_DAY_ALL" in url:
                return _response(200, [])
            return _response(200, {"stat": "很抱歉，沒有符合條件的資料!"})

        with patch("twstock_analyzer.data.sources.twse.requests.get", side_effect=fake_get):
            result = source.fetch_daily("2348", "2026-08-20", "2026-08-20")

        assert result.empty


class TestRetryBackoff:
    def test_retry_waits_between_attempts(self):
        """裝飾器的 docstring 承諾 1s/2s 指數退避——限流時不退避等於沒重試。"""
        from twstock_analyzer.data.loader import _retry_decorator

        @_retry_decorator
        def always_fails():
            raise RuntimeError("428 Client Error")

        with patch("twstock_analyzer.data.loader.time.sleep") as sleep, pytest.raises(RuntimeError):
            always_fails()

        assert [c.args[0] for c in sleep.call_args_list] == [1, 2]


class TestTwseHttpRecovery:
    def test_428_uses_targeted_exponential_backoff_then_recovers(self):
        source = TWSESource()
        responses = [
            _response(428, {}),
            _response(428, {}),
            _response(200, {"stat": "OK"}),
        ]
        for response in responses:
            response.headers = {}
            response.text = ""
            response.url = "https://www.twse.com.tw/test"

        with (
            patch("twstock_analyzer.data.sources.twse.requests.get", side_effect=responses),
            patch("twstock_analyzer.data.sources.twse.time.sleep") as sleep,
        ):
            result = source._get("https://www.twse.com.tw/test", timeout=30)

        assert result.status_code == 200
        assert [call.args[0] for call in sleep.call_args_list] == [2.0, 4.0]

    def test_retry_after_takes_precedence_over_backoff(self):
        source = TWSESource()
        limited = _response(428, {})
        limited.headers = {"Retry-After": "7"}
        limited.text = "precondition or edge-policy response"
        limited.url = "https://www.twse.com.tw/test"
        ok = _response(200, {})
        ok.headers = {}
        ok.text = ""
        ok.url = limited.url

        with (
            patch("twstock_analyzer.data.sources.twse.requests.get", side_effect=[limited, ok]),
            patch("twstock_analyzer.data.sources.twse.time.sleep") as sleep,
        ):
            source._get(limited.url)

        sleep.assert_called_once_with(7.0)


class TestBatchSummaryNamesFailures:
    """`update --stock existing` 的結尾訊息是使用者唯一會讀的那一行。

    抓失敗現在會被算進分母卻不被算進分子，結尾只剩「Updated 1085/1089」——
    綠色、看起來像完成。少掉的那幾檔是誰、為什麼少，必須寫在同一行。
    """

    def test_failed_stocks_are_named_in_the_summary(self, populated_db, monkeypatch):
        import sqlite3

        from typer.testing import CliRunner

        import twstock_analyzer.cli.main as cli_main
        from twstock_analyzer.cli.main import app

        conn = sqlite3.connect(populated_db)
        conn.execute(
            "INSERT OR REPLACE INTO daily_prices "
            "(stock_id, date, open, high, low, close, volume, adj_close, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2317", "2025-07-02", 100, 105, 98, 102, 500000, 102, "2025-07-02"),
        )
        conn.commit()
        conn.close()

        def flaky_get_data(stock_id, *args, **kwargs):
            if stock_id == "2317":
                raise requests.HTTPError("428 Client Error")
            return __import__("pandas").DataFrame()

        monkeypatch.setattr(cli_main.DataLoader, "get_data", staticmethod(flaky_get_data))

        original_db = cli_main.DEFAULT_DB
        cli_main.DEFAULT_DB = populated_db
        try:
            result = CliRunner().invoke(app, ["update", "--stock", "existing"])
        finally:
            cli_main.DEFAULT_DB = original_db

        assert result.exit_code == 0
        assert "2317" in result.stdout
        assert "1 檔更新失敗" in result.stdout


class TestInstitutionalRateLimitIsNotAHoliday:
    """T86 的空表有兩種來源，意義相反。

    假日是**答案**——那天沒有開盤；428 限流是**故障**——資料其實存在。舊行為
    把 428 吞掉後回傳空表，兩者長得一模一樣。更新端據此把那天記成非交易日，
    那一天就再也不會被重抓，法人買賣超永久缺一段。
    """

    def test_every_request_rate_limited_raises_instead_of_empty(self):
        source = TWSESource()

        with (
            patch("twstock_analyzer.data.sources.twse.requests.get", return_value=_response(428, {})),
            patch("time.sleep"),
            pytest.raises(Exception) as excinfo,
        ):
            source.fetch_institutional("2330", "2026-08-20", "2026-08-20")

        assert "428" in str(excinfo.value)

    def test_a_genuine_holiday_still_returns_an_empty_frame(self):
        """TWSE 對非交易日回 200 但沒有 data 欄位——那是答案，不該拋錯。"""
        source = TWSESource()

        with (
            patch("twstock_analyzer.data.sources.twse.requests.get", return_value=_response(200, {"stat": "OK"})),
            patch("time.sleep"),
        ):
            frame = source.fetch_institutional("2330", "2026-08-20", "2026-08-20")

        assert frame.empty
