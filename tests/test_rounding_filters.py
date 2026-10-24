"""test_rounding_filters (brief §10): quantità arrotondate allo step size, ordini sotto il
notional minimo scartati."""

from decimal import Decimal

import pytest

from bot.backtest.engine import run_backtest
from bot.exchange.rounding import order_rejection_reason, round_qty_down
from tests.backtest_helpers import BTC_LIKE, config, price_candles, targets


@pytest.mark.parametrize(
    "qty, step, expected",
    [
        (0.123456789, "0.00001", "0.12345"),
        (0.3, "0.1", "0.3"),             # con i float 0.3/0.1 = 2.9999999999999996
        (0.00000999, "0.00001", "0"),
        (5.0, "1", "5"),
        (-1.0, "0.01", "0"),
    ],
)
def test_round_qty_down(qty, step, expected):
    assert round_qty_down(qty, Decimal(step)) == Decimal(expected)


def test_rejection_reasons():
    assert order_rejection_reason(Decimal("0.001"), 50_000, BTC_LIKE) is None  # 50 USDT
    assert "notional minimo" in order_rejection_reason(Decimal("0.00009"), 50_000, BTC_LIKE)  # 4.5 USDT
    assert "sotto il minimo" in order_rejection_reason(Decimal("0.000001"), 50_000, BTC_LIKE)
    assert "non multipla" in order_rejection_reason(Decimal("0.000015"), 500_000, BTC_LIKE)
    assert "nulla" in order_rejection_reason(Decimal("0"), 50_000, BTC_LIKE)


def test_engine_quantities_are_multiples_of_step():
    candles = price_candles([43_210.77, 43_210.77, 44_000, 44_000])
    result = run_backtest(candles, targets([1, 0, 0, 0]), config(), BTC_LIKE)
    for qty in result.orders["qty"]:
        assert Decimal(str(qty)) % BTC_LIKE.step_size == 0


def test_engine_discards_order_below_min_notional():
    # Con 4 USDT di capitale l'ordine resta sotto i 5 USDT di notional minimo
    candles = price_candles([50_000] * 4)
    result = run_backtest(candles, targets([1, 1, 1, 1]), config(capital=4.0), BTC_LIKE)
    assert result.trades.empty
    assert (result.orders["status"] == "rifiutato").all()
    assert result.orders["reason"].str.contains("notional minimo").all()
    assert result.final_equity == 4.0
