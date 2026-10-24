"""test_buy_and_hold_matches_manual (brief §10): il buy & hold del motore coincide con il
calcolo manuale entro lo 0,01% (criterio di accettazione della Fase 2)."""

from datetime import datetime

import pytest

from bot.backtest.engine import run_backtest
from bot.backtest.verify import formula_buy_and_hold, manual_buy_and_hold
from bot.strategies import STRATEGIES, compute_targets
from tests.backtest_helpers import BTC_LIKE, LOOSE, config
from tests.conftest import candles as synthetic_candles

TOLERANCE = 0.0001  # 0,01%


def bh_result(df, cfg, filters):
    spec = STRATEGIES["buy_and_hold"]
    return run_backtest(df, compute_targets(spec, df, spec.params_model()), cfg, filters)


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("fee, slippage", [(0.001, 0.0005), (0.0, 0.0), (0.00075, 0.002)])
def test_engine_matches_manual_with_real_filters(seed, fee, slippage):
    df = synthetic_candles(datetime(2020, 1, 1), 2000, seed=seed)
    df[["open", "high", "low", "close"]] *= 400  # prezzi nell'ordine di BTC
    cfg = config(fee=fee, slippage=slippage)
    engine = bh_result(df, cfg, BTC_LIKE).final_equity
    manual = manual_buy_and_hold(df, cfg, BTC_LIKE)
    assert abs(engine / manual - 1) < TOLERANCE


@pytest.mark.parametrize("seed", [0, 3])
def test_engine_matches_textbook_formula_without_rounding(seed):
    # "Prezzo finale / iniziale meno i costi", con filtri che non arrotondano nulla
    df = synthetic_candles(datetime(2021, 6, 1), 1500, seed=seed)
    cfg = config(fee=0.001, slippage=0.0005)
    engine = bh_result(df, cfg, LOOSE).final_equity
    assert abs(engine / formula_buy_and_hold(df, cfg) - 1) < TOLERANCE


def test_buy_and_hold_makes_exactly_one_round_trip():
    df = synthetic_candles(datetime(2020, 1, 1), 500, seed=4)
    result = bh_result(df, config(), LOOSE)
    assert list(result.orders["side"]) == ["buy", "sell"]
    assert result.trades["exit_reason"].iat[0] == "fine periodo"
