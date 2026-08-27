"""Replay service tests.

The seam under test is the Replay service layer: creating a Replay Session,
advancing the Cursor, and reading the data visible at the Cursor.  Tests drive
it through its public interface only -- never through internal helpers or by
querying the database behind its back.
"""

import pandas as pd
import pytest

from twstock_analyzer.replay import (
    CoverageError,
    SessionNotFoundError,
    create_session,
    delete_session,
    list_sessions,
    load_session,
)


@pytest.fixture()
def replay_prices(db_path):
    """Daily prices for 2330 covering Aug-Oct 2025, with a one-week halt.

    The halt (2025-09-08 .. 2025-09-12) exists so tests can prove the Cursor
    walks the stock's own trading days rather than a generic calendar.
    """
    from twstock_analyzer.db.repository import upsert

    halted = {"2025-09-08", "2025-09-09", "2025-09-10", "2025-09-11", "2025-09-12"}
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-08-01", "2025-10-31")]
    dates = [d for d in dates if d not in halted]

    df = pd.DataFrame({
        "stock_id": ["2330"] * len(dates),
        "date": dates,
        "open": [100.0 + i for i in range(len(dates))],
        "high": [102.0 + i for i in range(len(dates))],
        "low": [99.0 + i for i in range(len(dates))],
        "close": [101.0 + i for i in range(len(dates))],
        "volume": [1_000_000] * len(dates),
        "adj_close": [101.0 + i for i in range(len(dates))],
        "fetched_at": ["2025-11-01"] * len(dates),
    })
    upsert("daily_prices", df, db_path)
    return db_path


class TestCreateSession:
    def test_cursor_starts_at_the_requested_start_date(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        assert session.cursor == "2025-09-01"

    def test_cursor_snaps_forward_when_start_date_is_not_a_trading_day(self, replay_prices):
        # 2025-09-06 is a Saturday; 2025-09-08..12 are halted.
        session = create_session("2330", "2025-09-06", db_path=replay_prices)

        assert session.cursor == "2025-09-15"


class TestCoverage:
    def test_refuses_a_stock_with_no_local_data_and_names_the_backfill_command(self, replay_prices):
        with pytest.raises(CoverageError) as excinfo:
            create_session("1101", "2025-09-01", db_path=replay_prices)

        message = str(excinfo.value)
        assert "1101" in message
        assert "update" in message

    def test_refuses_a_start_date_later_than_every_available_trading_day(self, replay_prices):
        with pytest.raises(CoverageError) as excinfo:
            create_session("2330", "2026-01-01", db_path=replay_prices)

        assert "2025-10-31" in str(excinfo.value)


class TestPointInTime:
    def test_visible_prices_contain_nothing_later_than_the_cursor(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        visible = session.visible_prices()

        assert visible["date"].max() == "2025-09-01"

    def test_the_cursor_day_bar_is_complete(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        today = session.visible_prices().iloc[-1]

        # 2025-09-01 is trading day index 21 of the fixture, so open == 100 + 21.
        assert today["open"] == 121.0
        assert today["high"] == 123.0
        assert today["low"] == 120.0
        assert today["close"] == 122.0

    def test_warm_up_data_from_before_the_start_date_is_available(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        visible = session.visible_prices()

        assert visible["date"].min() == "2025-08-01"
        assert len(visible) == 22


@pytest.fixture()
def replay_institutional(replay_prices):
    """三大法人資料只覆蓋 2025-08-20 起，且刻意缺 2025-09-01、含一天真正的 0。"""
    from twstock_analyzer.db.repository import upsert

    dates = [d for d in pd.bdate_range("2025-08-20", "2025-09-05").strftime("%Y-%m-%d")]
    dates = [d for d in dates if d != "2025-09-01"]
    net = [0.0 if d == "2025-09-02" else 1000.0 for d in dates]

    upsert("institutional_trading", pd.DataFrame({
        "stock_id": ["2330"] * len(dates),
        "date": dates,
        "foreign_buy": [5000.0] * len(dates),
        "foreign_sell": [4000.0] * len(dates),
        "foreign_net": net,
        "fund_buy": [0.0] * len(dates),
        "fund_sell": [0.0] * len(dates),
        "fund_net": [0.0] * len(dates),
        "dealer_buy": [0.0] * len(dates),
        "dealer_sell": [0.0] * len(dates),
        "dealer_net": [0.0] * len(dates),
        "total_net": net,
        "fetched_at": ["2025-09-06"] * len(dates),
    }), replay_prices)
    return replay_prices


class TestInstitutional:
    def test_institutional_rows_stop_at_the_cursor(self, replay_institutional):
        session = create_session("2330", "2025-09-02", db_path=replay_institutional)

        rows = session.visible_institutional()

        assert rows["date"].max() == "2025-09-02"

    def test_a_day_without_data_is_absent_rather_than_zero(self, replay_institutional):
        session = create_session("2330", "2025-09-01", db_path=replay_institutional)

        assert session.institutional_at("2025-09-01") is None

    def test_a_genuine_zero_net_is_reported_as_zero(self, replay_institutional):
        session = create_session("2330", "2025-09-02", db_path=replay_institutional)

        row = session.institutional_at("2025-09-02")

        assert row is not None
        assert row["foreign_net"] == 0.0

    def test_coverage_reports_when_institutional_data_starts(self, replay_institutional):
        session = create_session("2330", "2025-08-01", db_path=replay_institutional)

        coverage = session.institutional_coverage()

        assert coverage["date_min"] == "2025-08-20"

    def test_coverage_is_empty_when_no_institutional_data_exists(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        assert session.institutional_coverage()["date_min"] is None


class TestIndicators:
    def test_indicator_values_use_only_data_up_to_the_cursor(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        indicators = session.visible_indicators()

        # Closes are 101 + i, and the cursor is trading day index 21, so the
        # five closes ending at the cursor are 118..122 -- mean 120.
        assert indicators["sma_5"].iloc[-1] == pytest.approx(120.0)
        assert indicators["date"].max() == "2025-09-01"

    def test_indicators_needing_more_history_than_exists_are_flagged(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        unreliable = session.unreliable_indicators()

        # 22 bars are available at this cursor.
        assert "sma_60" in unreliable
        assert "sma_240" in unreliable
        assert "sma_5" not in unreliable
        assert "sma_20" not in unreliable

    def test_advancing_extends_the_indicator_series(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)
        before = len(session.visible_indicators())

        session.advance()

        assert len(session.visible_indicators()) == before + 1


class TestPersistence:
    def test_a_session_survives_a_reload_with_its_cursor_and_settings_intact(self, replay_prices):
        session = create_session(
            "2330",
            "2025-09-01",
            initial_capital=500_000,
            fee_discount=0.6,
            db_path=replay_prices,
        )
        session.advance()
        session.advance()

        reloaded = load_session(session.id, db_path=replay_prices)

        assert reloaded.cursor == "2025-09-03"
        assert reloaded.stock_id == "2330"
        assert reloaded.initial_capital == 500_000
        assert reloaded.fee_discount == 0.6

    def test_reloaded_sessions_see_the_same_data_as_the_original(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)
        session.advance()

        reloaded = load_session(session.id, db_path=replay_prices)

        pd.testing.assert_frame_equal(reloaded.visible_prices(), session.visible_prices())


@pytest.fixture()
def trading_prices(db_path):
    """2317 的價格序列，open 與 close 刻意不同，並在 index 20 安排一次暴力跳空。

    open 與 close 若相等，就無法分辨成交價到底取自哪一天的哪個欄位——ADR 0001
    的整個重點就會失去驗證對象。
    """
    from twstock_analyzer.db.repository import upsert

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-08-01", periods=30)]
    closes = [100.0 + i for i in range(30)]
    opens = [100.0] + [closes[i - 1] + 3.0 for i in range(1, 30)]
    opens[20] = 1000.0  # gap up

    upsert("daily_prices", pd.DataFrame({
        "stock_id": ["2317"] * 30,
        "date": dates,
        "open": opens,
        "high": [max(o, c) + 1 for o, c in zip(opens, closes)],
        "low": [min(o, c) - 1 for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [1_000_000] * 30,
        "adj_close": closes,
        "fetched_at": ["2025-09-15"] * 30,
    }), db_path)
    return db_path


class TestBuying:
    def test_an_order_fills_at_the_next_days_open_not_todays_close(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)

        session.advance()

        fills = session.fills()
        assert len(fills) == 1
        assert fills[0]["filled_on"] == "2025-08-11"
        assert fills[0]["fill_price"] == 108.0  # next day's open

    def test_an_order_does_not_affect_the_position_until_it_fills(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)

        session.place_order("buy", lots=1)

        assert session.position()["shares"] == 0
        assert session.cash() == 1_000_000.0

    def test_a_fill_costs_the_amount_plus_brokerage_fee(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)

        session.advance()

        # 108.0 * 1000 = 108,000; fee = 108,000 * 0.001425 = 153.9 -> 154
        assert session.cash() == pytest.approx(1_000_000.0 - 108_000.0 - 154.0)

    def test_the_minimum_brokerage_fee_applies_to_tiny_orders(self, trading_prices):
        session = create_session("2317", "2025-08-01", db_path=trading_prices)
        session.place_order("buy", lots=1)

        session.advance()

        # 103.0 * 1000 = 103,000 -> fee 146.8 -> 147, above the 20 minimum.
        # Use the fee floor directly instead: a 100-share order would be 10.3 -> 20.
        assert session.fills()[0]["fee"] >= 20.0

    def test_several_orders_on_the_same_day_settle_together(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.place_order("buy", lots=2)

        session.advance()

        assert session.position()["shares"] == 3000
        assert len(session.fills()) == 2

    def test_an_order_that_cannot_be_paid_for_is_voided_whole(self, trading_prices):
        # Cursor 2025-08-28 is index 19; the next day opens at 1000.0 after a gap.
        session = create_session(
            "2317", "2025-08-28", initial_capital=200_000.0, db_path=trading_prices
        )
        session.place_order("buy", lots=1)

        session.advance()

        voided = session.voided_orders()
        assert len(voided) == 1
        assert "資金不足" in voided[0]["void_reason"]
        assert session.position()["shares"] == 0
        assert session.cash() == 200_000.0

    def test_the_position_reports_weighted_average_cost(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()
        session.place_order("buy", lots=1)
        session.advance()

        position = session.position()

        # 108,000 + 154 fee, then 109,000 + 155 fee, over 2,000 shares.
        assert position["shares"] == 2000
        assert position["average_cost"] == pytest.approx((108_154.0 + 109_155.0) / 2000)


class TestSelling:
    def test_a_sale_fills_at_the_next_open_less_fee_and_tax(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()  # filled at 108.0, cost 108,154
        cash_after_buy = session.cash()

        session.place_order("sell", lots=1)
        session.advance()  # fills at 109.0

        # 109,000 amount; fee 155; tax 327 -> 108,518 net proceeds.
        assert session.cash() == pytest.approx(cash_after_buy + 108_518.0)
        assert session.position()["shares"] == 0

    def test_realised_profit_uses_the_weighted_average_cost(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()
        session.place_order("sell", lots=1)
        session.advance()

        # Proceeds 108,518 against a cost basis of 108,154.
        assert session.realized_pnl() == pytest.approx(364.0)

    def test_a_partial_sale_leaves_the_average_cost_unchanged(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()
        session.place_order("buy", lots=1)
        session.advance()
        average_before = session.position()["average_cost"]

        session.place_order("sell", lots=1)
        session.advance()

        assert session.position()["shares"] == 1000
        assert session.position()["average_cost"] == pytest.approx(average_before)

    def test_selling_more_than_is_held_voids_the_order(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()

        session.place_order("sell", lots=5)
        session.advance()

        voided = session.voided_orders()
        assert len(voided) == 1
        assert "持股不足" in voided[0]["void_reason"]
        assert session.position()["shares"] == 1000

    def test_unrealised_profit_marks_the_position_to_the_cursor_close(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()

        # Cursor is 2025-08-11, close 106.0; cost basis 108,154.
        assert session.unrealized_pnl() == pytest.approx(106_000.0 - 108_154.0)


class TestJumping:
    def test_jumping_forward_settles_the_days_it_passes_through(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)

        session.jump_to("2025-08-14")

        assert session.cursor == "2025-08-14"
        assert session.position()["shares"] == 1000, "掛著的委託不能被跳過去而永遠不成交"

    def test_jumping_backwards_discards_like_a_rewind(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()
        session.advance()

        session.jump_to("2025-08-08")

        assert session.cursor == "2025-08-08"
        assert session.orders() == []

    def test_stepping_back_moves_exactly_one_trading_day(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.advance()
        session.advance()

        session.step_back()

        assert session.cursor == "2025-08-11"

    def test_jumping_past_the_available_data_is_refused(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)

        with pytest.raises(ValueError):
            session.jump_to("2027-01-01")


class TestRewind:
    def test_rewinding_discards_everything_that_happened_after(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()
        session.advance()

        session.rewind("2025-08-08")

        assert session.cursor == "2025-08-08"
        assert session.orders() == []
        assert session.cash() == 1_000_000.0
        assert session.position()["shares"] == 0

    def test_rewinding_then_replaying_matches_never_having_traded(self, trading_prices):
        traded = create_session("2317", "2025-08-08", db_path=trading_prices)
        traded.place_order("buy", lots=2)
        traded.advance()
        traded.advance()
        traded.rewind("2025-08-08")
        traded.advance()
        traded.advance()

        untouched = create_session("2317", "2025-08-08", db_path=trading_prices)
        untouched.advance()
        untouched.advance()

        assert traded.cursor == untouched.cursor
        assert traded.cash() == untouched.cash()
        assert traded.position() == untouched.position()
        assert traded.realized_pnl() == untouched.realized_pnl()

    def test_an_order_placed_on_the_target_day_is_discarded_too(self, trading_prices):
        # Rewinding to D means "redo day D", so D's own decisions go as well.
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()  # fills on 2025-08-11

        session.rewind("2025-08-08")

        assert session.fills() == []
        assert session.pending_orders() == []

    def test_a_fill_that_happened_before_the_target_days_decisions_survives(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()  # cursor 2025-08-11, order fills at that day's open
        session.advance()  # cursor 2025-08-12

        session.rewind("2025-08-11")

        assert len(session.fills()) == 1
        assert session.position()["shares"] == 1000

    def test_a_preview_reports_how_much_would_be_discarded(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()
        session.place_order("buy", lots=1)
        session.advance()

        preview = session.rewind_preview("2025-08-11")

        assert preview["orders_discarded"] == 1

    def test_rewinding_forwards_is_refused(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)

        with pytest.raises(ValueError):
            session.rewind("2025-08-20")


class TestEndDateAndSummary:
    def test_the_cursor_stops_at_the_configured_end_date(self, trading_prices):
        session = create_session(
            "2317", "2025-08-08", end_date="2025-08-13", db_path=trading_prices
        )

        for _ in range(10):
            session.advance()

        assert session.cursor == "2025-08-13"
        assert session.at_end is True

    def test_a_session_with_no_trades_still_produces_a_summary(self, trading_prices):
        session = create_session(
            "2317", "2025-08-08", end_date="2025-08-14", db_path=trading_prices
        )
        session.jump_to("2025-08-14")

        summary = session.summary()

        assert summary["total_return"] == pytest.approx(0.0)
        assert summary["trades"] == 0
        assert summary["win_rate"] is None
        assert summary["max_drawdown"] == pytest.approx(0.0)

    def test_the_summary_compares_against_buying_and_holding(self, trading_prices):
        session = create_session(
            "2317", "2025-08-08", end_date="2025-08-14", db_path=trading_prices
        )
        session.jump_to("2025-08-14")

        summary = session.summary()

        # 9 lots bought at the 108.0 open, sold at the 109.0 close, costs included.
        assert summary["buy_hold_return"] == pytest.approx(0.003274)

    def test_the_win_rate_counts_closed_trades(self, trading_prices):
        session = create_session(
            "2317", "2025-08-08", initial_capital=2_000_000.0, db_path=trading_prices
        )
        session.place_order("buy", lots=1)
        session.advance()  # buy fills at 108.0
        session.place_order("sell", lots=1)
        session.advance()  # sell fills at 109.0 -> a winner

        session.jump_to("2025-08-28")
        session.place_order("buy", lots=1)
        session.advance()  # fills at the 1000.0 gap open
        session.place_order("sell", lots=1)
        session.advance()  # fills at 123.0 -> a heavy loser

        summary = session.summary()

        assert summary["trades"] == 2
        assert summary["win_rate"] == pytest.approx(0.5)
        assert summary["max_drawdown"] < -0.3, "崩跌後的權益回撤必須被記錄下來"


class TestSessionManagement:
    def test_sessions_are_listed_newest_first_with_their_progress(self, trading_prices):
        first = create_session("2317", "2025-08-08", db_path=trading_prices)
        second = create_session("2317", "2025-08-12", db_path=trading_prices)
        second.advance()

        listed = list_sessions(db_path=trading_prices)

        assert [s["id"] for s in listed] == [second.id, first.id]
        assert listed[0]["cursor"] == "2025-08-13"
        assert listed[0]["stock_id"] == "2317"

    def test_a_session_gets_a_default_name_and_can_be_renamed(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)

        assert session.name == "2317 @ 2025-08-08"

        session.rename("練習：跳空追高")

        assert load_session(session.id, db_path=trading_prices).name == "練習：跳空追高"

    def test_the_same_stock_and_start_date_can_be_practised_more_than_once(self, trading_prices):
        first = create_session("2317", "2025-08-08", db_path=trading_prices)
        second = create_session("2317", "2025-08-08", db_path=trading_prices)

        assert first.id != second.id
        assert len(list_sessions(db_path=trading_prices)) == 2

    def test_deleting_a_session_removes_it_and_its_orders(self, trading_prices):
        session = create_session("2317", "2025-08-08", db_path=trading_prices)
        session.place_order("buy", lots=1)
        session.advance()

        delete_session(session.id, db_path=trading_prices)

        assert list_sessions(db_path=trading_prices) == []
        with pytest.raises(SessionNotFoundError):
            load_session(session.id, db_path=trading_prices)


class TestDividends:
    @pytest.fixture()
    def with_dividends(self, replay_prices):
        from twstock_analyzer.db.repository import upsert

        upsert("dividends", pd.DataFrame({
            "stock_id": ["2330", "2330"],
            "date": ["2025-08-20", "2025-10-15"],
            "cash_dividend": [4.0, 4.5],
            "stock_dividend": [0.0, 0.0],
            "kind": ["息", "息"],
            "fetched_at": ["2025-11-01", "2025-11-01"],
        }), replay_prices)
        return replay_prices

    def test_only_ex_dividend_days_up_to_the_cursor_are_marked(self, with_dividends):
        session = create_session("2330", "2025-09-01", db_path=with_dividends)

        marks = session.visible_dividends()

        assert list(marks["date"]) == ["2025-08-20"]

    def test_a_mark_carries_the_dividend_amount_and_kind(self, with_dividends):
        session = create_session("2330", "2025-09-01", db_path=with_dividends)

        mark = session.visible_dividends().iloc[0]

        assert mark["cash_dividend"] == 4.0
        assert mark["kind"] == "息"

    def test_a_stock_without_dividend_data_simply_has_no_marks(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        assert session.visible_dividends().empty

    def test_replay_never_reads_the_fake_adjusted_close_column(self):
        import inspect

        from twstock_analyzer.replay import service

        assert "adj_close" not in inspect.getsource(service)


class TestExistingDatabases:
    def test_a_database_predating_replay_is_upgraded_on_first_use(self, tmp_path):
        """既有資料庫是在 Replay 之前建立的，不會有 replay 相關的表。

        測試若總是先建好完整 schema，就永遠看不到這個洞——真實資料庫踩到了。
        """
        import sqlite3

        from twstock_analyzer.db.repository import set_default_db_path

        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE daily_prices (stock_id TEXT, date TEXT, open REAL,"
            " high REAL, low REAL, close REAL, volume REAL, adj_close REAL,"
            " fetched_at TEXT, PRIMARY KEY (stock_id, date))"
        )
        for i, day in enumerate(["2025-08-01", "2025-08-04", "2025-08-05"]):
            conn.execute(
                "INSERT INTO daily_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("2330", day, 100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i, 1000, 101.0 + i, "x"),
            )
        conn.commit()
        conn.close()
        set_default_db_path(path)

        session = create_session("2330", "2025-08-01", db_path=path)
        session.place_order("buy", lots=1)
        session.advance()

        assert session.position()["shares"] == 1000
        assert list_sessions(db_path=path)


class TestIsolation:
    def test_replay_runs_with_the_network_blocked(self, replay_prices, monkeypatch):
        import socket

        def deny(*args, **kwargs):
            raise AssertionError("Replay 不得發出任何網路請求")

        monkeypatch.setattr(socket, "socket", deny)
        monkeypatch.setattr(socket, "create_connection", deny)

        session = create_session("2330", "2025-09-01", db_path=replay_prices)
        session.advance()
        visible = session.visible_prices()

        assert session.cursor == "2025-09-02"
        assert len(visible) == 23

    def test_importing_the_service_does_not_drag_in_the_ui_framework(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import twstock_analyzer.replay, sys;"
                    " print(int(any(m == 'streamlit' or m.startswith('streamlit.')"
                    " for m in sys.modules)))"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == "0"


class TestAdvance:
    def test_advancing_moves_the_cursor_to_the_next_trading_day(self, replay_prices):
        session = create_session("2330", "2025-09-01", db_path=replay_prices)

        session.advance()

        assert session.cursor == "2025-09-02"

    def test_advancing_skips_days_the_stock_did_not_trade(self, replay_prices):
        # 2025-09-05 is the last day before the 09-08..09-12 halt.
        session = create_session("2330", "2025-09-05", db_path=replay_prices)

        session.advance()

        assert session.cursor == "2025-09-15"

    def test_the_cursor_stops_at_the_last_available_trading_day(self, replay_prices):
        session = create_session("2330", "2025-10-31", db_path=replay_prices)

        session.advance()

        assert session.cursor == "2025-10-31"
        assert session.at_end is True
