# storage/db.py
import duckdb
from datetime import date
from typing import Optional

class Database:
    def __init__(self, path: str):
        self.conn = duckdb.connect(path)

    def init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                timestamp DATE PRIMARY KEY,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                timestamp DATE PRIMARY KEY,
                ema_20 DOUBLE, ema_50 DOUBLE, ema_200 DOUBLE,
                atr_14 DOUBLE, adx_14 DOUBLE, rsi_14 DOUBLE,
                bb_upper DOUBLE, bb_lower DOUBLE, bb_mid DOUBLE,
                volume_sma_20 DOUBLE
            )
        """)
        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS strategy_runs_id_seq")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_runs (
                id INTEGER PRIMARY KEY DEFAULT nextval('strategy_runs_id_seq'),
                created_at TIMESTAMP DEFAULT current_timestamp,
                strategy_json TEXT,
                status TEXT DEFAULT 'pending_approval',
                notes TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                run_id INTEGER PRIMARY KEY REFERENCES strategy_runs(id),
                sharpe DOUBLE, sortino DOUBLE,
                max_drawdown_pct DOUBLE, max_drawdown_days INTEGER,
                win_rate DOUBLE, avg_rr DOUBLE,
                total_trades INTEGER, pct_time_in_market DOUBLE, cagr DOUBLE,
                backtest_start DATE, backtest_end DATE
            )
        """)
        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS paper_positions_id_seq")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY DEFAULT nextval('paper_positions_id_seq'),
                run_id INTEGER REFERENCES strategy_runs(id),
                entry_date DATE, entry_price DOUBLE, stop_price DOUBLE, tranche INTEGER,
                exit_date DATE, exit_price DOUBLE, exit_reason TEXT, pnl_r DOUBLE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_curve (
                date DATE PRIMARY KEY,
                equity DOUBLE, drawdown_pct DOUBLE,
                run_id INTEGER REFERENCES strategy_runs(id)
            )
        """)

    def upsert_ohlcv(self, rows: list[dict]):
        if not rows:
            return
        import pandas as pd
        df = pd.DataFrame(rows)
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
        import pandas as pd
        df = pd.DataFrame(rows)
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
            """SELECT sr.*, br.sharpe, br.sortino, br.max_drawdown_pct, br.max_drawdown_days,
                      br.win_rate, br.avg_rr, br.total_trades, br.pct_time_in_market, br.cagr,
                      br.backtest_start, br.backtest_end
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
