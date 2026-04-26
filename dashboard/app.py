# dashboard/app.py
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).parent.parent))

import json as _json
import threading
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import config
from storage.db import Database
from reporting.reporter import (
    get_runs_summary, get_run_detail, get_equity_chart_data, get_compare_data
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
        return templates.TemplateResponse(request, "compare.html", {
            "runs": runs,
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

    return app


if __name__ == "__main__":
    import uvicorn
    # Note: if agent.py is already running, it holds the DuckDB lock.
    # Run `python agent.py` instead — it starts the dashboard automatically.
    _db = Database(config.DB_PATH)
    _db.init_schema()
    _app = create_app(_db)
    uvicorn.run(_app, host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT)
