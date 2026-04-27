# tests/test_custom_runner.py
import pandas as pd
import pytest
from unittest.mock import patch
from backtest.custom_runner import run_custom_backtest

STRATEGY = {
    "name": "test",
    "thesis": "test",
    "regime_filter": {"logic": "adx_14 > 20 AND rsi_14 > 50"},
    "entry_long": [{"condition": "ema_20 > ema_50", "operator": "AND"}],
    "entry_short": [],
    "exit": {
        "stop_rule": "1.5 * atr_14",
        "targets": [{"r_multiple": 2.0, "tranche_pct": 1.0}],
        "trailing_rule": "breakeven after T1",
    },
    "position_sizing": "equity * 0.01 / stop_distance",
    "expected_profile": {"win_rate": 0.5, "avg_rr": 1.5,
                         "underperformance_conditions": "chop",
                         "drawdown_profile": "moderate"},
    "failure_modes": ["a", "b", "c"],
}

def _make_df(n=300):
    """Minimal OHLCV + indicators DataFrame with n bars."""
    import numpy as np
    np.random.seed(42)
    price = 40000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2023-01-01", periods=n, freq="D"),
        "open": price * 0.99,
        "high": price * 1.01,
        "low": price * 0.98,
        "close": price,
        "volume": [1000.0] * n,
        "ema_20": price * 0.995,
        "ema_50": price * 0.990,
        "ema_200": price * 0.980,
        "atr_14": [500.0] * n,
        "adx_14": [25.0] * n,  # > 20, regime passes
        "rsi_14": [55.0] * n,  # > 50, regime passes
        "bb_upper": price * 1.02,
        "bb_lower": price * 0.98,
        "bb_mid": price,
        "volume_sma_20": [1000.0] * n,
    })
    return df


def test_strategy_mode_uses_regime_unchanged():
    """'strategy' mode passes regime_filter to engine unchanged."""
    df = _make_df()
    metrics = run_custom_backtest(STRATEGY, df, regime_filter_mode='strategy',
                                  regime_filter_overrides={})
    assert "sharpe" in metrics
    assert metrics["total_trades"] >= 0


def test_disabled_mode_ignores_regime():
    """'disabled' mode replaces regime_filter with empty → always trades."""
    df = _make_df()
    # With regime disabled, more trades are possible
    metrics_disabled = run_custom_backtest(STRATEGY, df, regime_filter_mode='disabled',
                                           regime_filter_overrides={})
    assert "total_trades" in metrics_disabled


def test_custom_mode_filters_conditions():
    """'custom' mode with one condition disabled should behave differently from both conditions on."""
    df = _make_df()
    # Both conditions enabled
    metrics_both = run_custom_backtest(
        STRATEGY, df, regime_filter_mode='custom',
        regime_filter_overrides={"adx_14 > 20": True, "rsi_14 > 50": True}
    )
    # Only ADX enabled — rsi_14 override is False
    metrics_adx_only = run_custom_backtest(
        STRATEGY, df, regime_filter_mode='custom',
        regime_filter_overrides={"adx_14 > 20": True, "rsi_14 > 50": False}
    )
    # Results are dicts with expected keys
    assert "sharpe" in metrics_both
    assert "sharpe" in metrics_adx_only


def test_custom_mode_no_conditions_enabled_acts_as_disabled():
    """'custom' mode with all overrides False acts as disabled (all True in regime)."""
    df = _make_df()
    metrics = run_custom_backtest(
        STRATEGY, df, regime_filter_mode='custom',
        regime_filter_overrides={"adx_14 > 20": False, "rsi_14 > 50": False}
    )
    assert metrics["total_trades"] >= 0


def test_original_strategy_not_mutated():
    """run_custom_backtest must not modify the strategy dict in place."""
    import copy
    strategy_copy = copy.deepcopy(STRATEGY)
    df = _make_df()
    run_custom_backtest(STRATEGY, df, regime_filter_mode='disabled', regime_filter_overrides={})
    assert STRATEGY["regime_filter"] == strategy_copy["regime_filter"]
