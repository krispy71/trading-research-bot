# backtest/custom_runner.py
import pandas as pd
from backtest.engine import run_backtest


def run_custom_backtest(
    strategy: dict,
    df: pd.DataFrame,
    regime_filter_mode: str,
    regime_filter_overrides: dict,
    starting_equity: float = 100_000,
) -> dict:
    """Run a backtest with a modified regime filter.

    Args:
        strategy: Strategy dict as produced by the generator.
        df: Merged OHLCV + indicators DataFrame.
        regime_filter_mode: One of 'strategy', 'disabled', 'custom'.
        regime_filter_overrides: Mapping of condition string → bool.
            Only used when regime_filter_mode == 'custom'.
        starting_equity: Starting portfolio value in USD.

    Returns:
        9-key metrics dict identical to run_backtest output.
    """
    if regime_filter_mode == 'strategy':
        modified = strategy
    elif regime_filter_mode == 'disabled':
        modified = {**strategy, 'regime_filter': {}}
    else:  # 'custom'
        original_logic = strategy.get('regime_filter', {}).get('logic', '')
        conditions = [c.strip() for c in original_logic.split(' AND ') if c.strip()]
        enabled = [c for c in conditions if regime_filter_overrides.get(c, True)]
        if not enabled:
            modified = {**strategy, 'regime_filter': {}}
        else:
            modified = {
                **strategy,
                'regime_filter': {'logic': ' AND '.join(enabled)},
            }

    return run_backtest(modified, df, starting_equity=starting_equity)
