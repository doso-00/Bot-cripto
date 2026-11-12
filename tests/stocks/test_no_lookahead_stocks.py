"""Niente look-ahead: il segnale ad `asof` usa solo barre con data ≤ asof."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from stocks.config import StocksSettings
from stocks.models import Action, Holding, Plan, PositionKind
from stocks.signals import evaluate
from tests.stocks.signal_data import (
    ASOF,
    EURUSD,
    core_pullback_closes,
    earnings_in,
    free_ctx,
    good_fundamentals,
    make_prices,
    no_earnings,
    opportunity_closes,
    rising,
)


@pytest.fixture
def s():
    return StocksSettings()


def with_random_future(
    prices: pd.DataFrame, rng: np.random.Generator, n: int = 40
) -> pd.DataFrame:
    """Aggiunge `n` barre casuali dopo l'ultima (prezzi tra 1 e 500, intervalli larghi)."""
    idx = pd.bdate_range(start=prices.index[-1] + timedelta(days=1), periods=n)
    c = rng.uniform(1.0, 500.0, n)
    future = pd.DataFrame(
        {
            "open": c,
            "high": c * rng.uniform(1.0, 1.3, n),
            "low": c * rng.uniform(0.7, 1.0, n),
            "close": c,
            "adj_close": c,
            "volume": rng.uniform(0, 1e6, n),
        },
        index=pd.Index([d.date() for d in idx], name="date"),
    )
    return pd.concat([prices, future])


def _scenarios():
    opp_holding = Holding(
        ticker="XYZ",
        kind=PositionKind.opportunity,
        qty=2,
        avg_price=100.0,
        currency="USD",
    )
    opp_plan = Plan(
        kind=PositionKind.opportunity,
        entry_ref=100.0,
        stop=92.0,
        target=120.0,
        deadline=ASOF + timedelta(days=10),
        currency="USD",
        stop_order_on_tr=True,
    )
    core_holding = Holding(
        ticker="XYZ", kind=PositionKind.core, qty=3, avg_price=100.0, currency="USD"
    )
    return [
        (make_prices(core_pullback_closes()), no_earnings(), None, None),
        (make_prices(opportunity_closes(), spread=3.0), no_earnings(), None, None),
        (make_prices(rising()), no_earnings(), None, None),
        (make_prices(core_pullback_closes()), earnings_in(3), None, None),
        (
            make_prices(rising(240, 90.0, 110.0) + [100.0]),
            no_earnings(),
            core_holding,
            None,
        ),
        (
            make_prices(rising(240, 90.0, 110.0) + [100.0]),
            no_earnings(),
            opp_holding,
            opp_plan,
        ),
    ]


def _eval(prices, e, holding, plan, s):
    return evaluate(
        prices, good_fundamentals(), 0, e, holding, plan, ASOF, free_ctx(), EURUSD, s
    )


def test_future_bars_do_not_change_signal(s):
    rng = np.random.default_rng(42)
    for prices, e, holding, plan in _scenarios():
        expected = _eval(prices, e, holding, plan, s).model_dump()
        for _ in range(5):
            future = with_random_future(prices, rng)
            assert _eval(future, e, holding, plan, s).model_dump() == expected


def test_bars_after_asof_are_ignored(s):
    holding = Holding(
        ticker="XYZ", kind=PositionKind.core, qty=3, avg_price=100.0, currency="USD"
    )
    base = make_prices(rising(240, 90.0, 110.0) + [100.0])

    # un crollo dopo asof farebbe scattare la vendita: ad asof si tiene
    crash = pd.concat(
        [base, make_prices([50.0] * 5, end=ASOF + timedelta(days=7)).iloc[-5:]]
    )
    assert crash.index[-1] > ASOF
    assert _eval(crash, no_earnings(), holding, None, s).action == Action.TIENI

    # dati fermi da 10 giorni: una barra futura non li rende "freschi"
    stale = make_prices(rising(), end=ASOF - timedelta(days=10))
    tomorrow = make_prices([100.0], end=date(2026, 11, 13))
    assert (
        _eval(pd.concat([stale, tomorrow]), no_earnings(), None, None, s).action
        == Action.DATI_MANCANTI
    )
