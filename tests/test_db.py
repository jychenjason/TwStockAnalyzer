"""Database schema and CRUD tests for TwStockAnalyzer."""

import os
import sqlite3

import pandas as pd
import pytest

from twstock_analyzer.db.schema import TABLE_DEFS, INDEX_DEFS, create_tables
from twstock_analyzer.db.repository import (
    _connect,
    _extract_column_names,
    get_latest_date,
    has_data,
    set_default_db_path,
    upsert,
    get_cache_metadata,
)


class TestSchema:
    def test_create_tables(self, db_path):
        """create_tables should create all tables without error."""
        assert os.path.exists(db_path)

    def test_all_tables_created(self, db_path):
        """All expected tables should exist in the DB."""
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        for name in TABLE_DEFS:
            assert name in tables, f"Table {name} missing"

    def test_indexes_created(self, db_path):
        """Indexes should be created."""
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected = {"idx_daily_stock_date", "idx_fund_stock_date", "idx_inst_stock_date"}
        for idx in expected:
            assert idx in indexes, f"Index {idx} missing"

    def test_wal_mode(self, db_path):
        """Journal mode should be WAL."""
        conn = sqlite3.connect(db_path)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        assert row[0] == "wal"

    def test_duplicate_pk_insert_succeeds(self, db_path, sample_daily_prices):
        """Inserting duplicate primary key should succeed (UPSERT)."""
        # Insert once
        affected1 = upsert("daily_prices", sample_daily_prices.head(5), db_path)
        assert affected1 > 0

        # Insert same data again (should not error)
        affected2 = upsert("daily_prices", sample_daily_prices.head(5), db_path)
        assert affected2 >= 0

        # Count should still be 5 (not 10)
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE stock_id='2330'"
        ).fetchone()[0]
        conn.close()
        assert count == 5


class TestUpsertRoundtrip:
    def test_upsert_daily_prices(self, db_path, sample_daily_prices):
        """Upsert and read back should match."""
        affected = upsert("daily_prices", sample_daily_prices, db_path)
        assert affected == len(sample_daily_prices)

        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            "SELECT * FROM daily_prices WHERE stock_id='2330'", conn
        )
        conn.close()
        assert len(df) == len(sample_daily_prices)

    def test_upsert_fundamentals(self, db_path, sample_fundamentals):
        affected = upsert("fundamentals", sample_fundamentals, db_path)
        assert affected == len(sample_fundamentals)

    def test_upsert_missing_columns_raises(self, db_path):
        bad_df = pd.DataFrame({"wrong_col": [1, 2, 3]})
        with pytest.raises(ValueError, match="Missing columns"):
            upsert("daily_prices", bad_df, db_path)

    def test_upsert_unknown_table_raises(self, db_path, sample_daily_prices):
        with pytest.raises(ValueError, match="Unknown table"):
            upsert("nonexistent_table", sample_daily_prices, db_path)


class TestHasData:
    def test_has_data_true(self, db_path, sample_daily_prices):
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)

        result = has_data("daily_prices", "2330", "2025-07-01", "2026-12-31")
        assert result["has_data"] is True
        assert result["row_count"] == 250
        assert result["date_min"] is not None
        assert result["date_max"] is not None

    def test_has_data_false_empty(self, db_path):
        set_default_db_path(db_path)
        result = has_data("daily_prices", "2330", "2025-07-01", "2026-12-31")
        assert result["has_data"] is False
        assert result["row_count"] == 0

    def test_has_data_wrong_stock(self, db_path, sample_daily_prices):
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)

        result = has_data("daily_prices", "9999", "2025-07-01", "2026-12-31")
        assert result["has_data"] is False

    def test_has_data_unknown_table_raises(self, db_path):
        with pytest.raises(ValueError, match="Unknown table"):
            has_data("nonexistent", "2330", "2024-01-01", "2024-12-31")


class TestGetLatestDate:
    def test_get_latest_date(self, db_path, sample_daily_prices):
        set_default_db_path(db_path)
        upsert("daily_prices", sample_daily_prices, db_path)

        latest = get_latest_date("daily_prices", "2330", db_path)
        assert latest is not None
        assert latest >= "2024-01-01"

    def test_get_latest_date_empty(self, db_path):
        set_default_db_path(db_path)
        latest = get_latest_date("daily_prices", "2330", db_path)
        assert latest is None


class TestExtractColumnNames:
    def test_extract_daily_prices_columns(self):
        cols = _extract_column_names(TABLE_DEFS["daily_prices"])
        assert "stock_id" in cols
        assert "date" in cols
        assert "open" in cols
        assert "high" in cols
        assert "low" in cols
        assert "close" in cols
        assert "volume" in cols
        assert "adj_close" in cols
        assert "fetched_at" in cols

    def test_extract_does_not_include_constraints(self):
        cols = _extract_column_names(TABLE_DEFS["daily_prices"])
        assert "PRIMARY" not in cols
        assert "KEY" not in cols


class TestCacheMetadata:
    def test_get_cache_metadata_no_data(self, db_path):
        set_default_db_path(db_path)
        result = get_cache_metadata("2330", "daily", db_path)
        assert result is None

    def test_upsert_with_cache_metadata(self, db_path):
        set_default_db_path(db_path)
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO cache_metadata
               (cache_key, stock_id, data_type, source, fetched_at,
                date_from, date_to, row_count)
               VALUES ('key1', '2330', 'daily', 'finmind', '2024-01-01T00:00:00',
                       '2024-01-01', '2024-12-31', 100)"""
        )
        conn.commit()
        conn.close()

        result = get_cache_metadata("2330", "daily", db_path)
        assert result is not None
        assert result["stock_id"] == "2330"
        assert result["row_count"] == 100
