# research/generator.py
import json
import logging
import re
import anthropic
import pandas as pd
import config

logger = logging.getLogger(__name__)

STRATEGY_SCHEMA_KEYS = [
    "name", "thesis", "regime_filter", "entry_long", "entry_short",
    "exit", "position_sizing", "expected_profile", "failure_modes"
]

PERSONA = """You are an elite quantitative trading researcher at a crypto hedge fund.
You care about expectancy, risk-adjusted returns, and robustness — not excitement.
You design mechanically executable rule-based strategies only."""

CONSTRAINTS = """Constraints:
- Use at least 3 non-correlated signals from: trend, volatility regime, market structure, momentum
- entry_long and entry_short: array of condition objects with "condition" (string) and "operator" ("AND"/"OR")
- exit.stop_rule: invalidation-based (structural or ATR-derived), never fixed percentage
- exit.targets: array with r_multiple and tranche_pct, partial scale-outs required
- position_sizing: formula using (equity * 0.01) / stop_distance
- regime_filter: conditions under which strategy goes to cash (must be explicit)
- failure_modes: EXACTLY 3 specific ways this strategy loses money
- expected_profile.win_rate: realistic (0.35–0.60 range)"""

OUTPUT_FORMAT = """Respond ONLY with a JSON object matching this schema exactly:
{
  "name": string,
  "thesis": string (one paragraph),
  "regime_filter": object with "logic" (string condition) and any thresholds,
  "entry_long": array of {"condition": string, "operator": "AND"|"OR"} (last item omits operator),
  "entry_short": array of {"condition": string, "operator": "AND"|"OR"} (last item omits operator),
  "exit": {
    "stop_rule": string,
    "targets": [{"r_multiple": number, "tranche_pct": number}],
    "trailing_rule": string
  },
  "position_sizing": string (formula),
  "expected_profile": {
    "win_rate": number,
    "avg_rr": number,
    "underperformance_conditions": string,
    "drawdown_profile": string
  },
  "failure_modes": [string, string, string]
}
No markdown fences. No explanation. JSON only."""


def build_prompt(recent_indicators: pd.DataFrame, prior_runs: list[dict]) -> str:
    indicator_json = recent_indicators.tail(90).to_json(orient="records", date_format="iso")

    prior_context = ""
    if prior_runs:
        summaries = []
        for r in prior_runs[-5:]:
            summaries.append(
                f"Run {r['id']} ({r.get('created_at', '')[:10]}): "
                f"Sharpe={r.get('sharpe')}, MaxDD={r.get('max_drawdown_pct')}, "
                f"WinRate={r.get('win_rate')}, AvgRR={r.get('avg_rr')}"
            )
        prior_context = "\n\nPrior strategy run results (learn from these):\n" + "\n".join(summaries)

    return f"""{PERSONA}

{CONSTRAINTS}

Current market data (last 90 days of daily indicators, BTC/USDT):
{indicator_json}
{prior_context}

Task: Design a complete rule-based BTC daily timeframe trading strategy.

{OUTPUT_FORMAT}"""


def parse_strategy_response(raw: str) -> dict:
    # Strip markdown fences if present
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        strategy = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Strategy response is not valid JSON: {e}")

    for key in STRATEGY_SCHEMA_KEYS:
        if key not in strategy:
            raise ValueError(f"Strategy response missing required key: '{key}'")

    if not isinstance(strategy["failure_modes"], list) or len(strategy["failure_modes"]) != 3:
        raise ValueError("failure_modes must be a list of exactly 3 strings")

    return strategy


def generate_strategy(recent_indicators: pd.DataFrame, prior_runs: list[dict]) -> dict:
    """Call Claude to generate a strategy. Returns parsed strategy dict."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = build_prompt(recent_indicators, prior_runs)

    logger.info(f"Calling {config.CLAUDE_MODEL} for strategy generation...")
    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text
    logger.info("Strategy response received, parsing...")
    return parse_strategy_response(raw)
