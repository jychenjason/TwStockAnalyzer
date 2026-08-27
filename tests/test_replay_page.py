"""Layering guards for the Replay page.

The page has no test seam of its own by design: it must be thin enough that
there is no logic worth testing.  These tests guard exactly that property --
if the page starts querying the database or computing rules itself, they fail.
"""

import ast
import inspect

import pytest


@pytest.fixture()
def page_source():
    from twstock_analyzer.streamlit_app import replay_page

    return inspect.getsource(replay_page)


def test_the_page_drives_the_replay_service(page_source):
    tree = ast.parse(page_source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "twstock_analyzer.replay" in imported


def test_the_page_does_not_query_the_database_itself(page_source):
    lowered = page_source.lower()

    assert "sqlite3" not in lowered
    assert "select " not in lowered


def test_the_page_reuses_the_existing_chart_module(page_source):
    tree = ast.parse(page_source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "twstock_analyzer.visualization.charts" in imported


class TestAutoplay:
    """Auto-play advances the Cursor, and it must do so AFTER the page is drawn.

    `st.rerun()` aborts the current script run, so advancing before the chart is
    rendered means the chart never gets rendered at all: the page freezes on the
    last fully-executed frame until the user hits pause.
    """

    @pytest.fixture()
    def page(self):
        from twstock_analyzer.streamlit_app import replay_page

        return replay_page

    @pytest.fixture()
    def session(self, db_path):
        import pandas as pd

        from twstock_analyzer.db.repository import upsert
        from twstock_analyzer.replay import create_session

        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-08-01", periods=10)]
        upsert("daily_prices", pd.DataFrame({
            "stock_id": ["2317"] * 10,
            "date": dates,
            "open": [100.0] * 10,
            "high": [102.0] * 10,
            "low": [99.0] * 10,
            "close": [101.0] * 10,
            "volume": [1_000_000] * 10,
            "adj_close": [101.0] * 10,
            "fetched_at": ["2025-09-01"] * 10,
        }), db_path)
        return create_session("2317", "2025-08-01", db_path=db_path)

    @pytest.fixture()
    def streamlit_stub(self, page, monkeypatch):
        reruns: list[bool] = []
        monkeypatch.setattr(page.time, "sleep", lambda *_: None)
        monkeypatch.setattr(page.st, "rerun", lambda: reruns.append(True))
        monkeypatch.setattr(page.st, "session_state", {})
        return reruns

    def test_a_tick_advances_the_cursor_and_reruns(self, page, session, streamlit_stub):
        page.st.session_state["replay_playing"] = True
        before = session.cursor

        page._autoplay_tick(session, 0.25)

        assert session.cursor != before
        assert streamlit_stub, "advancing must be followed by a rerun"

    def test_a_paused_session_does_not_move(self, page, session, streamlit_stub):
        page.st.session_state["replay_playing"] = False
        before = session.cursor

        page._autoplay_tick(session, 0.25)

        assert session.cursor == before
        assert not streamlit_stub

    def test_playback_stops_itself_at_the_end(self, page, session, streamlit_stub):
        page.st.session_state["replay_playing"] = True
        session.jump_to("2025-08-14")  # last day of the fixture

        page._autoplay_tick(session, 0.25)

        assert page.st.session_state["replay_playing"] is False
        assert not streamlit_stub

    def test_the_tick_runs_after_the_page_has_been_drawn(self, page):
        source = inspect.getsource(page._render_session)

        assert source.index("_render_chart") < source.index("_autoplay_tick")
        assert source.index("_render_summary") < source.index("_autoplay_tick")

    def test_the_controls_do_not_advance_on_their_own(self, page):
        source = inspect.getsource(page._render_controls)

        assert "time.sleep" not in source, "推進不能發生在畫面繪製之前"


def test_the_dashboard_exposes_the_replay_page():
    from twstock_analyzer.streamlit_app import app

    source = inspect.getsource(app.main)

    assert "render_replay" in source
