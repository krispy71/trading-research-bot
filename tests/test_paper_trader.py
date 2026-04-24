# tests/test_paper_trader.py
import pytest
from datetime import date
from unittest.mock import MagicMock
from paper.trader import PaperTrader

STRATEGY = {
    "regime_filter": {"logic": "adx_14 > 20"},
    "entry_long": [
        {"condition": "close > ema_200"},
        {"condition": "rsi_14 > 50"},
        {"condition": "adx_14 > 20"},
    ],
    "entry_short": [
        {"condition": "close < ema_200"},
        {"condition": "rsi_14 < 50"},
        {"condition": "adx_14 > 20"},
    ],
    "exit": {
        "stop_rule": "1.5 * atr_14",
        "targets": [
            {"r_multiple": 1.5, "tranche_pct": 0.5},
            {"r_multiple": 3.0, "tranche_pct": 0.5},
        ],
        "trailing_rule": "breakeven after 1R",
    },
}

def make_bar(close=45000.0, ema_200=40000.0, rsi_14=60.0, adx_14=25.0, atr_14=800.0, dt=date(2024, 6, 1)):
    return {
        "timestamp": dt, "open": close * 0.99, "high": close * 1.02,
        "low": close * 0.98, "close": close,
        "ema_20": close, "ema_50": close, "ema_200": ema_200,
        "atr_14": atr_14, "adx_14": adx_14, "rsi_14": rsi_14,
        "bb_upper": close * 1.05, "bb_lower": close * 0.95, "bb_mid": close,
        "volume_sma_20": 1000.0,
    }

@pytest.fixture
def db():
    from storage.db import Database
    d = Database(":memory:")
    d.init_schema()
    return d

def test_paper_trader_enters_long_position(db):
    run_id = db.insert_strategy_run('{}')
    trader = PaperTrader(db, run_id, STRATEGY, starting_equity=100000.0)
    bar = make_bar()
    trader.process_bar(bar)
    positions = db.open_paper_positions(run_id)
    assert len(positions) == 1
    assert positions[0]["entry_price"] == 45000.0

def test_paper_trader_skips_regime_filter(db):
    run_id = db.insert_strategy_run('{}')
    trader = PaperTrader(db, run_id, STRATEGY, starting_equity=100000.0)
    bar = make_bar(adx_14=10.0)  # below regime filter
    trader.process_bar(bar)
    positions = db.open_paper_positions(run_id)
    assert len(positions) == 0

def test_paper_trader_closes_on_stop(db):
    run_id = db.insert_strategy_run('{}')
    trader = PaperTrader(db, run_id, STRATEGY, starting_equity=100000.0)
    entry_bar = make_bar(close=45000.0, atr_14=800.0, dt=date(2024, 6, 1))
    trader.process_bar(entry_bar)
    # Stop is 45000 - 1.5*800 = 43800; next bar low goes below stop
    stop_bar = make_bar(close=43500.0, dt=date(2024, 6, 2))
    stop_bar["low"] = 43700.0
    trader.process_bar(stop_bar)
    positions = db.open_paper_positions(run_id)
    assert len(positions) == 0  # closed

def test_paper_trader_writes_equity_snapshot(db):
    run_id = db.insert_strategy_run('{}')
    trader = PaperTrader(db, run_id, STRATEGY, starting_equity=100000.0)
    bar = make_bar()
    trader.process_bar(bar)
    curve = db.get_equity_curve(run_id)
    assert len(curve) >= 1
