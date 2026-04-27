# tests/test_reporter.py
import pytest
from datetime import date, datetime, timezone
from storage.db import Database
from reporting.reporter import (
    get_runs_summary, get_run_detail, get_equity_chart_data, get_compare_data
)

@pytest.fixture
def db_with_data():
    db = Database(":memory:")
    db.init_schema()
    run_id = db.insert_strategy_run('{"name": "Alpha Strategy"}')
    db.insert_backtest_results(run_id, {
        "sharpe": 1.8, "sortino": 2.2, "max_drawdown_pct": 0.12, "max_drawdown_days": 30,
        "win_rate": 0.52, "avg_rr": 1.9, "total_trades": 35, "pct_time_in_market": 0.55,
        "cagr": 0.28, "backtest_start": date(2023, 1, 1), "backtest_end": date(2024, 1, 1),
    })
    db.upsert_equity_curve(date(2024, 1, 1), 100000.0, 0.0, run_id)
    db.upsert_equity_curve(date(2024, 1, 2), 101000.0, 0.0, run_id)
    return db, run_id

def test_get_runs_summary(db_with_data):
    db, run_id = db_with_data
    rows = get_runs_summary(db)
    assert len(rows) == 1
    assert rows[0]["sharpe"] == 1.8

def test_get_run_detail(db_with_data):
    db, run_id = db_with_data
    detail = get_run_detail(db, run_id)
    assert detail["run"]["id"] == run_id
    assert detail["backtest"]["sharpe"] == 1.8
    assert detail["strategy"]["name"] == "Alpha Strategy"

def test_get_equity_chart_data(db_with_data):
    db, run_id = db_with_data
    data = get_equity_chart_data(db, run_id)
    assert len(data["dates"]) == 2
    assert data["equity"][0] == 100000.0

def test_get_compare_data(db_with_data):
    db, run_id = db_with_data
    rows = get_compare_data(db)
    assert len(rows) >= 1
    assert "sharpe" in rows[0]

@pytest.fixture
def db():
    d = Database(":memory:")
    d.init_schema()
    return d

def test_get_custom_backtests_empty(db):
    from reporting.reporter import get_custom_backtests
    assert get_custom_backtests(db) == []


def test_get_custom_backtests_returns_rows(db):
    from reporting.reporter import get_custom_backtests
    run_id = db.insert_strategy_run('{"name": "alpha"}')
    db.insert_custom_backtest({
        "run_id": run_id, "interval": "1h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy",
        "regime_filter_overrides": "{}",
    })
    rows = get_custom_backtests(db)
    assert len(rows) == 1
    assert rows[0]["interval"] == "1h"


def test_get_custom_backtest_detail(db):
    from reporting.reporter import get_custom_backtest_detail
    run_id = db.insert_strategy_run('{"name": "alpha"}')
    db.insert_backtest_results(run_id, {
        "sharpe": 1.5, "sortino": 2.0, "max_drawdown_pct": 0.15,
        "max_drawdown_days": 45, "win_rate": 0.55, "avg_rr": 1.8,
        "total_trades": 40, "pct_time_in_market": 0.6, "cagr": 0.22,
        "backtest_start": date(2023, 1, 1), "backtest_end": date(2024, 1, 1),
    })
    backtest_id = db.insert_custom_backtest({
        "run_id": run_id, "interval": "4h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "disabled",
        "regime_filter_overrides": "{}",
    })
    detail = get_custom_backtest_detail(db, backtest_id)
    assert detail["custom"]["interval"] == "4h"
    assert detail["original_backtest"]["sharpe"] == 1.5
    assert detail["run"]["id"] == run_id


def test_get_custom_backtest_detail_missing_returns_none(db):
    from reporting.reporter import get_custom_backtest_detail
    detail = get_custom_backtest_detail(db, 999)
    assert detail["custom"] is None
