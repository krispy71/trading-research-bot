# paper/trader.py
import logging
from datetime import date
from storage.db import Database
from backtest.engine import evaluate_regime_filter, evaluate_conditions

logger = logging.getLogger(__name__)

class PaperTrader:
    def __init__(self, db: Database, run_id: int, strategy: dict, starting_equity: float):
        self.db = db
        self.run_id = run_id
        self.strategy = strategy

        # Restore equity from last snapshot (or use starting equity on first run)
        curve = db.get_equity_curve(run_id)
        if curve:
            self.equity = curve[-1]["equity"]
            self.peak_equity = max(r["equity"] for r in curve)
        else:
            self.equity = starting_equity
            self.peak_equity = starting_equity

        # Restore open position from DB if one exists
        self._position = None
        open_positions = db.open_paper_positions(run_id)
        if open_positions:
            pos = open_positions[0]
            side = "long" if pos["stop_price"] < pos["entry_price"] else "short"
            self._position = {
                "id": pos["id"],
                "side": side,
                "entry_price": pos["entry_price"],
                "stop_price": pos["stop_price"],
                "remaining_targets": list(strategy["exit"]["targets"]),
                "stop_distance": abs(pos["entry_price"] - pos["stop_price"]),
            }

    def process_bar(self, bar: dict):
        """Evaluate one daily bar. Updates DB with any position changes and equity snapshot."""
        regime_ok = evaluate_regime_filter(self.strategy.get("regime_filter", {}), bar)
        dt = bar["timestamp"]

        if self._position is not None:
            self._manage_position(bar)
        elif regime_ok:
            self._try_entry(bar)

        # Equity snapshot
        self.peak_equity = max(self.peak_equity, self.equity)
        drawdown = (self.equity - self.peak_equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        self.db.upsert_equity_curve(dt, self.equity, drawdown, self.run_id)

        if not regime_ok:
            logger.info(f"[{dt}] Regime filter inactive — sitting in cash")

    def _try_entry(self, bar: dict):
        entry_long = self.strategy.get("entry_long", [])
        entry_short = self.strategy.get("entry_short", [])
        atr = bar.get("atr_14", 0)

        if evaluate_conditions(entry_long, bar):
            stop = bar["close"] - 1.5 * atr
            pos_id = self.db.insert_paper_position(self.run_id, {
                "entry_date": bar["timestamp"],
                "entry_price": bar["close"],
                "stop_price": stop,
                "tranche": 1,
            })
            self._position = {
                "id": pos_id, "side": "long",
                "entry_price": bar["close"], "stop_price": stop,
                "remaining_targets": list(self.strategy["exit"]["targets"]),
                "stop_distance": abs(bar["close"] - stop),
            }
            logger.info(f"[{bar['timestamp']}] LONG entry at {bar['close']:.2f}, stop {stop:.2f}")

        elif evaluate_conditions(entry_short, bar):
            stop = bar["close"] + 1.5 * atr
            pos_id = self.db.insert_paper_position(self.run_id, {
                "entry_date": bar["timestamp"],
                "entry_price": bar["close"],
                "stop_price": stop,
                "tranche": 1,
            })
            self._position = {
                "id": pos_id, "side": "short",
                "entry_price": bar["close"], "stop_price": stop,
                "remaining_targets": list(self.strategy["exit"]["targets"]),
                "stop_distance": abs(bar["close"] - stop),
            }
            logger.info(f"[{bar['timestamp']}] SHORT entry at {bar['close']:.2f}, stop {stop:.2f}")

    def _manage_position(self, bar: dict):
        side = self._position["side"]
        stop = self._position["stop_price"]
        entry = self._position["entry_price"]
        stop_dist = self._position["stop_distance"]
        risk = self.equity * 0.01

        hit_stop = (side == "long" and bar["low"] <= stop) or \
                   (side == "short" and bar["high"] >= stop)

        if hit_stop:
            self.equity -= risk
            self.db.close_paper_position(
                self._position["id"], bar["timestamp"], stop, "stop", -1.0
            )
            logger.info(f"[{bar['timestamp']}] Stop hit. Equity: {self.equity:.2f}")
            self._position = None
            return

        remaining = self._position.get("remaining_targets", [])
        if remaining:
            t = remaining[0]
            target_px = (entry + t["r_multiple"] * stop_dist) if side == "long" \
                        else (entry - t["r_multiple"] * stop_dist)
            hit_target = (side == "long" and bar["high"] >= target_px) or \
                         (side == "short" and bar["low"] <= target_px)
            if hit_target:
                partial_pnl_r = t["r_multiple"] * t["tranche_pct"]
                self.equity += risk * partial_pnl_r
                self._position["remaining_targets"] = remaining[1:]
                self._position["stop_price"] = entry  # trail to breakeven
                logger.info(f"[{bar['timestamp']}] Target {t['r_multiple']}R hit. Equity: {self.equity:.2f}")

                if not self._position["remaining_targets"]:
                    total_r = sum(t2["r_multiple"] * t2["tranche_pct"] for t2 in self.strategy["exit"]["targets"])
                    self.db.close_paper_position(
                        self._position["id"], bar["timestamp"], target_px, "target", total_r
                    )
                    self._position = None
