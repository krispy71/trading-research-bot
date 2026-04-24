# Trading Research Bot — Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI web dashboard (localhost:8080) for reviewing strategy runs, approving/retiring strategies, and viewing reporting data including equity curves.

**Architecture:** FastAPI server with Jinja2 server-rendered HTML templates and classless CSS. All data served from DuckDB via the `reporting/reporter.py` query library. Approve/Retire are HTML form POSTs. Chart.js (CDN) renders the equity curve. No frontend build step.

**Tech Stack:** FastAPI, Uvicorn, Jinja2, Chart.js (CDN), Water.css (classless CSS, CDN)

**Prerequisite:** Complete the core pipeline plan (`2026-04-22-core-pipeline.md`) before starting this plan. The database layer (`storage/db.py`) and reporting library (`reporting/reporter.py`) must exist.

---

### Task 1: FastAPI app skeleton and base template

**Files:**
- Create: `dashboard/app.py`
- Create: `dashboard/templates/base.html`
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_dashboard.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from storage.db import Database

@pytest.fixture
def db():
    d = Database(":memory:")
    d.init_schema()
    return d

@pytest.fixture
def client(db):
    from dashboard.app import create_app
    app = create_app(db)
    return TestClient(app)

def test_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200

def test_runs_returns_200(client):
    response = client.get("/runs")
    assert response.status_code == 200

def test_run_detail_404_for_missing(client):
    response = client.get("/runs/999")
    assert response.status_code == 404

def test_runs_compare_returns_200(client):
    response = client.get("/runs/compare")
    assert response.status_code == 200

def test_equity_returns_200(client):
    response = client.get("/equity")
    assert response.status_code == 200
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_dashboard.py -v
```
Expected: `ModuleNotFoundError: No module named 'dashboard.app'`

- [ ] **Step 3: Create dashboard/app.py skeleton**

```python
# dashboard/app.py
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


def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Trading Research Bot Dashboard")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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


# Entry point
DB = Database(config.DB_PATH)
DB.init_schema()
app = create_app(DB)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard.app:app", host="127.0.0.1", port=config.DASHBOARD_PORT, reload=False)
```

- [ ] **Step 4: Create dashboard/templates/base.html**

```html
<!-- dashboard/templates/base.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Trading Research Bot{% endblock %}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/water.css@2/out/water.css">
  <style>
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; font-weight: bold; }
    .badge-pending  { background: #f0ad4e; color: #000; }
    .badge-approved { background: #5cb85c; color: #fff; }
    .badge-retired  { background: #777; color: #fff; }
    .badge-parse_error { background: #d9534f; color: #fff; }
    table { width: 100%; }
    td, th { vertical-align: middle; }
  </style>
</head>
<body>
  <nav>
    <strong>Trading Research Bot</strong> &nbsp;|&nbsp;
    <a href="/">Overview</a> &nbsp;
    <a href="/runs">Runs</a> &nbsp;
    <a href="/runs/compare">Compare</a> &nbsp;
    <a href="/equity">Equity Curve</a>
  </nav>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_dashboard.py -v
```
Expected: fails on missing templates — that's expected, templates come in next tasks. The 404 test and route registration tests should pass once templates exist. For now confirm routes are registered:

```bash
python -c "from dashboard.app import create_app; from storage.db import Database; d = Database(':memory:'); d.init_schema(); app = create_app(d); print([r.path for r in app.routes])"
```
Expected: list includes `/`, `/runs`, `/runs/compare`, `/runs/{run_id}`, `/equity`

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/templates/base.html tests/test_dashboard.py
git commit -m "feat: FastAPI dashboard skeleton with all routes and base template"
```

---

### Task 2: Overview and runs list templates

**Files:**
- Create: `dashboard/templates/index.html`
- Create: `dashboard/templates/runs.html`

- [ ] **Step 1: Create dashboard/templates/index.html**

```html
<!-- dashboard/templates/index.html -->
{% extends "base.html" %}
{% block title %}Overview — Trading Research Bot{% endblock %}
{% block content %}
<h1>Overview</h1>

{% if active %}
<h2>Active Strategy: {{ active.strategy_json | from_json | attr('name') }}</h2>
<table>
  <tr><th>Status</th><td><span class="badge badge-{{ active.status }}">{{ active.status }}</span></td></tr>
  <tr><th>Activated</th><td>{{ active.created_at }}</td></tr>
  {% if current_equity is not none %}
  <tr><th>Paper Equity</th><td>${{ "{:,.2f}".format(current_equity) }}</td></tr>
  <tr><th>Current Drawdown</th><td>{{ "{:.1%}".format(current_dd) }}</td></tr>
  {% endif %}
</table>
<p><a href="/runs/{{ active.id }}">View full strategy detail →</a> &nbsp; <a href="/equity">View equity curve →</a></p>
{% else %}
<p>No strategy is currently active. <a href="/runs">Review pending runs →</a></p>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Add `from_json` Jinja2 filter to app.py**

In `dashboard/app.py`, after `templates = Jinja2Templates(...)`, add:

```python
import json as _json

def _from_json(value):
    try:
        return _json.loads(value)
    except Exception:
        return {}

templates.env.filters["from_json"] = _from_json
```

- [ ] **Step 3: Create dashboard/templates/runs.html**

```html
<!-- dashboard/templates/runs.html -->
{% extends "base.html" %}
{% block title %}Runs — Trading Research Bot{% endblock %}
{% block content %}
<h1>Strategy Runs</h1>
{% if not runs %}
<p>No runs yet. The agent will generate the first run on its next scheduled execution.</p>
{% else %}
<table>
  <thead>
    <tr>
      <th>ID</th><th>Date</th><th>Name</th><th>Status</th>
      <th>Sharpe</th><th>Max DD</th><th>Win Rate</th><th>Avg R:R</th><th>CAGR</th>
      <th>Actions</th>
    </tr>
  </thead>
  <tbody>
  {% for run in runs %}
  {% set strategy = run.strategy_json | from_json %}
  <tr>
    <td><a href="/runs/{{ run.id }}">{{ run.id }}</a></td>
    <td>{{ run.created_at | string | truncate(10, end='') }}</td>
    <td>{{ strategy.name | default('—') }}</td>
    <td><span class="badge badge-{{ run.status }}">{{ run.status }}</span></td>
    <td>{{ "%.2f" | format(run.sharpe) if run.sharpe else '—' }}</td>
    <td>{{ "%.1f%%" | format(run.max_drawdown_pct * 100) if run.max_drawdown_pct else '—' }}</td>
    <td>{{ "%.1f%%" | format(run.win_rate * 100) if run.win_rate else '—' }}</td>
    <td>{{ "%.2f" | format(run.avg_rr) if run.avg_rr else '—' }}</td>
    <td>{{ "%.1f%%" | format(run.cagr * 100) if run.cagr else '—' }}</td>
    <td>
      {% if run.status == 'pending_approval' %}
      <form method="post" action="/runs/{{ run.id }}/approve" style="display:inline">
        <button type="submit">Approve</button>
      </form>
      {% endif %}
      {% if run.status in ('pending_approval', 'approved') %}
      <form method="post" action="/runs/{{ run.id }}/retire" style="display:inline">
        <button type="submit">Retire</button>
      </form>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_dashboard.py::test_index_returns_200 tests/test_dashboard.py::test_runs_returns_200 -v
```
Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/index.html dashboard/templates/runs.html dashboard/app.py
git commit -m "feat: overview and runs list dashboard pages"
```

---

### Task 3: Run detail, compare, and equity templates

**Files:**
- Create: `dashboard/templates/run_detail.html`
- Create: `dashboard/templates/compare.html`
- Create: `dashboard/templates/equity.html`

- [ ] **Step 1: Create dashboard/templates/run_detail.html**

```html
<!-- dashboard/templates/run_detail.html -->
{% extends "base.html" %}
{% block title %}Run {{ run.id }} — Trading Research Bot{% endblock %}
{% block content %}
<h1>Run {{ run.id }}: {{ strategy.name | default('Unknown') }}</h1>
<p><span class="badge badge-{{ run.status }}">{{ run.status }}</span> &nbsp; {{ run.created_at }}</p>

{% if run.status == 'pending_approval' %}
<form method="post" action="/runs/{{ run.id }}/approve" style="display:inline">
  <button type="submit">✓ Approve for Paper Trading</button>
</form>
{% endif %}
{% if run.status in ('pending_approval', 'approved') %}
<form method="post" action="/runs/{{ run.id }}/retire" style="display:inline">
  <button type="submit">Retire</button>
</form>
{% endif %}

<h2>Thesis</h2>
<p>{{ strategy.thesis | default('—') }}</p>

{% if backtest %}
<h2>Backtest Results</h2>
<table>
  <tr><th>Sharpe</th><td>{{ "%.3f" | format(backtest.sharpe) }}</td>
      <th>Sortino</th><td>{{ "%.3f" | format(backtest.sortino) }}</td></tr>
  <tr><th>Max Drawdown</th><td>{{ "%.1f%%" | format(backtest.max_drawdown_pct * 100) }} ({{ backtest.max_drawdown_days }} days)</td>
      <th>CAGR</th><td>{{ "%.1f%%" | format(backtest.cagr * 100) }}</td></tr>
  <tr><th>Win Rate</th><td>{{ "%.1f%%" | format(backtest.win_rate * 100) }}</td>
      <th>Avg R:R</th><td>{{ "%.2f" | format(backtest.avg_rr) }}</td></tr>
  <tr><th>Total Trades</th><td>{{ backtest.total_trades }}</td>
      <th>Time in Market</th><td>{{ "%.1f%%" | format(backtest.pct_time_in_market * 100) }}</td></tr>
  <tr><th>Period</th><td colspan="3">{{ backtest.backtest_start }} → {{ backtest.backtest_end }}</td></tr>
</table>
{% endif %}

<h2>Regime Filter</h2>
<pre>{{ strategy.regime_filter | tojson(indent=2) if strategy.regime_filter else '—' }}</pre>

<h2>Entry Rules</h2>
<h3>Long</h3>
<ul>{% for c in strategy.entry_long | default([]) %}<li><code>{{ c.condition }}</code> {{ c.operator if c.operator else '' }}</li>{% endfor %}</ul>
<h3>Short</h3>
<ul>{% for c in strategy.entry_short | default([]) %}<li><code>{{ c.condition }}</code> {{ c.operator if c.operator else '' }}</li>{% endfor %}</ul>

<h2>Exit Rules</h2>
{% if strategy.exit %}
<p><strong>Stop:</strong> {{ strategy.exit.stop_rule }}</p>
<p><strong>Trailing:</strong> {{ strategy.exit.trailing_rule }}</p>
<p><strong>Targets:</strong></p>
<ul>{% for t in strategy.exit.targets | default([]) %}<li>{{ t.r_multiple }}R — {{ "%.0f%%" | format(t.tranche_pct * 100) }} of position</li>{% endfor %}</ul>
{% endif %}

<h2>Position Sizing</h2>
<p>{{ strategy.position_sizing | default('—') }}</p>

<h2>Expected Performance Profile</h2>
{% if strategy.expected_profile %}
<ul>
  <li><strong>Win Rate:</strong> {{ "%.0f%%" | format(strategy.expected_profile.win_rate * 100) if strategy.expected_profile.win_rate else '—' }}</li>
  <li><strong>Avg R:R:</strong> {{ strategy.expected_profile.avg_rr | default('—') }}</li>
  <li><strong>Underperforms in:</strong> {{ strategy.expected_profile.underperformance_conditions | default('—') }}</li>
  <li><strong>Drawdown profile:</strong> {{ strategy.expected_profile.drawdown_profile | default('—') }}</li>
</ul>
{% endif %}

<h2>Known Failure Modes</h2>
<ol>{% for fm in strategy.failure_modes | default([]) %}<li>{{ fm }}</li>{% endfor %}</ol>

{% if positions %}
<h2>Open Paper Positions</h2>
<table>
  <thead><tr><th>ID</th><th>Entry Date</th><th>Entry Price</th><th>Stop</th><th>Tranche</th></tr></thead>
  <tbody>
  {% for p in positions %}
  <tr><td>{{ p.id }}</td><td>{{ p.entry_date }}</td><td>${{ "{:,.2f}".format(p.entry_price) }}</td><td>${{ "{:,.2f}".format(p.stop_price) }}</td><td>{{ p.tranche }}</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Create dashboard/templates/compare.html**

```html
<!-- dashboard/templates/compare.html -->
{% extends "base.html" %}
{% block title %}Compare Runs — Trading Research Bot{% endblock %}
{% block content %}
<h1>Strategy Comparison</h1>
{% if not runs %}
<p>No runs with backtest results yet.</p>
{% else %}
<table>
  <thead>
    <tr>
      <th>ID</th><th>Date</th><th>Name</th><th>Status</th>
      <th>Sharpe</th><th>Sortino</th><th>Max DD</th><th>DD Days</th>
      <th>Win Rate</th><th>Avg R:R</th><th>Trades</th><th>Time in Mkt</th><th>CAGR</th>
    </tr>
  </thead>
  <tbody>
  {% for run in runs %}
  {% set strategy = run.strategy_json | from_json %}
  <tr>
    <td><a href="/runs/{{ run.id }}">{{ run.id }}</a></td>
    <td>{{ run.created_at | string | truncate(10, end='') }}</td>
    <td>{{ strategy.name | default('—') }}</td>
    <td><span class="badge badge-{{ run.status }}">{{ run.status }}</span></td>
    <td>{{ "%.2f" | format(run.sharpe) if run.sharpe else '—' }}</td>
    <td>{{ "%.2f" | format(run.sortino) if run.sortino else '—' }}</td>
    <td>{{ "%.1f%%" | format(run.max_drawdown_pct * 100) if run.max_drawdown_pct else '—' }}</td>
    <td>{{ run.max_drawdown_days if run.max_drawdown_days else '—' }}</td>
    <td>{{ "%.1f%%" | format(run.win_rate * 100) if run.win_rate else '—' }}</td>
    <td>{{ "%.2f" | format(run.avg_rr) if run.avg_rr else '—' }}</td>
    <td>{{ run.total_trades if run.total_trades else '—' }}</td>
    <td>{{ "%.1f%%" | format(run.pct_time_in_market * 100) if run.pct_time_in_market else '—' }}</td>
    <td>{{ "%.1f%%" | format(run.cagr * 100) if run.cagr else '—' }}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Add missing columns to all_runs query in storage/db.py**

The compare page needs `sortino`, `max_drawdown_days`, `total_trades`, `pct_time_in_market`. Update `all_runs()` in `storage/db.py`:

```python
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
```

- [ ] **Step 4: Create dashboard/templates/equity.html**

```html
<!-- dashboard/templates/equity.html -->
{% extends "base.html" %}
{% block title %}Equity Curve — Trading Research Bot{% endblock %}
{% block content %}
<h1>Equity Curve</h1>
{% if not active %}
<p>No active strategy. <a href="/runs">Approve a run →</a></p>
{% elif not chart_data.dates %}
<p>No paper trading data yet for the active strategy.</p>
{% else %}
<p>Active strategy: <strong>{{ active.strategy_json | from_json | attr('name') }}</strong></p>
<canvas id="equityChart" height="80"></canvas>
<canvas id="drawdownChart" height="40"></canvas>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const dates = {{ chart_data.dates | tojson }};
const equity = {{ chart_data.equity | tojson }};
const drawdown = {{ chart_data.drawdown | tojson }};

new Chart(document.getElementById('equityChart'), {
  type: 'line',
  data: {
    labels: dates,
    datasets: [{
      label: 'Paper Equity ($)',
      data: equity,
      borderColor: '#5cb85c',
      backgroundColor: 'rgba(92,184,92,0.1)',
      fill: true,
      pointRadius: 0,
      tension: 0.1,
    }]
  },
  options: {
    plugins: { legend: { display: true } },
    scales: { x: { ticks: { maxTicksLimit: 12 } } }
  }
});

new Chart(document.getElementById('drawdownChart'), {
  type: 'line',
  data: {
    labels: dates,
    datasets: [{
      label: 'Drawdown (%)',
      data: drawdown.map(d => (d * 100).toFixed(2)),
      borderColor: '#d9534f',
      backgroundColor: 'rgba(217,83,79,0.1)',
      fill: true,
      pointRadius: 0,
      tension: 0.1,
    }]
  },
  options: {
    plugins: { legend: { display: true } },
    scales: { x: { ticks: { maxTicksLimit: 12 } } }
  }
});
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run all dashboard tests**

```bash
pytest tests/test_dashboard.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add dashboard/templates/run_detail.html dashboard/templates/compare.html dashboard/templates/equity.html storage/db.py
git commit -m "feat: run detail, compare, and equity curve dashboard pages"
```

---

### Task 4: Verify dashboard manually

- [ ] **Step 1: Seed test data**

```bash
python -c "
import json
from datetime import date
from storage.db import Database
db = Database('data.duckdb')
db.init_schema()
strategy = {
    'name': 'Test Dashboard Strategy',
    'thesis': 'Testing the dashboard works correctly.',
    'regime_filter': {'logic': 'adx_14 > 20'},
    'entry_long': [{'condition': 'close > ema_200'}, {'condition': 'rsi_14 > 50'}],
    'entry_short': [{'condition': 'close < ema_200'}, {'condition': 'rsi_14 < 50'}],
    'exit': {'stop_rule': '1.5x ATR', 'targets': [{'r_multiple': 2.0, 'tranche_pct': 1.0}], 'trailing_rule': 'breakeven after 1R'},
    'position_sizing': 'size = (equity * 0.01) / stop_distance',
    'expected_profile': {'win_rate': 0.5, 'avg_rr': 2.0, 'underperformance_conditions': 'chop', 'drawdown_profile': '15% max'},
    'failure_modes': ['False breakouts', 'Gap openings', 'Bear market trend']
}
run_id = db.insert_strategy_run(json.dumps(strategy))
db.insert_backtest_results(run_id, {
    'sharpe': 1.6, 'sortino': 2.1, 'max_drawdown_pct': 0.14, 'max_drawdown_days': 28,
    'win_rate': 0.51, 'avg_rr': 1.95, 'total_trades': 38, 'pct_time_in_market': 0.52,
    'cagr': 0.25, 'backtest_start': date(2023,1,1), 'backtest_end': date(2024,1,1),
})
from datetime import timedelta
for i in range(30):
    d = date(2024, 1, 1) + timedelta(days=i)
    db.upsert_equity_curve(d, 100000 + i * 200, -0.01 * (i % 5), run_id)
print(f'Seeded run_id={run_id}')
"
```

- [ ] **Step 2: Start the dashboard**

```bash
python dashboard/app.py
```
Expected: `Uvicorn running on http://127.0.0.1:8080`

- [ ] **Step 3: Verify pages load in browser**

Open each URL and confirm it renders correctly:
- `http://localhost:8080/` — Overview page
- `http://localhost:8080/runs` — Runs table with the seeded run
- `http://localhost:8080/runs/1` — Full run detail with all strategy sections
- `http://localhost:8080/runs/compare` — Comparison table
- `http://localhost:8080/equity` — Equity curve (empty if no active strategy yet)

- [ ] **Step 4: Test approve flow**

On `/runs`, click **Approve** for the seeded run. Verify:
- Redirected to `/runs/1`
- Status badge shows `approved`
- `/equity` now shows the equity chart

- [ ] **Step 5: Test retire flow**

On `/runs/1`, click **Retire**. Verify redirected to `/runs` and status shows `retired`.

- [ ] **Step 6: Commit**

```bash
git add .
git commit -m "chore: verified dashboard renders and approve/retire flows work end-to-end"
```
