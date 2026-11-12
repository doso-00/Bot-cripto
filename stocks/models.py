"""Modelli condivisi del pacchetto `stocks`.

I Task successivi aggiungono qui, con i campi che producono: Plan, Signal,
Fundamentals, EarningsInfo, Holding, TickerSnapshot, PortfolioSummary, Snapshot.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

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


class EarningsInfo(BaseModel):
    """Situazione trimestrali di un titolo (Task 5).

    Unità: `past_moves_pct`, `avg_abs_move_pct`, `expected_move_pct` sono frazioni
    (0.09 = 9%) nonostante il suffisso; `last_surprise_pct` è in percentuale come
    la dà Yahoo (5.2 = +5,2%). `past_moves_pct` è dal più recente al più vecchio.
    `expected_move_pct` lo calcola `stocks.earnings.expected_move_pct` (serve la
    rete): `earnings_info` lo lascia None e lo imposta il chiamante.
    """

    next_ts: datetime | None = None
    days_to_next: int | None = None
    reported_since_last_run: bool = False
    last_ts: datetime | None = None
    last_surprise_pct: float | None = None
    past_moves_pct: list[float] = []
    avg_abs_move_pct: float | None = None
    expected_move_pct: float | None = None


class Plan(BaseModel):
    """Piano di uscita scritto prima dell'acquisto (Task 6).

    `entry_ref`, `stop`, `target`, `review_level`, `exit_level` sono nella valuta del
    titolo (`currency`); `stop_eur` e `target_eur` sono gli stessi livelli in euro,
    arrotondati per difetto (stop) e per eccesso (obiettivo) al centesimo.
    Base (core): solo livelli di revisione e di uscita, da controllare sulla pagina.
    Occasione/evento: stop, obiettivo e data limite; `stop_order_on_tr` = True indica
    che lo stop va inserito come ordine stop-loss su Trade Republic.
    """

    kind: PositionKind
    entry_ref: float
    stop: float | None = None
    target: float | None = None
    deadline: date | None = None
    review_level: float | None = None
    exit_level: float | None = None
    currency: str
    stop_eur: float | None = None
    target_eur: float | None = None
    scenarios: list[str] = []
    stop_order_on_tr: bool = False


class Signal(BaseModel):
    """Azione suggerita per un titolo, con motivi in italiano e piano di uscita."""

    action: Action
    kind: PositionKind | None = None
    amount_eur: float | None = None
    plan: Plan | None = None
    reasons: list[str] = []
    # In Fase A nessuna regola è validata su dati storici: resta sempre False.
    validated: bool = False
    blocked_by: str | None = None


class SizingContext(BaseModel):
    """Situazione del portafoglio che serve per dimensionare un nuovo acquisto."""

    holdings_eur: dict[PositionKind, float]
    open_opps: int
    halted: bool


class TickerSnapshot(BaseModel):
    """Situazione di un titolo seguito a fine run (Task 8).

    `asof` è la data dell'ultima barra chiusa (se non ci sono prezzi: la data di oggi
    nel fuso della borsa). Prezzi e indicatori nella valuta di quotazione `currency`;
    `close_eur` e `pnl_net_eur` in euro. `change_1d_pct` e `drawdown_63d_pct` sono
    frazioni (0.012 = +1,2%; 0.08 = 8% sotto il massimo a 63 giorni).
    `proposed_by` è None per un titolo seguito solo perché è in portafoglio.
    Valori None = non calcolabili (dati assenti o storia troppo corta).
    """

    ticker: str
    name: str
    currency: str | None
    proposed_by: Literal["riccardo", "claude"] | None
    asof: date
    close: float | None = None
    close_eur: float | None = None
    change_1d_pct: float | None = None
    sma200: float | None = None
    atr14: float | None = None
    rsi14: float | None = None
    drawdown_63d_pct: float | None = None
    high_63d: float | None = None
    fundamentals: Fundamentals
    dividend_years_without_cut: int = 0
    earnings: EarningsInfo
    holding: Holding | None = None
    pnl_net_eur: float | None = None
    signal: Signal
    data_ok: bool
    data_issues: list[str] = []


class PortfolioSummary(BaseModel):
    """Riepilogo del portafoglio in euro.

    `invested_eur` = costo delle posizioni aperte; `value_eur` = valore di mercato delle
    posizioni con un prezzo disponibile; `pnl_net_eur` = somma degli utili netti delle
    singole posizioni (commissioni di acquisto e vendita e tasse stimate incluse);
    `cash_eur` = budget − investito; `peak_value_eur` = massimo storico del valore totale
    (liquidità + posizioni); `halted` = nuovi acquisti bloccati dal calo dal massimo.
    """

    invested_eur: float
    value_eur: float
    pnl_net_eur: float
    cash_eur: float
    halted: bool
    peak_value_eur: float


class Snapshot(BaseModel):
    """Risultato di un run di `stocks.refresh`, scritto in `out/snapshot.json`.

    `benchmarks`: ticker dell'indice -> variazione dell'ultimo giorno come frazione
    (0.004 = +0,4%). `issues`: problemi da mostrare in alto nella pagina.
    """

    generated_at: datetime
    run_slot: Literal["mattina", "sera", "manuale"]
    eurusd: float
    benchmarks: dict[str, float]
    tickers: list[TickerSnapshot]
    portfolio: PortfolioSummary
    issues: list[str] = []
