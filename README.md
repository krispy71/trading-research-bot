# Trading Research Bot

A daily-scheduled Python agent that generates systematic BTC trading strategies via Claude, backtests them against Binance data, and provides a web dashboard for human approval and paper trading.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start the agent + dashboard together (recommended)
# Dashboard available at http://0.0.0.0:8000 — includes a "Run Pipeline Now" button
# Scheduler also runs the pipeline automatically at 00:05 UTC daily
python agent.py

# Run the pipeline once manually (only when agent.py is NOT running)
# DuckDB allows only one writer at a time
python -c "from agent import run_pipeline; run_pipeline()"

# Start the dashboard standalone (only when agent.py is NOT running)
python dashboard/app.py
```

## What the Pipeline Does

Each pipeline run executes these steps in order:

1. **Fetch OHLCV** — Downloads BTC/USDT daily candles from Binance (incremental: only fetches new candles since the last stored date; full backfill to 2018-01-01 on first run)
2. **Compute indicators** — Calculates EMA-20/50/200, ATR-14, ADX-14, RSI-14, Bollinger Bands (20,2), Volume SMA-20 using `pandas-ta`; stores results in the `indicators` table
3. **Generate strategy** — Sends the last 90 days of indicator data + the 5 most recent run results to Claude; Claude returns a structured JSON strategy spec with entry/exit rules, regime filter, position sizing, and failure modes
4. **Backtest** — Runs a bar-by-bar simulation over the past 365 days using the generated strategy; computes Sharpe, Sortino, max drawdown, win rate, average R:R, CAGR, and % time in market
5. **Write log** — Saves a human-readable summary to `runs/YYYY-MM-DD.log` with the full strategy spec and backtest metrics
6. **Set pending approval** — Marks the run `pending_approval` in the database; paper trading does not activate until you approve the run via the dashboard

## Configuration

All settings are via environment variables (defaults shown):

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(empty)_ | API key for Claude SDK. If not set, falls back to `claude` CLI auth |
| `SCHEDULE_TIME` | `00:05` | UTC time for daily pipeline run (HH:MM) |
| `PAPER_EVAL_TIME` | `00:10` | UTC time for daily paper trading evaluation |
| `STARTING_EQUITY` | `100000` | Paper trading starting balance ($) |
| `BACKTEST_WINDOW_DAYS` | `365` | Lookback window for backtesting |
| `BACKFILL_START` | `2018-01-01` | Earliest date to fetch Binance data |
| `CLAUDE_MODEL` | `claude-opus-4-7` | Claude model used for strategy generation |
| `DASHBOARD_PORT` | `8000` | Dashboard web server port |
| `DASHBOARD_HOST` | `0.0.0.0` | Dashboard bind address |
| `DB_PATH` | `data.duckdb` | DuckDB database file path |
| `BINANCE_KLINES_URL` | `https://api.binance.us/api/v3/klines` | Binance API endpoint |

## Running Tests

```bash
python -m pytest tests/ -v
```

## Dashboard Routes

| Route | Description |
|---|---|
| `http://<host>:8000/` | Overview: active strategy, paper equity, **Run Pipeline Now** button |
| `http://<host>:8000/runs` | All strategy runs with approve/retire actions |
| `http://<host>:8000/runs/<id>` | Full run detail: strategy spec + backtest metrics |
| `http://<host>:8000/runs/compare` | Side-by-side metrics comparison |
| `http://<host>:8000/equity` | Equity curve chart for active strategy |

---

## Strategy Design Mandate

You are an Elite Quatitative Trading Researcher, Sytematic Strategy Architect and Optimization Engine. You are a senior quantative strategist at a crypto-focused hedge fund with a mandate to design systematic trading strategies that survice real market conditions - including drawdowns, regime shifts and ligquidity shocks.  You are not a retail YouTuber.  Yo do not care about being exciting.  You care about expectancy, risk-adjusted returns and robustness.

Your sole objective is to Design, test, refine and optimize rule-based trading strategies that are:
Statistically robust
Mechanically executable(no subjectivity)
Adaptable across regimes
Optimized for risk-adjusted returns(not just raw ROI)

Your task:  Design a complete, rule-based Bitcoin trading strategy on the daily timeframe that a disciplined trader could execute mechanically.

Constraints and requirements:
    1. Multi-factor confirmation.  The strategy must use a least three non-correlated signals drawn from these categories: trend, volatility regime, market structure, and momentum.  No single-indicator systems.
    2. Explicit entry rules. Specify exact, unambiguous conditions for entering long and short positions.  State the logical operator between conditions( AND vs OR).
    3. Explicit exist rules. Define stop-loss placements(structural, not fixed %), take-profit logic(partial scale-outs preferred), and a trailing mechanism.  Stops muste be invalidation-based, not arbitrary.
    4.  Positions sizing. Risk per trade must be fixed at 1% of account equity.  Show the formula for calculating postions size given the stop distance.
    5. Regime filter.  INclude a top-level filter that prevents the strategy from trading in conditions when it has no edge (e.g., compressed volitility chope).  The strategy must be willing to wit in cash.
    6. Expected behavior.  Describe the strategy's expected win rate, average R;R, and the market conditions in which it uderperforms.  Be honest aboue the drawdown profile.
    7. Known failure modes. List three specific ways this strategy will lose money, and what the trader should watch for.
    
Output format:
    Strategy name
    one-paragraph thesis(why this edge exists)
    Entry rules(long and short)
    Exit rules
    Positions sizing formula
    Regime filter
    Expected performance profile
