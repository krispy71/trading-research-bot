# Trading Research Bot — Core Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the daily-scheduled pipeline: fetch BTC data → generate strategy via Claude → backtest → log results for human review → paper trade approved strategies.

**Architecture:** Persistent APScheduler agent orchestrates a pipeline of focused modules. All state (OHLCV, indicators, strategy runs, backtest results, paper positions, equity curve) lives in a single DuckDB file. The dashboard (separate plan) reads from the same DB.

**Tech Stack:** Python 3.11+, duckdb, pandas, pandas-ta, requests, anthropic, apscheduler

---

### Task 1: Project scaffold and config

**Files:**
- Create: `requirements.txt`
- Create: `config.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`
- Create: `data/__init__.py`, `research/__init__.py`, `backtest/__init__.py`, `paper/__init__.py`, `storage/__init__.py`, `reporting/__init__.py`, `dashboard/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
anthropic>=0.40.0
apscheduler>=3.10.4
duckdb>=1.1.0
pandas>=2.2.0
pandas-ta>=0.3.14b
requests>=2.32.0
fastapi>=0.115.0
uvicorn>=0.32.0
jinja2>=3.1.4
pytest>=8.3.0
pytest-mock>=3.14.0
```

- [ ] **Step 2: Create config.py**

```python
import os

SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", "00:05")       # UTC HH:MM
PAPER_EVAL_TIME = os.getenv("PAPER_EVAL_TIME", "00:10")   # UTC HH:MM, after pipeline
STARTING_EQUITY = float(os.getenv("STARTING_EQUITY", "100000"))
BACKTEST_WINDOW_DAYS = int(os.getenv("BACKTEST_WINDOW_DAYS", "365"))
BACKFILL_START = os.getenv("BACKFILL_START", "2018-01-01")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-7")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", "data.duckdb")
RUNS_DIR = os.getenv("RUNS_DIR", "runs")
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
```

- [ ] **Step 3: Create package __init__.py files and runs/ directory**

```bash
mkdir -p data research backtest paper storage reporting dashboard/templates runs tests
touch data/__init__.py research/__init__.py backtest/__init__.py
touch paper/__init__.py storage/__init__.py reporting/__init__.py
touch dashboard/__init__.py tests/__init__.py
```

- [ ] **Step 4: Create .gitignore**

```
data.duckdb
data.duckdb.wal
runs/
__pycache__/
*.pyc
.env
*.egg-info/
.venv/
```

- [ ] **Step 5: Install dependencies**

```bash
pip install -r requirements.txt
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config.py .gitignore data/ research/ backtest/ paper/ storage/ reporting/ dashboard/ tests/ runs/
git commit -m "feat: project scaffold, config, package structure"
```

---

### Task 2: Database layer

**Files:**
- Create: `storage/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_db.py -v
```
Expected: `ModuleNotFoundError: No module named 'storage.db'`

- [ ] **Step 3: Implement storage/db.py**

```python
# storage/db.py
import duckdb
from datetime import date
from typing import Optional

class Database:
    def __init__(self, path: str):
        self.conn = duckdb.connect(path)

    def init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                timestamp DATE PRIMARY KEY,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE
            );
            CREATE TABLE IF NOT EXISTS indicators (
                timestamp DATE PRIMARY KEY,
                ema_20 DOUBLE, ema_50 DOUBLE, ema_200 DOUBLE,
                atr_14 DOUBLE, adx_14 DOUBLE, rsi_14 DOUBLE,
                bb_upper DOUBLE, bb_lower DOUBLE, bb_mid DOUBLE,
                volume_sma_20 DOUBLE
            );
            CREATE SEQUENCE IF NOT EXISTS strategy_runs_id_seq;
            CREATE TABLE IF NOT EXISTS strategy_runs (
                id INTEGER PRIMARY KEY DEFAULT nextval('strategy_runs_id_seq'),
                created_at TIMESTAMP DEFAULT current_timestamp,
                strategy_json TEXT,
                status TEXT DEFAULT 'pending_approval',
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS backtest_results (
                run_id INTEGER PRIMARY KEY REFERENCES strategy_runs(id),
                sharpe DOUBLE, sortino DOUBLE,
                max_drawdown_pct DOUBLE, max_drawdown_days INTEGER,
                win_rate DOUBLE, avg_rr DOUBLE,
                total_trades INTEGER, pct_time_in_market DOUBLE, cagr DOUBLE,
                backtest_start DATE, backtest_end DATE
            );
            CREATE SEQUENCE IF NOT EXISTS paper_positions_id_seq;
            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY DEFAULT nextval('paper_positions_id_seq'),
                run_id INTEGER REFERENCES strategy_runs(id),
                entry_date DATE, entry_price DOUBLE, stop_price DOUBLE, tranche INTEGER,
                exit_date DATE, exit_price DOUBLE, exit_reason TEXT, pnl_r DOUBLE
            );
            CREATE TABLE IF NOT EXISTS equity_curve (
                date DATE PRIMARY KEY,
                equity DOUBLE, drawdown_pct DOUBLE,
                run_id INTEGER REFERENCES strategy_runs(id)
            );
        """)

    def upsert_ohlcv(self, rows: list[dict]):
        if not rows:
            return
        df = __import__('pandas').DataFrame(rows)
        self.conn.execute("INSERT OR REPLACE INTO ohlcv SELECT * FROM df")

    def latest_ohlcv_timestamp(self) -> Optional[date]:
        result = self.conn.execute("SELECT MAX(timestamp) FROM ohlcv").fetchone()
        return result[0] if result else None

    def get_ohlcv(self, start: date, end: date):
        return self.conn.execute(
            "SELECT * FROM ohlcv WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            [start, end]
        ).df()

    def upsert_indicators(self, rows: list[dict]):
        if not rows:
            return
        df = __import__('pandas').DataFrame(rows)
        self.conn.execute("INSERT OR REPLACE INTO indicators SELECT * FROM df")

    def get_indicators(self, start: date, end: date):
        return self.conn.execute(
            "SELECT * FROM indicators WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            [start, end]
        ).df()

    def get_recent_indicators(self, days: int):
        return self.conn.execute(
            "SELECT * FROM indicators ORDER BY timestamp DESC LIMIT ?", [days]
        ).df()

    def insert_strategy_run(self, strategy_json: str) -> int:
        result = self.conn.execute(
            "INSERT INTO strategy_runs (strategy_json) VALUES (?) RETURNING id",
            [strategy_json]
        ).fetchone()
        return result[0]

    def get_strategy_run(self, run_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM strategy_runs WHERE id = ?", [run_id]
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.description]
        return dict(zip(cols, row))

    def update_strategy_status(self, run_id: int, status: str, notes: str = None):
        self.conn.execute(
            "UPDATE strategy_runs SET status = ?, notes = COALESCE(?, notes) WHERE id = ?",
            [status, notes, run_id]
        )

    def get_active_strategy(self) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM strategy_runs WHERE status = 'approved' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.description]
        return dict(zip(cols, row))

    def retire_all_approved(self):
        self.conn.execute("UPDATE strategy_runs SET status = 'retired' WHERE status = 'approved'")

    def all_runs(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT sr.*, br.sharpe, br.max_drawdown_pct, br.win_rate, br.avg_rr, br.cagr
               FROM strategy_runs sr
               LEFT JOIN backtest_results br ON sr.id = br.run_id
               ORDER BY sr.created_at DESC"""
        ).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def insert_backtest_results(self, run_id: int, metrics: dict):
        self.conn.execute("""
            INSERT OR REPLACE INTO backtest_results
            (run_id, sharpe, sortino, max_drawdown_pct, max_drawdown_days,
             win_rate, avg_rr, total_trades, pct_time_in_market, cagr,
             backtest_start, backtest_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            run_id, metrics["sharpe"], metrics["sortino"],
            metrics["max_drawdown_pct"], metrics["max_drawdown_days"],
            metrics["win_rate"], metrics["avg_rr"], metrics["total_trades"],
            metrics["pct_time_in_market"], metrics["cagr"],
            metrics["backtest_start"], metrics["backtest_end"],
        ])

    def get_backtest_results(self, run_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM backtest_results WHERE run_id = ?", [run_id]
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.description]
        return dict(zip(cols, row))

    def insert_paper_position(self, run_id: int, pos: dict) -> int:
        result = self.conn.execute("""
            INSERT INTO paper_positions (run_id, entry_date, entry_price, stop_price, tranche)
            VALUES (?, ?, ?, ?, ?) RETURNING id
        """, [run_id, pos["entry_date"], pos["entry_price"], pos["stop_price"], pos["tranche"]]).fetchone()
        return result[0]

    def close_paper_position(self, pos_id: int, exit_date: date, exit_price: float, exit_reason: str, pnl_r: float):
        self.conn.execute("""
            UPDATE paper_positions
            SET exit_date = ?, exit_price = ?, exit_reason = ?, pnl_r = ?
            WHERE id = ?
        """, [exit_date, exit_price, exit_reason, pnl_r, pos_id])

    def get_paper_position(self, pos_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM paper_positions WHERE id = ?", [pos_id]
        ).fetchone()
        if not row:
            return None
        cols = [d[0] for d in self.conn.description]
        return dict(zip(cols, row))

    def open_paper_positions(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM paper_positions WHERE run_id = ? AND exit_date IS NULL",
            [run_id]
        ).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def upsert_equity_curve(self, dt: date, equity: float, drawdown_pct: float, run_id: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO equity_curve (date, equity, drawdown_pct, run_id) VALUES (?, ?, ?, ?)",
            [dt, equity, drawdown_pct, run_id]
        )

    def get_equity_curve(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM equity_curve WHERE run_id = ? ORDER BY date", [run_id]
        ).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_recent_runs_with_metrics(self, n: int) -> list[dict]:
        rows = self.conn.execute("""
            SELECT sr.id, sr.created_at, sr.status,
                   br.sharpe, br.sortino, br.max_drawdown_pct, br.win_rate, br.avg_rr, br.cagr
            FROM strategy_runs sr
            LEFT JOIN backtest_results br ON sr.id = br.run_id
            WHERE sr.status NOT IN ('parse_error')
            ORDER BY sr.created_at DESC LIMIT ?
        """, [n]).fetchall()
        if not rows:
            return []
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def close(self):
        self.conn.close()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_db.py -v
```
Expected: all 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add storage/db.py tests/test_db.py
git commit -m "feat: database layer with full DuckDB schema"
```

---

### Task 3: Data fetcher

**Files:**
- Create: `data/fetcher.py`
- Create: `tests/test_fetcher.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_fetcher.py -v
```
Expected: `ModuleNotFoundError: No module named 'data.fetcher'`

- [ ] **Step 3: Implement data/fetcher.py**

```python
# data/fetcher.py
import logging
import requests
import pandas as pd
import pandas_ta as ta
from datetime import date, datetime, timedelta
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
            ts = date.fromtimestamp(k[0] / 1000)
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
        last_ts = date.fromtimestamp(klines[-1][0] / 1000)
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
        df["bb_upper"] = bb["BBU_20_2.0"]
        df["bb_mid"] = bb["BBM_20_2.0"]
        df["bb_lower"] = bb["BBL_20_2.0"]
    else:
        df["bb_upper"] = df["bb_mid"] = df["bb_lower"] = None
    df["volume_sma_20"] = ta.sma(volume, length=20)

    indicator_cols = ["ema_20", "ema_50", "ema_200", "atr_14", "adx_14", "rsi_14",
                      "bb_upper", "bb_lower", "bb_mid", "volume_sma_20"]
    df = df.dropna(subset=indicator_cols).reset_index(drop=True)
    return df[["timestamp"] + indicator_cols]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_fetcher.py -v
```
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add data/fetcher.py tests/test_fetcher.py
git commit -m "feat: Binance OHLCV fetcher and indicator computation"
```

---

### Task 4: LLM strategy generator

**Files:**
- Create: `research/generator.py`
- Create: `tests/test_generator.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_generator.py -v
```
Expected: `ModuleNotFoundError: No module named 'research.generator'`

- [ ] **Step 3: Implement research/generator.py**

```python
# research/generator.py
import json
import logging
import re
import anthropic
import pandas as pd
import config

logger = logging.getLogger(__name__)

STRATEGY_SCHEMA_KEYS = [
    "name", "thesis", "regime_filter", "entry_long", "entry_short",
    "exit", "position_sizing", "expected_profile", "failure_modes"
]

PERSONA = """You are an elite quantitative trading researcher at a crypto hedge fund.
You care about expectancy, risk-adjusted returns, and robustness — not excitement.
You design mechanically executable rule-based strategies only."""

CONSTRAINTS = """Constraints:
- Use at least 3 non-correlated signals from: trend, volatility regime, market structure, momentum
- entry_long and entry_short: array of condition objects with "condition" (string) and "operator" ("AND"/"OR")
- exit.stop_rule: invalidation-based (structural or ATR-derived), never fixed percentage
- exit.targets: array with r_multiple and tranche_pct, partial scale-outs required
- position_sizing: formula using (equity * 0.01) / stop_distance
- regime_filter: conditions under which strategy goes to cash (must be explicit)
- failure_modes: EXACTLY 3 specific ways this strategy loses money
- expected_profile.win_rate: realistic (0.35–0.60 range)"""

OUTPUT_FORMAT = """Respond ONLY with a JSON object matching this schema exactly:
{
  "name": string,
  "thesis": string (one paragraph),
  "regime_filter": object with "logic" (string condition) and any thresholds,
  "entry_long": array of {"condition": string, "operator": "AND"|"OR"} (last item omits operator),
  "entry_short": array of {"condition": string, "operator": "AND"|"OR"} (last item omits operator),
  "exit": {
    "stop_rule": string,
    "targets": [{"r_multiple": number, "tranche_pct": number}],
    "trailing_rule": string
  },
  "position_sizing": string (formula),
  "expected_profile": {
    "win_rate": number,
    "avg_rr": number,
    "underperformance_conditions": string,
    "drawdown_profile": string
  },
  "failure_modes": [string, string, string]
}
No markdown fences. No explanation. JSON only."""


def build_prompt(recent_indicators: pd.DataFrame, prior_runs: list[dict]) -> str:
    indicator_json = recent_indicators.tail(90).to_json(orient="records", date_format="iso")

    prior_context = ""
    if prior_runs:
        summaries = []
        for r in prior_runs[-5:]:
            summaries.append(
                f"Run {r['id']} ({r.get('created_at', '')[:10]}): "
                f"Sharpe={r.get('sharpe')}, MaxDD={r.get('max_drawdown_pct')}, "
                f"WinRate={r.get('win_rate')}, AvgRR={r.get('avg_rr')}"
            )
        prior_context = "\n\nPrior strategy run results (learn from these):\n" + "\n".join(summaries)

    return f"""{PERSONA}

{CONSTRAINTS}

Current market data (last 90 days of daily indicators, BTC/USDT):
{indicator_json}
{prior_context}

Task: Design a complete rule-based BTC daily timeframe trading strategy.

{OUTPUT_FORMAT}"""


def parse_strategy_response(raw: str) -> dict:
    # Strip markdown fences if present
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        strategy = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Strategy response is not valid JSON: {e}")

    for key in STRATEGY_SCHEMA_KEYS:
        if key not in strategy:
            raise ValueError(f"Strategy response missing required key: '{key}'")

    if not isinstance(strategy["failure_modes"], list) or len(strategy["failure_modes"]) != 3:
        raise ValueError("failure_modes must be a list of exactly 3 strings")

    return strategy


def generate_strategy(recent_indicators: pd.DataFrame, prior_runs: list[dict]) -> dict:
    """Call Claude to generate a strategy. Returns parsed strategy dict."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = build_prompt(recent_indicators, prior_runs)

    logger.info(f"Calling {config.CLAUDE_MODEL} for strategy generation...")
    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text
    logger.info("Strategy response received, parsing...")
    return parse_strategy_response(raw)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_generator.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add research/generator.py tests/test_generator.py
git commit -m "feat: LLM strategy generator with prompt builder and JSON parser"
```

---

### Task 5: Backtesting engine

**Files:**
- Create: `backtest/engine.py`
- Create: `tests/test_backtest.py`

- [ ] **Step 1: Write failing tests**

```python
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
        dt = date(2023, 1, 1)
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_backtest.py -v
```
Expected: `ModuleNotFoundError: No module named 'backtest.engine'`

- [ ] **Step 3: Implement backtest/engine.py**

```python
# backtest/engine.py
import math
import logging
import pandas as pd
import numpy as np
from typing import Any

logger = logging.getLogger(__name__)

# Simple expression evaluator for strategy conditions.
# Conditions are strings like "close > ema_200" or "adx_14 > 20".
# Only supports: >, <, >=, <=, ==, != with numeric RHS or field RHS.
def _eval_condition(condition: str, bar: dict) -> bool:
    ops = [">=", "<=", "!=", ">", "<", "=="]
    for op in ops:
        if op in condition:
            lhs, rhs = [s.strip() for s in condition.split(op, 1)]
            left = bar.get(lhs, float(lhs) if lhs.replace(".", "").lstrip("-").isdigit() else None)
            try:
                right = float(rhs)
            except ValueError:
                right = bar.get(rhs)
            if left is None or right is None:
                return False
            if op == ">":  return left > right
            if op == "<":  return left < right
            if op == ">=": return left >= right
            if op == "<=": return left <= right
            if op == "==": return left == right
            if op == "!=": return left != right
    return False


def evaluate_regime_filter(regime_filter: dict, bar: dict) -> bool:
    logic = regime_filter.get("logic", "")
    if not logic:
        return True
    return _eval_condition(logic, bar)


def evaluate_conditions(conditions: list[dict], bar: dict) -> bool:
    """Evaluate a list of AND conditions (operator field is informational only — all must be True)."""
    return all(_eval_condition(c["condition"], bar) for c in conditions)


def _compute_stop(strategy: dict, bar: dict, side: str) -> float:
    atr = bar.get("atr_14", 0)
    if side == "long":
        return bar["close"] - 1.5 * atr
    else:
        return bar["close"] + 1.5 * atr


def _compute_metrics(trades: list[dict], equity_curve: list[float], starting_equity: float, total_bars: int) -> dict:
    if not trades:
        return {
            "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0, "max_drawdown_days": 0,
            "win_rate": 0.0, "avg_rr": 0.0, "total_trades": 0,
            "pct_time_in_market": 0.0, "cagr": 0.0,
        }

    returns = pd.Series(equity_curve).pct_change().dropna()
    mean_r = returns.mean()
    std_r = returns.std()
    downside = returns[returns < 0].std()
    sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
    sortino = (mean_r / downside * math.sqrt(252)) if downside > 0 else 0.0

    eq = pd.Series(equity_curve)
    rolling_max = eq.cummax()
    dd = (eq - rolling_max) / rolling_max
    max_dd_pct = float(abs(dd.min()))
    # Drawdown duration: longest consecutive stretch below peak
    in_dd = (dd < 0).astype(int)
    max_dd_days = int(in_dd.groupby((in_dd == 0).cumsum()).cumsum().max())

    rr_values = [t["pnl_r"] for t in trades if t.get("pnl_r") is not None]
    win_rate = len([r for r in rr_values if r > 0]) / len(rr_values) if rr_values else 0.0
    avg_rr = float(np.mean(rr_values)) if rr_values else 0.0

    bars_in_trade = sum(t.get("bars_held", 0) for t in trades)
    pct_time = bars_in_trade / total_bars if total_bars > 0 else 0.0
    years = total_bars / 365
    cagr = ((equity_curve[-1] / starting_equity) ** (1 / years) - 1) if years > 0 else 0.0

    return {
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "max_drawdown_days": max_dd_days,
        "win_rate": round(win_rate, 4),
        "avg_rr": round(avg_rr, 4),
        "total_trades": len(trades),
        "pct_time_in_market": round(pct_time, 4),
        "cagr": round(cagr, 4),
    }


def run_backtest(strategy: dict, df: pd.DataFrame, starting_equity: float = 100000.0) -> dict:
    """
    Bar-by-bar backtest of a strategy dict against a DataFrame of OHLCV+indicators.
    Returns metrics dict.
    """
    equity = starting_equity
    position = None  # None or dict with keys: side, entry_price, stop_price, targets, entry_bar
    trades = []
    equity_curve = [equity]
    regime_filter = strategy.get("regime_filter", {})
    entry_long = strategy.get("entry_long", [])
    entry_short = strategy.get("entry_short", [])
    targets = strategy["exit"]["targets"]
    total_bars = len(df)

    for i, row in df.iterrows():
        bar = row.to_dict()
        # Convert timestamp to date if needed
        if hasattr(bar["timestamp"], "date"):
            bar["timestamp"] = bar["timestamp"].date()

        # --- Regime filter ---
        in_regime = evaluate_regime_filter(regime_filter, bar)
        if not in_regime:
            if position is None:
                equity_curve.append(equity)
                continue

        # --- Manage open position ---
        if position is not None:
            side = position["side"]
            entry_price = position["entry_price"]
            stop_price = position["stop_price"]
            stop_distance = abs(entry_price - stop_price)

            # Check stop
            hit_stop = (side == "long" and bar["low"] <= stop_price) or \
                       (side == "short" and bar["high"] >= stop_price)

            if hit_stop:
                pnl_r = -1.0
                risk_amount = equity * 0.01
                equity -= risk_amount
                trades.append({
                    "side": side, "entry_price": entry_price, "exit_price": stop_price,
                    "exit_reason": "stop", "pnl_r": pnl_r,
                    "bars_held": i - position["entry_bar"],
                })
                position = None
                equity_curve.append(equity)
                continue

            # Check targets (use first unmet target)
            remaining_targets = position.get("remaining_targets", list(targets))
            if remaining_targets:
                t = remaining_targets[0]
                target_price = (entry_price + t["r_multiple"] * stop_distance) if side == "long" \
                               else (entry_price - t["r_multiple"] * stop_distance)
                hit_target = (side == "long" and bar["high"] >= target_price) or \
                             (side == "short" and bar["low"] <= target_price)
                if hit_target:
                    pnl_r = t["r_multiple"] * t["tranche_pct"]
                    risk_amount = equity * 0.01
                    equity += risk_amount * t["r_multiple"] * t["tranche_pct"]
                    remaining_targets = remaining_targets[1:]
                    position["remaining_targets"] = remaining_targets
                    # Trail stop to breakeven after first target
                    position["stop_price"] = entry_price
                    if not remaining_targets:
                        # All tranches closed
                        total_pnl_r = sum(t2["r_multiple"] * t2["tranche_pct"] for t2 in targets)
                        trades.append({
                            "side": side, "entry_price": entry_price, "exit_price": target_price,
                            "exit_reason": "target", "pnl_r": total_pnl_r,
                            "bars_held": i - position["entry_bar"],
                        })
                        position = None

            equity_curve.append(equity)
            continue

        # --- Entry logic ---
        if position is None:
            if evaluate_conditions(entry_long, bar):
                stop = _compute_stop(strategy, bar, "long")
                position = {
                    "side": "long",
                    "entry_price": bar["close"],
                    "stop_price": stop,
                    "remaining_targets": list(targets),
                    "entry_bar": i,
                }
            elif evaluate_conditions(entry_short, bar):
                stop = _compute_stop(strategy, bar, "short")
                position = {
                    "side": "short",
                    "entry_price": bar["close"],
                    "stop_price": stop,
                    "remaining_targets": list(targets),
                    "entry_bar": i,
                }

        equity_curve.append(equity)

    return _compute_metrics(trades, equity_curve, starting_equity, total_bars)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_backtest.py -v
```
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/engine.py tests/test_backtest.py
git commit -m "feat: bar-by-bar backtesting engine with regime filter and partial scale-outs"
```

---

### Task 6: Paper trading engine

**Files:**
- Create: `paper/trader.py`
- Create: `tests/test_paper_trader.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_paper_trader.py -v
```
Expected: `ModuleNotFoundError: No module named 'paper.trader'`

- [ ] **Step 3: Implement paper/trader.py**

```python
# paper/trader.py
import logging
from datetime import date
from storage.db import Database
from backtest.engine import evaluate_regime_filter, evaluate_conditions

logger = logging.getLogger(__name__)

class PaperTrader:
    def __init__(self, db: Database, run_id: int, strategy: dict, starting_equity: float):
        self.db = db
        self.run_id = run_id
        self.strategy = strategy
        self.equity = starting_equity
        self.peak_equity = starting_equity
        self._position = None  # active open position dict or None

    def process_bar(self, bar: dict):
        """Evaluate one daily bar. Updates DB with any position changes and equity snapshot."""
        regime_ok = evaluate_regime_filter(self.strategy.get("regime_filter", {}), bar)
        dt = bar["timestamp"]

        if self._position is not None:
            self._manage_position(bar)
        elif regime_ok:
            self._try_entry(bar)

        # Equity snapshot
        self.peak_equity = max(self.peak_equity, self.equity)
        drawdown = (self.equity - self.peak_equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        self.db.upsert_equity_curve(dt, self.equity, drawdown, self.run_id)

        if not regime_ok:
            logger.info(f"[{dt}] Regime filter inactive — sitting in cash")

    def _try_entry(self, bar: dict):
        entry_long = self.strategy.get("entry_long", [])
        entry_short = self.strategy.get("entry_short", [])
        atr = bar.get("atr_14", 0)

        if evaluate_conditions(entry_long, bar):
            stop = bar["close"] - 1.5 * atr
            pos_id = self.db.insert_paper_position(self.run_id, {
                "entry_date": bar["timestamp"],
                "entry_price": bar["close"],
                "stop_price": stop,
                "tranche": 1,
            })
            self._position = {
                "id": pos_id, "side": "long",
                "entry_price": bar["close"], "stop_price": stop,
                "remaining_targets": list(self.strategy["exit"]["targets"]),
                "stop_distance": abs(bar["close"] - stop),
            }
            logger.info(f"[{bar['timestamp']}] LONG entry at {bar['close']:.2f}, stop {stop:.2f}")

        elif evaluate_conditions(entry_short, bar):
            stop = bar["close"] + 1.5 * atr
            pos_id = self.db.insert_paper_position(self.run_id, {
                "entry_date": bar["timestamp"],
                "entry_price": bar["close"],
                "stop_price": stop,
                "tranche": 1,
            })
            self._position = {
                "id": pos_id, "side": "short",
                "entry_price": bar["close"], "stop_price": stop,
                "remaining_targets": list(self.strategy["exit"]["targets"]),
                "stop_distance": abs(bar["close"] - stop),
            }
            logger.info(f"[{bar['timestamp']}] SHORT entry at {bar['close']:.2f}, stop {stop:.2f}")

    def _manage_position(self, bar: dict):
        side = self._position["side"]
        stop = self._position["stop_price"]
        entry = self._position["entry_price"]
        stop_dist = self._position["stop_distance"]
        risk = self.equity * 0.01

        hit_stop = (side == "long" and bar["low"] <= stop) or \
                   (side == "short" and bar["high"] >= stop)

        if hit_stop:
            self.equity -= risk
            self.db.close_paper_position(
                self._position["id"], bar["timestamp"], stop, "stop", -1.0
            )
            logger.info(f"[{bar['timestamp']}] Stop hit. Equity: {self.equity:.2f}")
            self._position = None
            return

        remaining = self._position.get("remaining_targets", [])
        if remaining:
            t = remaining[0]
            target_px = (entry + t["r_multiple"] * stop_dist) if side == "long" \
                        else (entry - t["r_multiple"] * stop_dist)
            hit_target = (side == "long" and bar["high"] >= target_px) or \
                         (side == "short" and bar["low"] <= target_px)
            if hit_target:
                partial_pnl_r = t["r_multiple"] * t["tranche_pct"]
                self.equity += risk * partial_pnl_r
                self._position["remaining_targets"] = remaining[1:]
                self._position["stop_price"] = entry  # trail to breakeven
                logger.info(f"[{bar['timestamp']}] Target {t['r_multiple']}R hit. Equity: {self.equity:.2f}")

                if not self._position["remaining_targets"]:
                    total_r = sum(t2["r_multiple"] * t2["tranche_pct"] for t2 in self.strategy["exit"]["targets"])
                    self.db.close_paper_position(
                        self._position["id"], bar["timestamp"], target_px, "target", total_r
                    )
                    self._position = None
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_paper_trader.py -v
```
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add paper/trader.py tests/test_paper_trader.py
git commit -m "feat: paper trading state machine with position tracking and equity curve"
```

---

### Task 7: Reporting query library

**Files:**
- Create: `reporting/reporter.py`
- Create: `tests/test_reporter.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_reporter.py
import pytest
from datetime import date
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_reporter.py -v
```
Expected: `ModuleNotFoundError: No module named 'reporting.reporter'`

- [ ] **Step 3: Implement reporting/reporter.py**

```python
# reporting/reporter.py
import json
from storage.db import Database

def get_runs_summary(db: Database) -> list[dict]:
    return db.all_runs()

def get_run_detail(db: Database, run_id: int) -> dict:
    run = db.get_strategy_run(run_id)
    backtest = db.get_backtest_results(run_id)
    positions = db.open_paper_positions(run_id)
    strategy = json.loads(run["strategy_json"]) if run and run.get("strategy_json") else {}
    return {"run": run, "backtest": backtest, "positions": positions, "strategy": strategy}

def get_equity_chart_data(db: Database, run_id: int) -> dict:
    curve = db.get_equity_curve(run_id)
    return {
        "dates": [str(r["date"]) for r in curve],
        "equity": [r["equity"] for r in curve],
        "drawdown": [r["drawdown_pct"] for r in curve],
    }

def get_compare_data(db: Database) -> list[dict]:
    return db.all_runs()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_reporter.py -v
```
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add reporting/reporter.py tests/test_reporter.py
git commit -m "feat: reporting query library for dashboard data access"
```

---

### Task 8: Agent orchestrator

**Files:**
- Create: `agent.py`
- Create: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests**

```python
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
    db = MagicMock()
    db.latest_ohlcv_timestamp.return_value = date(2024, 1, 1)
    db.get_recent_indicators.return_value = pd.DataFrame()
    db.get_recent_runs_with_metrics.return_value = []
    db.insert_strategy_run.return_value = 42

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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_agent.py -v
```
Expected: `ModuleNotFoundError: No module named 'agent'`

- [ ] **Step 3: Implement agent.py**

```python
# agent.py
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

import config
from data.fetcher import fetch_ohlcv, compute_indicators
from research.generator import generate_strategy
from backtest.engine import run_backtest
from paper.trader import PaperTrader
from storage.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB = Database(config.DB_PATH)
DB.init_schema()


def run_pipeline(runs_dir: str = config.RUNS_DIR):
    logger.info("=== Daily research pipeline start ===")
    Path(runs_dir).mkdir(exist_ok=True)
    today = date.today()

    # 1. Fetch and store OHLCV + indicators
    last_ts = DB.latest_ohlcv_timestamp()
    fetch_start = date.fromisoformat(config.BACKFILL_START) if last_ts is None \
                  else last_ts + timedelta(days=1)

    if fetch_start <= today:
        rows = fetch_ohlcv(fetch_start, today, backfill_start=date.fromisoformat(config.BACKFILL_START))
        if rows:
            import pandas as pd
            df_ohlcv = pd.DataFrame(rows)
            DB.upsert_ohlcv(rows)
            all_ohlcv = DB.get_ohlcv(date.fromisoformat(config.BACKFILL_START), today)
            indicators_df = compute_indicators(all_ohlcv)
            if not indicators_df.empty:
                DB.upsert_indicators(indicators_df.to_dict("records"))
            logger.info(f"Fetched {len(rows)} new candles, computed indicators.")

    # 2. Generate strategy
    recent_indicators = DB.get_recent_indicators(90)
    prior_runs = DB.get_recent_runs_with_metrics(5)
    run_id = DB.insert_strategy_run("{}")  # placeholder until parsed

    try:
        strategy = generate_strategy(recent_indicators, prior_runs)
        DB.update_strategy_status.__self__ if False else None  # type hint hint
        # Update with real JSON
        DB.conn.execute(
            "UPDATE strategy_runs SET strategy_json = ? WHERE id = ?",
            [json.dumps(strategy), run_id]
        )
        logger.info(f"Strategy generated: {strategy['name']}")
    except (ValueError, Exception) as e:
        logger.error(f"Strategy generation/parse failed: {e}")
        DB.update_strategy_status(run_id, "parse_error", notes=str(e))
        _write_log(runs_dir, run_id, None, None, error=str(e))
        return

    # 3. Backtest
    backtest_end = today
    backtest_start = today - timedelta(days=config.BACKTEST_WINDOW_DAYS)
    df = DB.get_ohlcv(backtest_start, backtest_end)
    indicators = DB.get_indicators(backtest_start, backtest_end)
    if not df.empty and not indicators.empty:
        merged = df.merge(indicators, on="timestamp")
        metrics = run_backtest(strategy, merged, starting_equity=config.STARTING_EQUITY)
        metrics["backtest_start"] = backtest_start
        metrics["backtest_end"] = backtest_end
        DB.insert_backtest_results(run_id, metrics)
        logger.info(f"Backtest complete: Sharpe={metrics['sharpe']}, MaxDD={metrics['max_drawdown_pct']}")
    else:
        metrics = None
        logger.warning("Not enough data for backtest.")

    # 4. Set pending and write log
    DB.update_strategy_status(run_id, "pending_approval")
    _write_log(runs_dir, run_id, strategy, metrics)
    logger.info(f"Run {run_id} pending approval. Log written to {runs_dir}/")


def _write_log(runs_dir: str, run_id: int, strategy, metrics, error: str = None):
    filename = Path(runs_dir) / f"{date.today().isoformat()}.log"
    with open(filename, "w") as f:
        f.write(f"=== Trading Research Bot — Run {run_id} ===\n")
        f.write(f"Date: {date.today()}\n\n")
        if error:
            f.write(f"ERROR: {error}\nStatus: parse_error\n")
            return
        f.write(f"Strategy: {strategy['name']}\n")
        f.write(f"Thesis: {strategy['thesis']}\n\n")
        if metrics:
            f.write("--- Backtest Results ---\n")
            f.write(f"Sharpe:         {metrics['sharpe']}\n")
            f.write(f"Sortino:        {metrics['sortino']}\n")
            f.write(f"Max Drawdown:   {metrics['max_drawdown_pct']:.1%} ({metrics['max_drawdown_days']} days)\n")
            f.write(f"Win Rate:       {metrics['win_rate']:.1%}\n")
            f.write(f"Avg R:R:        {metrics['avg_rr']}\n")
            f.write(f"Total Trades:   {metrics['total_trades']}\n")
            f.write(f"Time in Market: {metrics['pct_time_in_market']:.1%}\n")
            f.write(f"CAGR:           {metrics['cagr']:.1%}\n\n")
        f.write("--- Full Strategy Spec ---\n")
        f.write(json.dumps(strategy, indent=2))
        f.write(f"\n\nStatus: pending_approval (run_id={run_id})\n")
        f.write("Approve at http://localhost:8080/runs\n")


def run_paper_trading():
    logger.info("=== Daily paper trading evaluation ===")
    active = DB.get_active_strategy()
    if active is None:
        logger.info("No approved strategy. Skipping paper trading.")
        return

    approved_count = DB.conn.execute(
        "SELECT COUNT(*) FROM strategy_runs WHERE status = 'approved'"
    ).fetchone()[0]
    if approved_count > 1:
        logger.error("Multiple approved strategies found. Skipping paper trading to avoid conflict.")
        return

    strategy = json.loads(active["strategy_json"])
    trader = PaperTrader(DB, active["id"], strategy, starting_equity=config.STARTING_EQUITY)

    today = date.today()
    bar_rows = DB.get_ohlcv(today, today)
    ind_rows = DB.get_indicators(today, today)
    if bar_rows.empty or ind_rows.empty:
        logger.warning("No data for today yet. Skipping paper evaluation.")
        return

    merged = bar_rows.merge(ind_rows, on="timestamp")
    for _, row in merged.iterrows():
        trader.process_bar(row.to_dict())
    logger.info("Paper trading evaluation complete.")


def main():
    scheduler = BlockingScheduler(timezone="UTC")
    h, m = config.SCHEDULE_TIME.split(":")
    ph, pm = config.PAPER_EVAL_TIME.split(":")
    scheduler.add_job(run_pipeline, "cron", hour=int(h), minute=int(m))
    scheduler.add_job(run_paper_trading, "cron", hour=int(ph), minute=int(pm))
    logger.info(f"Agent started. Pipeline at {config.SCHEDULE_TIME} UTC, paper at {config.PAPER_EVAL_TIME} UTC.")
    scheduler.start()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_agent.py -v
```
Expected: all 2 tests PASS

- [ ] **Step 5: Run full test suite to confirm nothing is broken**

```bash
pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add agent.py tests/test_agent.py
git commit -m "feat: APScheduler agent orchestrating full daily pipeline"
```

---

### Task 9: Verify end-to-end pipeline manually

- [ ] **Step 1: Set up .env with API key**

```bash
export ANTHROPIC_API_KEY="your-key-here"
export DB_PATH="data.duckdb"
```

- [ ] **Step 2: Run pipeline once manually**

```bash
python -c "from agent import run_pipeline; run_pipeline()"
```
Expected: logs showing fetch → generate → backtest → log written to `runs/YYYY-MM-DD.log`

- [ ] **Step 3: Verify DB has data**

```bash
python -c "
import duckdb
conn = duckdb.connect('data.duckdb')
print('OHLCV rows:', conn.execute('SELECT COUNT(*) FROM ohlcv').fetchone())
print('Runs:', conn.execute('SELECT id, status FROM strategy_runs').fetchall())
print('Backtest:', conn.execute('SELECT * FROM backtest_results').fetchall())
"
```
Expected: OHLCV count > 2000, one run with status `pending_approval`, one backtest result row

- [ ] **Step 4: Commit**

```bash
git add .
git commit -m "chore: verified end-to-end pipeline runs successfully"
```
