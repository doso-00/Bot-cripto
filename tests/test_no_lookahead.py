"""test_no_lookahead (brief §10): modificando le candele future rispetto a t, il segnale
(e l'equity) fino a t non cambia; il segnale di t si esegue all'apertura di t+1."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from bot.backtest.engine import run_backtest
from bot.strategies import STRATEGIES, compute_targets
from bot.strategies.sma_cross import SmaCrossParams
from tests.backtest_helpers import LOOSE, config, price_candles, targets
from tests.conftest import candles as synthetic_candles

PARAMS = {"buy_and_hold": STRATEGIES["buy_and_hold"].params_model(), "sma_cross": SmaCrossParams(fast=5, slow=20)}


def scramble_after(df: pd.DataFrame, t: int, seed: int) -> pd.DataFrame:
    """Copia di df con prezzi e volumi casuali dopo la riga t."""
    rng = np.random.default_rng(seed)
    out = df.copy()
    future = out.index > t
    factor = rng.uniform(0.5, 1.5, future.sum())
    for col in ("open", "high", "low", "close"):
        out.loc[future, col] = out.loc[future, col] * factor
    out.loc[future, "volume"] = rng.uniform(0, 100, future.sum())
    return out


@pytest.mark.parametrize("name", sorted(STRATEGIES))
@pytest.mark.parametrize("t", [0, 25, 150, 298])
def test_strategy_signal_does_not_depend_on_future(name, t):
    spec = STRATEGIES[name]
    df = synthetic_candles(datetime(2020, 1, 1), 300, seed=1)
    base = compute_targets(spec, df, PARAMS[name])
    for seed in range(3):
        changed = compute_targets(spec, scramble_after(df, t, seed), PARAMS[name])
        pd.testing.assert_series_equal(base.iloc[: t + 1], changed.iloc[: t + 1])


@pytest.mark.parametrize("t", [30, 120, 250])
def test_engine_equity_until_t_does_not_depend_on_future(t):
    spec = STRATEGIES["sma_cross"]
    df = synthetic_candles(datetime(2020, 1, 1), 300, seed=2)
    base = run_backtest(df, compute_targets(spec, df, PARAMS["sma_cross"]), config(), LOOSE)
    other_df = scramble_after(df, t, seed=7)
    other = run_backtest(other_df, compute_targets(spec, other_df, PARAMS["sma_cross"]), config(), LOOSE)
    pd.testing.assert_frame_equal(base.equity.iloc[: t + 1], other.equity.iloc[: t + 1])


def test_signal_at_t_executes_at_open_of_t_plus_1():
    # Il segnale arriva alla chiusura della candela 2; la candela 3 apre a 105
    candles = price_candles([100, 101, 102, 105, 107], closes=[101, 102, 104, 106, 108])
    result = run_backtest(candles, targets([0, 0, 1, 1, 1]), config(fee=0.0, slippage=0.0), LOOSE)
    buy = result.orders.iloc[0]
    assert buy["ts"] == candles["open_time"].iat[3]
    assert buy["price_exec"] == 105


def test_zero_volume_candle_postpones_execution():
    # La candela 3 non ha scambi (es. fermo dell'exchange): l'ordine slitta alla 4
    candles = price_candles([100, 100, 100, 100, 110, 110])
    candles.loc[3, "volume"] = 0
    result = run_backtest(candles, targets([0, 0, 1, 1, 1, 1]), config(fee=0.0, slippage=0.0), LOOSE)
    assert result.orders.iloc[0]["ts"] == candles["open_time"].iat[4]
    assert result.orders.iloc[0]["price_exec"] == 110
