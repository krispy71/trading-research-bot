# tests/test_fetcher.py
import pytest
from unittest.mock import patch, MagicMock
from datetime import date
import pandas as pd
from data.fetcher import fetch_ohlcv, compute_indicators

SAMPLE_KLINES = [
    [1704067200000, "40000", "41000", "39000", "40500", "1000", 1704153599999, "0", 100, "0", "0", "0"],
    [1704153600000, "40500", "42000", "40000", "41000", "1200", 1704239999999, "0", 110, "0", "0", "0"],
]

def test_fetch_ohlcv_returns_list_of_dicts():
    with patch("data.fetcher.requests.get") as mock_get:
        mock_get.return_value.json.return_value = SAMPLE_KLINES
        mock_get.return_value.raise_for_status = MagicMock()
        rows = fetch_ohlcv(date(2024, 1, 1), date(2024, 1, 2))
    assert len(rows) == 2
    assert rows[0]["timestamp"] == date(2024, 1, 1)
    assert rows[0]["close"] == 40500.0

def test_fetch_ohlcv_warns_if_data_starts_after_backfill(caplog):
    with patch("data.fetcher.requests.get") as mock_get:
        mock_get.return_value.json.return_value = SAMPLE_KLINES
        mock_get.return_value.raise_for_status = MagicMock()
        import logging
        with caplog.at_level(logging.WARNING):
            fetch_ohlcv(date(2018, 1, 1), date(2024, 1, 2), backfill_start=date(2018, 1, 1))
    # warning fires if earliest returned date > backfill_start
    # SAMPLE_KLINES start at 2024-01-01, so warning should appear
    assert any("2018-01-01" in r.message for r in caplog.records)

def test_compute_indicators_returns_expected_columns():
    # Need enough rows for EMA-200
    dates = pd.date_range("2018-01-01", periods=250, freq="D")
    close = pd.Series([float(30000 + i * 10) for i in range(250)], index=dates)
    df = pd.DataFrame({
        "timestamp": dates.date,
        "open": close * 0.99, "high": close * 1.01,
        "low": close * 0.98, "close": close.values, "volume": [1000.0] * 250,
    })
    result = compute_indicators(df)
    for col in ["ema_20", "ema_50", "ema_200", "atr_14", "adx_14", "rsi_14",
                "bb_upper", "bb_lower", "bb_mid", "volume_sma_20"]:
        assert col in result.columns, f"Missing column: {col}"

def test_compute_indicators_drops_nan_rows():
    dates = pd.date_range("2018-01-01", periods=250, freq="D")
    close = pd.Series([float(30000 + i * 10) for i in range(250)], index=dates)
    df = pd.DataFrame({
        "timestamp": dates.date,
        "open": close * 0.99, "high": close * 1.01,
        "low": close * 0.98, "close": close.values, "volume": [1000.0] * 250,
    })
    result = compute_indicators(df)
    assert result.isnull().sum().sum() == 0
