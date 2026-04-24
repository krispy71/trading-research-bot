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
