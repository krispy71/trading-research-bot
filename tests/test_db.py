# tests/test_db.py
import pytest
import duckdb
import pandas as pd
from datetime import date
from storage.db import Database

@pytest.fixture
def db():
    d = Database(":memory:")
    d.init_schema()
    return d

def test_init_schema_creates_tables(db):
    tables = db.conn.execute("SHOW TABLES").fetchall()
    names = {t[0] for t in tables}
    assert {"ohlcv", "indicators", "strategy_runs", "backtest_results", "paper_positions", "equity_curve"} <= names

def test_upsert_ohlcv_and_latest_timestamp(db):
    rows = [
        {"timestamp": date(2024, 1, 1), "open": 40000.0, "high": 41000.0, "low": 39000.0, "close": 40500.0, "volume": 1000.0},
        {"timestamp": date(2024, 1, 2), "open": 40500.0, "high": 42000.0, "low": 40000.0, "close": 41000.0, "volume": 1200.0},
    ]
    db.upsert_ohlcv(rows)
    assert db.latest_ohlcv_timestamp() == date(2024, 1, 2)

def test_upsert_ohlcv_idempotent(db):
    rows = [{"timestamp": date(2024, 1, 1), "open": 40000.0, "high": 41000.0, "low": 39000.0, "close": 40500.0, "volume": 1000.0}]
    db.upsert_ohlcv(rows)
    db.upsert_ohlcv(rows)
    count = db.conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
    assert count == 1

def test_insert_strategy_run_and_fetch(db):
    run_id = db.insert_strategy_run('{"name": "test"}')
    run = db.get_strategy_run(run_id)
    assert run["strategy_json"] == '{"name": "test"}'
    assert run["status"] == "pending_approval"

def test_update_strategy_status(db):
    run_id = db.insert_strategy_run('{"name": "test"}')
    db.update_strategy_status(run_id, "approved")
    run = db.get_strategy_run(run_id)
    assert run["status"] == "approved"

def test_get_active_strategy_returns_none_when_empty(db):
    assert db.get_active_strategy() is None

def test_get_active_strategy(db):
    run_id = db.insert_strategy_run('{"name": "active"}')
    db.update_strategy_status(run_id, "approved")
    active = db.get_active_strategy()
    assert active["id"] == run_id

def test_insert_backtest_results(db):
    run_id = db.insert_strategy_run('{}')
    db.insert_backtest_results(run_id, {
        "sharpe": 1.5, "sortino": 2.0, "max_drawdown_pct": 0.15,
        "max_drawdown_days": 45, "win_rate": 0.55, "avg_rr": 1.8,
        "total_trades": 40, "pct_time_in_market": 0.6, "cagr": 0.22,
        "backtest_start": date(2023, 1, 1), "backtest_end": date(2024, 1, 1),
    })
    results = db.get_backtest_results(run_id)
    assert results["sharpe"] == 1.5

def test_insert_paper_position(db):
    run_id = db.insert_strategy_run('{}')
    pos_id = db.insert_paper_position(run_id, {
        "entry_date": date(2024, 1, 1), "entry_price": 40000.0,
        "stop_price": 38000.0, "tranche": 1,
    })
    assert pos_id is not None

def test_close_paper_position(db):
    run_id = db.insert_strategy_run('{}')
    pos_id = db.insert_paper_position(run_id, {
        "entry_date": date(2024, 1, 1), "entry_price": 40000.0,
        "stop_price": 38000.0, "tranche": 1,
    })
    db.close_paper_position(pos_id, date(2024, 1, 5), 42000.0, "target", 1.0)
    pos = db.get_paper_position(pos_id)
    assert pos["exit_reason"] == "target"
    assert pos["pnl_r"] == 1.0

def test_upsert_equity_curve(db):
    run_id = db.insert_strategy_run('{}')
    db.upsert_equity_curve(date(2024, 1, 1), 100000.0, 0.0, run_id)
    db.upsert_equity_curve(date(2024, 1, 1), 101000.0, 0.0, run_id)  # idempotent
    count = db.conn.execute("SELECT COUNT(*) FROM equity_curve").fetchone()[0]
    assert count == 1
