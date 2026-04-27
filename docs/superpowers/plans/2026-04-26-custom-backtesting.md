# Custom Backtesting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a custom backtesting UI that lets users re-run any strategy across different candle intervals (15m–1w), date ranges, and regime filter modes, saving results to the DB for side-by-side comparison.

**Architecture:** The `ohlcv` and `indicators` tables are migrated to a multi-interval schema (composite PK on `timestamp + interval`). Intraday OHLCV is fetched incrementally and stored; indicators are computed on-demand (never stored for intraday). Backtests run asynchronously via FastAPI `BackgroundTasks`, with status polling via meta-refresh. Results are stored in a new `custom_backtests` table.

**Tech Stack:** Python 3.12, DuckDB, pandas, pandas-ta, FastAPI + Jinja2, Binance US REST API (`/api/v3/klines`)

---

## File Structure

**Create:**
- `backtest/custom_runner.py` — `run_custom_backtest()` with regime mode logic
- `dashboard/templates/backtest_form.html` — strategy/interval/date/regime form
- `dashboard/templates/backtest_status.html` — async polling status page
- `dashboard/templates/backtest_result.html` — result + comparison view
- `tests/test_custom_runner.py` — unit tests for all three regime modes

**Modify:**
- `storage/db.py` — schema migration + `custom_backtests` table + 9 new methods + update 6 existing methods
- `data/fetcher.py` — add `interval` param to `fetch_ohlcv`
- `dashboard/app.py` — 4 new routes + new reporter imports
- `reporting/reporter.py` — 2 new query functions
- `dashboard/templates/base.html` — add "Backtest" nav link
- `dashboard/templates/compare.html` — add Custom Backtests section
- `tests/test_db.py` — migration test + new method tests
- `tests/test_fetcher.py` — interval parameter tests
- `tests/test_dashboard.py` — new route tests

---

## Task 1: DB Schema Migration + `custom_backtests` Table

**Files:**
- Modify: `storage/db.py`
- Modify: `tests/test_db.py`

> Context: `storage/db.py` currently has `init_schema()` creating `ohlcv` and `indicators` with a single `timestamp DATE PRIMARY KEY`. We need to migrate those to `(timestamp TIMESTAMP, interval TEXT, PRIMARY KEY (timestamp, interval))` and add the `custom_backtests` table. The migration must be idempotent (safe to run on already-migrated DBs). Existing methods `upsert_ohlcv`, `upsert_indicators`, `get_ohlcv`, `get_indicators`, `get_recent_indicators`, and `latest_ohlcv_timestamp` must be updated to default to `interval='1d'` so `agent.py` continues working without changes.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/aarbuckle/claude-projects/trading-research-bot
python -m pytest tests/test_db.py::test_migration_preserves_ohlcv_data tests/test_db.py::test_init_schema_creates_custom_backtests_table tests/test_db.py::test_init_schema_is_idempotent_with_new_schema -v
```

Expected: FAIL (old schema, no custom_backtests table)

- [ ] **Step 3: Add `import logging` at top of `storage/db.py` and add `_migrate_interval_schema()`**

At the top of `storage/db.py`, change the imports block to add logging:

```python
# storage/db.py
import logging
import threading
import duckdb
import pandas as pd
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)
```

Then add this method inside the `Database` class (before `init_schema`):

```python
def _migrate_interval_schema(self):
    """Migrate ohlcv and indicators to multi-interval schema if not already done. Idempotent."""
    ohlcv_cols = {r[1] for r in self._exec("PRAGMA table_info(ohlcv)").fetchall()}
    if 'interval' not in ohlcv_cols:
        self._exec("ALTER TABLE ohlcv RENAME TO ohlcv_old")
        self._exec("""
            CREATE TABLE ohlcv (
                timestamp TIMESTAMP, interval TEXT,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
                PRIMARY KEY (timestamp, interval)
            )
        """)
        self._exec("""
            INSERT INTO ohlcv
            SELECT timestamp::TIMESTAMP, '1d', open, high, low, close, volume
            FROM ohlcv_old
        """)
        self._exec("DROP TABLE ohlcv_old")
        logger.info("Migrated ohlcv table to multi-interval schema.")

    ind_cols = {r[1] for r in self._exec("PRAGMA table_info(indicators)").fetchall()}
    if 'interval' not in ind_cols:
        self._exec("ALTER TABLE indicators RENAME TO indicators_old")
        self._exec("""
            CREATE TABLE indicators (
                timestamp TIMESTAMP, interval TEXT,
                ema_20 DOUBLE, ema_50 DOUBLE, ema_200 DOUBLE,
                atr_14 DOUBLE, adx_14 DOUBLE, rsi_14 DOUBLE,
                bb_upper DOUBLE, bb_lower DOUBLE, bb_mid DOUBLE,
                volume_sma_20 DOUBLE,
                PRIMARY KEY (timestamp, interval)
            )
        """)
        self._exec("""
            INSERT INTO indicators
            SELECT timestamp::TIMESTAMP, '1d', ema_20, ema_50, ema_200,
                   atr_14, adx_14, rsi_14, bb_upper, bb_lower, bb_mid, volume_sma_20
            FROM indicators_old
        """)
        self._exec("DROP TABLE indicators_old")
        logger.info("Migrated indicators table to multi-interval schema.")
```

- [ ] **Step 4: Replace `init_schema()` with the migrated version**

Replace the entire `init_schema` method with:

```python
def init_schema(self):
    self._exec("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            timestamp TIMESTAMP,
            interval  TEXT,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
            PRIMARY KEY (timestamp, interval)
        )
    """)
    self._exec("""
        CREATE TABLE IF NOT EXISTS indicators (
            timestamp TIMESTAMP,
            interval  TEXT,
            ema_20 DOUBLE, ema_50 DOUBLE, ema_200 DOUBLE,
            atr_14 DOUBLE, adx_14 DOUBLE, rsi_14 DOUBLE,
            bb_upper DOUBLE, bb_lower DOUBLE, bb_mid DOUBLE,
            volume_sma_20 DOUBLE,
            PRIMARY KEY (timestamp, interval)
        )
    """)
    self._migrate_interval_schema()
    self._exec("CREATE SEQUENCE IF NOT EXISTS strategy_runs_id_seq")
    self._exec("""
        CREATE TABLE IF NOT EXISTS strategy_runs (
            id INTEGER PRIMARY KEY DEFAULT nextval('strategy_runs_id_seq'),
            created_at TIMESTAMP DEFAULT current_timestamp,
            strategy_json TEXT,
            status TEXT DEFAULT 'pending_approval',
            notes TEXT
        )
    """)
    self._exec("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            run_id INTEGER PRIMARY KEY REFERENCES strategy_runs(id),
            sharpe DOUBLE, sortino DOUBLE,
            max_drawdown_pct DOUBLE, max_drawdown_days INTEGER,
            win_rate DOUBLE, avg_rr DOUBLE,
            total_trades INTEGER, pct_time_in_market DOUBLE, cagr DOUBLE,
            backtest_start DATE, backtest_end DATE
        )
    """)
    self._exec("CREATE SEQUENCE IF NOT EXISTS paper_positions_id_seq")
    self._exec("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY DEFAULT nextval('paper_positions_id_seq'),
            run_id INTEGER REFERENCES strategy_runs(id),
            entry_date DATE, entry_price DOUBLE, stop_price DOUBLE, tranche INTEGER,
            exit_date DATE, exit_price DOUBLE, exit_reason TEXT, pnl_r DOUBLE
        )
    """)
    self._exec("""
        CREATE TABLE IF NOT EXISTS equity_curve (
            date DATE,
            equity DOUBLE, drawdown_pct DOUBLE,
            run_id INTEGER REFERENCES strategy_runs(id),
            PRIMARY KEY (date, run_id)
        )
    """)
    self._exec("CREATE SEQUENCE IF NOT EXISTS custom_backtests_id_seq")
    self._exec("""
        CREATE TABLE IF NOT EXISTS custom_backtests (
            id                      INTEGER PRIMARY KEY DEFAULT nextval('custom_backtests_id_seq'),
            created_at              TIMESTAMP DEFAULT current_timestamp,
            run_id                  INTEGER REFERENCES strategy_runs(id),
            interval                TEXT,
            date_from               TIMESTAMP,
            date_to                 TIMESTAMP,
            regime_filter_mode      TEXT,
            regime_filter_overrides TEXT,
            sharpe                  DOUBLE,
            sortino                 DOUBLE,
            max_drawdown_pct        DOUBLE,
            max_drawdown_days       INTEGER,
            win_rate                DOUBLE,
            avg_rr                  DOUBLE,
            total_trades            INTEGER,
            pct_time_in_market      DOUBLE,
            cagr                    DOUBLE,
            error_message           TEXT
        )
    """)
```

Note: `error_message TEXT` is added (not in spec's table DDL) to store error strings when `total_trades = -2`. The spec said to use `cagr`, but `cagr` is `DOUBLE` and cannot hold a string.

- [ ] **Step 5: Update existing methods to filter/default to `interval='1d'`**

Replace `upsert_ohlcv`, `latest_ohlcv_timestamp`, `get_ohlcv`, `upsert_indicators`, `get_indicators`, and `get_recent_indicators` with these versions:

```python
def upsert_ohlcv(self, rows: list[dict]):
    """Insert/replace 1d OHLCV rows. Delegates to upsert_ohlcv_interval."""
    self.upsert_ohlcv_interval(rows, '1d')

def latest_ohlcv_timestamp(self) -> Optional[date]:
    with self._lock:
        result = self.conn.execute(
            "SELECT MAX(timestamp)::DATE FROM ohlcv WHERE interval = '1d'"
        ).fetchone()
    return result[0] if result else None

def get_ohlcv(self, start: date, end: date) -> pd.DataFrame:
    return self._exec_df(
        """SELECT timestamp, open, high, low, close, volume FROM ohlcv
           WHERE interval = '1d' AND timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp""",
        [start, end]
    )

def upsert_indicators(self, rows: list[dict]):
    """Insert/replace 1d indicator rows. Delegates to upsert_indicators_interval."""
    self.upsert_indicators_interval(rows, '1d')

def get_indicators(self, start: date, end: date) -> pd.DataFrame:
    return self._exec_df(
        """SELECT timestamp, ema_20, ema_50, ema_200, atr_14, adx_14, rsi_14,
                  bb_upper, bb_lower, bb_mid, volume_sma_20
           FROM indicators
           WHERE interval = '1d' AND timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp""",
        [start, end]
    )

def get_recent_indicators(self, days: int) -> pd.DataFrame:
    return self._exec_df(
        """SELECT timestamp, ema_20, ema_50, ema_200, atr_14, adx_14, rsi_14,
                  bb_upper, bb_lower, bb_mid, volume_sma_20
           FROM indicators WHERE interval = '1d'
           ORDER BY timestamp DESC LIMIT ?""",
        [days]
    )
```

- [ ] **Step 6: Run the migration and schema tests**

```bash
python -m pytest tests/test_db.py -v
```

Expected: all tests PASS (migration test, custom_backtests table, idempotent test, and all existing DB tests)

- [ ] **Step 7: Commit**

```bash
git add storage/db.py tests/test_db.py
git commit -m "feat: migrate ohlcv/indicators to multi-interval schema, add custom_backtests table"
```

---

## Task 2: New DB Methods for Multi-Interval and Custom Backtests

**Files:**
- Modify: `storage/db.py`
- Modify: `tests/test_db.py`

> Context: Task 1 updated the schema. Now add the 9 new DB methods. `upsert_ohlcv_interval` and `upsert_indicators_interval` are new general-purpose insert methods that set the interval on each row. `get_ohlcv_interval`, `get_indicators_interval`, and `latest_ohlcv_timestamp_interval` serve intraday custom backtest reads. The `custom_backtests` CRUD methods support the async backtest flow.

- [ ] **Step 1: Write failing tests for the 9 new methods**

Add to `tests/test_db.py`:

```python
from datetime import datetime, timezone

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
    from datetime import datetime, timezone
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
    from datetime import datetime, timezone
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
    from datetime import datetime, timezone
    db.insert_custom_backtest({
        "run_id": run_id, "interval": "1d",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy", "regime_filter_overrides": "{}",
    })
    rows = db.all_custom_backtests()
    assert len(rows) == 1
    assert rows[0]["interval"] == "1d"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_db.py::test_upsert_ohlcv_interval_stores_intraday tests/test_db.py::test_insert_custom_backtest_and_get -v
```

Expected: FAIL (methods don't exist yet)

- [ ] **Step 3: Add the 9 new methods to `storage/db.py`**

Add these methods inside the `Database` class (after the existing `upsert_ohlcv` block, before `insert_strategy_run`):

```python
def upsert_ohlcv_interval(self, rows: list[dict], interval: str):
    if not rows:
        return
    df = pd.DataFrame([{**r, 'interval': interval} for r in rows])
    df = df[['timestamp', 'interval', 'open', 'high', 'low', 'close', 'volume']]
    with self._lock:
        self.conn.execute("INSERT OR REPLACE INTO ohlcv SELECT * FROM df")

def latest_ohlcv_timestamp_interval(self, interval: str) -> Optional[datetime]:
    with self._lock:
        result = self.conn.execute(
            "SELECT MAX(timestamp) FROM ohlcv WHERE interval = ?", [interval]
        ).fetchone()
    val = result[0] if result else None
    if val is None:
        return None
    # DuckDB returns TIMESTAMP as datetime; normalize to UTC-aware
    if hasattr(val, 'tzinfo') and val.tzinfo is None:
        from datetime import timezone
        val = val.replace(tzinfo=timezone.utc)
    return val

def get_ohlcv_interval(self, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    return self._exec_df(
        """SELECT timestamp, open, high, low, close, volume FROM ohlcv
           WHERE interval = ? AND timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp""",
        [interval, start, end]
    )

def upsert_indicators_interval(self, rows: list[dict], interval: str):
    if not rows:
        return
    df = pd.DataFrame([{**r, 'interval': interval} for r in rows])
    df = df[['timestamp', 'interval', 'ema_20', 'ema_50', 'ema_200',
             'atr_14', 'adx_14', 'rsi_14', 'bb_upper', 'bb_lower', 'bb_mid', 'volume_sma_20']]
    with self._lock:
        self.conn.execute("INSERT OR REPLACE INTO indicators SELECT * FROM df")

def get_indicators_interval(self, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    return self._exec_df(
        """SELECT timestamp, ema_20, ema_50, ema_200, atr_14, adx_14, rsi_14,
                  bb_upper, bb_lower, bb_mid, volume_sma_20
           FROM indicators
           WHERE interval = ? AND timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp""",
        [interval, start, end]
    )

def insert_custom_backtest(self, params: dict) -> int:
    with self._lock:
        result = self.conn.execute("""
            INSERT INTO custom_backtests
            (run_id, interval, date_from, date_to, regime_filter_mode,
             regime_filter_overrides, total_trades)
            VALUES (?, ?, ?, ?, ?, ?, -1)
            RETURNING id
        """, [
            params["run_id"], params["interval"],
            params["date_from"], params["date_to"],
            params["regime_filter_mode"], params["regime_filter_overrides"],
        ]).fetchone()
    return result[0]

def update_custom_backtest_results(self, backtest_id: int, metrics: dict):
    self._exec("""
        UPDATE custom_backtests SET
            sharpe = ?, sortino = ?, max_drawdown_pct = ?, max_drawdown_days = ?,
            win_rate = ?, avg_rr = ?, total_trades = ?, pct_time_in_market = ?, cagr = ?
        WHERE id = ?
    """, [
        metrics["sharpe"], metrics["sortino"], metrics["max_drawdown_pct"],
        metrics["max_drawdown_days"], metrics["win_rate"], metrics["avg_rr"],
        metrics["total_trades"], metrics["pct_time_in_market"], metrics["cagr"],
        backtest_id,
    ])

def get_custom_backtest(self, backtest_id: int) -> Optional[dict]:
    rows, cols = self._exec_rows(
        "SELECT * FROM custom_backtests WHERE id = ?", [backtest_id]
    )
    if not rows:
        return None
    return dict(zip(cols, rows[0]))

def all_custom_backtests(self) -> list[dict]:
    rows, cols = self._exec_rows("""
        SELECT cb.*, sr.strategy_json
        FROM custom_backtests cb
        LEFT JOIN strategy_runs sr ON cb.run_id = sr.id
        ORDER BY cb.created_at DESC
    """)
    if not rows:
        return []
    return [dict(zip(cols, r)) for r in rows]
```

Also add an error-setting helper (used by the background task on failure):

```python
def set_custom_backtest_error(self, backtest_id: int, error_message: str):
    self._exec("""
        UPDATE custom_backtests SET total_trades = -2, error_message = ?
        WHERE id = ?
    """, [error_message, backtest_id])
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_db.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add storage/db.py tests/test_db.py
git commit -m "feat: add multi-interval DB methods and custom_backtests CRUD"
```

---

## Task 3: `fetch_ohlcv` Interval Support

**Files:**
- Modify: `data/fetcher.py`
- Modify: `tests/test_fetcher.py`

> Context: `fetch_ohlcv` currently hardcodes `"interval": "1d"` in the Binance API params and returns rows with `date` timestamps. We need to add an `interval` parameter and use full `datetime` timestamps (not `date`) for all intervals so intraday bars retain their time component. The `agent.py` daily pipeline calls `fetch_ohlcv(start, today)` without `interval` — this must still work. `latest_ohlcv_timestamp()` in `db.py` casts to DATE, so the comparison `last_ts + timedelta(days=1) <= today` is unaffected.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_fetcher.py`:

```python
def test_fetch_ohlcv_accepts_interval_param():
    """interval param is passed to Binance API and included in returned rows."""
    with patch("data.fetcher.requests.get") as mock_get:
        mock_get.return_value.json.return_value = SAMPLE_KLINES
        mock_get.return_value.raise_for_status = MagicMock()
        rows = fetch_ohlcv(date(2024, 1, 1), date(2024, 1, 2), interval='1h')
    assert len(rows) == 2
    assert rows[0]["interval"] == "1h"
    call_params = mock_get.call_args[1]["params"]
    assert call_params["interval"] == "1h"


def test_fetch_ohlcv_default_interval_is_1d():
    with patch("data.fetcher.requests.get") as mock_get:
        mock_get.return_value.json.return_value = SAMPLE_KLINES
        mock_get.return_value.raise_for_status = MagicMock()
        rows = fetch_ohlcv(date(2024, 1, 1), date(2024, 1, 2))
    assert rows[0]["interval"] == "1d"


def test_fetch_ohlcv_timestamp_is_datetime():
    """Returned timestamps are datetime objects (not date) for all intervals."""
    with patch("data.fetcher.requests.get") as mock_get:
        mock_get.return_value.json.return_value = SAMPLE_KLINES
        mock_get.return_value.raise_for_status = MagicMock()
        rows = fetch_ohlcv(date(2024, 1, 1), date(2024, 1, 2))
    from datetime import datetime
    assert isinstance(rows[0]["timestamp"], datetime)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_fetcher.py::test_fetch_ohlcv_accepts_interval_param tests/test_fetcher.py::test_fetch_ohlcv_default_interval_is_1d tests/test_fetcher.py::test_fetch_ohlcv_timestamp_is_datetime -v
```

Expected: FAIL

- [ ] **Step 3: Update `fetch_ohlcv` in `data/fetcher.py`**

Replace the entire `fetch_ohlcv` function:

```python
def fetch_ohlcv(
    start: date,
    end: date,
    interval: str = '1d',
    backfill_start: Optional[date] = None,
) -> list[dict]:
    """Fetch BTC/USDT candles from Binance between start and end (inclusive).

    Returns rows with datetime timestamps (UTC-aware) for all intervals.
    """
    rows = []
    cursor_ms = int(datetime.combine(start, datetime.min.time()).timestamp() * 1000)
    end_ms = int(datetime.combine(end, datetime.max.time()).timestamp() * 1000)

    while cursor_ms <= end_ms:
        params = {
            "symbol": "BTCUSDT",
            "interval": interval,
            "startTime": cursor_ms,
            "limit": 1000,
        }
        resp = requests.get(config.BINANCE_KLINES_URL, params=params, timeout=30)
        resp.raise_for_status()
        klines = resp.json()
        if not klines:
            break
        for k in klines:
            bar_open_ms = k[0]
            if bar_open_ms > end_ms:
                break
            ts = datetime.fromtimestamp(bar_open_ms / 1000, tz=timezone.utc)
            rows.append({
                "timestamp": ts,
                "interval": interval,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        # Advance cursor past the last bar's close time (k[6] = close time ms)
        cursor_ms = klines[-1][6] + 1
        if len(klines) < 1000:
            break

    if rows and backfill_start and rows[0]["timestamp"].date() > backfill_start:
        logger.warning(
            f"Binance data starts at {rows[0]['timestamp'].date()}, "
            f"later than requested backfill start {backfill_start}. "
            f"Using earliest available data."
        )
    return rows
```

- [ ] **Step 4: Run all fetcher tests**

```bash
python -m pytest tests/test_fetcher.py -v
```

Expected: all PASS. The existing `test_fetch_ohlcv_returns_list_of_dicts` will need the timestamp assertion adjusted — update it:

```python
def test_fetch_ohlcv_returns_list_of_dicts():
    with patch("data.fetcher.requests.get") as mock_get:
        mock_get.return_value.json.return_value = SAMPLE_KLINES
        mock_get.return_value.raise_for_status = MagicMock()
        rows = fetch_ohlcv(date(2024, 1, 1), date(2024, 1, 2))
    assert len(rows) == 2
    # timestamp is now datetime, not date
    assert rows[0]["timestamp"].date() == date(2024, 1, 1)
    assert rows[0]["close"] == 40500.0
```

- [ ] **Step 5: Run the full test suite to confirm nothing broke**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add data/fetcher.py tests/test_fetcher.py
git commit -m "feat: add interval param to fetch_ohlcv, return datetime timestamps"
```

---

## Task 4: Custom Backtest Runner

**Files:**
- Create: `backtest/custom_runner.py`
- Create: `tests/test_custom_runner.py`

> Context: `run_backtest(strategy, df, starting_equity)` in `backtest/engine.py` uses `strategy["regime_filter"]["logic"]` (a string) via `evaluate_regime_filter`. An empty string or empty dict for `regime_filter` causes `evaluate_regime_filter` to return `True` (always trade). The custom runner modifies the strategy dict before calling `run_backtest`. Three modes: `'strategy'` (unchanged), `'disabled'` (empty regime_filter → always trade), `'custom'` (filter conditions by override booleans).

- [ ] **Step 1: Write failing tests**

Create `tests/test_custom_runner.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_custom_runner.py -v
```

Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Create `backtest/custom_runner.py`**

```python
# backtest/custom_runner.py
import pandas as pd
from backtest.engine import run_backtest


def run_custom_backtest(
    strategy: dict,
    df: pd.DataFrame,
    regime_filter_mode: str,
    regime_filter_overrides: dict,
    starting_equity: float = 100_000,
) -> dict:
    """Run a backtest with a modified regime filter.

    Args:
        strategy: Strategy dict as produced by the generator.
        df: Merged OHLCV + indicators DataFrame.
        regime_filter_mode: One of 'strategy', 'disabled', 'custom'.
        regime_filter_overrides: Mapping of condition string → bool.
            Only used when regime_filter_mode == 'custom'.
        starting_equity: Starting portfolio value in USD.

    Returns:
        9-key metrics dict identical to run_backtest output.
    """
    if regime_filter_mode == 'strategy':
        modified = strategy
    elif regime_filter_mode == 'disabled':
        modified = {**strategy, 'regime_filter': {}}
    else:  # 'custom'
        original_logic = strategy.get('regime_filter', {}).get('logic', '')
        conditions = [c.strip() for c in original_logic.split(' AND ') if c.strip()]
        enabled = [c for c in conditions if regime_filter_overrides.get(c, True)]
        if not enabled:
            modified = {**strategy, 'regime_filter': {}}
        else:
            modified = {
                **strategy,
                'regime_filter': {'logic': ' AND '.join(enabled)},
            }

    return run_backtest(modified, df, starting_equity=starting_equity)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_custom_runner.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/custom_runner.py tests/test_custom_runner.py
git commit -m "feat: add custom backtest runner with regime filter override support"
```

---

## Task 5: Reporter Functions

**Files:**
- Modify: `reporting/reporter.py`
- Modify: `tests/test_reporter.py`

> Context: `reporting/reporter.py` has 4 functions that delegate to DB methods and format data for templates. Two new functions are needed: `get_custom_backtests` for the compare page's custom backtests section, and `get_custom_backtest_detail` for the result page (returns custom backtest + original strategy backtest for comparison).

- [ ] **Step 1: Write failing tests**

Read `tests/test_reporter.py` first to see existing style. Then add:

```python
from datetime import datetime, timezone

def test_get_custom_backtests_empty(db):
    from reporting.reporter import get_custom_backtests
    assert get_custom_backtests(db) == []


def test_get_custom_backtests_returns_rows(db):
    from reporting.reporter import get_custom_backtests
    run_id = db.insert_strategy_run('{"name": "alpha"}')
    db.insert_custom_backtest({
        "run_id": run_id, "interval": "1h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy",
        "regime_filter_overrides": "{}",
    })
    rows = get_custom_backtests(db)
    assert len(rows) == 1
    assert rows[0]["interval"] == "1h"


def test_get_custom_backtest_detail(db):
    from reporting.reporter import get_custom_backtest_detail
    run_id = db.insert_strategy_run('{"name": "alpha"}')
    db.insert_backtest_results(run_id, {
        "sharpe": 1.5, "sortino": 2.0, "max_drawdown_pct": 0.15,
        "max_drawdown_days": 45, "win_rate": 0.55, "avg_rr": 1.8,
        "total_trades": 40, "pct_time_in_market": 0.6, "cagr": 0.22,
        "backtest_start": date(2023, 1, 1), "backtest_end": date(2024, 1, 1),
    })
    backtest_id = db.insert_custom_backtest({
        "run_id": run_id, "interval": "4h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "disabled",
        "regime_filter_overrides": "{}",
    })
    detail = get_custom_backtest_detail(db, backtest_id)
    assert detail["custom"]["interval"] == "4h"
    assert detail["original_backtest"]["sharpe"] == 1.5
    assert detail["run"]["id"] == run_id


def test_get_custom_backtest_detail_missing_returns_none(db):
    from reporting.reporter import get_custom_backtest_detail
    detail = get_custom_backtest_detail(db, 999)
    assert detail["custom"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_reporter.py -k "custom" -v
```

Expected: FAIL

- [ ] **Step 3: Add new functions to `reporting/reporter.py`**

Append to the end of `reporting/reporter.py`:

```python
def get_custom_backtests(db: Database) -> list[dict]:
    """All saved custom backtest results with strategy name, ordered by created_at DESC."""
    return db.all_custom_backtests()


def get_custom_backtest_detail(db: Database, backtest_id: int) -> dict:
    """Single custom backtest with full params + original strategy backtest metrics."""
    custom = db.get_custom_backtest(backtest_id)
    if custom is None:
        return {"custom": None, "original_backtest": None, "run": None}
    run = db.get_strategy_run(custom["run_id"])
    original_backtest = db.get_backtest_results(custom["run_id"])
    return {
        "custom": custom,
        "original_backtest": original_backtest,
        "run": run,
    }
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_reporter.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add reporting/reporter.py tests/test_reporter.py
git commit -m "feat: add get_custom_backtests and get_custom_backtest_detail reporter functions"
```

---

## Task 6: Dashboard Routes

**Files:**
- Modify: `dashboard/app.py`
- Modify: `tests/test_dashboard.py`

> Context: `dashboard/app.py` uses `create_app(db, pipeline_fn=None)`. Four new routes needed. The `POST /backtest/run` route validates bar count before starting, creates a DB row with `total_trades=-1`, then queues a `BackgroundTasks` task that fetches/computes/backtests and updates the row. `GET /backtest/<id>/status` polls via meta-refresh and redirects to the result when complete. All routes use existing `templates.TemplateResponse(request, name, ctx)` pattern.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_dashboard.py`:

```python
def test_backtest_form_returns_200(db):
    """GET /backtest returns the form page."""
    run_id = db.insert_strategy_run('{"name": "alpha", "regime_filter": {"logic": "adx_14 > 20"}}')
    db.update_strategy_status(run_id, "approved")
    from dashboard.app import create_app
    app = create_app(db)
    c = TestClient(app)
    response = c.get("/backtest")
    assert response.status_code == 200


def test_backtest_run_rejects_insufficient_bars(db):
    """POST /backtest/run with too-short date range returns 400."""
    run_id = db.insert_strategy_run('{"name": "alpha", "regime_filter": {"logic": "adx_14 > 20"}}')
    from dashboard.app import create_app
    app = create_app(db)
    c = TestClient(app)
    response = c.post("/backtest/run", data={
        "run_id": str(run_id),
        "interval": "1w",
        "date_preset": "custom",
        "date_from": "2024-01-01T00:00",
        "date_to": "2024-01-07T00:00",  # 1 week → ~1 bar at 1w interval
        "regime_filter_mode": "strategy",
    }, follow_redirects=False)
    assert response.status_code == 400


def test_backtest_status_in_progress(db):
    """GET /backtest/<id>/status returns 200 for in-progress backtest."""
    run_id = db.insert_strategy_run('{}')
    from datetime import datetime, timezone
    backtest_id = db.insert_custom_backtest({
        "run_id": run_id, "interval": "1h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy",
        "regime_filter_overrides": "{}",
    })
    from dashboard.app import create_app
    app = create_app(db)
    c = TestClient(app)
    response = c.get(f"/backtest/{backtest_id}/status", follow_redirects=False)
    assert response.status_code == 200
    assert "refresh" in response.text.lower()


def test_backtest_result_page(db):
    """GET /backtest/<id> returns 200 for completed backtest."""
    run_id = db.insert_strategy_run('{"name": "alpha", "regime_filter": {"logic": ""}}')
    db.insert_backtest_results(run_id, {
        "sharpe": 1.2, "sortino": 1.8, "max_drawdown_pct": 0.1,
        "max_drawdown_days": 20, "win_rate": 0.5, "avg_rr": 1.4,
        "total_trades": 15, "pct_time_in_market": 0.35, "cagr": 0.15,
        "backtest_start": date(2023, 1, 1), "backtest_end": date(2024, 1, 1),
    })
    from datetime import datetime, timezone
    backtest_id = db.insert_custom_backtest({
        "run_id": run_id, "interval": "4h",
        "date_from": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "date_to": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "regime_filter_mode": "strategy",
        "regime_filter_overrides": "{}",
    })
    db.update_custom_backtest_results(backtest_id, {
        "sharpe": 0.9, "sortino": 1.2, "max_drawdown_pct": 0.15,
        "max_drawdown_days": 30, "win_rate": 0.45, "avg_rr": 1.2,
        "total_trades": 10, "pct_time_in_market": 0.3, "cagr": 0.1,
    })
    from dashboard.app import create_app
    app = create_app(db)
    c = TestClient(app)
    response = c.get(f"/backtest/{backtest_id}")
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_dashboard.py::test_backtest_form_returns_200 tests/test_dashboard.py::test_backtest_run_rejects_insufficient_bars -v
```

Expected: FAIL (routes don't exist)

- [ ] **Step 3: Add imports and helpers to `dashboard/app.py`**

At the top of `dashboard/app.py`, update the reporter import line and add the datetime/json imports needed:

```python
import json as _json
import threading
from datetime import datetime, timezone
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import config
from storage.db import Database
from reporting.reporter import (
    get_runs_summary, get_run_detail, get_equity_chart_data, get_compare_data,
    get_custom_backtests, get_custom_backtest_detail,
)
```

Add this helper function inside `create_app`, before the existing routes (after `_pipeline_state` is defined):

```python
INTERVAL_MINUTES = {'15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440, '1w': 10080}

def _estimate_bars(interval: str, date_from: datetime, date_to: datetime) -> int:
    total_minutes = (date_to - date_from).total_seconds() / 60
    return int(total_minutes / INTERVAL_MINUTES.get(interval, 1440))

def _parse_date_range(preset: str, date_from_str: str, date_to_str: str):
    now = datetime.now(tz=timezone.utc)
    presets = {
        "30d": 30, "90d": 90, "6mo": 182, "1yr": 365,
        "2yr": 730, "3yr": 1095, "all": 365 * 8,
    }
    if preset in presets:
        days = presets[preset]
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - \
               __import__('datetime').timedelta(days=days), now
    # custom
    from datetime import datetime as _dt
    dt_from = _dt.fromisoformat(date_from_str).replace(tzinfo=timezone.utc)
    dt_to = _dt.fromisoformat(date_to_str).replace(tzinfo=timezone.utc)
    return dt_from, dt_to
```

- [ ] **Step 4: Add the 4 new routes inside `create_app`**

Add after the existing `/equity` route and before `return app`:

```python
@app.get("/backtest", response_class=HTMLResponse)
async def backtest_form(request: Request):
    all_runs = db.all_runs()
    valid_runs = [r for r in all_runs if r.get("status") != "parse_error"]
    return templates.TemplateResponse(request, "backtest_form.html", {
        "runs": valid_runs,
        "intervals": ["15m", "30m", "1h", "4h", "1d", "1w"],
    })


@app.post("/backtest/run")
async def run_backtest_custom(
    background_tasks: BackgroundTasks,
    run_id: int = Form(...),
    interval: str = Form(...),
    date_preset: str = Form(...),
    date_from: str = Form(""),
    date_to: str = Form(""),
    regime_filter_mode: str = Form(...),
    regime_filter_overrides: str = Form("{}"),
):
    dt_from, dt_to = _parse_date_range(date_preset, date_from, date_to)
    estimated = _estimate_bars(interval, dt_from, dt_to)
    if estimated < 250:
        raise HTTPException(
            status_code=400,
            detail=f"Date range yields ~{estimated} bars for {interval} interval. "
                   f"Minimum 250 bars required for valid indicator computation."
        )

    run = db.get_strategy_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Strategy run not found")

    import json as _j
    try:
        overrides_dict = _j.loads(regime_filter_overrides)
    except Exception:
        overrides_dict = {}

    backtest_id = db.insert_custom_backtest({
        "run_id": run_id,
        "interval": interval,
        "date_from": dt_from,
        "date_to": dt_to,
        "regime_filter_mode": regime_filter_mode,
        "regime_filter_overrides": _j.dumps(overrides_dict),
    })

    strategy = _j.loads(run["strategy_json"])

    def _run_backtest_task():
        try:
            import pandas as pd
            from data.fetcher import fetch_ohlcv, compute_indicators
            from backtest.custom_runner import run_custom_backtest
            import config as _cfg

            # Fetch missing bars
            backfill_start = __import__('datetime').date.fromisoformat(_cfg.BACKFILL_START)
            latest = db.latest_ohlcv_timestamp_interval(interval)
            fetch_start_dt = dt_from if latest is None else max(dt_from, latest)
            rows = fetch_ohlcv(
                fetch_start_dt.date(), dt_to.date(),
                interval=interval,
                backfill_start=backfill_start,
            )
            if rows:
                db.upsert_ohlcv_interval(rows, interval)

            # Get stored bars for the requested range
            ohlcv_df = db.get_ohlcv_interval(interval, dt_from, dt_to)
            if ohlcv_df.empty:
                raise ValueError("No OHLCV data available for the requested range.")

            # Compute indicators on-demand (not stored for intraday)
            full_df = compute_indicators(ohlcv_df)
            if len(full_df) < 250:
                raise ValueError(
                    f"Only {len(full_df)} bars with valid indicators after warmup. "
                    f"Minimum 250 required."
                )

            metrics = run_custom_backtest(
                strategy, full_df,
                regime_filter_mode=regime_filter_mode,
                regime_filter_overrides=overrides_dict,
            )
            db.update_custom_backtest_results(backtest_id, metrics)
        except Exception as exc:
            db.set_custom_backtest_error(backtest_id, str(exc))

    background_tasks.add_task(_run_backtest_task)
    return RedirectResponse(url=f"/backtest/{backtest_id}/status", status_code=303)


@app.get("/backtest/{backtest_id}/status", response_class=HTMLResponse)
async def backtest_status(request: Request, backtest_id: int):
    row = db.get_custom_backtest(backtest_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    if row["total_trades"] == -2:
        return templates.TemplateResponse(request, "backtest_status.html", {
            "backtest_id": backtest_id,
            "error": row.get("error_message", "Unknown error"),
            "done": False,
        })
    if row["total_trades"] != -1:
        return RedirectResponse(url=f"/backtest/{backtest_id}", status_code=303)
    return templates.TemplateResponse(request, "backtest_status.html", {
        "backtest_id": backtest_id,
        "error": None,
        "done": False,
    })


@app.get("/backtest/{backtest_id}", response_class=HTMLResponse)
async def backtest_result(request: Request, backtest_id: int):
    detail = get_custom_backtest_detail(db, backtest_id)
    if detail["custom"] is None:
        raise HTTPException(status_code=404, detail="Backtest not found")
    if detail["custom"]["total_trades"] == -1:
        return RedirectResponse(url=f"/backtest/{backtest_id}/status", status_code=303)
    import json as _j
    overrides = {}
    try:
        overrides = _j.loads(detail["custom"].get("regime_filter_overrides", "{}"))
    except Exception:
        pass
    return templates.TemplateResponse(request, "backtest_result.html", {
        **detail,
        "overrides": overrides,
    })
```

- [ ] **Step 5: Run dashboard tests**

```bash
python -m pytest tests/test_dashboard.py -v
```

Expected: all PASS (including the 4 new tests)

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py tests/test_dashboard.py
git commit -m "feat: add custom backtest routes (form, run, status, result)"
```

---

## Task 7: Dashboard Templates

**Files:**
- Create: `dashboard/templates/backtest_form.html`
- Create: `dashboard/templates/backtest_status.html`
- Create: `dashboard/templates/backtest_result.html`
- Modify: `dashboard/templates/base.html`
- Modify: `dashboard/templates/compare.html`

> Context: Templates use Jinja2 with Water.css. The form uses inline JavaScript to dynamically show/hide the custom date inputs and to parse regime_filter conditions from the selected strategy's embedded JSON. The status page uses `<meta http-equiv="refresh" content="3">` for no-JS polling. The result page shows a two-column comparison table.

- [ ] **Step 1: Add "Backtest" link to `base.html` nav**

In `dashboard/templates/base.html`, replace the `<nav>` block:

```html
  <nav>
    <strong>Trading Research Bot</strong> &nbsp;|&nbsp;
    <a href="/">Overview</a> &nbsp;
    <a href="/runs">Runs</a> &nbsp;
    <a href="/runs/compare">Compare</a> &nbsp;
    <a href="/equity">Equity Curve</a> &nbsp;
    <a href="/backtest">Backtest</a>
  </nav>
```

- [ ] **Step 2: Create `dashboard/templates/backtest_form.html`**

```html
{% extends "base.html" %}
{% block title %}Custom Backtest — Trading Research Bot{% endblock %}
{% block content %}
<h1>Custom Backtest</h1>

<form method="post" action="/backtest/run">
  <label>Strategy
    <select name="run_id" id="run_select" required>
      {% for run in runs %}
      {% set s = run.strategy_json | from_json %}
      <option value="{{ run.id }}"
        data-regime='{{ run.strategy_json | from_json | tojson }}'
        data-regime-logic="{{ (run.strategy_json | from_json).regime_filter.logic | default('') }}">
        #{{ run.id }} — {{ s.name | default('(unnamed)') }} [{{ run.status }}]
      </option>
      {% endfor %}
    </select>
  </label>

  <label>Interval
    <select name="interval">
      {% for iv in intervals %}
      <option value="{{ iv }}" {% if iv == '1d' %}selected{% endif %}>{{ iv }}</option>
      {% endfor %}
    </select>
  </label>

  <fieldset>
    <legend>Date Range</legend>
    {% for label, val in [("Last 30d","30d"),("Last 90d","90d"),("Last 6mo","6mo"),
                          ("Last 1yr","1yr"),("Last 2yr","2yr"),("Last 3yr","3yr"),
                          ("All time","all"),("Custom","custom")] %}
    <label><input type="radio" name="date_preset" value="{{ val }}"
      {% if val == '1yr' %}checked{% endif %}
      onchange="document.getElementById('custom_dates').style.display=
        (this.value==='custom'?'block':'none')"> {{ label }}</label>
    {% endfor %}
    <div id="custom_dates" style="display:none; margin-top:0.5em">
      <label>From <input type="datetime-local" name="date_from"></label>
      <label>To <input type="datetime-local" name="date_to"></label>
    </div>
  </fieldset>

  <fieldset>
    <legend>Regime Filter</legend>
    <label><input type="radio" name="regime_filter_mode" value="strategy" checked
      onchange="toggleConditions(this.value)"> Use strategy's regime filter</label><br>
    <label><input type="radio" name="regime_filter_mode" value="disabled"
      onchange="toggleConditions(this.value)"> Disable regime filter entirely</label><br>
    <label><input type="radio" name="regime_filter_mode" value="custom"
      onchange="toggleConditions(this.value)"> Custom — select conditions below</label>

    <div id="conditions_panel" style="display:none; margin-top:0.5em">
      <div id="conditions_list"></div>
      <input type="hidden" name="regime_filter_overrides" id="overrides_input" value="{}">
    </div>
  </fieldset>

  <button type="submit">Run Backtest</button>
</form>

<script>
function getConditions(select) {
  var opt = select.options[select.selectedIndex];
  var logic = opt.getAttribute('data-regime-logic') || '';
  if (!logic) return [];
  return logic.split(' AND ').map(s => s.trim()).filter(Boolean);
}

function renderConditions() {
  var select = document.getElementById('run_select');
  var conds = getConditions(select);
  var list = document.getElementById('conditions_list');
  list.innerHTML = '';
  conds.forEach(function(cond) {
    var id = 'cond_' + btoa(cond).replace(/[^a-zA-Z0-9]/g,'');
    var label = document.createElement('label');
    label.style.display = 'block';
    var cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = true; cb.id = id;
    cb.setAttribute('data-cond', cond);
    cb.addEventListener('change', updateOverrides);
    label.appendChild(cb);
    label.appendChild(document.createTextNode(' ' + cond));
    list.appendChild(label);
  });
  if (conds.length === 0) {
    list.innerHTML = '<em>This strategy has no parseable regime filter conditions.</em>';
  }
  updateOverrides();
}

function updateOverrides() {
  var overrides = {};
  document.querySelectorAll('#conditions_list input[type=checkbox]').forEach(function(cb) {
    overrides[cb.getAttribute('data-cond')] = cb.checked;
  });
  document.getElementById('overrides_input').value = JSON.stringify(overrides);
}

function toggleConditions(mode) {
  document.getElementById('conditions_panel').style.display =
    (mode === 'custom') ? 'block' : 'none';
}

document.getElementById('run_select').addEventListener('change', renderConditions);
renderConditions();
</script>
{% endblock %}
```

- [ ] **Step 3: Create `dashboard/templates/backtest_status.html`**

```html
{% extends "base.html" %}
{% block title %}Backtest Status — Trading Research Bot{% endblock %}
{% block content %}
{% if not error %}
<meta http-equiv="refresh" content="3">
{% endif %}

<h1>Backtest #{{ backtest_id }}</h1>

{% if error %}
<p style="color: #d9534f;"><strong>Error:</strong> {{ error }}</p>
<p><a href="/backtest">← Run another backtest</a></p>
{% else %}
<p>Backtest in progress — fetching bars and running simulation…</p>
<p style="color: #888; font-size: 0.9em;">This page refreshes every 3 seconds automatically.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Create `dashboard/templates/backtest_result.html`**

```html
{% extends "base.html" %}
{% block title %}Backtest #{{ custom.id }} — Trading Research Bot{% endblock %}
{% block content %}
<h1>Custom Backtest #{{ custom.id }}</h1>

{% set run_strategy = run.strategy_json | from_json %}

<div style="display:flex; gap:2em; flex-wrap:wrap">
  <div style="min-width:260px">
    <h2>Parameters</h2>
    <table>
      <tr><th>Strategy</th><td><a href="/runs/{{ run.id }}">#{{ run.id }} — {{ run_strategy.name | default('—') }}</a></td></tr>
      <tr><th>Interval</th><td>{{ custom.interval }}</td></tr>
      <tr><th>Date From</th><td>{{ custom.date_from | string | truncate(19, end='') }}</td></tr>
      <tr><th>Date To</th><td>{{ custom.date_to | string | truncate(19, end='') }}</td></tr>
      <tr><th>Regime Mode</th><td>{{ custom.regime_filter_mode }}</td></tr>
      {% if custom.regime_filter_mode == 'custom' %}
      <tr><th>Active Conditions</th><td>
        {% for cond, enabled in overrides.items() %}
          {% if enabled %}<code>{{ cond }}</code><br>{% endif %}
        {% endfor %}
      </td></tr>
      {% endif %}
    </table>
  </div>

  <div style="flex:1; min-width:360px">
    <h2>Metrics Comparison</h2>
    <table>
      <thead>
        <tr>
          <th>Metric</th>
          <th>This Backtest</th>
          <th>Original ({{ original_backtest.backtest_start | string | truncate(10, end='') if original_backtest else '—' }})</th>
        </tr>
      </thead>
      <tbody>
        {% set ob = original_backtest or {} %}
        {% for label, ckey, fmt in [
          ("Sharpe", "sharpe", "%.2f"),
          ("Sortino", "sortino", "%.2f"),
          ("Max Drawdown %", "max_drawdown_pct", "%.1f%%"),
          ("Max Drawdown Days", "max_drawdown_days", "%d"),
          ("Win Rate", "win_rate", "%.1f%%"),
          ("Avg R:R", "avg_rr", "%.2f"),
          ("Total Trades", "total_trades", "%d"),
          ("% Time in Market", "pct_time_in_market", "%.1f%%"),
          ("CAGR", "cagr", "%.1f%%"),
        ] %}
        <tr>
          <td>{{ label }}</td>
          <td>
            {% if custom[ckey] is not none %}
              {% if ckey in ["max_drawdown_pct", "win_rate", "pct_time_in_market", "cagr"] %}
                {{ "%.1f%%" | format(custom[ckey] * 100) }}
              {% elif ckey == "max_drawdown_days" or ckey == "total_trades" %}
                {{ custom[ckey] }}
              {% else %}
                {{ "%.2f" | format(custom[ckey]) }}
              {% endif %}
            {% else %}—{% endif %}
          </td>
          <td>
            {% if ob.get(ckey) is not none %}
              {% if ckey in ["max_drawdown_pct", "win_rate", "pct_time_in_market", "cagr"] %}
                {{ "%.1f%%" | format(ob[ckey] * 100) }}
              {% elif ckey == "max_drawdown_days" or ckey == "total_trades" %}
                {{ ob[ckey] }}
              {% else %}
                {{ "%.2f" | format(ob[ckey]) }}
              {% endif %}
            {% else %}—{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<p style="margin-top:1.5em"><a href="/backtest">← Run another backtest</a> &nbsp; <a href="/runs/compare">Compare all</a></p>
{% endblock %}
```

- [ ] **Step 5: Add Custom Backtests section to `compare.html`**

Add to `dashboard/app.py` — update the `/runs/compare` route to also pass custom backtests:

```python
@app.get("/runs/compare", response_class=HTMLResponse)
async def runs_compare(request: Request):
    runs = get_compare_data(db)
    custom_backtests = get_custom_backtests(db)
    return templates.TemplateResponse(request, "compare.html", {
        "runs": runs,
        "custom_backtests": custom_backtests,
    })
```

Then append to `dashboard/templates/compare.html` before `{% endblock %}`:

```html
<h2 style="margin-top:2em">Custom Backtests</h2>
{% if not custom_backtests %}
<p>No custom backtests saved yet. <a href="/backtest">Run one</a>.</p>
{% else %}
<table>
  <thead>
    <tr>
      <th>ID</th><th>Strategy</th><th>Interval</th><th>Date Range</th><th>Regime Mode</th>
      <th>Sharpe</th><th>Max DD</th><th>Win Rate</th><th>CAGR</th>
    </tr>
  </thead>
  <tbody>
  {% for cb in custom_backtests %}
  {% set s = cb.strategy_json | from_json %}
  <tr>
    <td><a href="/backtest/{{ cb.id }}">{{ cb.id }}</a></td>
    <td>{{ s.name | default('—') }}</td>
    <td>{{ cb.interval }}</td>
    <td>{{ cb.date_from | string | truncate(10, end='') }} – {{ cb.date_to | string | truncate(10, end='') }}</td>
    <td>{{ cb.regime_filter_mode }}</td>
    <td>{{ "%.2f" | format(cb.sharpe) if cb.sharpe else '—' }}</td>
    <td>{{ "%.1f%%" | format(cb.max_drawdown_pct * 100) if cb.max_drawdown_pct else '—' }}</td>
    <td>{{ "%.1f%%" | format(cb.win_rate * 100) if cb.win_rate else '—' }}</td>
    <td>{{ "%.1f%%" | format(cb.cagr * 100) if cb.cagr else '—' }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
```

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add dashboard/templates/ dashboard/app.py
git commit -m "feat: add backtest form, status, result templates and custom backtests to compare page"
```

---

## Final Verification

- [ ] **Run all tests one final time**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Smoke-test the dashboard manually**

```bash
python agent.py
# Open http://localhost:8000/backtest
# Select a strategy, interval=1h, Last 30d, strategy regime mode → Run Backtest
# Verify redirect to /backtest/<id>/status with refresh spinner
# Verify redirect to /backtest/<id> when complete with metric comparison table
# Open http://localhost:8000/runs/compare → verify Custom Backtests section appears
```
