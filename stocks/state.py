"""Stato persistente del cruscotto: operazioni, piani, revisioni e registro.

Lo stato è un unico documento JSON, passato al codice come file locale.
"""

import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from stocks.models import Action, Holding, Plan, PositionKind

# Tolleranza sulle quantità frazionarie (Trade Republic permette frazioni di azione).
_QTY_EPS = 1e-9


class Trade(BaseModel):
    """Operazione inserita da Riccardo. `currency` è la valuta mostrata da Trade Republic
    (di solito EUR, anche per titoli USA)."""

    ts: datetime
    ticker: str
    side: Literal["buy", "sell"]
    qty: float
    price: float
    currency: str
    fee_eur: float = 1.0
    kind: PositionKind
    note: str = ""


class PlanRevision(BaseModel):
    """Modifica a un campo di un piano, con chi l'ha proposta e se è stata accettata."""

    ts: datetime
    ticker: str
    field: str
    old: str | None
    new: str | None
    source: Literal["engine", "claude", "riccardo"]
    reason: str
    accepted: bool


class JournalEntry(BaseModel):
    """Riga del registro: azione suggerita per un titolo in un dato giorno."""

    ts: datetime
    ticker: str
    action: Action
    close: float | None = None
    reasons: list[str]
    source: Literal["engine", "claude"]
    event_class: str | None = None


class State(BaseModel):
    """Intero stato persistente. `version` serve a migrare il formato in futuro."""

    version: int = 1
    trades: list[Trade] = []
    plans: dict[str, Plan] = {}
    revisions: list[PlanRevision] = []
    journal: list[JournalEntry] = []
    equity_history_eur: list[tuple[date, float]] = []


def load_state(path: Path) -> State:
    """Legge lo stato. File assente, vuoto o solo spazi → stato vuoto.

    JSON non valido solleva un errore (ValueError): non si scartano in silenzio
    le operazioni dell'utente.
    """
    path = Path(path)
    if not path.exists():
        return State()
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return State()
    return State.model_validate_json(text)


def save_state(state: State, path: Path) -> None:
    """Scrive lo stato in modo atomico: file temporaneo nella stessa cartella, poi replace."""
    path = Path(path)
    data = state.model_dump_json(indent=2)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def holdings_from_trades(trades: list[Trade]) -> dict[str, Holding]:
    """Posizioni aperte ricavate dalle operazioni, in ordine cronologico.

    Prezzo medio = media ponderata dei prezzi di acquisto (le commissioni non entrano);
    una vendita parziale riduce la quantità e lascia invariato il prezzo medio.
    Vendita oltre la quantità posseduta, o valuta/tipo diversi per lo stesso titolo → ValueError.
    """
    state: dict[str, tuple[float, float]] = {}  # ticker -> (qty, avg_price)
    meta: dict[str, tuple[str, PositionKind]] = {}
    for t in sorted(trades, key=lambda x: x.ts):
        if t.ticker in meta and meta[t.ticker] != (t.currency, t.kind):
            raise ValueError(
                f"{t.ticker}: le operazioni hanno valuta o tipo di posizione diversi "
                f"({meta[t.ticker][0]}/{meta[t.ticker][1].value} e {t.currency}/{t.kind.value})"
            )
        meta[t.ticker] = (t.currency, t.kind)
        qty, avg = state.get(t.ticker, (0.0, 0.0))
        if t.side == "buy":
            new_qty = qty + t.qty
            avg = (qty * avg + t.qty * t.price) / new_qty
            qty = new_qty
        else:
            if t.qty > qty + _QTY_EPS:
                raise ValueError(f"{t.ticker}: vendita di {t.qty} su {qty} posseduti")
            qty = max(qty - t.qty, 0.0)
        state[t.ticker] = (qty, avg)
    return {
        tk: Holding(
            ticker=tk, kind=meta[tk][1], qty=q, avg_price=a, currency=meta[tk][0]
        )
        for tk, (q, a) in state.items()
        if q > _QTY_EPS
    }
