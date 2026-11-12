"""Modelli condivisi del pacchetto `stocks`.

I Task successivi aggiungono qui, con i campi che producono: Plan, Signal,
Fundamentals, EarningsInfo, Holding, TickerSnapshot, PortfolioSummary, Snapshot.
"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel


class Action(StrEnum):
    """Azione suggerita per un titolo (testo mostrato in pagina)."""

    COMPRA = "COMPRA"
    ACCUMULA = "ACCUMULA"
    TIENI = "TIENI"
    ATTENDI = "ATTENDI"
    RIVEDI = "RIVEDI"
    RIDUCI = "RIDUCI"
    VENDI = "VENDI"
    NESSUNA_AZIONE = "NESSUNA_AZIONE"
    DATI_MANCANTI = "DATI_MANCANTI"


class PositionKind(StrEnum):
    """Tipo di posizione: base (core), occasione di breve, evento (trimestrale)."""

    core = "core"
    opportunity = "opportunity"
    event = "event"


class Holding(BaseModel):
    """Posizione in portafoglio inserita da Riccardo (portfolio.yaml)."""

    ticker: str
    kind: PositionKind
    qty: float
    avg_price: float
    currency: str


class Fundamentals(BaseModel):
    """Fondamentali di un titolo da Yahoo; i campi non disponibili sono None.

    Unità (come le restituisce yfinance): `dividend_yield_pct` è già in percentuale
    (0.58 = 0,58%); `payout_ratio` e `roe` sono frazioni (0.35 = 35%);
    `debt_to_equity` è in percento (16.97 = 0,17x).
    """

    name: str | None = None
    currency: str | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    dividend_yield_pct: float | None = None
    payout_ratio: float | None = None
    debt_to_equity: float | None = None
    roe: float | None = None
    market_cap: float | None = None
    ex_dividend_date: date | None = None
