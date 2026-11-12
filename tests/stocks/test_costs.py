"""Test di commissioni, tasse e pareggio."""

import pytest

from stocks.config import StocksSettings
from stocks.costs import breakeven_price, net_pnl_eur, round_trip_fees


@pytest.fixture
def s():
    return StocksSettings()


def test_round_trip_fees_is_2_eur(s):
    assert round_trip_fees(s) == 2.0


def test_net_pnl_loss_has_no_tax(s):
    assert net_pnl_eur(100.0, 90.0, s) == pytest.approx(-12.0)


def test_net_pnl_gain_taxed_26pct(s):
    assert net_pnl_eur(100.0, 120.0, s) == pytest.approx(13.32)


def test_breakeven_covers_fees(s):
    entry, qty = 50.0, 4.0
    be = breakeven_price(entry, qty, s)
    assert be == pytest.approx(50.5)
    assert net_pnl_eur(entry * qty, be * qty, s) == pytest.approx(0.0)
