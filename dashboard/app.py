# dashboard/app.py
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent.parent))

import json as _json
import threading
from datetime import datetime, timedelta, timezone
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

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _from_json(value):
    try:
        return _json.loads(value)
    except Exception:
        return {}


def create_app(db: Database, pipeline_fn=None) -> FastAPI:
    app = FastAPI(title="Trading Research Bot Dashboard")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["from_json"] = _from_json

    _pipeline_state = {"running": False}  # simple lock to prevent concurrent runs

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
            return (
                now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days),
                now,
            )
        # custom
        dt_from = datetime.fromisoformat(date_from_str).replace(tzinfo=timezone.utc)
        dt_to = datetime.fromisoformat(date_to_str).replace(tzinfo=timezone.utc)
        return dt_from, dt_to

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        active = db.get_active_strategy()
        equity = []
        if active:
            equity = db.get_equity_curve(active["id"])
        current_equity = equity[-1]["equity"] if equity else None
        current_dd = equity[-1]["drawdown_pct"] if equity else None
        return templates.TemplateResponse(request, "index.html", {
            "active": active,
            "current_equity": current_equity,
            "current_dd": current_dd,
            "pipeline_available": pipeline_fn is not None,
            "pipeline_running": _pipeline_state["running"],
            "pipeline_started": request.query_params.get("pipeline_started") == "1",
        })

    @app.post("/pipeline/run")
    async def trigger_pipeline(background_tasks: BackgroundTasks):
        if pipeline_fn is None:
            raise HTTPException(status_code=503, detail="Pipeline not available in standalone dashboard mode")
        if _pipeline_state["running"]:
            return RedirectResponse(url="/?pipeline_started=1", status_code=303)

        def _run():
            _pipeline_state["running"] = True
            try:
                pipeline_fn()
            finally:
                _pipeline_state["running"] = False

        background_tasks.add_task(_run)
        return RedirectResponse(url="/?pipeline_started=1", status_code=303)

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_list(request: Request):
        runs = get_runs_summary(db)
        return templates.TemplateResponse(request, "runs.html", {
            "runs": runs,
        })

    @app.get("/runs/compare", response_class=HTMLResponse)
    async def runs_compare(request: Request):
        runs = get_compare_data(db)
        custom_backtests_list = get_custom_backtests(db)
        return templates.TemplateResponse(request, "compare.html", {
            "runs": runs,
            "custom_backtests": custom_backtests_list,
        })

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: int):
        detail = get_run_detail(db, run_id)
        if detail["run"] is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return templates.TemplateResponse(request, "run_detail.html", detail)

    @app.post("/runs/{run_id}/approve")
    async def approve_run(run_id: int):
        run = db.get_strategy_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        db.retire_all_approved()
        db.update_strategy_status(run_id, "approved")
        return RedirectResponse(url=f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/retire")
    async def retire_run(run_id: int):
        run = db.get_strategy_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        db.update_strategy_status(run_id, "retired")
        return RedirectResponse(url="/runs", status_code=303)

    @app.get("/equity", response_class=HTMLResponse)
    async def equity_view(request: Request):
        active = db.get_active_strategy()
        chart_data = {"dates": [], "equity": [], "drawdown": []}
        if active:
            chart_data = get_equity_chart_data(db, active["id"])
        return templates.TemplateResponse(request, "equity.html", {
            "active": active,
            "chart_data": chart_data,
        })

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
        try:
            dt_from, dt_to = _parse_date_range(date_preset, date_from, date_to)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date range: {exc}"
            )
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

        try:
            overrides_dict = _json.loads(regime_filter_overrides)
        except Exception:
            overrides_dict = {}

        backtest_id = db.insert_custom_backtest({
            "run_id": run_id,
            "interval": interval,
            "date_from": dt_from,
            "date_to": dt_to,
            "regime_filter_mode": regime_filter_mode,
            "regime_filter_overrides": _json.dumps(overrides_dict),
        })

        strategy = _json.loads(run["strategy_json"])

        def _run_backtest_task():
            try:
                import pandas as pd
                from data.fetcher import fetch_ohlcv, compute_indicators
                from backtest.custom_runner import run_custom_backtest

                # Fetch missing bars for this interval
                latest = db.latest_ohlcv_timestamp_interval(interval)
                fetch_start = dt_from if latest is None else max(dt_from, latest)
                rows = fetch_ohlcv(
                    fetch_start.date(), dt_to.date(),
                    interval=interval,
                    backfill_start=None,
                )
                if rows:
                    db.upsert_ohlcv_interval(rows, interval)

                # Get stored bars for the requested range
                ohlcv_df = db.get_ohlcv_interval(interval, dt_from, dt_to)
                if ohlcv_df.empty:
                    raise ValueError("No OHLCV data available for the requested range.")

                # Compute indicators on-demand (not stored for intraday)
                indicators_df = compute_indicators(ohlcv_df)
                if len(indicators_df) < 250:
                    raise ValueError(
                        f"Only {len(indicators_df)} bars with valid indicators after warmup. "
                        f"Minimum 250 required."
                    )

                # Merge OHLCV price data with indicators (engine needs both)
                merged_df = ohlcv_df.merge(indicators_df, on="timestamp", how="inner")

                metrics = run_custom_backtest(
                    strategy, merged_df,
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
            })
        if row["total_trades"] != -1:
            return RedirectResponse(url=f"/backtest/{backtest_id}", status_code=303)
        return templates.TemplateResponse(request, "backtest_status.html", {
            "backtest_id": backtest_id,
            "error": None,
        })

    @app.get("/backtest/{backtest_id}", response_class=HTMLResponse)
    async def backtest_result(request: Request, backtest_id: int):
        detail = get_custom_backtest_detail(db, backtest_id)
        if detail["custom"] is None:
            raise HTTPException(status_code=404, detail="Backtest not found")
        if detail["custom"]["total_trades"] == -1:
            return RedirectResponse(url=f"/backtest/{backtest_id}/status", status_code=303)
        overrides = {}
        try:
            overrides = _json.loads(detail["custom"].get("regime_filter_overrides", "{}"))
        except Exception:
            pass
        return templates.TemplateResponse(request, "backtest_result.html", {
            **detail,
            "overrides": overrides,
        })

    return app


if __name__ == "__main__":
    import uvicorn
    # Note: if agent.py is already running, it holds the DuckDB lock.
    # Run `python agent.py` instead — it starts the dashboard automatically.
    _db = Database(config.DB_PATH)
    _db.init_schema()
    _app = create_app(_db)
    uvicorn.run(_app, host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT)
