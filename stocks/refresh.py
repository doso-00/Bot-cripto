"""Orchestratore della dashboard azioni: `uv run python -m stocks.refresh`.

Per ogni titolo seguito (watchlist ∪ posizioni aperte) scarica prezzi, fondamentali,
dividendi e trimestrali, calcola indicatori e segnale e scrive `out/snapshot.json`.
Nello stato aggiorna i piani delle posizioni aperte, il registro delle azioni e la
storia del valore del portafoglio.

Un titolo che fallisce non ferma gli altri (Review Focus 1): resta nello snapshot con
`data_ok = False`, azione `DATI_MANCANTI` e il motivo in `issues`. Solo il cambio
EUR/USD è indispensabile: senza, il run si ferma senza scrivere nulla.

Il run avviene in tre passi, perché il blocco del portafoglio (che serve al motore
regole) dipende dai prezzi di tutte le posizioni: 1) scarico dati per titolo;
2) piani mancanti e valore del portafoglio; 3) segnali, registro e snapshot.
"""

import argparse
import logging
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from stocks.config import StocksSettings, Watchlist, load_settings, load_watchlist
from stocks.costs import net_pnl_eur
from stocks.data import (
    CLOSE_MARGIN_MINUTES,
    dividend_years_without_cut,
    exchange_for_ticker,
    fetch_dividends,
    fetch_fundamentals,
    fetch_fx_eurusd,
    fetch_history,
    to_eur,
)
from stocks.earnings import earnings_info, expected_move_pct, fetch_earnings
from stocks.indicators import atr, drawdown_from_high, rsi, sma, trend_closes
from stocks.models import (
    Action,
    EarningsInfo,
    Fundamentals,
    Holding,
    PortfolioSummary,
    PositionKind,
    Signal,
    SizingContext,
    Snapshot,
    TickerSnapshot,
)
from stocks.risk import max_order_eur, portfolio_halted
from stocks.signals import (
    ATR_WINDOW,
    HIGH_WINDOW,
    NEW_BUY_STOP_PREFIX,
    OPEN_POSITION_STOP_PREFIX,  # noqa: F401 - riesportato per i test
    RSI_WINDOW,
    SMA_WINDOW,
    build_plan,
    evaluate,
    holding_entry,
    open_position_stop_line,
)
from stocks.state import (
    JournalEntry,
    PlanRevision,
    State,
    Trade,
    holdings_from_trades,
    load_state,
    save_state,
)

# nome esplicito: con `python -m` __name__ vale "__main__"
logger = logging.getLogger("stocks.refresh")

PAGE_TZ = ZoneInfo("Europe/Rome")
SNAPSHOT_FILE = "snapshot.json"
# Per la variazione di un giorno degli indici bastano poche barre;
# 1 anno è il periodo minimo di fetch_history.
BENCHMARK_HISTORY_YEARS = 1
# Suffissi di borsa quotati in euro (fallback se Yahoo non dà la valuta).
_EUR_SUFFIXES = (".MI", ".DE", ".PA", ".AS")
_QTY_EPS = 1e-9

EU_MOVES_ISSUE = "movimenti storici non disponibili per i titoli europei"
PLAN_CREATED_REASON = "piano creato alla registrazione dell'acquisto"
PLAN_REMOVED_REASON = "posizione chiusa: piano rimosso"
NO_PRICES_REASON = (
    "Yahoo non ha restituito prezzi per questo titolo (ticker errato o non più quotato?)."
)
EMPTY_FUNDAMENTALS_ISSUE = (
    "Dati di bilancio non disponibili: nessun nuovo acquisto di base per questo titolo."
)
NO_EXPECTED_MOVE_ISSUE = "Movimento atteso dalle opzioni non disponibile"
BUDGET_ASSIGNED_REASON = "budget già assegnato ad altre proposte di questo aggiornamento"
_BUY_ACTIONS = (Action.COMPRA, Action.ACCUMULA)


class NoMarketDataError(RuntimeError):
    """Manca un dato senza il quale nessun titolo è valutabile (il cambio EUR/USD)."""


@dataclass
class _TickerData:
    """Dati scaricati per un titolo. `error` valorizzato = nessun prezzo utilizzabile."""

    ticker: str
    today: date  # data di oggi nel fuso della borsa del titolo
    prices: pd.DataFrame | None = None
    fundamentals: Fundamentals = field(default_factory=Fundamentals)
    currency: str | None = None
    div_years: int = 0
    earnings: EarningsInfo = field(default_factory=EarningsInfo)
    close_eur: float | None = None
    issues: list[str] = field(default_factory=list)
    # Note da mostrare anche in alto nella pagina (spengono "dati OK"), senza bloccare il segnale.
    page_issues: list[str] = field(default_factory=list)
    error: str | None = None


# --- Passo 1: dati per titolo -------------------------------------------------


def _is_us(ticker: str) -> bool:
    """Titolo USA = nessun suffisso di borsa (stessa convenzione di `exchange_for_ticker`)."""
    return "." not in ticker


def _fallback_currency(ticker: str) -> str | None:
    """Valuta di quotazione ricavata dal suffisso: nessuno → USD, borse euro → EUR."""
    if _is_us(ticker):
        return "USD"
    suffix = "." + ticker.rsplit(".", 1)[1].upper()
    return "EUR" if suffix in _EUR_SUFFIXES else None


def _exchange_today(ticker: str, now: datetime) -> date:
    tz, _ = exchange_for_ticker(ticker)
    return now.astimezone(ZoneInfo(tz)).date()


def _expected_session(ticker: str, now: datetime) -> date:
    """Ultima seduta che dovrebbe essere nei dati (ruling R23).

    L'ultimo giorno feriale <= oggi (fuso della borsa) la cui chiusura + 20 minuti è già
    passata. Le festività non sono gestite: il giorno dopo una festa l'avviso può comparire.
    """
    tz, close_time = exchange_for_ticker(ticker)
    local = now.astimezone(ZoneInfo(tz))
    day = local.date()
    closed = datetime.combine(day, close_time, tzinfo=local.tzinfo) + timedelta(
        minutes=CLOSE_MARGIN_MINUTES
    )
    if day.weekday() >= 5 or local < closed:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _fundamentals_empty(f: Fundamentals) -> bool:
    """True se Yahoo non ha dato nessun dato di `info` (la data di stacco viene da `calendar`)."""
    return all(v is None for k, v in f.model_dump().items() if k != "ex_dividend_date")


def _fetch_ticker(
    ticker: str, now: datetime, eurusd: float, s: StocksSettings, client
) -> _TickerData:
    """Scarica tutto ciò che serve per un titolo.

    Senza prezzi il titolo è in errore e il resto non si scarica. Fondamentali,
    dividendi, trimestrali e opzioni sono accessori: un loro errore diventa una nota
    in `issues` e si prosegue con valori vuoti.
    """
    d = _TickerData(ticker=ticker, today=_exchange_today(ticker, now))
    try:
        prices = fetch_history(ticker, s.history_years, now, client)
    except Exception as exc:  # noqa: BLE001 - un titolo non deve fermare gli altri
        logger.warning("%s: prezzi non disponibili (%s)", ticker, exc)
        d.error = f"Prezzi non disponibili da Yahoo ({exc})."
        d.issues.append(d.error)
        return d
    if prices.empty:
        logger.warning("%s: nessun prezzo restituito da Yahoo", ticker)
        d.error = NO_PRICES_REASON
        d.issues.append(d.error)
        return d
    d.prices = prices
    asof = prices.index[-1]
    close = float(prices["close"].iloc[-1])
    expected = _expected_session(ticker, now)
    if asof < expected:
        msg = (
            f"Ultimo prezzo disponibile del {asof:%d/%m}: la seduta del {expected:%d/%m} "
            "non è ancora nei dati di Yahoo."
        )
        logger.warning("%s: %s", ticker, msg)
        d.issues.append(msg)
        d.page_issues.append(msg)

    try:
        f = fetch_fundamentals(ticker, client)
        if _fundamentals_empty(f):
            # Yahoo a volte risponde vuoto senza errori ("Invalid Crumb"): un nuovo tentativo.
            logger.warning("%s: fondamentali vuoti, nuovo tentativo", ticker)
            f = fetch_fundamentals(ticker, client)
    except Exception as exc:  # noqa: BLE001 - accessorio
        logger.warning("%s: fondamentali non disponibili (%s)", ticker, exc)
        f = Fundamentals()
        d.issues.append(f"Dati fondamentali non disponibili da Yahoo ({exc}).")
    if _fundamentals_empty(f):
        d.issues.append(EMPTY_FUNDAMENTALS_ISSUE)
        d.page_issues.append(EMPTY_FUNDAMENTALS_ISSUE)
    if f.currency is None:
        fallback = _fallback_currency(ticker)
        if fallback is not None:
            d.issues.append(
                f"Yahoo non fornisce la valuta: assunta {fallback} dal suffisso del ticker."
            )
        else:
            d.issues.append("Yahoo non fornisce la valuta e il suffisso del ticker non la indica.")
        f = f.model_copy(update={"currency": fallback})
    d.fundamentals = f
    d.currency = f.currency
    if d.currency is not None:
        try:
            d.close_eur = to_eur(close, d.currency, eurusd)
        except ValueError:
            d.close_eur = None  # il motore regole segnala la valuta non supportata

    try:
        d.div_years = dividend_years_without_cut(fetch_dividends(ticker, client), asof)
    except Exception as exc:  # noqa: BLE001 - accessorio
        logger.warning("%s: dividendi non disponibili (%s)", ticker, exc)
        d.issues.append(f"Storico dei dividendi non disponibile ({exc}).")

    d.earnings = _earnings(ticker, prices, close, now, s, client, d.issues)
    return d


def _earnings(
    ticker: str,
    prices: pd.DataFrame,
    close: float,
    now: datetime,
    s: StocksSettings,
    client,
    issues: list[str],
) -> EarningsInfo:
    """Trimestrali del titolo, con le correzioni dell'orchestratore.

    Titoli europei: movimenti storici azzerati (ruling R6, orari Yahoo inaffidabili).
    Movimento atteso dalle opzioni: chiesto solo con una trimestrale entro la finestra
    (`0 <= days_to_next <= event_window_days`), perché costa chiamate di rete; a data
    passata l'evento è già avvenuto e lo straddle non lo misura più.
    """
    try:
        e = earnings_info(fetch_earnings(ticker, client), prices, now)
    except Exception as exc:  # noqa: BLE001 - accessorio
        logger.warning("%s: trimestrali non disponibili (%s)", ticker, exc)
        issues.append(f"Dati sulle trimestrali non disponibili ({exc}).")
        e = EarningsInfo()
    if not _is_us(ticker):
        e = e.model_copy(update={"past_moves_pct": [], "avg_abs_move_pct": None})
        issues.append(EU_MOVES_ISSUE)
    if (
        e.next_ts is not None
        and e.days_to_next is not None
        and 0 <= e.days_to_next <= s.event_window_days
    ):
        try:
            move = expected_move_pct(ticker, e.next_ts.date(), close, client)
        except Exception as exc:  # noqa: BLE001 - accessorio
            logger.warning("%s: movimento atteso non calcolabile (%s)", ticker, exc)
            move = None
        if move is None:
            issues.append(NO_EXPECTED_MOVE_ISSUE)
        e = e.model_copy(update={"expected_move_pct": move})
    return e


def _benchmarks(
    watchlist: Watchlist, now: datetime, client
) -> tuple[dict[str, float], list[str]]:
    """Variazione dell'ultimo giorno chiuso di ogni indice (frazione); errori → issues."""
    out: dict[str, float] = {}
    issues: list[str] = []
    for symbol in watchlist.benchmarks.values():
        try:
            closes = fetch_history(symbol, BENCHMARK_HISTORY_YEARS, now, client)["close"]
            if len(closes) < 2:
                raise ValueError("meno di due chiusure disponibili")
            out[symbol] = float(closes.iloc[-1] / closes.iloc[-2] - 1)
        except Exception as exc:  # noqa: BLE001 - gli indici sono accessori
            logger.warning("Indice %s non disponibile (%s)", symbol, exc)
            issues.append(f"Indice {symbol}: variazione del giorno non disponibile ({exc}).")
    return out, issues


# --- Passo 2: posizioni, piani e portafoglio ------------------------------------


def _holdings(trades: list[Trade]) -> tuple[dict[str, Holding], dict[str, str]]:
    """Posizioni aperte per titolo; operazioni incoerenti su un titolo non bloccano gli altri."""
    by_ticker: dict[str, list[Trade]] = {}
    for t in trades:
        by_ticker.setdefault(t.ticker, []).append(t)
    holdings: dict[str, Holding] = {}
    errors: dict[str, str] = {}
    for ticker, group in by_ticker.items():
        try:
            holdings.update(holdings_from_trades(group))
        except (ValueError, TypeError, ArithmeticError) as exc:
            # ArithmeticError: es. un acquisto con quantità 0 (divisione per zero nel medio)
            errors[ticker] = str(exc) or type(exc).__name__
    return holdings, errors


def _position_start(trades: list[Trade], ticker: str) -> date | None:
    """Data del primo acquisto della posizione aperta (dopo l'ultima chiusura totale)."""
    qty, start = 0.0, None
    for t in sorted((t for t in trades if t.ticker == ticker), key=lambda x: x.ts):
        if t.side == "buy":
            if qty <= _QTY_EPS:
                start = t.ts.date()
            qty += t.qty
        else:
            qty -= t.qty
    return start


def _plan_revision(
    now: datetime, ticker: str, old: str | None, new: str | None, reason: str
) -> PlanRevision:
    return PlanRevision(
        ts=now,
        ticker=ticker,
        field="plan",
        old=old,
        new=new,
        source="engine",
        reason=reason,
        accepted=True,
    )


def _sync_plans(
    state: State,
    holdings: dict[str, Holding],
    trade_errors: dict[str, str],
    data: dict[str, _TickerData],
    eurusd: float,
    now: datetime,
    s: StocksSettings,
) -> None:
    """Piani nello stato: uno per posizione aperta (ruling R3).

    Una posizione senza piano lo riceve con `build_plan`: ingresso = prezzo medio nella
    valuta del titolo (`holding_entry`), prezzi fino al primo acquisto. Per un piano con
    stop la prima riga dice di inserire (o correggere) l'ordine stop-loss al livello
    ricalcolato sul prezzo di carico. I piani dei titoli non più posseduti si rimuovono,
    così un riacquisto futuro non eredita un piano vecchio. I piani dei `COMPRA` non si
    salvano qui: solo all'acquisto registrato. Un errore resta confinato al suo titolo.
    """
    for ticker in list(state.plans):
        if ticker not in holdings and ticker not in trade_errors:
            old = state.plans.pop(ticker)
            state.revisions.append(
                _plan_revision(now, ticker, old.model_dump_json(), None, PLAN_REMOVED_REASON)
            )
            logger.info("%s: posizione chiusa, piano rimosso", ticker)

    for ticker, holding in holdings.items():
        d = data[ticker]
        if ticker in state.plans or d.error is not None or d.currency is None:
            continue  # senza prezzi o valuta il piano si crea a un run successivo
        start = _position_start(state.trades, ticker)
        try:
            entry, notes = holding_entry(holding, d.currency, eurusd)
            plan = build_plan(holding.kind, entry, d.prices, start, d.currency, eurusd, s)
        except Exception as exc:  # noqa: BLE001 - un titolo non deve fermare gli altri
            logger.warning("%s: piano di uscita non creato (%s)", ticker, exc)
            d.issues.append(f"Piano di uscita non creato: {exc}")
            continue
        scenarios = list(plan.scenarios)
        if plan.stop_eur is not None:
            # L'acquisto è già fatto: non "subito dopo l'acquisto", ma inserisci o correggi.
            scenarios = [
                open_position_stop_line(plan.stop_eur)
                if line.startswith(NEW_BUY_STOP_PREFIX)
                else line
                for line in scenarios
            ]
        plan = plan.model_copy(update={"scenarios": [*scenarios, *notes]})
        state.plans[ticker] = plan
        state.revisions.append(
            _plan_revision(now, ticker, None, plan.model_dump_json(), PLAN_CREATED_REASON)
        )
        logger.info("%s: creato il piano di uscita della posizione", ticker)


@dataclass
class _Portfolio:
    summary: PortfolioSummary
    ctx: SizingContext
    pnl_by_ticker: dict[str, float]
    issues: list[str]


def _portfolio(
    state: State,
    holdings: dict[str, Holding],
    trade_errors: dict[str, str],
    data: dict[str, _TickerData],
    eurusd: float,
    now: datetime,
    s: StocksSettings,
) -> _Portfolio:
    """Valori in euro delle posizioni, liquidità, storia del valore e blocco acquisti.

    Investito: qty × prezzo medio (convertito al cambio di oggi se la posizione è in
    USD). Valore: qty × chiusura in euro. Liquidità (ruling R26): budget − acquisti
    (prezzo × qty + commissione) + vendite (prezzo × qty − commissione), in euro (le
    operazioni in USD al cambio di oggi); valore totale = liquidità + valore delle
    posizioni, così le perdite realizzate restano nella storia e nel blocco del −15%.
    Utile netto per posizione con commissioni e
    tasse; quello del portafoglio è la somma (prudente: non compensa perdite e guadagni).
    La storia riceve un punto al giorno (data di Roma) solo se tutte le posizioni hanno un
    valore (prezzo e operazioni valide): un valore parziale simulerebbe un crollo e
    bloccherebbe gli acquisti.
    """
    issues: list[str] = []
    invested_by_kind: dict[PositionKind, float] = {}
    pnl_by_ticker: dict[str, float] = {}
    invested_total = value_total = 0.0
    missing: list[str] = list(trade_errors)
    for ticker, h in holdings.items():
        try:
            # to_eur lascia invariati gli euro e converte i dollari al cambio di oggi
            invested = h.qty * to_eur(h.avg_price, h.currency, eurusd)
        except ValueError:
            missing.append(ticker)
            continue
        invested_total += invested
        invested_by_kind[h.kind] = invested_by_kind.get(h.kind, 0.0) + invested
        close_eur = data[ticker].close_eur
        if close_eur is None:
            missing.append(ticker)
            continue
        value = h.qty * close_eur
        value_total += value
        pnl_by_ticker[ticker] = net_pnl_eur(invested, value, s)

    cash = s.budget_eur
    for t in state.trades:
        try:
            gross = to_eur(t.price * t.qty, t.currency, eurusd)
        except ValueError:
            missing.append(t.ticker)
            continue
        cash += gross - t.fee_eur if t.side == "sell" else -(gross + t.fee_eur)
    missing = sorted(set(missing))
    if missing:
        issues.append(
            f"Valore del portafoglio incompleto (mancano i dati di {', '.join(missing)}): "
            "storia del valore non aggiornata in questo run."
        )
    else:
        today = now.astimezone(PAGE_TZ).date()
        history = [(d, v) for d, v in state.equity_history_eur if d != today]
        history.append((today, cash + value_total))
        state.equity_history_eur = sorted(history, key=lambda p: p[0])

    values = [v for _, v in state.equity_history_eur]
    halted = portfolio_halted(values, s)
    summary = PortfolioSummary(
        invested_eur=invested_total,
        value_eur=value_total,
        pnl_net_eur=sum(pnl_by_ticker.values()),
        cash_eur=cash,
        halted=halted,
        peak_value_eur=max(values) if values else s.budget_eur,
    )
    open_opps = sum(1 for h in holdings.values() if h.kind != PositionKind.core)
    ctx = SizingContext(holdings_eur=invested_by_kind, open_opps=open_opps, halted=halted)
    return _Portfolio(summary, ctx, pnl_by_ticker, issues)


# --- Passo 3: segnali, registro, snapshot -----------------------------------------


def _last(series: pd.Series) -> float | None:
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)


def _indicators(prices: pd.DataFrame) -> dict:
    """Indicatori mostrati: tendenza sulle chiusure aggiustate (R25), prezzo e ATR sul grezzo."""
    closes = prices["close"]
    trend = trend_closes(prices)
    return {
        "close": float(closes.iloc[-1]),
        "change_1d_pct": float(closes.iloc[-1] / closes.iloc[-2] - 1) if len(closes) >= 2 else None,
        "sma200": _last(sma(trend, SMA_WINDOW)),
        "atr14": _last(atr(prices, ATR_WINDOW)),
        "rsi14": _last(rsi(trend, RSI_WINDOW)),
        "drawdown_63d_pct": _last(drawdown_from_high(trend, HIGH_WINDOW)),
        "high_63d": float(trend.iloc[-HIGH_WINDOW:].max()),
    }


def _missing(reason: str) -> Signal:
    return Signal(action=Action.DATI_MANCANTI, reasons=[reason])


def _signal(
    d: _TickerData,
    holding: Holding | None,
    trade_error: str | None,
    state: State,
    ctx: SizingContext,
    eurusd: float,
    s: StocksSettings,
) -> Signal:
    if trade_error is not None:
        return _missing(f"Operazioni registrate non valide: {trade_error}")
    if d.error is not None:
        return _missing(d.error)
    plan = state.plans.get(d.ticker) if holding is not None else None
    try:
        # asof = oggi nel fuso della borsa: così la regola "dati vecchi" vede quanto è
        # vecchia l'ultima barra (le barre non chiuse sono già state scartate).
        return evaluate(
            d.prices, d.fundamentals, d.div_years, d.earnings,
            holding, plan, d.today, ctx, eurusd, s,
        )
    except Exception as exc:  # noqa: BLE001 - un titolo non deve fermare gli altri
        logger.exception("%s: errore nel calcolo del segnale", d.ticker)
        return _missing(f"Errore nel calcolo del segnale: {exc}")


def _budget_assigned(
    sig: Signal, base: SizingContext, running: SizingContext, s: StocksSettings
) -> Signal:
    """Un acquisto bloccato solo dagli importi già proposti in questo run lo dice (R24).

    Se con il portafoglio reale l'acquisto avrebbe avuto un importo, il blocco viene dalle
    proposte precedenti di questo aggiornamento, non da un limite del portafoglio.
    """
    if (
        sig.action != Action.NESSUNA_AZIONE
        or sig.blocked_by is None
        or sig.kind is None
        or running.halted
        or running == base
    ):
        return sig
    amount, _ = max_order_eur(sig.kind, base.holdings_eur, base.open_opps, s)
    if amount <= 0:
        return sig
    return sig.model_copy(update={"blocked_by": BUDGET_ASSIGNED_REASON})


def _assign(running: SizingContext, sig: Signal) -> None:
    """Riserva l'importo di un acquisto proposto per i titoli valutati dopo."""
    if sig.action not in _BUY_ACTIONS or not sig.amount_eur or sig.kind is None:
        return
    running.holdings_eur[sig.kind] = running.holdings_eur.get(sig.kind, 0.0) + sig.amount_eur
    if sig.kind != PositionKind.core:
        running.open_opps += 1


def _followed(
    watchlist: Watchlist, holdings: dict[str, Holding], trade_errors: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Titoli seguiti: watchlist nell'ordine del file, poi i soli posseduti in ordine alfabetico."""
    proposed: dict[str, str] = {}
    for item in watchlist.items:
        proposed.setdefault(item.ticker, item.proposed_by)
    extra = sorted((set(holdings) | set(trade_errors)) - set(proposed))
    return [*proposed, *extra], proposed


def run_refresh(
    now: datetime,
    state_path: Path,
    out_dir: Path,
    settings: StocksSettings,
    watchlist: Watchlist,
    client=yf,
    run_slot: str = "manuale",
) -> Snapshot:
    """Esegue un aggiornamento completo; scrive `out_dir/snapshot.json` e salva lo stato.

    `now` deve avere il fuso orario. Solleva `NoMarketDataError` (senza scrivere nulla)
    se manca il cambio EUR/USD; ogni altro errore resta confinato al suo titolo.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("`now` deve avere il fuso orario (es. Europe/Rome)")
    s = settings
    started = time.monotonic()
    state_path, out_dir = Path(state_path), Path(out_dir)
    state = load_state(state_path)

    try:
        eurusd = fetch_fx_eurusd(now, client)
    except Exception as exc:  # noqa: BLE001 - riportato come errore bloccante
        raise NoMarketDataError(f"Cambio EUR/USD non disponibile ({exc})") from exc
    logger.info("Cambio EUR/USD: %.4f", eurusd)

    holdings, trade_errors = _holdings(state.trades)
    followed, proposed = _followed(watchlist, holdings, trade_errors)

    # Passo 1
    data: dict[str, _TickerData] = {}
    for i, ticker in enumerate(followed, 1):
        logger.info("Scarico %s (%d/%d)", ticker, i, len(followed))
        try:
            data[ticker] = _fetch_ticker(ticker, now, eurusd, s, client)
        except Exception as exc:  # noqa: BLE001 - un titolo non deve fermare gli altri
            logger.exception("%s: errore imprevisto nello scarico", ticker)
            msg = f"Errore imprevisto nello scarico dei dati ({exc})."
            data[ticker] = _TickerData(ticker=ticker, today=_exchange_today(ticker, now),
                                       error=msg, issues=[msg])
    benchmarks, issues = _benchmarks(watchlist, now, client)

    # Passo 2
    _sync_plans(state, holdings, trade_errors, data, eurusd, now, s)
    portfolio = _portfolio(state, holdings, trade_errors, data, eurusd, now, s)
    issues.extend(portfolio.issues)

    # Passo 3
    last_action: dict[str, Action] = {}
    for entry in state.journal:
        if entry.source == "engine":
            last_action[entry.ticker] = entry.action

    # Importi in sequenza (R24): prima i titoli posseduti, poi gli altri, nell'ordine della
    # watchlist; ogni acquisto proposto riduce il budget residuo per i titoli successivi.
    running = portfolio.ctx.model_copy(deep=True)
    signals: dict[str, Signal] = {}
    for ticker in sorted(followed, key=lambda tk: tk not in holdings):
        sig = _signal(data[ticker], holdings.get(ticker), trade_errors.get(ticker), state,
                      running, eurusd, s)
        sig = _budget_assigned(sig, portfolio.ctx, running, s)
        _assign(running, sig)
        signals[ticker] = sig

    tickers: list[TickerSnapshot] = []
    for ticker in followed:
        d = data[ticker]
        holding = holdings.get(ticker)
        signal = signals[ticker]
        data_ok = d.error is None and signal.action != Action.DATI_MANCANTI
        if not data_ok:
            for reason in signal.reasons:
                if reason not in d.issues:
                    d.issues.append(reason)
            issues.append(f"{ticker}: {' '.join(signal.reasons)}")
        else:
            issues.extend(f"{ticker}: {msg}" for msg in d.page_issues)
        snap = TickerSnapshot(
            ticker=ticker,
            name=d.fundamentals.name or ticker,
            currency=d.currency,
            proposed_by=proposed.get(ticker),
            asof=d.prices.index[-1] if d.prices is not None else d.today,
            close_eur=d.close_eur,
            **(_indicators(d.prices) if d.prices is not None else {}),
            fundamentals=d.fundamentals,
            dividend_years_without_cut=d.div_years,
            earnings=d.earnings,
            holding=holding,
            pnl_net_eur=portfolio.pnl_by_ticker.get(ticker),
            signal=signal,
            data_ok=data_ok,
            data_issues=d.issues,
        )
        tickers.append(snap)
        logger.info("%s: %s", ticker, signal.action.value)

        if snap.close is not None and last_action.get(ticker) != signal.action:
            state.journal.append(
                JournalEntry(
                    ts=now,
                    ticker=ticker,
                    action=signal.action,
                    close=snap.close,
                    reasons=signal.reasons,
                    source="engine",
                )
            )

    snapshot = Snapshot(
        generated_at=now,
        run_slot=run_slot,
        eurusd=eurusd,
        benchmarks=benchmarks,
        tickers=tickers,
        portfolio=portfolio.summary,
        issues=issues,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / SNAPSHOT_FILE).write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    save_state(state, state_path)
    ok = sum(t.data_ok for t in tickers)
    logger.info(
        "Snapshot scritto in %s: %d/%d titoli con dati, %d problemi, %.1f s",
        out_dir / SNAPSHOT_FILE,
        ok,
        len(tickers),
        len(issues),
        time.monotonic() - started,
    )
    return snapshot


# --- CLI ------------------------------------------------------------------------


def main(argv: list[str] | None = None, client=yf, now: datetime | None = None) -> int:
    """Exit code 0 anche con titoli in errore; 2 se nessun titolo ha dati o manca il cambio."""
    parser = argparse.ArgumentParser(
        prog="python -m stocks.refresh",
        description="Aggiorna dati e segnali dei titoli seguiti e scrive lo snapshot.",
    )
    parser.add_argument("--state", default="out/state.json", help="file JSON dello stato")
    parser.add_argument("--out", default="out", help="cartella di output dello snapshot")
    parser.add_argument("--slot", default="manuale", choices=["mattina", "sera", "manuale"])
    args = parser.parse_args(argv)

    now = now or datetime.now(PAGE_TZ)
    try:
        snapshot = run_refresh(
            now,
            Path(args.state),
            Path(args.out),
            load_settings(),
            load_watchlist(),
            client=client,
            run_slot=args.slot,
        )
    except NoMarketDataError as exc:
        logger.error("%s: snapshot non scritto", exc)
        return 2
    if not any(t.data_ok for t in snapshot.tickers):
        logger.error("Nessun titolo ha dati validi")
        return 2
    return 0


def _configure_cli() -> None:
    """Log in italiano su stderr e silenzio sugli avvisi interni di yfinance."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # yfinance usa API di pandas deprecate: avvisi non nostri e non azionabili.
    for category in (getattr(pd.errors, "Pandas4Warning", DeprecationWarning), FutureWarning):
        warnings.filterwarnings("ignore", category=category, module=r"yfinance(\.|$)")


if __name__ == "__main__":
    _configure_cli()
    sys.exit(main())
