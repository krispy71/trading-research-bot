# reporting/reporter.py
import json
from storage.db import Database

def get_runs_summary(db: Database) -> list[dict]:
    """Get summary of all strategy runs with their backtest metrics."""
    return db.all_runs()

def get_run_detail(db: Database, run_id: int) -> dict:
    """Get detailed information for a specific run including strategy, backtest, and positions."""
    run = db.get_strategy_run(run_id)
    backtest = db.get_backtest_results(run_id)
    positions = db.open_paper_positions(run_id)
    strategy = json.loads(run["strategy_json"]) if run and run.get("strategy_json") else {}
    return {"run": run, "backtest": backtest, "positions": positions, "strategy": strategy}

def get_equity_chart_data(db: Database, run_id: int) -> dict:
    """Get equity curve data formatted for chart visualization."""
    curve = db.get_equity_curve(run_id)
    return {
        "dates": [str(r["date"]) for r in curve],
        "equity": [r["equity"] for r in curve],
        "drawdown": [r["drawdown_pct"] for r in curve],
    }

def get_compare_data(db: Database) -> list[dict]:
    """Get all runs in a format suitable for comparison views."""
    return db.all_runs()

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
