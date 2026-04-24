# data/fetcher.py
import logging
import requests
import pandas as pd
import pandas_ta as ta
from datetime import date, datetime, timedelta, timezone
from typing import Optional
import config

logger = logging.getLogger(__name__)

def fetch_ohlcv(
    start: date,
    end: date,
    backfill_start: Optional[date] = None,
) -> list[dict]:
    """Fetch BTC/USDT daily candles from Binance between start and end (inclusive)."""
    rows = []
    cursor = start
    while cursor <= end:
        params = {
            "symbol": "BTCUSDT",
            "interval": "1d",
            "startTime": int(datetime.combine(cursor, datetime.min.time()).timestamp() * 1000),
            "limit": 1000,
        }
        resp = requests.get(config.BINANCE_KLINES_URL, params=params, timeout=30)
        resp.raise_for_status()
        klines = resp.json()
        if not klines:
            break
        for k in klines:
            ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date()
            if ts > end:
                break
            rows.append({
                "timestamp": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        last_ts = datetime.fromtimestamp(klines[-1][0] / 1000, tz=timezone.utc).date()
        cursor = last_ts + timedelta(days=1)
        if len(klines) < 1000:
            break

    if rows and backfill_start and rows[0]["timestamp"] > backfill_start:
        logger.warning(
            f"Binance data starts at {rows[0]['timestamp']}, "
            f"earlier than requested backfill start {backfill_start}. "
            f"Using earliest available data."
        )
    return rows


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators on a OHLCV DataFrame. Returns rows with no NaNs."""
    df = df.copy().sort_values("timestamp").reset_index(drop=True)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    df["ema_20"] = ta.ema(close, length=20)
    df["ema_50"] = ta.ema(close, length=50)
    df["ema_200"] = ta.ema(close, length=200)
    df["atr_14"] = ta.atr(high, low, close, length=14)
    adx = ta.adx(high, low, close, length=14)
    df["adx_14"] = adx["ADX_14"] if adx is not None and "ADX_14" in adx else None
    df["rsi_14"] = ta.rsi(close, length=14)
    bb = ta.bbands(close, length=20, std=2)
    if bb is not None:
        # Column names vary by pandas_ta version; find them by prefix
        bb_upper_col = next((c for c in bb.columns if c.startswith("BBU_")), None)
        bb_mid_col = next((c for c in bb.columns if c.startswith("BBM_")), None)
        bb_lower_col = next((c for c in bb.columns if c.startswith("BBL_")), None)
        df["bb_upper"] = bb[bb_upper_col] if bb_upper_col else None
        df["bb_mid"] = bb[bb_mid_col] if bb_mid_col else None
        df["bb_lower"] = bb[bb_lower_col] if bb_lower_col else None
    else:
        df["bb_upper"] = df["bb_mid"] = df["bb_lower"] = None
    df["volume_sma_20"] = ta.sma(volume, length=20)

    indicator_cols = ["ema_20", "ema_50", "ema_200", "atr_14", "adx_14", "rsi_14",
                      "bb_upper", "bb_lower", "bb_mid", "volume_sma_20"]
    df = df.dropna(subset=indicator_cols).reset_index(drop=True)
    return df[["timestamp"] + indicator_cols]
