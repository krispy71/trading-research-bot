# tests/test_db.py
import pytest
import duckdb
import pandas as pd
from datetime import date, datetime, timezone
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


def test_migration_preserves_ohlcv_data():
    """Simulate upgrade: old schema → init_schema() → data still present with interval='1d'."""
    d = Database(":memory:")
    # Manually create old-style schema (no interval column)
    d.conn.execute("""
        CREATE TABLE ohlcv (
            timestamp DATE PRIMARY KEY,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE
        )
    """)
    d.conn.execute("""
        CREATE TABLE indicators (
            timestamp DATE PRIMARY KEY,
            ema_20 DOUBLE, ema_50 DOUBLE, ema_200 DOUBLE,
            atr_14 DOUBLE, adx_14 DOUBLE, rsi_14 DOUBLE,
            bb_upper DOUBLE, bb_lower DOUBLE, bb_mid DOUBLE,
            volume_sma_20 DOUBLE
        )
    """)
    d.conn.execute(
        "INSERT INTO ohlcv VALUES ('2024-01-01', 40000, 41000, 39000, 40500, 1000)"
    )
    d.conn.execute(
        "INSERT INTO indicators VALUES ('2024-01-01', 1,2,3,4,5,6,7,8,9,10)"
    )
    # Run migration via init_schema
    d.init_schema()
    # Data preserved with interval='1d'
    ohlcv_rows = d.conn.execute("SELECT interval, open FROM ohlcv").fetchall()
    assert len(ohlcv_rows) == 1
    assert ohlcv_rows[0][0] == '1d'
    assert ohlcv_rows[0][1] == 40000.0
    ind_rows = d.conn.execute("SELECT interval, ema_20 FROM indicators").fetchall()
    assert len(ind_rows) == 1
    assert ind_rows[0][0] == '1d'


def test_init_schema_creates_custom_backtests_table(db):
    tables = db.conn.execute("SHOW TABLES").fetchall()
    names = {t[0] for t in tables}
    assert "custom_backtests" in names


def test_init_schema_is_idempotent_with_new_schema(db):
    """Running init_schema() twice on an already-migrated DB is safe."""
    db.init_schema()  # second call
    count = db.conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
    assert count == 0  # no data added, just no crash


def test_upsert_ohlcv_interval_stores_intraday(db):
    rows = [
        {"timestamp": datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
         "open": 40000.0, "high": 41000.0, "low": 39000.0, "close": 40500.0, "volume": 100.0},
        {"timestamp": datetime(2024, 1, 1, 0, 15, tzinfo=timezone.utc),
         "open": 40500.0, "high": 42000.0, "low": 40000.0, "close": 41000.0, "volume": 120.0},
    ]
    db.upsert_ohlcv_interval(rows, '15m')
    count = db.conn.execute("SELECT COUNT(*) FROM ohlcv WHERE interval='15m'").fetchone()[0]
    assert count == 2


def test_latest_ohlcv_timestamp_interval(db):
    rows = [
        {"timestamp": datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
         "open": 40000.0, "high": 41000.0, "low": 39000.0, "close": 40500.0, "volume": 100.0},
        {"timestamp": datetime(2024, 1, 1, 0, 15, tzinfo=timezone.utc),
         "open": 40500.0, "high": 42000.0, "low": 40000.0, "close": 41000.0, "volume": 120.0},
    ]
    db.upsert_ohlcv_interval(rows, '15m')
    latest = db.latest_ohlcv_timestamp_interval('15m')
    assert latest == datetime(2024, 1, 1, 0, 15, tzinfo=timezone.utc)
    assert db.latest_ohlcv_timestamp_interval('1h') is None


def test_get_ohlcv_interval(db):
    rows = [
        {"timestamp": datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
         "open": 40000.0, "high": 41000.0, "low": 39000.0, "close": 40500.0, "volume": 100.0},
    ]
    db.upsert_ohlcv_interval(rows, '1h')
    df = db.get_ohlcv_interval('1h',
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert len(df) == 1
    assert df.iloc[0]["close"] == 40500.0


def test_upsert_indicators_interval(db):
    rows = [{"timestamp": datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
             "ema_20": 1.0, "ema_50": 2.0, "ema_200": 3.0,
             "atr_14": 4.0, "adx_14": 5.0, "rsi_14": 6.0,
             "bb_upper": 7.0, "bb_lower": 8.0, "bb_mid": 9.0, "volume_sma_20": 10.0}]
    db.upsert_indicators_interval(rows, '1h')
    count = db.conn.execute("SELECT COUNT(*) FROM indicators WHERE interval='1h'").fetchone()[0]
    assert count == 1


def test_get_indicators_interval(db):
    rows = [{"timestamp": datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
             "ema_20": 1.0, "ema_50": 2.0, "ema_200": 3.0,
             "atr_14": 4.0, "adx_14": 5.0, "rsi_14": 6.0,
             "bb_upper": 7.0, "bb_lower": 8.0, "bb_mid": 9.0, "volume_sma_20": 10.0}]
    db.upsert_indicators_interval(rows, '1h')
    df = db.get_indicators_interval('1h',
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert len(df) == 1
    assert df.iloc[0]["ema_20"] == 1.0


def test_insert_custom_backtest_and_get(db):
    run_id = db.insert_strategy_run('{"name": "test"}')
    backtest_id = db.insert_custom_backtest({
        "run_id": run_id,
        "interval": "1h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy",
        "regime_filter_overrides": "{}",
    })
    assert backtest_id is not None
    row = db.get_custom_backtest(backtest_id)
    assert row["interval"] == "1h"
    assert row["total_trades"] == -1  # in-progress sentinel


def test_update_custom_backtest_results(db):
    run_id = db.insert_strategy_run('{}')
    backtest_id = db.insert_custom_backtest({
        "run_id": run_id,
        "interval": "4h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "disabled",
        "regime_filter_overrides": "{}",
    })
    db.update_custom_backtest_results(backtest_id, {
        "sharpe": 1.2, "sortino": 1.8, "max_drawdown_pct": 0.12,
        "max_drawdown_days": 30, "win_rate": 0.55, "avg_rr": 1.5,
        "total_trades": 20, "pct_time_in_market": 0.4, "cagr": 0.18,
    })
    row = db.get_custom_backtest(backtest_id)
    assert row["total_trades"] == 20
    assert row["sharpe"] == 1.2


def test_all_custom_backtests(db):
    run_id = db.insert_strategy_run('{}')
    db.insert_custom_backtest({
        "run_id": run_id, "interval": "1d",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy", "regime_filter_overrides": "{}",
    })
    rows = db.all_custom_backtests()
    assert len(rows) == 1
    assert rows[0]["interval"] == "1d"
