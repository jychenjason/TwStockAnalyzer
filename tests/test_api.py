"""Tests for the FastAPI REST API server."""

import os
import shutil
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from twstock_analyzer.api.server import create_app


@pytest.fixture(autouse=True)
def _setup_writable_db():
    """Copy the real DB to a temp writable location for all tests."""
    src = os.path.join(os.path.dirname(__file__), "..", "data", "twstock.db")
    if not os.path.exists(src):
        src = "/home/jason/projects/TwStockAnalyzer/data/twstock.db"
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    shutil.copy2(src, tmp.name)
    os.chmod(tmp.name, 0o644)
    old = os.environ.get("TWSTOCK_DB")
    os.environ["TWSTOCK_DB"] = tmp.name
    yield tmp.name
    if old is None:
        os.environ.pop("TWSTOCK_DB", None)
    else:
        os.environ["TWSTOCK_DB"] = old
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


def test_health_endpoint():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_stocks_endpoint_empty():
    client = TestClient(create_app())
    resp = client.get("/stocks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_prices_endpoint():
    client = TestClient(create_app())
    resp = client.get("/stocks/2330/prices?limit=5")
    assert resp.status_code in (200, 404)


def test_analysis_endpoint():
    client = TestClient(create_app())
    resp = client.get("/stocks/2330/analysis?indicators=ma,rsi")
    assert resp.status_code in (200, 404)


def test_fundamentals_endpoint():
    client = TestClient(create_app())
    resp = client.get("/stocks/2330/fundamentals")
    assert resp.status_code == 200


def test_institutional_endpoint():
    client = TestClient(create_app())
    resp = client.get("/stocks/2330/institutional")
    assert resp.status_code == 200


def test_update_existing_endpoint():
    client = TestClient(create_app())
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "updated"
        run.return_value.stderr = ""
        resp = client.post(
            "/stocks/update-existing",
            params={"data_type": "institutional", "force": True, "start": "2024-01-01"},
        )

    assert resp.status_code == 200
    assert resp.json()["scope"] == "existing"
    run.assert_called_once()
    command = run.call_args.args[0]
    assert command[-7:] == [
        "update", "existing", "--type", "institutional", "--force", "--start", "2024-01-01",
    ]


def test_screen_endpoint():
    client = TestClient(create_app())
    resp = client.post("/screen", params={"pe_min": 10, "pe_max": 20})
    assert resp.status_code == 200


def test_backtest_endpoint():
    client = TestClient(create_app())
    resp = client.post("/backtest", params={"stocks": "2330", "capital": 1000000, "signal": "ma_cross"})
    assert resp.status_code == 200
