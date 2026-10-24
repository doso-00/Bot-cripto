"""test_slippage_direction (brief §10): lo slippage peggiora sempre il prezzo."""

import pytest

from bot.backtest.engine import execution_price, run_backtest
from tests.backtest_helpers import LOOSE, config, price_candles, targets


@pytest.mark.parametrize("rate", [0.0001, 0.0005, 0.001, 0.002])
@pytest.mark.parametrize("reference", [0.5, 100.0, 65_000.0])
def test_execution_price_is_always_worse(rate, reference):
    assert execution_price(reference, "buy", rate) > reference
    assert execution_price(reference, "sell", rate) < reference


def test_engine_applies_slippage_against_us_on_both_sides():
    candles = price_candles([100, 100, 100, 100])
    result = run_backtest(candles, targets([1, 0, 0, 0]), config(fee=0.0, slippage=0.001), LOOSE)
    buy, sell = result.orders.iloc[0], result.orders.iloc[1]
    assert buy["price_exec"] == pytest.approx(100.1) and sell["price_exec"] == pytest.approx(99.9)
    # Prezzo fermo: senza commissioni si perde esattamente lo slippage
    assert result.final_equity < 1000
    assert result.trades["slippage_cost"].iat[0] == pytest.approx(1000 - result.final_equity, rel=1e-9)


def test_more_slippage_never_improves_result():
    candles = price_candles([100, 101, 99, 103, 98, 105, 104])
    sig = targets([1, 0, 1, 0, 1, 0, 0])
    finals = [run_backtest(candles, sig, config(slippage=s), LOOSE).final_equity for s in (0, 0.0005, 0.001, 0.002)]
    assert finals == sorted(finals, reverse=True)
