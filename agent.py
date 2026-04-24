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
        DB.conn.execute(
            "UPDATE strategy_runs SET strategy_json = ? WHERE id = ?",
            [json.dumps(strategy), run_id]
        )
        logger.info(f"Strategy generated: {strategy['name']}")
    except Exception as e:
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
