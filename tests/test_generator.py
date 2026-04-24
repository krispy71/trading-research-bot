# tests/test_generator.py
import json
import pytest
from unittest.mock import MagicMock, patch
from research.generator import build_prompt, parse_strategy_response, STRATEGY_SCHEMA_KEYS

VALID_STRATEGY = {
    "name": "Trend Momentum Filter",
    "thesis": "Edge exists because momentum and trend alignment reduce false breakouts.",
    "regime_filter": {"adx_min": 20, "logic": "ADX_14 > 20"},
    "entry_long": [{"condition": "close > ema_200", "operator": "AND"},
                   {"condition": "rsi_14 > 50", "operator": "AND"},
                   {"condition": "adx_14 > 20"}],
    "entry_short": [{"condition": "close < ema_200", "operator": "AND"},
                    {"condition": "rsi_14 < 50", "operator": "AND"},
                    {"condition": "adx_14 > 20"}],
    "exit": {
        "stop_rule": "1.5 * ATR_14 below entry",
        "targets": [{"r_multiple": 1.5, "tranche_pct": 0.5}, {"r_multiple": 3.0, "tranche_pct": 0.5}],
        "trailing_rule": "Trail stop to breakeven after 1R"
    },
    "position_sizing": "size = (equity * 0.01) / (entry - stop)",
    "expected_profile": {
        "win_rate": 0.45,
        "avg_rr": 1.8,
        "underperformance_conditions": "choppy low-ADX markets",
        "drawdown_profile": "max 20% in ranging markets"
    },
    "failure_modes": [
        "False breakouts in low ADX environments",
        "Gap openings bypass stop levels",
        "Consecutive losing trades in 2018-style bear market"
    ]
}

def test_parse_strategy_response_valid():
    raw = json.dumps(VALID_STRATEGY)
    result = parse_strategy_response(raw)
    assert result["name"] == "Trend Momentum Filter"

def test_parse_strategy_response_strips_markdown_fences():
    raw = f"```json\n{json.dumps(VALID_STRATEGY)}\n```"
    result = parse_strategy_response(raw)
    assert result["name"] == "Trend Momentum Filter"

def test_parse_strategy_response_missing_key_raises():
    bad = {k: v for k, v in VALID_STRATEGY.items() if k != "failure_modes"}
    with pytest.raises(ValueError, match="failure_modes"):
        parse_strategy_response(json.dumps(bad))

def test_parse_strategy_response_wrong_failure_modes_count_raises():
    bad = {**VALID_STRATEGY, "failure_modes": ["only one"]}
    with pytest.raises(ValueError, match="exactly 3"):
        parse_strategy_response(json.dumps(bad))

def test_build_prompt_contains_key_sections():
    import pandas as pd
    from datetime import date
    recent_indicators = pd.DataFrame([{
        "timestamp": date(2024, 1, 1), "ema_20": 40000.0, "ema_50": 39000.0,
        "ema_200": 35000.0, "atr_14": 800.0, "adx_14": 25.0, "rsi_14": 55.0,
        "bb_upper": 42000.0, "bb_lower": 38000.0, "bb_mid": 40000.0, "volume_sma_20": 1000.0
    }])
    prior_runs = []
    prompt = build_prompt(recent_indicators, prior_runs)
    assert "regime_filter" in prompt
    assert "failure_modes" in prompt
    assert "entry_long" in prompt
    assert "position_sizing" in prompt
