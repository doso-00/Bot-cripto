"""Test dei limiti di rischio."""

import pytest

from stocks.config import StocksSettings
from stocks.models import PositionKind
from stocks.risk import max_order_eur, portfolio_halted

K = PositionKind


@pytest.fixture
def s():
    return StocksSettings()


def test_core_capped_at_30pct(s):
    assert max_order_eur(K.core, {}, 0, s) == (300.0, None)


def test_opportunity_blocked_when_two_open(s):
    amount, reason = max_order_eur(K.opportunity, {}, 2, s)
    assert amount == 0.0
    assert reason is not None


def test_event_capped_at_100(s):
    assert max_order_eur(K.event, {}, 0, s) == (100.0, None)


def test_min_order_wins(s):
    # budget occasioni residuo = 200 - 140 = 60 -> sotto il minimo
    amount, reason = max_order_eur(K.opportunity, {K.opportunity: 100.0, K.event: 40.0}, 1, s)
    assert (amount, reason) == (0.0, "importo sotto il minimo di 100 €")


def test_portfolio_halt_at_15pct(s):
    assert portfolio_halted([1000.0, 850.0], s) is True
    assert portfolio_halted([1000.0, 860.0], s) is False
