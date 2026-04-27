# tests/test_dashboard.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from datetime import date
from storage.db import Database

@pytest.fixture
def db():
    d = Database(":memory:")
    d.init_schema()
    return d

@pytest.fixture
def client(db):
    from dashboard.app import create_app
    app = create_app(db)
    return TestClient(app)

def test_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200

def test_runs_returns_200(client):
    response = client.get("/runs")
    assert response.status_code == 200

def test_run_detail_404_for_missing(client):
    response = client.get("/runs/999")
    assert response.status_code == 404

def test_runs_compare_returns_200(client):
    response = client.get("/runs/compare")
    assert response.status_code == 200

def test_equity_returns_200(client):
    response = client.get("/equity")
    assert response.status_code == 200

def test_pipeline_run_unavailable_without_fn(client):
    response = client.post("/pipeline/run")
    assert response.status_code == 503

def test_pipeline_run_triggers_fn(db):
    from dashboard.app import create_app
    called = []
    app = create_app(db, pipeline_fn=lambda: called.append(1))
    c = TestClient(app)
    response = c.post("/pipeline/run")
    assert response.status_code == 200  # after redirect
    import time; time.sleep(0.1)  # let background task run
    assert len(called) == 1


def test_backtest_form_returns_200(db):
    """GET /backtest returns the form page."""
    run_id = db.insert_strategy_run('{"name": "alpha", "regime_filter": {"logic": "adx_14 > 20"}}')
    db.update_strategy_status(run_id, "approved")
    from dashboard.app import create_app
    app = create_app(db)
    c = TestClient(app)
    response = c.get("/backtest")
    assert response.status_code == 200


def test_backtest_run_rejects_insufficient_bars(db):
    """POST /backtest/run with too-short date range returns 400."""
    run_id = db.insert_strategy_run('{"name": "alpha", "regime_filter": {"logic": "adx_14 > 20"}}')
    from dashboard.app import create_app
    app = create_app(db)
    c = TestClient(app)
    response = c.post("/backtest/run", data={
        "run_id": str(run_id),
        "interval": "1w",
        "date_preset": "custom",
        "date_from": "2024-01-01T00:00",
        "date_to": "2024-01-07T00:00",  # 1 week → ~1 bar at 1w interval
        "regime_filter_mode": "strategy",
    }, follow_redirects=False)
    assert response.status_code == 400


def test_backtest_status_in_progress(db):
    """GET /backtest/<id>/status returns 200 for in-progress backtest."""
    run_id = db.insert_strategy_run('{}')
    from datetime import datetime, timezone
    backtest_id = db.insert_custom_backtest({
        "run_id": run_id, "interval": "1h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy",
        "regime_filter_overrides": "{}",
    })
    from dashboard.app import create_app
    app = create_app(db)
    c = TestClient(app)
    response = c.get(f"/backtest/{backtest_id}/status", follow_redirects=False)
    assert response.status_code == 200
    assert "refresh" in response.text.lower()


def test_backtest_result_page(db):
    """GET /backtest/<id> returns 200 for completed backtest."""
    run_id = db.insert_strategy_run('{"name": "alpha", "regime_filter": {"logic": ""}}')
    db.insert_backtest_results(run_id, {
        "sharpe": 1.2, "sortino": 1.8, "max_drawdown_pct": 0.1,
        "max_drawdown_days": 20, "win_rate": 0.5, "avg_rr": 1.4,
        "total_trades": 15, "pct_time_in_market": 0.35, "cagr": 0.15,
        "backtest_start": date(2023, 1, 1), "backtest_end": date(2024, 1, 1),
    })
    from datetime import datetime, timezone
    backtest_id = db.insert_custom_backtest({
        "run_id": run_id, "interval": "4h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy",
        "regime_filter_overrides": "{}",
    })
    db.update_custom_backtest_results(backtest_id, {
        "sharpe": 0.9, "sortino": 1.2, "max_drawdown_pct": 0.15,
        "max_drawdown_days": 30, "win_rate": 0.45, "avg_rr": 1.2,
        "total_trades": 10, "pct_time_in_market": 0.3, "cagr": 0.1,
    })
    from dashboard.app import create_app
    app = create_app(db)
    c = TestClient(app)
    response = c.get(f"/backtest/{backtest_id}")
    assert response.status_code == 200
