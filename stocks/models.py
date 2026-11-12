"""Modelli condivisi del pacchetto `stocks`.

I Task successivi aggiungono qui, con i campi che producono: Plan, Signal,
Fundamentals, EarningsInfo, Holding, TickerSnapshot, PortfolioSummary, Snapshot.
"""

from enum import StrEnum


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
