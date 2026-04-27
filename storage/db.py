# storage/db.py
import logging
import threading
import duckdb
import pandas as pd
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str):
        self.conn = duckdb.connect(path)
        self._lock = threading.RLock()

    def _exec(self, sql: str, params=None):
        """Thread-safe execute. Always acquire the lock before using conn."""
        with self._lock:
            if params is not None:
                return self.conn.execute(sql, params)
            return self.conn.execute(sql)

    def _exec_df(self, sql: str, params=None) -> pd.DataFrame:
        with self._lock:
            if params is not None:
                return self.conn.execute(sql, params).df()
            return self.conn.execute(sql).df()

    def _exec_rows(self, sql: str, params=None):
        with self._lock:
            if params is not None:
                result = self.conn.execute(sql, params)
            else:
                result = self.conn.execute(sql)
            rows = result.fetchall()
            cols = [d[0] for d in self.conn.description]
            return rows, cols

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

    def upsert_ohlcv_interval(self, rows: list[dict], interval: str):
        """Stub — full implementation in Task 2."""
        if not rows:
            return
        df = pd.DataFrame([{**r, 'interval': interval} for r in rows])
        df = df[['timestamp', 'interval', 'open', 'high', 'low', 'close', 'volume']]
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO ohlcv SELECT * FROM df")

    def upsert_indicators_interval(self, rows: list[dict], interval: str):
        """Stub — full implementation in Task 2."""
        if not rows:
            return
        df = pd.DataFrame([{**r, 'interval': interval} for r in rows])
        df = df[['timestamp', 'interval', 'ema_20', 'ema_50', 'ema_200',
                 'atr_14', 'adx_14', 'rsi_14', 'bb_upper', 'bb_lower', 'bb_mid', 'volume_sma_20']]
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO indicators SELECT * FROM df")

    def insert_strategy_run(self, strategy_json: str) -> int:
        with self._lock:
            result = self.conn.execute(
                "INSERT INTO strategy_runs (strategy_json) VALUES (?) RETURNING id",
                [strategy_json]
            ).fetchone()
        return result[0]

    def get_strategy_run(self, run_id: int) -> Optional[dict]:
        rows, cols = self._exec_rows(
            "SELECT * FROM strategy_runs WHERE id = ?", [run_id]
        )
        if not rows:
            return None
        return dict(zip(cols, rows[0]))

    def update_strategy_status(self, run_id: int, status: str, notes: str = None):
        self._exec(
            "UPDATE strategy_runs SET status = ?, notes = COALESCE(?, notes) WHERE id = ?",
            [status, notes, run_id]
        )

    def get_active_strategy(self) -> Optional[dict]:
        rows, cols = self._exec_rows(
            "SELECT * FROM strategy_runs WHERE status = 'approved' ORDER BY created_at DESC LIMIT 1"
        )
        if not rows:
            return None
        return dict(zip(cols, rows[0]))

    def retire_all_approved(self):
        self._exec("UPDATE strategy_runs SET status = 'retired' WHERE status = 'approved'")

    def all_runs(self) -> list[dict]:
        rows, cols = self._exec_rows(
            """SELECT sr.*, br.sharpe, br.sortino, br.max_drawdown_pct, br.max_drawdown_days,
                      br.win_rate, br.avg_rr, br.total_trades, br.pct_time_in_market, br.cagr,
                      br.backtest_start, br.backtest_end
               FROM strategy_runs sr
               LEFT JOIN backtest_results br ON sr.id = br.run_id
               ORDER BY sr.created_at DESC"""
        )
        if not rows:
            return []
        return [dict(zip(cols, r)) for r in rows]

    def insert_backtest_results(self, run_id: int, metrics: dict):
        self._exec("""
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
        rows, cols = self._exec_rows(
            "SELECT * FROM backtest_results WHERE run_id = ?", [run_id]
        )
        if not rows:
            return None
        return dict(zip(cols, rows[0]))

    def insert_paper_position(self, run_id: int, pos: dict) -> int:
        with self._lock:
            result = self.conn.execute("""
                INSERT INTO paper_positions (run_id, entry_date, entry_price, stop_price, tranche)
                VALUES (?, ?, ?, ?, ?) RETURNING id
            """, [run_id, pos["entry_date"], pos["entry_price"], pos["stop_price"], pos["tranche"]]).fetchone()
        return result[0]

    def close_paper_position(self, pos_id: int, exit_date: date, exit_price: float, exit_reason: str, pnl_r: float):
        self._exec("""
            UPDATE paper_positions
            SET exit_date = ?, exit_price = ?, exit_reason = ?, pnl_r = ?
            WHERE id = ?
        """, [exit_date, exit_price, exit_reason, pnl_r, pos_id])

    def get_paper_position(self, pos_id: int) -> Optional[dict]:
        rows, cols = self._exec_rows(
            "SELECT * FROM paper_positions WHERE id = ?", [pos_id]
        )
        if not rows:
            return None
        return dict(zip(cols, rows[0]))

    def open_paper_positions(self, run_id: int) -> list[dict]:
        rows, cols = self._exec_rows(
            "SELECT * FROM paper_positions WHERE run_id = ? AND exit_date IS NULL",
            [run_id]
        )
        if not rows:
            return []
        return [dict(zip(cols, r)) for r in rows]

    def upsert_equity_curve(self, dt: date, equity: float, drawdown_pct: float, run_id: int):
        self._exec(
            "INSERT OR REPLACE INTO equity_curve (date, equity, drawdown_pct, run_id) VALUES (?, ?, ?, ?)",
            [dt, equity, drawdown_pct, run_id]
        )

    def get_equity_curve(self, run_id: int) -> list[dict]:
        rows, cols = self._exec_rows(
            "SELECT * FROM equity_curve WHERE run_id = ? ORDER BY date", [run_id]
        )
        if not rows:
            return []
        return [dict(zip(cols, r)) for r in rows]

    def get_recent_runs_with_metrics(self, n: int) -> list[dict]:
        rows, cols = self._exec_rows("""
            SELECT sr.id, sr.created_at, sr.status,
                   br.sharpe, br.sortino, br.max_drawdown_pct, br.win_rate, br.avg_rr, br.cagr
            FROM strategy_runs sr
            LEFT JOIN backtest_results br ON sr.id = br.run_id
            WHERE sr.status NOT IN ('parse_error')
            ORDER BY sr.created_at DESC LIMIT ?
        """, [n])
        if not rows:
            return []
        return [dict(zip(cols, r)) for r in rows]

    def close(self):
        self.conn.close()
