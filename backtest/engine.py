# backtest/engine.py
import math
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

def _eval_condition(condition: str, bar: dict) -> bool:
    ops = [">=", "<=", "!=", ">", "<", "=="]
    for op in ops:
        if op in condition:
            lhs, rhs = [s.strip() for s in condition.split(op, 1)]
            left = bar.get(lhs)
            if left is None:
                try:
                    left = float(lhs)
                except ValueError:
                    return False
            try:
                right = float(rhs)
            except ValueError:
                right = bar.get(rhs)
            if left is None or right is None:
                return False
            if op == ">":  return left > right
            if op == "<":  return left < right
            if op == ">=": return left >= right
            if op == "<=": return left <= right
            if op == "==": return left == right
            if op == "!=": return left != right
    return False


def evaluate_regime_filter(regime_filter: dict, bar: dict) -> bool:
    logic = regime_filter.get("logic", "")
    if not logic:
        return True
    return _eval_condition(logic, bar)


def evaluate_conditions(conditions: list[dict], bar: dict) -> bool:
    """Evaluate a list of conditions — all must be True (AND semantics)."""
    return all(_eval_condition(c["condition"], bar) for c in conditions)


def _compute_stop(bar: dict, side: str) -> float:
    atr = bar.get("atr_14", 0)
    if side == "long":
        return bar["close"] - 1.5 * atr
    else:
        return bar["close"] + 1.5 * atr


def _compute_metrics(trades: list[dict], equity_curve: list[float], starting_equity: float, total_bars: int) -> dict:
    if not trades:
        return {
            "sharpe": 0.0, "sortino": 0.0, "max_drawdown_pct": 0.0, "max_drawdown_days": 0,
            "win_rate": 0.0, "avg_rr": 0.0, "total_trades": 0,
            "pct_time_in_market": 0.0, "cagr": 0.0,
        }

    returns = pd.Series(equity_curve).pct_change().dropna()
    mean_r = returns.mean()
    std_r = returns.std()
    downside = returns[returns < 0].std()
    sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
    sortino = (mean_r / downside * math.sqrt(252)) if downside > 0 else 0.0

    eq = pd.Series(equity_curve)
    rolling_max = eq.cummax()
    dd = (eq - rolling_max) / rolling_max
    max_dd_pct = float(abs(dd.min()))
    in_dd = (dd < 0).astype(int)
    max_dd_days = int(in_dd.groupby((in_dd == 0).cumsum()).cumsum().max())

    rr_values = [t["pnl_r"] for t in trades if t.get("pnl_r") is not None]
    win_rate = len([r for r in rr_values if r > 0]) / len(rr_values) if rr_values else 0.0
    avg_rr = float(np.mean(rr_values)) if rr_values else 0.0

    bars_in_trade = sum(t.get("bars_held", 0) for t in trades)
    pct_time = bars_in_trade / total_bars if total_bars > 0 else 0.0
    years = total_bars / 365
    cagr = ((equity_curve[-1] / starting_equity) ** (1 / years) - 1) if years > 0 else 0.0

    return {
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "max_drawdown_days": max_dd_days,
        "win_rate": round(win_rate, 4),
        "avg_rr": round(avg_rr, 4),
        "total_trades": len(trades),
        "pct_time_in_market": round(pct_time, 4),
        "cagr": round(cagr, 4),
    }


def run_backtest(strategy: dict, df: pd.DataFrame, starting_equity: float = 100000.0) -> dict:
    equity = starting_equity
    position = None
    trades = []
    equity_curve = [equity]
    regime_filter = strategy.get("regime_filter", {})
    entry_long = strategy.get("entry_long", [])
    entry_short = strategy.get("entry_short", [])
    targets = strategy["exit"]["targets"]
    total_bars = len(df)

    for i, row in df.iterrows():
        bar = row.to_dict()
        if hasattr(bar["timestamp"], "date"):
            bar["timestamp"] = bar["timestamp"].date()

        in_regime = evaluate_regime_filter(regime_filter, bar)
        if not in_regime:
            if position is None:
                equity_curve.append(equity)
                continue

        if position is not None:
            side = position["side"]
            entry_price = position["entry_price"]
            stop_price = position["stop_price"]
            stop_distance = abs(entry_price - stop_price)

            hit_stop = (side == "long" and bar["low"] <= stop_price) or \
                       (side == "short" and bar["high"] >= stop_price)

            if hit_stop:
                risk_amount = equity * 0.01
                equity -= risk_amount
                trades.append({
                    "side": side, "entry_price": entry_price, "exit_price": stop_price,
                    "exit_reason": "stop", "pnl_r": -1.0,
                    "bars_held": i - position["entry_bar"],
                })
                position = None
                equity_curve.append(equity)
                continue

            remaining_targets = position.get("remaining_targets", list(targets))
            if remaining_targets:
                t = remaining_targets[0]
                target_price = (entry_price + t["r_multiple"] * stop_distance) if side == "long" \
                               else (entry_price - t["r_multiple"] * stop_distance)
                hit_target = (side == "long" and bar["high"] >= target_price) or \
                             (side == "short" and bar["low"] <= target_price)
                if hit_target:
                    risk_amount = equity * 0.01
                    equity += risk_amount * t["r_multiple"] * t["tranche_pct"]
                    remaining_targets = remaining_targets[1:]
                    position["remaining_targets"] = remaining_targets
                    position["stop_price"] = entry_price  # trail to breakeven
                    if not remaining_targets:
                        total_pnl_r = sum(t2["r_multiple"] * t2["tranche_pct"] for t2 in targets)
                        trades.append({
                            "side": side, "entry_price": entry_price, "exit_price": target_price,
                            "exit_reason": "target", "pnl_r": total_pnl_r,
                            "bars_held": i - position["entry_bar"],
                        })
                        position = None

            equity_curve.append(equity)
            continue

        if position is None:
            if evaluate_conditions(entry_long, bar):
                stop = _compute_stop(bar, "long")
                position = {
                    "side": "long", "entry_price": bar["close"], "stop_price": stop,
                    "remaining_targets": list(targets), "entry_bar": i,
                }
            elif evaluate_conditions(entry_short, bar):
                stop = _compute_stop(bar, "short")
                position = {
                    "side": "short", "entry_price": bar["close"], "stop_price": stop,
                    "remaining_targets": list(targets), "entry_bar": i,
                }

        equity_curve.append(equity)

    return _compute_metrics(trades, equity_curve, starting_equity, total_bars)
