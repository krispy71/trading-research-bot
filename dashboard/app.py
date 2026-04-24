# dashboard/app.py
import json as _json
from fastapi import FastAPI, Request, HTTPException
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


def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Trading Research Bot Dashboard")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["from_json"] = _from_json

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        active = db.get_active_strategy()
        equity = []
        if active:
            equity = db.get_equity_curve(active["id"])
        current_equity = equity[-1]["equity"] if equity else None
        current_dd = equity[-1]["drawdown_pct"] if equity else None
        return templates.TemplateResponse("index.html", {
            "request": request,
            "active": active,
            "current_equity": current_equity,
            "current_dd": current_dd,
        })

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_list(request: Request):
        runs = get_runs_summary(db)
        return templates.TemplateResponse("runs.html", {
            "request": request,
            "runs": runs,
        })

    @app.get("/runs/compare", response_class=HTMLResponse)
    async def runs_compare(request: Request):
        runs = get_compare_data(db)
        return templates.TemplateResponse("compare.html", {
            "request": request,
            "runs": runs,
        })

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: int):
        detail = get_run_detail(db, run_id)
        if detail["run"] is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return templates.TemplateResponse("run_detail.html", {
            "request": request,
            **detail,
        })

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
        return templates.TemplateResponse("equity.html", {
            "request": request,
            "active": active,
            "chart_data": chart_data,
        })

    return app


if __name__ == "__main__":
    import uvicorn
    _db = Database(config.DB_PATH)
    _db.init_schema()
    _app = create_app(_db)
    uvicorn.run(_app, host="127.0.0.1", port=config.DASHBOARD_PORT)
