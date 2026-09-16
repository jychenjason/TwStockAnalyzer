"""EPS 與營收年增率的來源解析。

原本 `fundamentals` 表開了 eps / revenue / revenue_yoy 三欄，卻沒有任何來源會填
它們——唯一寫入的 BWIBBU_d 只給本益比和殖利率。這裡測的是補上的兩支 TWSE
OpenAPI：

* ``t187ap14_L`` 營益分析彙總表 —— 基本每股盈餘、營業收入（累計至該季）
* ``t187ap05_L`` 月營收彙總表 —— 去年同月增減(%)

樣本直接取自實際 API 回應（2026-08-17），欄名一字不改，因為這兩支的欄名是中文
且含全形括號，抄錯一個字就整欄變 None 而不會報錯。
"""

from unittest.mock import Mock

import pandas as pd
import pytest

from twstock_analyzer.data.sources.twse import TWSESource

#: t187ap14_L 的真實回應樣本。
QUARTERLY_SAMPLE = [
    {
        "出表日期": "1150817", "年度": "115", "季別": "2",
        "公司代號": "1101", "公司名稱": "臺灣水泥股份有限公司", "產業別": "水泥工業",
        "基本每股盈餘(元)": "0.38", "普通股每股面額": "新台幣                 10.0000元",
        "營業收入": "71289957.00", "營業利益": "5170177.00",
        "營業外收入及支出": "1464159.00", "稅後淨利": "4569799.00",
    },
    {
        "出表日期": "1150817", "年度": "115", "季別": "2",
        "公司代號": "2330", "公司名稱": "台灣積體電路製造股份有限公司", "產業別": "半導體業",
        "基本每股盈餘(元)": "-1.25", "普通股每股面額": "新台幣                 10.0000元",
        "營業收入": "1000000.00", "營業利益": "", "營業外收入及支出": "", "稅後淨利": "",
    },
]

#: t187ap05_L 的真實回應樣本。
MONTHLY_SAMPLE = [
    {
        "出表日期": "1150816", "資料年月": "11507",
        "公司代號": "1101", "公司名稱": "台泥", "產業別": "水泥工業",
        "營業收入-當月營收": "13744103", "營業收入-上月營收": "13382706",
        "營業收入-去年當月營收": "13535929",
        "營業收入-上月比較增減(%)": "2.70047776585692",
        "營業收入-去年同月增減(%)": "1.5379365538929763",
        "累計營業收入-當月累計營收": "85211435",
        "累計營業收入-去年累計營收": "83916845",
        "累計營業收入-前期比較增減(%)": "1.5427057583015662",
        "備註": "-",
    },
    {
        "出表日期": "1150816", "資料年月": "11507",
        "公司代號": "9999", "公司名稱": "測試", "產業別": "其他",
        "營業收入-當月營收": "", "營業收入-上月營收": "",
        "營業收入-去年當月營收": "",
        "營業收入-上月比較增減(%)": "", "營業收入-去年同月增減(%)": "",
        "累計營業收入-當月累計營收": "", "累計營業收入-去年累計營收": "",
        "累計營業收入-前期比較增減(%)": "", "備註": "-",
    },
]


@pytest.fixture()
def source():
    return TWSESource()


class TestQuarterlyFinancials:
    def test_eps_is_parsed(self, source):
        frame = source._parse_quarterly_financials(QUARTERLY_SAMPLE)
        row = frame.set_index("stock_id").loc["1101"]
        assert row["eps"] == pytest.approx(0.38)

    def test_roc_year_and_quarter_become_a_sortable_period(self, source):
        # 民國 115 年第 2 季 → 2026Q2。取「最新一季」靠的是這個格式的字典序，
        # 由 tests/test_screening_financials.py 驗證實際的排序結果。
        frame = source._parse_quarterly_financials(QUARTERLY_SAMPLE)
        assert frame.iloc[0]["period"] == "2026Q2"

    def test_an_unparseable_quarter_is_dropped_rather_than_guessed(self, source):
        frame = source._parse_quarterly_financials(
            [{"公司代號": "1101", "年度": "115", "季別": "9", "基本每股盈餘(元)": "1.0"}]
        )
        assert frame.empty

    def test_revenue_is_parsed(self, source):
        frame = source._parse_quarterly_financials(QUARTERLY_SAMPLE)
        row = frame.set_index("stock_id").loc["1101"]
        assert row["revenue"] == pytest.approx(71289957.0)

    def test_a_negative_eps_keeps_its_sign(self, source):
        # 虧損不是「沒有資料」，不能被吃成 0 或 None。
        frame = source._parse_quarterly_financials(QUARTERLY_SAMPLE)
        assert frame.set_index("stock_id").loc["2330", "eps"] == pytest.approx(-1.25)

    def test_a_blank_number_is_null_not_zero(self, source):
        # 空字串代表未申報；填 0 會讓「沒賺沒賠」和「不知道」變成同一件事。
        frame = source._parse_quarterly_financials(QUARTERLY_SAMPLE)
        assert pd.isna(frame.set_index("stock_id").loc["2330", "net_income"])

    def test_rows_without_a_company_code_are_dropped(self, source):
        frame = source._parse_quarterly_financials(
            [*QUARTERLY_SAMPLE, {"年度": "115", "季別": "2", "基本每股盈餘(元)": "9.9"}]
        )
        assert len(frame) == 2

    def test_an_empty_response_gives_an_empty_frame(self, source):
        assert source._parse_quarterly_financials([]).empty


class TestMonthlyRevenue:
    def test_revenue_yoy_is_parsed(self, source):
        frame = source._parse_monthly_revenue(MONTHLY_SAMPLE)
        row = frame.set_index("stock_id").loc["1101"]
        assert row["revenue_yoy"] == pytest.approx(1.5379365538929763)

    def test_roc_year_month_becomes_an_iso_month(self, source):
        frame = source._parse_monthly_revenue(MONTHLY_SAMPLE)
        assert frame.iloc[0]["month"] == "2026-07"

    def test_monthly_revenue_is_parsed(self, source):
        frame = source._parse_monthly_revenue(MONTHLY_SAMPLE)
        assert frame.set_index("stock_id").loc["1101", "revenue"] == pytest.approx(13744103.0)

    def test_a_blank_yoy_is_null_not_zero(self, source):
        # 0% 成長是一個真實的數字，不能拿來代表「沒申報」。
        frame = source._parse_monthly_revenue(MONTHLY_SAMPLE)
        assert pd.isna(frame.set_index("stock_id").loc["9999", "revenue_yoy"])

    def test_an_empty_response_gives_an_empty_frame(self, source):
        assert source._parse_monthly_revenue([]).empty


class TestFetchers:
    def test_quarterly_fetch_hits_the_financial_analysis_endpoint(self, source, monkeypatch):
        called = {}

        def fake_get(url, **kwargs):
            called["url"] = url
            return _FakeResponse(QUARTERLY_SAMPLE)

        fake_session = Mock()
        fake_session.get.side_effect = fake_get
        source._session = fake_session
        source._warmed_up = True
        frame = source.fetch_quarterly_financials()

        assert "t187ap14_L" in called["url"]
        assert not frame.empty

    def test_monthly_fetch_hits_the_revenue_endpoint(self, source, monkeypatch):
        called = {}

        def fake_get(url, **kwargs):
            called["url"] = url
            return _FakeResponse(MONTHLY_SAMPLE)

        fake_session = Mock()
        fake_session.get.side_effect = fake_get
        source._session = fake_session
        source._warmed_up = True
        frame = source.fetch_monthly_revenue()

        assert "t187ap05_L" in called["url"]
        assert not frame.empty


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload
