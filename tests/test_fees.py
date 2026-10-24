"""test_fees (brief §10): commissioni corrette su acquisto e vendita."""

import pytest

from bot.backtest.engine import run_backtest
from tests.backtest_helpers import LOOSE, config, price_candles, targets


def test_fees_on_buy_and_sell():
    # Compra all'open della candela 1 (100), vende all'open della candela 3 (120)
    candles = price_candles([100, 100, 110, 120, 120])
    result = run_backtest(candles, targets([1, 1, 0, 0, 0]), config(fee=0.001, slippage=0.0), LOOSE)

    buy, sell = result.orders.iloc[0], result.orders.iloc[1]
    qty = 1000 / 1.001 / 100
    assert buy["side"] == "buy" and buy["qty"] == pytest.approx(qty, rel=1e-9)
    assert buy["fee"] == pytest.approx(qty * 100 * 0.001, rel=1e-9)
    assert sell["side"] == "sell" and sell["fee"] == pytest.approx(qty * 120 * 0.001, rel=1e-9)

    expected_final = qty * 120 * (1 - 0.001)
    assert result.final_equity == pytest.approx(expected_final, rel=1e-9)
    trade = result.trades.iloc[0]
    assert trade["fees"] == pytest.approx(buy["fee"] + sell["fee"], rel=1e-9)
    assert trade["net_pnl"] == pytest.approx(expected_final - 1000, rel=1e-9)


def test_zero_fee_and_slippage_reproduces_price_return():
    candles = price_candles([100, 100, 150, 150])
    result = run_backtest(candles, targets([1, 0, 0, 0]), config(fee=0.0, slippage=0.0), LOOSE)
    assert result.final_equity == pytest.approx(1500, rel=1e-9)


def test_net_pnl_equals_gross_minus_costs():
    candles = price_candles([100, 100, 90, 130, 130])
    result = run_backtest(candles, targets([1, 1, 0, 0, 0]), config(fee=0.001, slippage=0.002), LOOSE)
    t = result.trades.iloc[0]
    assert t["net_pnl"] == pytest.approx(t["gross_pnl"] - t["fees"] - t["slippage_cost"], rel=1e-12)
    assert t["net_pnl"] == pytest.approx(result.final_equity - 1000, rel=1e-9)
