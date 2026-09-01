"""Backtest universe screening tests."""

import inspect
import json
import os
import sqlite3
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from twstock_analyzer.api.server import create_app
from twstock_analyzer.backtesting.metrics import BacktestMetrics
from twstock_analyzer.screening.screener import ScreenCriteria


def _endpoint(path: str, method: str = "POST"):
    app = create_app()
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == path and method in route.methods
    )


def _result():
    return SimpleNamespace(
        metrics=BacktestMetrics(),
        equity_curve=pd.Series(dtype=float),
    )


def test_backtest_exports_every_screen_criteria():
    parameters = set(inspect.signature(_endpoint("/backtest")).parameters)
    aliases = {
        "ma_direction": "ma_crossover_direction",
        "ma_within": "ma_crossover_within",
    }
    exported = {aliases.get(name, name) for name in parameters}
    expected = {field.name for field in fields(ScreenCriteria)}
    assert expected <= exported


def test_backtest_screens_candidate_universe():
    screened = pd.DataFrame({"stock_id": ["2330"]})
    with (
        patch("twstock_analyzer.screening.screener.run_screen", return_value=screened) as screen,
        patch("twstock_analyzer.backtesting.engine.run_backtest", return_value=_result()) as backtest,
    ):
        response = _endpoint("/backtest")(
            stocks="2330,2317",
            pe_max=20,
            volume_spike=2,
            spike_direction="up",
        )

    assert response["stocks"] == ["2330"]
    assert response["screen"]["mode"] == "current_snapshot"
    assert response["screen"]["criteria"]["pe_max"] == 20
    assert screen.call_args.kwargs["stock_ids"] == ["2330", "2317"]
    assert backtest.call_args.kwargs["stock_ids"] == ["2330"]


def test_backtest_uses_saved_screen(tmp_path):
    db_path = tmp_path / "twstock.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE screening_criteria (id INTEGER PRIMARY KEY, criteria_json TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO screening_criteria VALUES (?, ?)",
        (7, json.dumps({"rsi_max": 35})),
    )
    conn.commit()
    conn.close()

    old_db = os.environ.get("TWSTOCK_DB")
    os.environ["TWSTOCK_DB"] = str(db_path)
    try:
        with (
            patch(
                "twstock_analyzer.screening.screener.run_screen",
                return_value=pd.DataFrame({"stock_id": ["2330"]}),
            ),
            patch("twstock_analyzer.backtesting.engine.run_backtest", return_value=_result()),
        ):
            response = _endpoint("/backtest")(screen_id=7)
    finally:
        if old_db is None:
            os.environ.pop("TWSTOCK_DB", None)
        else:
            os.environ["TWSTOCK_DB"] = old_db

    assert response["screen"]["saved_screen_id"] == 7
    assert response["screen"]["criteria"] == {"rsi_max": 35}
