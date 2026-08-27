"""Tests for data source modules."""

import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from twstock_analyzer.data.loader import (
    BaseDataSource,
    DataFetchError,
    DataFallbackError,
    DataLoader,
    RETRY_MAX_ATTEMPTS,
    RETRY_BASE_DELAY,
    validate_stock_id,
    validate_date,
)
from twstock_analyzer.data.sources.finmind import FinMindSource
from twstock_analyzer.data.sources.twse import TWSESource


class TestValidation:
    def test_validate_stock_id_valid(self):
        assert validate_stock_id("2330") == "2330"
        assert validate_stock_id("2454") == "2454"

    def test_validate_stock_id_invalid_length(self):
        with pytest.raises(ValueError, match="4-digit"):
            validate_stock_id("123")

    def test_validate_stock_id_non_numeric(self):
        with pytest.raises(ValueError, match="4-digit"):
            validate_stock_id("abcd")

    def test_validate_stock_id_too_long(self):
        with pytest.raises(ValueError, match="4-digit"):
            validate_stock_id("12345")

    def test_validate_date_formats(self):
        assert validate_date("2024-01-15") == "2024-01-15"
        assert validate_date("2024/01/15") == "2024-01-15"
        assert validate_date("20240115") == "2024-01-15"

    def test_validate_date_invalid(self):
        with pytest.raises(ValueError, match="Invalid date"):
            validate_date("not-a-date")


class TestBaseDataSourceInterface:
    def test_base_class_is_abstract(self):
        """BaseDataSource cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseDataSource()

    def test_subclass_must_implement_name(self):
        """Concrete subclass must define name property."""
        class MySource(BaseDataSource):
            @property
            def name(self):
                return "my"

            def fetch_daily(self, stock_id, start_date, end_date):
                return pd.DataFrame()

            def fetch_fundamentals(self, stock_id, period):
                return pd.DataFrame()

            def fetch_institutional(self, stock_id, date):
                return pd.DataFrame()

        source = MySource()
        assert source.name == "my"

    def test_should_skip_default(self):
        """Default should_skip returns False."""
        class TestSource(BaseDataSource):
            @property
            def name(self):
                return "test"

            def fetch_daily(self, stock_id, start_date, end_date):
                return pd.DataFrame()

            def fetch_fundamentals(self, stock_id, period):
                return pd.DataFrame()

            def fetch_institutional(self, stock_id, date):
                return pd.DataFrame()

        assert TestSource.should_skip() is False


class TestFinMindSource:
    def test_name(self):
        assert FinMindSource.name == "finmind"

    def test_should_skip_no_token(self, mock_env_no_finmind):
        assert FinMindSource.should_skip() is True

    def test_should_skip_with_token(self, mock_env_with_finmind):
        assert FinMindSource.should_skip() is False

    def test_fetch_daily_mocked(self, mock_env_with_finmind):
        source = FinMindSource()
        with patch.object(source, '_get_dl') as mock_dl:
            mock_df = pd.DataFrame({
                'trade_date': ['2024-01-02'],
                'stock_id': ['2330'],
                'open': [500.0],
                'high': [510.0],
                'low': [499.0],
                'close': [505.0],
                'volume': [10000000],
                'turnover': [5000000000.0],
            })
            mock_dl.return_value.taiwan_stock_daily.return_value = mock_df
            result = source.fetch_daily("2330", "2024-01-01", "2024-01-03")
            assert not result.empty
            assert "date" in result.columns
            assert "close" in result.columns

    def test_fetch_daily_empty_raises(self, mock_env_with_finmind):
        source = FinMindSource()
        with patch.object(source, '_get_dl') as mock_dl:
            mock_dl.return_value.taiwan_stock_daily.return_value = pd.DataFrame()
            with pytest.raises(DataFetchError):
                source.fetch_daily("2330", "2024-01-01", "2024-01-03")

    def test_fetch_daily_generic_error_propagates(self, mock_env_with_finmind):
        source = FinMindSource()
        with patch.object(source, '_get_dl') as mock_dl:
            mock_dl.return_value.taiwan_stock_daily.side_effect = RuntimeError("network error")
            with pytest.raises(DataFetchError, match="network error"):
                source.fetch_daily("2330", "2024-01-01", "2024-01-03")

    def test_normalize_daily_df_maps_columns(self):
        source = FinMindSource()
        df = pd.DataFrame({
            'TradeDate': ['2024-01-02'],
            'Open': [500.0],
            'High': [510.0],
            'Low': [499.0],
            'Close': [505.0],
            'Volume': [10000000],
            'Turnover': [5000000000.0],
        })
        result = source._normalize_daily_df(df)
        assert "date" in result.columns
        assert "open" in result.columns
        assert "close" in result.columns
        assert "volume" in result.columns
        assert "turnover" in result.columns


class TestTWSESource:
    def test_name(self):
        assert TWSESource.name == "twse"

    def test_should_skip_default(self):
        assert TWSESource.should_skip() is False

    def test_normalize_date_roc(self):
        assert TWSESource._normalize_date("113/01/02") == "2024-01-02"

    def test_normalize_date_already_iso(self):
        result = TWSESource._normalize_date("2024-01-02")
        assert result == "2024-01-02"

    def test_parse_daily_data(self):
        source = TWSESource()
        data = {
            "data": [
                ["113/01/02", "2330", "TSMC", "505", "10000000", "500", "510", "500", "510", "499", "5", "5000000000"],
                ["113/01/03", "2331", "Other", "100", "1000", "99", "101", "99", "101", "98", "1", "100000"],
            ]
        }
        result = source._parse_daily_data(data, "2330", "2024-01-01", "2024-12-31")
        assert len(result) == 1
        assert result.iloc[0]["date"] == "2024-01-02"

    def test_parse_daily_data_empty(self):
        source = TWSESource()
        result = source._parse_daily_data({}, "2330", "2024-01-01", "2024-12-31")
        assert result.empty

    def test_parse_fundamental_data(self):
        source = TWSESource()
        data = {
            "data": [
                ["2330", "2024-01", "35.0", "0.03"],
                ["2331", "2024-01", "30.0", "0.02"],
            ]
        }
        result = source._parse_fundamental_data(data, "2330")
        assert len(result) == 1
        assert result.iloc[0]["pe_ratio"] == 35.0

    def test_parse_institutional_data(self):
        source = TWSESource()
        # T86 (三大法人買賣超日報) row layout: 0=code, 1=name,
        # 2/3/4=foreign buy/sell/net, 8/9/10=fund buy/sell/net,
        # 11=dealer_net, 12/15=dealer_buy, 13/16=dealer_sell, 18=total net.
        data = {
            "data": [
                ["2330", "台積電", "10000", "9000", "1000",
                 "0", "0", "0", "3000", "2500", "500",
                 "100", "120", "0", "0", "80", "0", "0", "1500"],
            ]
        }
        result = source._parse_institutional_data(data, "2330", "20240102")
        assert len(result) == 1
        row = result.iloc[0]
        assert row["date"] == "2024-01-02"
        assert row["foreign_buy"] == 10000.0
        assert row["foreign_net"] == 1000.0
        assert row["fund_buy"] == 3000.0
        assert row["fund_net"] == 500.0
        assert row["dealer_buy"] == 200.0   # row[12] + row[15] = 120 + 80
        assert row["dealer_sell"] == 0.0    # row[13] + row[16] = 0 + 0
        assert row["dealer_net"] == 100.0   # row[11] = 100
        assert row["total_net"] == 1500.0


class TestRetryLogic:
    def test_retry_on_transient_error(self, mock_env_no_finmind):
        """Generic exceptions should be retried; DataFetchError is not retried."""
        from twstock_analyzer.data.loader import _retry_decorator

        call_count = 0

        @ _retry_decorator
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient")
            return "success"

        with patch("twstock_analyzer.data.loader.time.sleep"):
            result = flaky_func()
        assert result == "success"
        assert call_count == 3

    def test_data_fetch_error_not_retried(self):
        """DataFetchError should propagate immediately."""
        from twstock_analyzer.data.loader import _retry_decorator

        call_count = 0

        @ _retry_decorator
        def failing_func():
            nonlocal call_count
            call_count += 1
            raise DataFetchError("known error")

        with pytest.raises(DataFetchError, match="known error"):
            failing_func()
        assert call_count == 1  # No retries for DataFetchError

    def test_max_retries_exhausted(self):
        """After RETRY_MAX_ATTEMPTS failures, raise last exception."""
        from twstock_analyzer.data.loader import _retry_decorator

        call_count = 0

        @ _retry_decorator
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent error")

        with patch("twstock_analyzer.data.loader.time.sleep"):
            with pytest.raises(RuntimeError, match="persistent error"):
                always_fails()
        assert call_count == RETRY_MAX_ATTEMPTS


class TestDataLoader:
    def test_fallback_chain(self, mock_env_with_finmind):
        """DataLoader should try sources in order."""
        source1 = MagicMock()
        source1.should_skip.return_value = False
        source1.fetch_daily.return_value = pd.DataFrame({"date": ["2024-01-01"]})

        source2 = MagicMock()
        source2.should_skip.return_value = False

        loader = DataLoader([source1, source2])
        result = loader.get_data("2330", "daily", "2024-01-01", "2024-01-02")
        assert not result.empty
        source1.fetch_daily.assert_called_once()
        source2.fetch_daily.assert_not_called()

    def test_fallback_to_second_source(self, mock_env_with_finmind):
        """When first source fails, should try second."""
        source1 = MagicMock()
        source1.should_skip.return_value = False
        source1.fetch_daily.side_effect = Exception("network error")

        source2 = MagicMock()
        source2.should_skip.return_value = False
        source2.fetch_daily.return_value = pd.DataFrame({"date": ["2024-01-01"]})

        loader = DataLoader([source1, source2])
        result = loader.get_data("2330", "daily", "2024-01-01", "2024-01-02")
        assert not result.empty

    def test_all_sources_fail_raises(self, mock_env_with_finmind):
        """When all sources fail, should raise DataFallbackError."""
        source1 = MagicMock()
        source1.should_skip.return_value = False
        source1.fetch_daily.side_effect = Exception("fail1")

        source2 = MagicMock()
        source2.should_skip.return_value = False
        source2.fetch_daily.side_effect = Exception("fail2")

        loader = DataLoader([source1, source2])
        with pytest.raises(DataFallbackError):
            loader.get_data("2330", "daily", "2024-01-01", "2024-01-02")

    def test_skipped_source(self, mock_env_no_finmind):
        """Sources that should_skip() return True are skipped."""
        source1 = MagicMock()
        source1.should_skip.return_value = True

        source2 = MagicMock()
        source2.should_skip.return_value = False
        source2.fetch_daily.return_value = pd.DataFrame({"date": ["2024-01-01"]})

        loader = DataLoader([source1, source2])
        result = loader.get_data("2330", "daily", "2024-01-01", "2024-01-02")
        assert not result.empty
        source1.fetch_daily.assert_not_called()

    def test_unknown_data_type(self):
        loader = DataLoader([])
        with pytest.raises(ValueError, match="Unknown data_type"):
            loader.get_data("2330", "unknown", "2024-01-01", "2024-01-02")

    def test_invalid_stock_id(self):
        loader = DataLoader([])
        with pytest.raises(ValueError, match="4-digit"):
            loader.get_data("abc", "daily", "2024-01-01", "2024-01-02")
