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
BINANCE_KLINES_URL = os.getenv("BINANCE_KLINES_URL", "https://api.binance.us/api/v3/klines")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
