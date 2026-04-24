# tests/test_backtest.py
import pytest
import pandas as pd
from datetime import date
from backtest.engine import run_backtest, evaluate_regime_filter, evaluate_conditions

STRATEGY = {
    "name": "Test Strategy",
    "regime_filter": {"logic": "adx_14 > 20"},
    "entry_long": [
        {"condition": "close > ema_200"},
        {"condition": "rsi_14 > 50"},
        {"condition": "adx_14 > 20"}
    ],
    "entry_short": [
        {"condition": "close < ema_200"},
        {"condition": "rsi_14 < 50"},
        {"condition": "adx_14 > 20"}
    ],
    "exit": {
        "stop_rule": "1.5 * atr_14 below entry for long, above for short",
        "targets": [
            {"r_multiple": 1.5, "tranche_pct": 0.5},
            {"r_multiple": 3.0, "tranche_pct": 0.5},
        ],
        "trailing_rule": "trail to breakeven after 1R"
    },
    "position_sizing": "size = (equity * 0.01) / (entry - stop)",
}

def make_bar(close=45000.0, ema_200=40000.0, rsi_14=55.0, adx_14=25.0, atr_14=800.0):
    return {
        "timestamp": date(2024, 6, 1),
        "open": close * 0.99, "high": close * 1.01, "low": close * 0.98,
        "close": close, "ema_20": close * 1.001, "ema_50": close * 0.999,
        "ema_200": ema_200, "atr_14": atr_14, "adx_14": adx_14, "rsi_14": rsi_14,
        "bb_upper": close * 1.05, "bb_lower": close * 0.95, "bb_mid": close,
        "volume_sma_20": 1000.0,
    }

def test_evaluate_regime_filter_passes():
    bar = make_bar(adx_14=25.0)
    assert evaluate_regime_filter(STRATEGY["regime_filter"], bar) is True

def test_evaluate_regime_filter_fails():
    bar = make_bar(adx_14=15.0)
    assert evaluate_regime_filter(STRATEGY["regime_filter"], bar) is False

def test_evaluate_conditions_long_all_met():
    bar = make_bar(close=45000.0, ema_200=40000.0, rsi_14=55.0, adx_14=25.0)
    assert evaluate_conditions(STRATEGY["entry_long"], bar) is True

def test_evaluate_conditions_long_not_met():
    bar = make_bar(close=35000.0, ema_200=40000.0, rsi_14=55.0, adx_14=25.0)
    assert evaluate_conditions(STRATEGY["entry_long"], bar) is False

def test_run_backtest_returns_metrics_keys():
    # Build 400 bars with uptrend: close > ema_200, rsi > 50, adx > 20
    bars = []
    for i in range(400):
        bars.append({
            "timestamp": pd.Timestamp("2023-01-01") + pd.Timedelta(days=i),
            "open": 30000 + i * 50,
            "high": 30000 + i * 50 + 500,
            "low": 30000 + i * 50 - 300,
            "close": 30000 + i * 50,
            "ema_20": 30000 + i * 48,
            "ema_50": 30000 + i * 45,
            "ema_200": 28000 + i * 10,
            "atr_14": 800.0,
            "adx_14": 30.0,
            "rsi_14": 60.0,
            "bb_upper": 32000 + i * 50,
            "bb_lower": 28000 + i * 50,
            "bb_mid": 30000 + i * 50,
            "volume_sma_20": 1000.0,
        })
    df = pd.DataFrame(bars)
    metrics = run_backtest(STRATEGY, df, starting_equity=100000.0)
    for key in ["sharpe", "sortino", "max_drawdown_pct", "max_drawdown_days",
                "win_rate", "avg_rr", "total_trades", "pct_time_in_market", "cagr"]:
        assert key in metrics, f"Missing metric: {key}"

def test_run_backtest_no_trades_in_choppy_market():
    bars = []
    for i in range(400):
        bars.append({
            "timestamp": pd.Timestamp("2023-01-01") + pd.Timedelta(days=i),
            "open": 30000.0, "high": 30500.0, "low": 29500.0, "close": 30000.0,
            "ema_20": 30000.0, "ema_50": 30000.0, "ema_200": 30000.0,
            "atr_14": 500.0, "adx_14": 10.0,  # below regime filter threshold
            "rsi_14": 50.0, "bb_upper": 31000.0, "bb_lower": 29000.0, "bb_mid": 30000.0,
            "volume_sma_20": 1000.0,
        })
    df = pd.DataFrame(bars)
    metrics = run_backtest(STRATEGY, df, starting_equity=100000.0)
    assert metrics["total_trades"] == 0
    assert metrics["pct_time_in_market"] == 0.0
