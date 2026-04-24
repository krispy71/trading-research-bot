# tests/test_agent.py
import pytest
from unittest.mock import MagicMock, patch
from datetime import date

def test_run_pipeline_logs_parse_error(tmp_path):
    from agent import run_pipeline
    db = MagicMock()
    db.latest_ohlcv_timestamp.return_value = date(2024, 1, 1)
    db.get_recent_indicators.return_value = __import__('pandas').DataFrame()
    db.get_recent_runs_with_metrics.return_value = []
    db.insert_strategy_run.return_value = 1

    with patch("agent.fetch_ohlcv", return_value=[]), \
         patch("agent.compute_indicators", return_value=__import__('pandas').DataFrame()), \
         patch("agent.generate_strategy", side_effect=ValueError("bad json")), \
         patch("agent.DB", db):
        run_pipeline(runs_dir=str(tmp_path))

    db.update_strategy_status.assert_called_with(1, "parse_error", notes=pytest.approx("bad json", abs=0))

def test_run_pipeline_writes_log_file(tmp_path):
    from agent import run_pipeline
    import json, pandas as pd

    strategy = {
        "name": "Test", "thesis": "t", "regime_filter": {"logic": "adx_14 > 20"},
        "entry_long": [], "entry_short": [], "exit": {"stop_rule": "", "targets": [], "trailing_rule": ""},
        "position_sizing": "", "expected_profile": {}, "failure_modes": ["a", "b", "c"]
    }
    metrics = {
        "sharpe": 1.5, "sortino": 2.0, "max_drawdown_pct": 0.1, "max_drawdown_days": 20,
        "win_rate": 0.5, "avg_rr": 1.8, "total_trades": 30, "pct_time_in_market": 0.5,
        "cagr": 0.2, "backtest_start": date(2023, 1, 1), "backtest_end": date(2024, 1, 1),
    }
    ohlcv_df = pd.DataFrame([{"timestamp": date(2023, 1, 1), "open": 40000.0, "high": 41000.0, "low": 39000.0, "close": 40500.0, "volume": 1000.0}])
    indicators_df = pd.DataFrame([{"timestamp": date(2023, 1, 1), "ema_20": 40000.0, "ema_50": 39000.0, "ema_200": 35000.0, "atr_14": 500.0, "adx_14": 25.0, "rsi_14": 55.0, "bb_upper": 42000.0, "bb_mid": 40000.0, "bb_lower": 38000.0, "volume_sma_20": 900.0}])
    db = MagicMock()
    db.latest_ohlcv_timestamp.return_value = date(2024, 1, 1)
    db.get_recent_indicators.return_value = pd.DataFrame()
    db.get_recent_runs_with_metrics.return_value = []
    db.insert_strategy_run.return_value = 42
    db.get_ohlcv.return_value = ohlcv_df
    db.get_indicators.return_value = indicators_df

    with patch("agent.fetch_ohlcv", return_value=[]), \
         patch("agent.compute_indicators", return_value=pd.DataFrame()), \
         patch("agent.generate_strategy", return_value=strategy), \
         patch("agent.run_backtest", return_value=metrics), \
         patch("agent.DB", db):
        run_pipeline(runs_dir=str(tmp_path))

    log_files = list(__import__('pathlib').Path(tmp_path).glob("*.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text()
    assert "Test" in content
    assert "Sharpe" in content
