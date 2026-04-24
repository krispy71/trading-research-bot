# Trading Research Bot — Design Spec
**Date:** 2026-04-22

## Overview

A daily-scheduled Python agent that uses Claude to generate systematic BTC trading strategies, backtests them against historical Binance data, logs results for human review, and executes approved strategies in paper trading mode. All state persists in a local DuckDB database for future reporting and analysis.

---

## Architecture

**Execution model:** Persistent Python process using APScheduler. Two scheduled jobs:
1. **Daily research pipeline** (configurable time) — fetch → generate → backtest → log
2. **Daily paper trading evaluation** (after market close) — evaluate active strategy against latest candle

**Top-level modules:**

| Module | Responsibility |
|---|---|
| `agent.py` | Scheduler setup, pipeline orchestration, stage routing |
| `data/fetcher.py` | Binance API client, incremental OHLCV fetch, indicator computation |
| `research/generator.py` | Claude API calls, prompt construction, structured output parsing |
| `backtest/engine.py` | Pure-Python bar-by-bar backtester |
| `paper/trader.py` | Paper trading state machine, position and equity tracking |
| `storage/db.py` | DuckDB connection, all read/write operations |
| `reporting/reporter.py` | CLI reporting over historical runs and equity data |

---

## Data Layer

**Source:** Binance public REST API (`/api/v3/klines`, BTC/USDT daily) — no API key required.

**Initial backfill:** January 1, 2018. If Binance data does not extend that far, a warning is written to the log and the earliest available date is used.

**Fetch strategy:** Incremental. Backfill on first run; subsequent runs fetch only candles since the last stored timestamp.

**Retention:** All data kept permanently. Never purged.

**DuckDB schema:**

```sql
-- Raw price data
ohlcv (timestamp PK, open, high, low, close, volume)

-- Pre-computed indicators (written after each fetch)
indicators (timestamp PK, ema_20, ema_50, ema_200, atr_14, adx_14, rsi_14,
            bb_upper, bb_lower, bb_mid, volume_sma_20)

-- Strategy research runs
strategy_runs (id PK, created_at, strategy_json, status, notes)
-- status: pending_approval | approved | retired | parse_error

-- Backtest results per run
backtest_results (run_id FK, sharpe, sortino, max_drawdown_pct, max_drawdown_days,
                  win_rate, avg_rr, total_trades, pct_time_in_market, cagr,
                  backtest_start, backtest_end)

-- Paper trading positions
paper_positions (id PK, run_id FK, entry_date, entry_price, stop_price,
                 tranche, exit_date, exit_price, exit_reason, pnl_r)
-- exit_reason: stop | target | trailing_stop | regime_filter | manual

-- Daily equity snapshots
equity_curve (date PK, equity, drawdown_pct, run_id FK)
```

**Indicator library:** Computed in Python using `pandas-ta` after each OHLCV fetch. Stored as columns in the `indicators` table. Indicators: EMA-20/50/200, ATR-14, ADX-14, RSI-14, Bollinger Bands (20,2), Volume SMA-20.

---

## LLM Strategy Generation

**Model:** `claude-opus-4-7`

**Trigger:** Daily, immediately after data fetch completes.

**Prompt construction includes:**
- Full persona and strategy constraints (from CLAUDE.md)
- Last 90 days of indicator values as compact JSON
- Current regime context: ATR percentile (vs 1-year history), ADX level, EMA trend alignment
- Last 5 strategy runs with their backtest metrics (so the model can learn from prior attempts)

**Output format:** Strict JSON schema. If parsing fails, the run is recorded as `status: parse_error` and the pipeline halts for the day with a log entry.

**Required JSON fields in strategy spec:**
- `name`, `thesis`
- `regime_filter` — conditions under which no trade is taken (go to cash)
- `entry_long`, `entry_short` — array of conditions with explicit AND/OR operators
- `exit` — `stop_rule`, `targets` (array with R multiples and tranche sizes), `trailing_rule`
- `position_sizing` — formula referencing ATR or structural stop distance, 1% equity risk
- `expected_profile` — win_rate, avg_rr, underperformance_conditions, drawdown_profile
- `failure_modes` — exactly 3 specific failure scenarios

---

## Backtesting Engine

**Approach:** Pure-Python bar-by-bar event simulation. No third-party backtesting framework.

**Window:** Rolling 1-year lookback from run date, queried from DuckDB.

**Simulation rules:**
- Regime filter evaluated first each bar — if not met, skip (cash)
- Entry conditions evaluated at daily close
- Position sizing: `position_size = (account_equity * 0.01) / stop_distance_in_$`
- Partial scale-outs tracked as separate tranches in `paper_positions`
- Trailing stop updated bar-by-bar per strategy spec
- Max 1 open position at a time

**Metrics written to `backtest_results`:** Sharpe, Sortino, max drawdown (% and duration in days), win rate, average R:R, total trades, % time in market, CAGR.

---

## Human Approval Gate

After backtesting:
1. Results + full strategy spec written to `runs/YYYY-MM-DD.log` (human-readable)
2. `strategy_runs.status` set to `pending_approval`
3. Paper trading does **not** activate until you manually approve

**Approval CLI:**
```bash
python approve.py --run-id <id>    # approve a strategy for paper trading
python approve.py --list           # show all pending runs
```

Approving a strategy automatically retires any currently active strategy.

---

## Paper Trading Engine

**Activation:** Runs daily (after market close). Only activates if exactly one strategy has `status: approved`. If multiple approved strategies exist, logs an error and skips.

**Position tracking:** Stored in `paper_positions`. Each tranche (partial scale-out) is a separate record. Exit reason always logged.

**Starting equity:** Configurable (default: $100,000 paper).

**Equity curve:** Daily snapshots written to `equity_curve`. Running drawdown and Sharpe computed incrementally.

**Regime monitoring:** Regime filter status logged every day regardless of open positions. Open positions are not force-closed on regime change — the strategy's own exit rules govern.

**Deactivation:** Manual (`python approve.py --retire <id>`) or automatic when a new strategy is approved.

---

## Reporting CLI

```bash
python report.py --summary              # table of all runs with key backtest metrics
python report.py --run-id <id>          # full detail: spec + backtest + paper results
python report.py --equity               # equity curve for active strategy
python report.py --compare              # side-by-side backtest metrics for all runs
```

All queries run directly against DuckDB. No separate reporting infrastructure.

---

## Project Structure

```
trading-research-bot/
├── agent.py                  # scheduler, pipeline orchestration
├── approve.py                # approval CLI
├── report.py                 # reporting CLI
├── config.py                 # configurable parameters (equity, schedule time, etc.)
├── data/
│   └── fetcher.py            # Binance fetch + indicator computation
├── research/
│   └── generator.py          # Claude API + prompt construction + output parsing
├── backtest/
│   └── engine.py             # bar-by-bar backtester
├── paper/
│   └── trader.py             # paper trading state machine
├── storage/
│   └── db.py                 # DuckDB connection + all queries
├── reporting/
│   └── reporter.py           # report generation
├── runs/                     # daily log files (YYYY-MM-DD.log)
├── data.duckdb               # persistent database (gitignored)
├── docs/
│   └── superpowers/specs/
│       └── 2026-04-22-trading-research-bot-design.md
├── CLAUDE.md
└── README.md
```

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `anthropic` | Claude API client |
| `apscheduler` | Persistent scheduler |
| `duckdb` | Local analytical database |
| `pandas` | OHLCV data manipulation |
| `pandas-ta` | Technical indicator computation |
| `requests` | Binance REST API calls |

---

## Configuration (`config.py`)

- `SCHEDULE_TIME` — daily run time (default: `"00:05"` UTC, 5 min after daily close)
- `STARTING_EQUITY` — paper trading starting balance (default: `100_000`)
- `BACKTEST_WINDOW_DAYS` — lookback for backtesting (default: `365`)
- `BACKFILL_START` — earliest date to fetch (default: `"2018-01-01"`)
- `CLAUDE_MODEL` — model ID (default: `"claude-opus-4-7"`)
