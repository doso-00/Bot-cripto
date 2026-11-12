"""Schema della revisione di Claude (`out/claude_review.json`).

Due volte al giorno una sessione Claude programmata legge le notizie dei titoli e
scrive questo file. Lo schema è rigido (`extra="forbid"`): un campo sconosciuto rende
l'intero file non valido e la pagina esce con i soli segnali del codice.
Il file contiene solo testo e proposte: nessun numero mostrato in pagina viene da qui.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewsItem(_Strict):
    """Una notizia letta da Claude."""

    title: str
    url: str
    source: str
    published: date | None = None


class Proposal(_Strict):
    """Modifica proposta da Claude: il validatore decide se ammetterla.

    `value`: tighten_stop → prezzo nella valuta del titolo (es. "95.5");
    shorten_deadline / extend_deadline → data ISO "YYYY-MM-DD"; gli altri tipi lo ignorano.
    """

    type: Literal[
        "tighten_stop",
        "shorten_deadline",
        "extend_deadline",
        "request_review",
        "downgrade_buy",
        "watch",
    ]
    value: str | None = None
    reason: str
    reason_kind: Literal["evento", "notizia", "tecnico"]


class TickerReview(_Strict):
    """Lettura delle notizie di un titolo, con le eventuali proposte."""

    ticker: str
    event_class: Literal[
        "trimestrale_sopra_attese",
        "trimestrale_sotto_attese",
        "guidance_su",
        "guidance_giu",
        "taglio_dividendo",
        "aumento_dividendo",
        "acquisizione",
        "legale",
        "macro",
        "nessuno",
    ]
    summary: str
    explanation: str
    news: list[NewsItem] = []
    proposals: list[Proposal] = []


class GlossaryItem(_Strict):
    """Voce del glossario mostrata in pagina."""

    term: str
    definition: str


class ClaudeReview(_Strict):
    """Intero file `claude_review.json`."""

    generated_at: datetime
    tickers: list[TickerReview]
    glossary: list[GlossaryItem] = []
    market_note: str = ""
