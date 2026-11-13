"""Pagina HTML della dashboard: `uv run python -m stocks.render` → `out/dashboard.html`.

Legge la vista finale (`out/final.json`, scritta da `stocks.apply_review`) e lo stato, e
riempie il modello `stocks/templates/dashboard.html` (segnaposto `string.Template`).
Il file prodotto è il contenuto di un Artifact: il guscio doctype/html/head/body lo
aggiunge la pubblicazione, quindi qui non c'è.

Regole della pagina:
- ogni testo passa da `html.escape` (soprattutto quello scritto da Claude: sintesi,
  spiegazioni, motivi delle proposte, glossario, nota di mercato) e nessun link di
  notizia viene reso se non inizia con `https://`;
- ogni numero porta l'etichetta `misurato` (calcolato dal codice sui dati) o `stima`
  (dipende da un'ipotesi: tasse, cambio di oggi, livelli convertiti in euro);
- formati italiani (virgola decimale, "€" dopo il numero), orari nel fuso Europe/Rome;
- l'età dell'aggiornamento ("aggiornata 3 ore fa") e l'avviso "dati non aggiornati" li
  calcola uno script nel browser dall'attributo `data-generated-at` (ruling R21): la pagina
  pubblicata resta la stessa per ore, un'età calcolata qui sarebbe falsa. Senza JavaScript
  resta la data/ora assoluta.
"""

import argparse
import html
import logging
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from string import Template
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from stocks.apply_review import FINAL_FILE, FinalView
from stocks.config import StocksSettings, load_settings
from stocks.data import to_eur
from stocks.models import Action, Plan, PositionKind, TickerSnapshot
from stocks.review import TickerReview
from stocks.state import PlanRevision, State, load_state

logger = logging.getLogger("stocks.render")

PAGE_TZ = ZoneInfo("Europe/Rome")
TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
OUTPUT_FILE = "dashboard.html"

# Oltre questa età lo snapshot è vecchio: un aggiornamento programmato è saltato.
STALE_AFTER = timedelta(hours=18)
# Trimestrali con data passata da al massimo tanti giorni restano in calendario
# (risultati non ancora pubblicati), come nel motore regole.
PAST_EARNINGS_DAYS = 3
MAX_REVISIONS_SHOWN = 20
NA = "n.d."

DISCLAIMER = "Strumento informativo, non consulenza finanziaria. Gli ordini li decidi e li esegui tu."
NOT_VALIDATED = "regola non ancora validata"
MODEL_TEXT = (
    "Il modello di machine learning arriva a dicembre: prima verrà misurato sui dati "
    "storici e mostrato solo se batte un riferimento semplice."
)
TRADE_NOTE = (
    "Per registrare un acquisto o una vendita scrivi a Claude: titolo, quantità, "
    "prezzo in euro, data (procedura in docs/REGISTRARE_OPERAZIONI.md)."
)
FRACTIONAL_STOP_NOTE = (
    "Verifica sull'app che Trade Republic accetti l'ordine stop-loss per la quantità che "
    "compri (anche frazioni di azione); se non lo accetta, controlla tu il livello qui."
)
CLAUDE_PROPOSED = "proposto da Claude, da confermare"

_WEEKDAYS = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
_BUY = (Action.COMPRA, Action.ACCUMULA)
_QUIET = (Action.NESSUNA_AZIONE, Action.TIENI)
# Ordine in "Cosa fare": prima ciò che chiede un'azione.
_PRIORITY = {
    Action.VENDI: 0,
    Action.RIDUCI: 1,
    Action.RIVEDI: 2,
    Action.COMPRA: 3,
    Action.ACCUMULA: 4,
    Action.DATI_MANCANTI: 5,
    Action.ATTENDI: 6,
    Action.TIENI: 7,
    Action.NESSUNA_AZIONE: 8,
}
_ACTION_TONE = {
    Action.COMPRA: "good",
    Action.ACCUMULA: "good",
    Action.VENDI: "bad",
    Action.RIDUCI: "bad",
    Action.DATI_MANCANTI: "bad",
    Action.RIVEDI: "warn",
    Action.ATTENDI: "info",
    Action.TIENI: "",
    Action.NESSUNA_AZIONE: "",
}
_KIND_LABEL = {
    PositionKind.core: "base",
    PositionKind.opportunity: "occasione",
    PositionKind.event: "evento",
}
_SLOT_LABEL = {
    "mattina": "aggiornamento del mattino",
    "sera": "aggiornamento della sera",
    "manuale": "aggiornamento manuale",
}
_BENCHMARK_NAMES = {"^GSPC": "S&P 500", "^STOXX50E": "Euro Stoxx 50"}
_EVENT_LABEL = {
    "trimestrale_sopra_attese": ("trimestrale sopra le attese", "good"),
    "trimestrale_sotto_attese": ("trimestrale sotto le attese", "bad"),
    "guidance_su": ("previsioni alzate", "good"),
    "guidance_giu": ("previsioni abbassate", "bad"),
    "taglio_dividendo": ("taglio del dividendo", "bad"),
    "aumento_dividendo": ("aumento del dividendo", "good"),
    "acquisizione": ("acquisizione", "info"),
    "legale": ("questione legale", "warn"),
    "macro": ("macroeconomia", "info"),
    "nessuno": ("nessun evento", ""),
}
_FIELD_LABEL = {
    "stop": "stop",
    "deadline": "data limite",
    "action": "azione",
    "watch": "da guardare",
    "plan": "piano",
}
_SOURCE_LABEL = {"engine": "motore", "claude": "Claude", "riccardo": "Riccardo"}
_BASE_GLOSSARY = (
    (
        "Stop-loss",
        (
            "Ordine che vende da solo se il prezzo scende fino al livello indicato. Limita la "
            "perdita, ma in un forte ribasso la vendita può avvenire a un prezzo più basso."
        ),
    ),
    (
        "Trimestrale",
        (
            "Pubblicazione dei risultati degli ultimi tre mesi. Può muovere molto il prezzo "
            "in una sola notte."
        ),
    ),
    (
        "Stacco del dividendo",
        (
            "Dal giorno dello stacco chi compra non riceve il prossimo dividendo; quel giorno "
            "il prezzo di solito scende di circa l'importo del dividendo."
        ),
    ),
    (
        "Media a 200 giorni",
        (
            "Media dei prezzi di chiusura degli ultimi 200 giorni di borsa. Un prezzo sopra la "
            "media indica una tendenza di lungo periodo positiva."
        ),
    ),
    (
        "Ritracciamento",
        "Discesa temporanea dal massimo recente; qui si misura sugli ultimi 3 mesi.",
    ),
    (
        "Misurato e stima",
        (
            "Misurato: calcolato dal codice sui dati di mercato. Stima: dipende da "
            "un'ipotesi, come le tasse future o il cambio euro/dollaro di oggi."
        ),
    ),
)


# --- Formati -----------------------------------------------------------------


def e(value) -> str:
    """Testo pronto per l'HTML (anche dentro gli attributi)."""
    return html.escape("" if value is None else str(value), quote=True)


def _finite(x) -> bool:
    return x is not None and math.isfinite(x)


def fmt_num(x: float, decimals: int = 2) -> str:
    """1234.5 → "1.234,50" (separatori italiani)."""
    text = f"{x:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _signed(x: float, body: str) -> str:
    if x > 0:
        return f"+{body}"
    if x < 0:
        return f"−{body}"
    return body


def fmt_eur(x: float | None, signed: bool = False) -> str:
    if not _finite(x):
        return NA
    body = f"{fmt_num(abs(x) if signed else x)} €"
    return _signed(x, body) if signed else body


def fmt_money(x: float | None, currency: str | None) -> str:
    if not _finite(x) or currency is None:
        return NA
    return fmt_eur(x) if currency == "EUR" else f"{fmt_num(x)} {currency}"


def fmt_pct(frac: float | None, decimals: int = 1, signed: bool = True) -> str:
    """Frazione → percentuale: 0.12 → "+12,0%"."""
    if not _finite(frac):
        return NA
    body = f"{fmt_num(abs(frac) * 100, decimals)}%"
    return _signed(frac, body) if signed else body


def fmt_qty(qty: float) -> str:
    text = f"{qty:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _rome(ts: datetime) -> datetime:
    # Un orario senza fuso si considera già ora italiana (come in apply_review).
    return ts.astimezone(PAGE_TZ) if ts.tzinfo else ts.replace(tzinfo=PAGE_TZ)


def fmt_date(d: date) -> str:
    return f"{_WEEKDAYS[d.weekday()]} {d:%d/%m/%Y}"


def fmt_dt(ts: datetime) -> str:
    local = _rome(ts)
    return f"{fmt_date(local.date())} {local:%H:%M}"


# --- Pezzi di HTML -----------------------------------------------------------


def _val(text: str, label: str | None, css: str = "") -> str:
    """Numero con l'etichetta misurato/stima (nessuna etichetta se il dato manca)."""
    cls = f"n {css}".strip()
    # Lo spazio prima dell'etichetta permette di andare a capo nelle colonne strette.
    tag = f' <span class="tag">{e(label)}</span>' if label and text != NA else ""
    return f'<span class="{cls}">{e(text)}</span>{tag}'


def _change(frac: float | None, label: str = "misurato") -> str:
    css = (
        "up"
        if _finite(frac) and frac > 0
        else "down"
        if _finite(frac) and frac < 0
        else ""
    )
    return _val(fmt_pct(frac), label, css)


def _chip(text: str, tone: str = "", soft: bool = False) -> str:
    cls = " ".join(c for c in ("chip", tone, "soft" if soft else "") if c)
    return f'<span class="{cls}">{e(text)}</span>'


def _action_chip(action: Action) -> str:
    return _chip(action.value.replace("_", " "), _ACTION_TONE[action])


def _kv(items: list[tuple[str, str]]) -> str:
    rows = "".join(f"<div><dt>{e(k)}</dt><dd>{v}</dd></div>" for k, v in items)
    return f'<dl class="kv">{rows}</dl>'


def _ul(texts: list[str], css: str = "") -> str:
    if not texts:
        return ""
    attr = f' class="{css}"' if css else ""
    return f"<ul{attr}>" + "".join(f"<li>{e(t)}</li>" for t in texts) + "</ul>"


def _level(value: float | None, value_eur: float | None, currency: str | None) -> str:
    """Livello del piano nella valuta del titolo, più l'equivalente in euro se serve."""
    text = _val(fmt_money(value, currency), "misurato")
    if currency != "EUR" and _finite(value_eur):
        text += f' <span class="muted">≈ </span>{_val(fmt_eur(value_eur), "stima")}'
    return text


def _to_eur(value: float | None, currency: str | None, eurusd: float) -> float | None:
    if not _finite(value) or currency is None:
        return None
    try:
        return to_eur(value, currency, eurusd)
    except ValueError:
        return None


# --- Calendario ----------------------------------------------------------------


@dataclass
class _CalendarEntry:
    when: date
    sort_key: datetime
    when_text: str
    ticker: str
    what: str


def _calendar(view: FinalView, today: date) -> list[_CalendarEntry]:
    """Prossime trimestrali e stacchi del dividendo dei titoli seguiti, per data."""
    entries: list[_CalendarEntry] = []
    for t in view.snapshot.tickers:
        nxt = t.earnings.next_ts
        if nxt is not None:
            local = _rome(nxt)
            if local.date() >= today - timedelta(days=PAST_EARNINGS_DAYS):
                # Mezzanotte nel fuso della borsa = Yahoo non dà l'ora.
                has_time = nxt.tzinfo is not None and (nxt.hour, nxt.minute) != (0, 0)
                when_text = fmt_date(local.date())
                if has_time:
                    when_text += f" ~{local:%H:%M}"
                what = "Trimestrale"
                if local.date() < today:
                    what += ": data passata, risultati non ancora pubblicati"
                entries.append(
                    _CalendarEntry(local.date(), local, when_text, t.ticker, what)
                )
        ex_div = t.fundamentals.ex_dividend_date
        if ex_div is not None and ex_div >= today:
            entries.append(
                _CalendarEntry(
                    ex_div,
                    datetime.combine(ex_div, datetime.min.time(), PAGE_TZ),
                    fmt_date(ex_div),
                    t.ticker,
                    "Stacco dividendo: per riceverlo il titolo va comprato prima di questa data",
                )
            )
    return sorted(entries, key=lambda c: (c.sort_key, c.ticker))


def _calendar_html(entries: list[_CalendarEntry]) -> str:
    if not entries:
        return '<p class="empty">Nessuna trimestrale o stacco del dividendo noto per i titoli seguiti.</p>'
    rows = "".join(
        f'<li><span class="when">{e(c.when_text)}</span>'
        f'<span class="what"><b>{e(c.ticker)}</b> · {e(c.what)}</span></li>'
        for c in entries
    )
    return (
        f'<ul class="cal">{rows}</ul>'
        '<p class="small muted">Orari in ora italiana, indicativi: li comunica l\'azienda e '
        "possono cambiare.</p>"
    )


# --- Intestazione e avvisi ---------------------------------------------------


def _header(view: FinalView, next_event: _CalendarEntry | None) -> str:
    snap = view.snapshot
    data_ok = not snap.issues and all(t.data_ok for t in snap.tickers)
    status_chip = (
        _chip("dati OK", "good") if data_ok else _chip("dati incompleti", "warn")
    )
    # L'età la scrive lo script della pagina nello span vuoto (R21).
    generated = _rome(snap.generated_at).isoformat(timespec="seconds")
    stale_hours = int(STALE_AFTER.total_seconds() // 3600)
    status = (
        f'<p class="when"><strong>{e(fmt_dt(snap.generated_at))}</strong> (ora italiana)'
        f'<span id="age" data-generated-at="{e(generated)}" '
        f'data-stale-after-hours="{stale_hours}"></span></p>'
        f'<p class="status">{status_chip}'
        f"{_chip(_SLOT_LABEL.get(snap.run_slot, snap.run_slot), soft=True)}</p>"
    )
    if next_event is not None:
        nxt = (
            f'<p class="next">Prossimo evento: <b>{e(next_event.ticker)}</b> · '
            f"{e(next_event.what.split(':')[0].lower())} "
            f'<span class="n">{e(next_event.when_text)}</span></p>'
        )
    else:
        nxt = '<p class="next muted">Prossimo evento: nessuno in calendario.</p>'
    facts = [
        f"<span>{e(_BENCHMARK_NAMES.get(sym, sym))} oggi {_change(chg)}</span>"
        for sym, chg in snap.benchmarks.items()
    ]
    if _finite(snap.eurusd):
        facts.append(
            f"<span>Cambio 1 € = {_val(fmt_num(snap.eurusd, 4) + ' USD', 'misurato')}</span>"
        )
    return status + nxt + f'<p class="facts">{"".join(facts)}</p>'


def _banner(tone: str, title: str, body: str = "") -> str:
    return f'<div class="banner {tone}" role="status"><strong>{e(title)}</strong>{body}</div>'


def _banners(view: FinalView, now: datetime, s: StocksSettings) -> str:
    snap = view.snapshot
    out: list[str] = []
    if _rome(now) - _rome(snap.generated_at) > STALE_AFTER:
        # Vista già vecchia quando la pagina viene scritta; `data-stale` evita che lo
        # script ne aggiunga un secondo.
        out.append(
            _banner(
                "bad",
                "Dati non aggiornati",
                f"<p>L'ultimo aggiornamento è di {e(fmt_dt(snap.generated_at))}: prezzi e "
                "segnali potrebbero non valere più. Non decidere su questi numeri.</p>",
            ).replace('class="banner bad"', 'class="banner bad" data-stale', 1)
        )
    missing = [t.ticker for t in snap.tickers if not t.data_ok]
    if missing:
        out.append(
            _banner(
                "bad",
                f"Dati mancanti per {', '.join(missing)}",
                "<p>Per questi titoli nessun acquisto finché i dati non tornano.</p>",
            )
        )
    if snap.issues:
        out.append(
            _banner(
                "warn", "Problemi nei dati di questo aggiornamento", _ul(snap.issues)
            )
        )
    if snap.portfolio.halted:
        out.append(
            _banner(
                "bad",
                "Nuovi acquisti bloccati",
                f"<p>Il portafoglio è sceso del {e(fmt_pct(s.portfolio_halt_drawdown, 0, False))} "
                f"dal suo massimo ({e(fmt_eur(snap.portfolio.peak_value_eur))}): rivedi le "
                "posizioni prima di comprare altro.</p>",
            )
        )
    if not view.review_available:
        out.append(
            _banner(
                "warn",
                "Revisione notizie non disponibile",
                "<p>La pagina mostra solo i segnali calcolati dal codice: in questo "
                "aggiornamento nessuna notizia è stata letta.</p>",
            )
        )
    if view.market_note.strip():
        out.append(
            _banner(
                "info",
                "Nota di mercato (scritta da Claude)",
                f"<p>{e(view.market_note)}</p>",
            )
        )
    return "".join(out)


# --- Schede dei titoli -------------------------------------------------------


def _plan_html(plan: Plan, t: TickerSnapshot, eurusd: float, title: str) -> str:
    parts = [f"<h3>{e(title)}</h3>"]
    stop_kind = plan.kind in (PositionKind.opportunity, PositionKind.event)
    if stop_kind and _finite(plan.stop_eur):
        parts.append(
            '<p class="hint">Inserisci su Trade Republic un ordine stop-loss a '
            f"{e(fmt_eur(plan.stop_eur))}</p>"
        )
    items: list[tuple[str, str]] = []
    if plan.stop is not None:
        items.append(("Stop", _level(plan.stop, plan.stop_eur, plan.currency)))
    if plan.target is not None:
        items.append(("Obiettivo", _level(plan.target, plan.target_eur, plan.currency)))
    if plan.deadline is not None:
        items.append(
            ("Data limite", f'<span class="n">{e(fmt_date(plan.deadline))}</span>')
        )
    if plan.review_level is not None:
        items.append(
            (
                "Livello di revisione",
                _level(
                    plan.review_level,
                    _to_eur(plan.review_level, plan.currency, eurusd),
                    plan.currency,
                ),
            )
        )
    if plan.exit_level is not None:
        items.append(
            (
                "Livello di uscita",
                _level(
                    plan.exit_level,
                    _to_eur(plan.exit_level, plan.currency, eurusd),
                    plan.currency,
                ),
            )
        )
    if items:
        parts.append(_kv(items))
    if plan.kind == PositionKind.core and plan.review_level is not None:
        parts.append(
            '<p class="small muted">Posizione base: questi livelli non sono ordini da inserire, '
            "si controllano su questa pagina.</p>"
        )
    parts.append(_ul(plan.scenarios))
    if stop_kind and _finite(plan.stop_eur):
        parts.append(f'<p class="small muted">{e(FRACTIONAL_STOP_NOTE)}</p>')
    return f'<div class="plan">{"".join(parts)}</div>'


def _spread(frac: float | None) -> str:
    """Ampiezza di un movimento in entrambe le direzioni: 0.04 → "±4,0%"."""
    return f"±{fmt_pct(frac, signed=False)}" if _finite(frac) else NA


def _earnings_html(t: TickerSnapshot) -> str:
    """Movimenti misurati attorno alla trimestrale (solo per i segnali di tipo evento)."""
    en = t.earnings
    # La data è già nei motivi, nell'intestazione e nel calendario.
    rows: list[tuple[str, str]] = []
    rows.append(
        (
            "Movimento atteso dalle opzioni",
            _val(_spread(en.expected_move_pct), "misurato"),
        )
    )
    rows.append(
        (
            "Movimento medio dopo le trimestrali",
            _val(_spread(en.avg_abs_move_pct), "misurato"),
        )
    )
    html_parts = [_kv(rows)]
    if en.past_moves_pct:
        moves = "".join(f"<span>{_change(m, '')}</span>" for m in en.past_moves_pct)
        html_parts.append(
            f'<p class="small muted">Giorno dopo le ultime {len(en.past_moves_pct)} trimestrali, '
            'dalla più recente <span class="tag">misurato</span></p>'
            f'<p class="moves">{moves}</p>'
        )
    return "".join(html_parts)


def _ticker_facts(t: TickerSnapshot) -> str:
    items = [
        ("Prezzo", _val(fmt_money(t.close, t.currency), "misurato")),
        ("Ultimo giorno", _change(t.change_1d_pct)),
        (
            "Dal massimo a 3 mesi",
            _change(-t.drawdown_63d_pct if _finite(t.drawdown_63d_pct) else None),
        ),
    ]
    if t.currency not in ("EUR", None):
        items.insert(1, ("In euro", _val(fmt_eur(t.close_eur), "stima")))
    return _kv(items)


def _meta(t: TickerSnapshot, view: FinalView) -> str:
    chips = []
    if t.holding is not None:
        chips.append(_chip("in portafoglio", "info", soft=True))
    if t.ticker in view.watched:
        chips.append(_chip("da guardare", "warn", soft=True))
    if t.proposed_by == "claude":
        chips.append(_chip(CLAUDE_PROPOSED, soft=True))
    return f'<div class="meta">{"".join(chips)}</div>' if chips else ""


def _issues(t: TickerSnapshot) -> str:
    if not t.data_issues:
        return ""
    return f'<div class="issues">Note sui dati {_ul(t.data_issues)}</div>'


def _review_line(review: TickerReview | None) -> str:
    if review is None or review.event_class == "nessuno":
        return ""
    return (
        f'<p class="small"><b>Notizie:</b> {e(review.summary)} '
        '<a href="#notizie">Spiegazione ›</a></p>'
    )


def _signal_body(t: TickerSnapshot, view: FinalView, eurusd: float) -> list[str]:
    """Contenuto comune a schede ed elenco compatto: blocco, importo, motivi, piano."""
    sig = t.signal
    parts: list[str] = []
    if sig.blocked_by:
        # Prima il blocco: non è un acquisto, anche se i motivi sotto lo descrivono.
        parts.append(
            f'<p class="blocked"><strong>Acquisto bloccato:</strong> {e(sig.blocked_by)}</p>'
        )
    if sig.action in _BUY:
        parts.append(
            '<div class="buy"><span>Importo</span>'
            f'<span class="amount n">{e(fmt_eur(sig.amount_eur))}</span>'
            f"{_chip(NOT_VALIDATED, 'warn', soft=True)}</div>"
        )
    if sig.reasons:
        label = "Perché sarebbe stato un acquisto" if sig.blocked_by else "Perché"
        parts.append(
            f'<div><p class="small muted">{e(label)}</p>{_ul(sig.reasons)}</div>'
        )
    parts.append(_ticker_facts(t))
    if sig.kind == PositionKind.event:
        parts.append(_earnings_html(t))
    if sig.plan is not None:
        title = (
            "Piano per la trimestrale"
            if sig.action == Action.ATTENDI
            else "Piano della posizione"
            if t.holding is not None
            else "Piano scritto prima dell'acquisto"
        )
        parts.append(_plan_html(sig.plan, t, eurusd, title))
    parts.append(_review_line(view.reviews.get(t.ticker)))
    parts.append(_meta(t, view))
    parts.append(_issues(t))
    parts.append(f'<p class="small muted">Dati al {e(fmt_date(t.asof))}</p>')
    return parts


def _head_chips(t: TickerSnapshot) -> str:
    chips = [_action_chip(t.signal.action)]
    if t.signal.blocked_by:
        chips.append(_chip("bloccato", "warn"))
    if t.signal.kind is not None and not t.signal.blocked_by:
        chips.append(_chip(_KIND_LABEL[t.signal.kind], soft=True))
    return f'<div class="chips">{"".join(chips)}</div>'


def _ident(t: TickerSnapshot) -> str:
    name = (
        f'<span class="name">{e(t.name)}</span>'
        if t.name and t.name != t.ticker
        else ""
    )
    return f'<div class="card-id"><span class="sym">{e(t.ticker)}</span>{name}</div>'


def _card(t: TickerSnapshot, view: FinalView, eurusd: float) -> str:
    head = f'<div class="card-head">{_ident(t)}{_head_chips(t)}</div>'
    return f'<article class="card">{head}{"".join(_signal_body(t, view, eurusd))}</article>'


def _quiet_row(t: TickerSnapshot, view: FinalView, eurusd: float) -> str:
    summary = (
        f'<summary><b class="sym">{e(t.ticker)}</b>{_action_chip(t.signal.action)}'
        f"{_val(fmt_money(t.close, t.currency), None)} {_change(t.change_1d_pct, '')}</summary>"
    )
    return f'<details>{summary}<div class="body">{"".join(_signal_body(t, view, eurusd))}</div></details>'


def _todo(view: FinalView) -> tuple[str, str]:
    snap = view.snapshot
    tickers = sorted(snap.tickers, key=lambda t: (_PRIORITY[t.signal.action], t.ticker))
    active = [
        t for t in tickers if t.signal.action not in _QUIET or t.signal.blocked_by
    ]
    quiet = [t for t in tickers if t not in active]
    parts: list[str] = []
    if active:
        parts.append(
            '<div class="stack">'
            + "".join(_card(t, view, snap.eurusd) for t in active)
            + "</div>"
        )
    else:
        parts.append('<p class="empty">Oggi nessuna azione da fare.</p>')
    if quiet:
        rows = "".join(_quiet_row(t, view, snap.eurusd) for t in quiet)
        parts.append(f'<h3>Senza azioni oggi</h3><div class="quiet">{rows}</div>')
    count = f'<span class="small muted">{len(active)} da guardare su {len(tickers)} titoli seguiti</span>'
    return count, "".join(parts)


# --- Portafoglio -------------------------------------------------------------


def _revision_value(field: str, value: str | None) -> str:
    if value is None:
        return "—"
    if field == "deadline":
        try:
            return f"{date.fromisoformat(value):%d/%m/%Y}"
        except ValueError:
            pass
    return value


def _revision_item(r: PlanRevision) -> str:
    status = (
        _chip("accettata", "good", soft=True)
        if r.accepted
        else _chip("rifiutata", "bad", soft=True)
    )
    change = ""
    if r.field != "plan":  # i piani interi sono JSON: basta il motivo
        change = (
            f"<span>{e(_revision_value(r.field, r.old))} → "
            f"{e(_revision_value(r.field, r.new))}</span>"
        )
    return (
        "<li>"
        f'<span class="row"><span class="n small muted">{e(fmt_dt(r.ts))}</span>'
        f"<b>{e(r.ticker)}</b><span>{e(_FIELD_LABEL.get(r.field, r.field))}</span>{status}"
        f'<span class="small muted">da {e(_SOURCE_LABEL.get(r.source, r.source))}</span></span>'
        f'{change}<span class="small">{e(r.reason)}</span>'
        "</li>"
    )


def _portfolio(view: FinalView, state: State, s: StocksSettings) -> str:
    snap = view.snapshot
    p = snap.portfolio
    held = [t for t in snap.tickers if t.holding is not None]
    # Investito: misurato se tutti i prezzi di carico sono in euro. Valore: misurato solo se
    # anche i titoli sono quotati in euro (altrimenti usa il cambio di oggi). Liquidità:
    # sempre stima (operazioni in USD al cambio di oggi, commissioni ipotizzate).
    invested_label = "misurato" if all(t.holding.currency == "EUR" for t in held) else "stima"
    value_label = (
        "misurato"
        if all(t.holding.currency == "EUR" and t.currency == "EUR" for t in held)
        else "stima"
    )
    pnl_css = "up" if p.pnl_net_eur > 0 else "down" if p.pnl_net_eur < 0 else ""
    totals = [
        ("Netto stimato", _val(fmt_eur(p.pnl_net_eur, signed=True), "stima", pnl_css)),
        ("Valore posizioni", _val(fmt_eur(p.value_eur), value_label)),
        ("Investito", _val(fmt_eur(p.invested_eur), invested_label)),
        ("Liquidità", _val(fmt_eur(p.cash_eur), "stima")),
    ]
    rows = "".join(f"<div><dt>{e(k)}</dt><dd>{v}</dd></div>" for k, v in totals)
    parts = [f'<dl class="totals">{rows}</dl>']
    parts.append(
        f'<p class="small muted">Netto stimato: dopo le commissioni ({e(fmt_eur(s.fee_per_order_eur))} '
        f"per ordine, in acquisto e in vendita) e le tasse del {e(fmt_pct(s.capital_gains_tax, 0, False))} "
        "sulle plusvalenze, come se vendessi oggi.</p>"
    )
    if held:
        cards = []
        for t in held:
            h = t.holding
            pnl = t.pnl_net_eur
            css = (
                "up"
                if _finite(pnl) and pnl > 0
                else "down"
                if _finite(pnl) and pnl < 0
                else ""
            )
            facts = _kv(
                [
                    ("Quantità", f'<span class="n">{e(fmt_qty(h.qty))} az.</span>'),
                    (
                        "Prezzo medio",
                        _val(fmt_money(h.avg_price, h.currency), "misurato"),
                    ),
                    ("Ultimo prezzo", _val(fmt_money(t.close, t.currency), "misurato")),
                    ("Netto stimato", _val(fmt_eur(pnl, signed=True), "stima", css)),
                ]
            )
            plan = (
                _plan_html(t.signal.plan, t, snap.eurusd, "Piano della posizione")
                if t.signal.plan is not None
                else '<p class="small muted">Piano non ancora disponibile.</p>'
            )
            head = (
                f'<div class="card-head">{_ident(t)}<div class="chips">'
                f"{_action_chip(t.signal.action)}{_chip(_KIND_LABEL[h.kind], soft=True)}</div></div>"
            )
            cards.append(f'<article class="card">{head}{facts}{plan}</article>')
        parts.append('<div class="stack">' + "".join(cards) + "</div>")
    else:
        parts.append(
            f'<p class="empty">Nessuna posizione aperta: budget disponibile {e(fmt_eur(s.budget_eur))}.</p>'
        )
    parts.append(f'<p class="note">{e(TRADE_NOTE)}</p>')
    revisions = sorted(state.revisions, key=lambda r: r.ts, reverse=True)[
        :MAX_REVISIONS_SHOWN
    ]
    if revisions:
        items = "".join(_revision_item(r) for r in revisions)
        parts.append(
            f"<details><summary>Storico revisioni dei piani ({len(state.revisions)})</summary>"
            f'<ul class="rev">{items}</ul></details>'
        )
    return "".join(parts)


# --- Notizie, glossario, proposte scartate -----------------------------------


def _news_link(item) -> str:
    title = e(item.title)
    if item.url.startswith("https://"):
        title = f'<a href="{e(item.url)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    meta = [e(item.source)]
    if item.published is not None:
        meta.append(e(f"{item.published:%d/%m/%Y}"))
    return f'<li>{title} <span class="small muted">· {" · ".join(meta)}</span></li>'


def _news(view: FinalView) -> str:
    if not view.review_available:
        return (
            '<p class="empty">Revisione notizie non disponibile in questo aggiornamento: '
            "nessuna notizia letta.</p>"
        )
    order = [t.ticker for t in view.snapshot.tickers]
    reviews = sorted(view.reviews.values(), key=lambda r: order.index(r.ticker))
    if not reviews:
        return '<p class="empty">Nessuna notizia rilevante per i titoli seguiti.</p>'
    cards = []
    for r in reviews:
        label, tone = _EVENT_LABEL.get(r.event_class, (r.event_class, ""))
        links = "".join(_news_link(n) for n in r.news)
        cards.append(
            '<article class="card">'
            f'<div class="card-head"><span class="sym">{e(r.ticker)}</span>'
            f'<div class="chips">{_chip(label, tone, soft=True)}</div></div>'
            f'<p class="lead">{e(r.summary)}</p>'
            f"<p>{e(r.explanation)}</p>"
            + (f'<ul class="news">{links}</ul>' if links else "")
            + "</article>"
        )
    parts = [
        '<p class="small muted">Testi scritti da Claude leggendo le notizie; i numeri della pagina vengono solo dai dati.</p>'
    ]
    parts.append('<div class="stack">' + "".join(cards) + "</div>")
    if view.applied:
        items = "".join(_revision_item(r) for r in view.applied)
        parts.append(
            f'<h3>Modifiche accettate in questo aggiornamento</h3><ul class="rev">{items}</ul>'
        )
    return "".join(parts)


def _glossary(view: FinalView) -> str:
    terms = [(g.term, g.definition) for g in view.glossary]
    seen = {term.strip().lower() for term, _ in terms}
    terms += [(t, d) for t, d in _BASE_GLOSSARY if t.lower() not in seen]
    rows = "".join(f"<div><dt>{e(t)}</dt><dd>{e(d)}</dd></div>" for t, d in terms)
    return f'<dl class="gloss">{rows}</dl>'


def _rejected(view: FinalView) -> str:
    if not view.rejected:
        return '<p class="empty">Nessuna proposta scartata in questo aggiornamento.</p>'
    items = "".join(_revision_item(r) for r in view.rejected)
    return (
        '<p class="small muted">Claude può spiegare e restringere i piani, mai allentarli: il '
        "codice scarta le proposte che non rispettano le regole.</p>"
        f'<ul class="rev">{items}</ul>'
    )


def _footer() -> str:
    return (
        f"<strong>{e(DISCLAIMER)}</strong>"
        '<p class="small muted">Prezzi da Yahoo Finance (fonte non ufficiale: può avere ritardi o '
        'errori). <span class="tag">misurato</span> calcolato dal codice sui dati di mercato; '
        '<span class="tag">stima</span> dipende da un\'ipotesi (tasse, cambio di oggi, livelli '
        "convertiti in euro).</p>"
    )


# --- Assemblaggio --------------------------------------------------------------


def render_html(
    view: FinalView, state: State, settings: StocksSettings, now: datetime | None = None
) -> str:
    """Pagina completa (senza guscio doctype/html/head/body, lo aggiunge l'Artifact)."""
    now = now or datetime.now(PAGE_TZ)
    today = _rome(now).date()
    calendar = _calendar(view, today)
    next_event = next((c for c in calendar if c.when >= today), None)
    todo_count, todo = _todo(view)
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        header=_header(view, next_event),
        banners=_banners(view, now, settings),
        todo_count=todo_count,
        todo=todo,
        portfolio=_portfolio(view, state, settings),
        news=_news(view),
        calendar=_calendar_html(calendar),
        model=f"<p>{e(MODEL_TEXT)}</p>",
        glossary=_glossary(view),
        rejected=_rejected(view),
        footer=_footer(),
    )


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    """Exit code 0 se la pagina è scritta; 2 se vista finale o stato non sono leggibili."""
    parser = argparse.ArgumentParser(
        prog="python -m stocks.render",
        description="Scrive la pagina HTML della dashboard dalla vista finale.",
    )
    parser.add_argument(
        "--state", default="out/state.json", help="file JSON dello stato"
    )
    parser.add_argument(
        "--out", default="out", help="cartella con final.json e dashboard.html"
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    try:
        view = FinalView.model_validate_json((out_dir / FINAL_FILE).read_text("utf-8"))
        state = load_state(Path(args.state))
    except (OSError, ValueError, ValidationError) as exc:
        logger.error("Vista finale o stato non leggibili: %s", exc)
        return 2
    page = render_html(view, state, load_settings(), now)
    target = out_dir / OUTPUT_FILE
    target.write_text(page, encoding="utf-8")
    logger.info("Pagina scritta in %s (%d caratteri)", target, len(page))
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
