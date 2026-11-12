"""Dati sintetici per i test del motore dei segnali (nessuna rete)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from stocks.models import EarningsInfo, Fundamentals, SizingContext

ASOF = date(2026, 11, 12)  # giovedì
EURUSD = 1.17


def make_prices(closes, end: date = ASOF, spread: float = 1.0) -> pd.DataFrame:
    """Barre giornaliere (giorni feriali) che finiscono a `end`; high/low = close ± spread."""
    idx = pd.bdate_range(end=end, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": c,
            "high": c + spread,
            "low": c - spread,
            "close": c,
            "adj_close": c,
            "volume": 1000.0,
        },
        index=pd.Index([d.date() for d in idx], name="date"),
    )


def rising(n: int = 240, start: float = 50.0, stop: float = 100.0) -> list[float]:
    """Salita regolare: prezzo sopra la SMA200 e all'ultimo massimo."""
    return list(np.linspace(start, stop, n))


def core_pullback_closes() -> list[float]:
    """Salita fino a 100 poi ritracciamento a 92 (−8% dal massimo a 3 mesi)."""
    return rising() + [98.0, 96.0, 94.0, 92.0]


def opportunity_closes() -> list[float]:
    """Salita fino a 100 poi dieci cali da 2 fino a 80 (−20%, RSI molto basso)."""
    return rising() + [100.0 - 2 * k for k in range(1, 11)]


def rr_rejected_closes() -> list[float]:
    """Salita fino a 100 poi cinque cali da 2,2 fino a 89 (−11%, RSI basso)."""
    return rising() + [100.0 - 2.2 * k for k in range(1, 6)]


def good_fundamentals(currency: str | None = "USD") -> Fundamentals:
    return Fundamentals(
        currency=currency,
        trailing_pe=25.0,
        roe=0.20,
        debt_to_equity=40.0,
        payout_ratio=0.30,
    )


def bad_fundamentals(currency: str = "USD") -> Fundamentals:
    """Azienda in perdita: non supera il filtro di qualità."""
    return Fundamentals(currency=currency, trailing_pe=-5.0, roe=0.20)


def no_earnings() -> EarningsInfo:
    return EarningsInfo()


def earnings_in(
    days: int, expected: float | None = 0.0951, avg: float | None = 0.0402
) -> EarningsInfo:
    """Trimestrale tra `days` giorni (anche ≤ 0: data passata, utile non ancora pubblicato)."""
    return EarningsInfo(
        next_ts=datetime(2026, 11, 17, 16, 0, tzinfo=ZoneInfo("America/New_York")),
        days_to_next=days,
        past_moves_pct=[0.0874, -0.0177] if avg is not None else [],
        avg_abs_move_pct=avg,
        expected_move_pct=expected,
    )


def free_ctx() -> SizingContext:
    return SizingContext(holdings_eur={}, open_opps=0, halted=False)
