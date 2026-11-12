"""Motore delle regole: per ogni titolo un'azione, i motivi e il piano di uscita.

Funzioni pure: ricevono dati già scaricati e restituiscono un `Signal`, senza rete.
`evaluate` usa solo le barre giornaliere con data <= `asof` (niente look-ahead).
Prezzi e livelli sono nella valuta del titolo; gli importi e i livelli `*_eur` in euro.
"""

import re
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import pandas as pd

from stocks.config import StocksSettings
from stocks.costs import round_trip_fees
from stocks.data import to_eur
from stocks.indicators import atr, drawdown_from_high, rsi, sma
from stocks.models import (
    Action,
    EarningsInfo,
    Fundamentals,
    Holding,
    Plan,
    PositionKind,
    Signal,
    SizingContext,
)
from stocks.risk import max_order_eur

# Finestre fisse della specifica (non sono soglie di rischio, quindi non stanno in settings.yaml).
# Ultima barra più vecchia di così (giorni di calendario) -> dati mancanti.
MAX_STALE_DAYS = 5
SMA_WINDOW = 200
ATR_WINDOW = 14
RSI_WINDOW = 14
HIGH_WINDOW = 63  # circa 3 mesi di borsa
# Trimestrale passata da più di così (giorni) senza utile riportato: evento "scaduto"
# (ruling R10), l'ATTENDI decade e le regole normali proseguono.
STALE_EVENT_DAYS = 3
STALE_EVENT_REASON = (
    "Risultati della trimestrale non ancora riportati dalla fonte dati."
)

CURRENCY_MISMATCH_REASON = "Valuta della posizione diversa da quella del titolo."
CONVERTED_ENTRY_REASON = (
    "Prezzo di carico convertito in dollari al cambio di oggi (approssimazione)."
)

# Tolleranza relativa sui confronti con le soglie: 1 - 90/100 vale 0.09999999999999998,
# che senza tolleranza risulterebbe "sotto il 10%".
_TOL = 1e-9

_BUY_ACTIONS = (Action.COMPRA, Action.ACCUMULA)


# --- Confronti e formattazione ------------------------------------------------


def _le(a: float, b: float) -> bool:
    return a <= b + _TOL * max(1.0, abs(b))


def _ge(a: float, b: float) -> bool:
    return a >= b - _TOL * max(1.0, abs(b))


def _num(x: float, decimals: int = 2) -> str:
    """Numero all'italiana: 1.234,56."""
    text = f"{x:,.{decimals}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def _pct(fraction: float, decimals: int = 1, signed: bool = False) -> str:
    """Frazione in percentuale: 0.095 -> '9,5%' ('+9,5%' con `signed`)."""
    sign = "+" if signed and fraction > 0 else ""
    return f"{sign}{_num(fraction * 100, decimals)}%"


_ELIDED = {"del": "dell'", "il": "l'", "al": "all'"}


def _art(prep: str, text: str) -> str:
    """Preposizione/articolo davanti a un numero: 'del 20%' ma 'dell'8%', 'l'11/12'.

    In italiano si elide davanti ai numeri che si leggono con vocale iniziale:
    uno, undici, otto, ottanta..., ottocento...
    """
    match = re.match(r"[+\-±]?(\d+)", text)
    digits = match.group(1) if match else ""
    if digits in ("1", "11") or digits.startswith("8"):
        return f"{_ELIDED[prep]}{text}"
    return f"{prep} {text}"


def _money(x: float, currency: str) -> str:
    return f"{_num(x)} €" if currency == "EUR" else f"{_num(x)} {currency}"


def _round_eur(x: float, rounding: str) -> float:
    """Arrotonda al centesimo partendo dalla rappresentazione decimale più corta.

    `Decimal(str(x))` evita che 73.60000000000001 o 114.99999999999999 perdano o
    guadagnino un centesimo per errori di virgola mobile.
    """
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=rounding))


def _upto(prices: pd.DataFrame, asof: date) -> pd.DataFrame:
    """Solo le barre con data <= asof, in ordine di data."""
    if prices.empty:
        return prices
    return prices.loc[[d <= asof for d in prices.index]].sort_index()


# --- Qualità -----------------------------------------------------------------


def quality_check(
    f: Fundamentals, div_years: int, s: StocksSettings
) -> tuple[bool, list[str]]:
    """Filtro di qualità per le posizioni base.

    Requisiti: utili positivi (`trailing_pe > 0`), ROE >= soglia, debito/patrimonio
    <= soglia o non disponibile, payout <= soglia o non disponibile. Un dato richiesto
    ma assente (P/E, ROE) fa fallire il controllo: non si può verificare.
    I dividendi sono un plus nei motivi, non un requisito.
    Restituisce (esito, motivi): se l'esito è False i motivi sono solo i criteri non
    superati; se è True sono un riepilogo positivo.
    """
    failures = []
    if f.trailing_pe is None:
        failures.append(
            "Utili non verificabili: il rapporto prezzo/utili non è disponibile."
        )
    elif f.trailing_pe <= 0:
        failures.append(
            "L'azienda non è in utile (rapporto prezzo/utili non positivo)."
        )
    if f.roe is None:
        failures.append("Redditività non verificabile: il ROE non è disponibile.")
    elif not _ge(f.roe, s.quality_min_roe):
        failures.append(
            f"Redditività bassa: ROE {_pct(f.roe)}, sotto il minimo {_art('del', _pct(s.quality_min_roe))}."
        )
    if f.debt_to_equity is not None and not _le(
        f.debt_to_equity, s.quality_max_debt_to_equity
    ):
        failures.append(
            f"Debito alto: pari {_art('al', _num(f.debt_to_equity, 0) + '%')} del patrimonio "
            f"(massimo {_num(s.quality_max_debt_to_equity, 0)}%)."
        )
    if f.payout_ratio is not None and not _le(f.payout_ratio, s.quality_max_payout):
        failures.append(
            f"Dividendo poco sostenibile: distribuisce {_art('il', _pct(f.payout_ratio))} degli utili "
            f"(massimo {_pct(s.quality_max_payout)})."
        )
    if failures:
        return False, failures

    parts = [f"in utile (P/E {_num(f.trailing_pe, 1)})", f"ROE {_pct(f.roe)}"]
    if f.debt_to_equity is not None:
        parts.append(
            f"debito pari {_art('al', _num(f.debt_to_equity, 0) + '%')} del patrimonio"
        )
    if f.payout_ratio is not None:
        parts.append(f"distribuisce {_art('il', _pct(f.payout_ratio))} degli utili")
    reasons = ["Qualità sufficiente: " + ", ".join(parts) + "."]
    if div_years > 0:
        reasons.append(
            f"Dividendo pagato senza tagli da {div_years} anni: un plus, non un requisito."
        )
    return True, reasons


# --- Piani -------------------------------------------------------------------


def build_plan(
    kind: PositionKind,
    entry: float,
    prices: pd.DataFrame,
    asof: date,
    currency: str,
    eurusd: float,
    s: StocksSettings,
) -> Plan:
    """Piano di uscita per un ingresso a `entry` (valuta del titolo) alla data `asof`.

    Base: revisione a −15% e uscita a −20% da `entry`, nessuna data limite.
    Occasione ed evento (stesse formule): stop = max(entry − 2×ATR14, entry × 0,92);
    obiettivo = entry + 6×ATR14, limitato al massimo delle chiusure degli ultimi 63 giorni
    solo se quel massimo è sopra `entry` (ruling R9);
    data limite = asof + 30 giorni di calendario. Usa solo le barre con data <= asof.
    Solleva ValueError se non ci sono barre sufficienti per l'ATR.
    """
    if kind == PositionKind.core:
        review = entry * (1 - s.core_review_drawdown)
        exit_level = entry * (1 - s.core_exit_drawdown)
        return Plan(
            kind=kind,
            entry_ref=entry,
            review_level=review,
            exit_level=exit_level,
            currency=currency,
            scenarios=[
                (
                    f"Se scende a {_money(review, currency)} ({_pct(-s.core_review_drawdown, 0)} "
                    "dall'ingresso): rivedi la posizione leggendo la spiegazione."
                ),
                (
                    f"Se scende a {_money(exit_level, currency)} ({_pct(-s.core_exit_drawdown, 0)} "
                    "dall'ingresso): vendi."
                ),
            ],
            stop_order_on_tr=False,
        )

    prices = _upto(prices, asof)
    atr14 = float(atr(prices, ATR_WINDOW).iloc[-1]) if len(prices) else float("nan")
    if pd.isna(atr14):
        raise ValueError(
            f"Dati insufficienti per l'ATR a {ATR_WINDOW} giorni: impossibile fissare lo stop."
        )
    high = float(prices["close"].iloc[-HIGH_WINDOW:].max())
    stop = max(entry - s.opp_atr_stop_mult * atr14, entry * (1 - s.opp_max_stop_pct))
    target = entry + s.opp_atr_target_mult * atr14
    if high > entry:
        target = min(target, high)
    deadline = asof + timedelta(days=s.opp_max_days)
    stop_eur = _round_eur(to_eur(stop, currency, eurusd), ROUND_FLOOR)
    target_eur = _round_eur(to_eur(target, currency, eurusd), ROUND_CEILING)
    return Plan(
        kind=kind,
        entry_ref=entry,
        stop=stop,
        target=target,
        deadline=deadline,
        currency=currency,
        stop_eur=stop_eur,
        target_eur=target_eur,
        scenarios=[
            (
                f"Subito dopo l'acquisto inserisci su Trade Republic un ordine stop-loss a {_num(stop_eur)} € "
                f"({_money(stop, currency)})."
            ),
            f"Se raggiunge l'obiettivo di {_money(target, currency)} (circa {_num(target_eur)} €): vendi.",
            (
                f"Se {_art('il', f'{deadline:%d/%m/%Y}')} non ha toccato né lo stop né l'obiettivo: "
                "vendi alla scadenza."
            ),
        ],
        stop_order_on_tr=True,
    )


# --- Trimestrali -------------------------------------------------------------


def _earnings_soon(e: EarningsInfo, s: StocksSettings) -> bool:
    """Trimestrale entro la finestra e utile non ancora pubblicato.

    `next_ts` resta valorizzato finché Yahoo non riporta l'utile (Task 5), quindi
    `days_to_next` <= 0 vuol dire "data passata, risultati non ancora usciti" (ruling R2);
    oltre `STALE_EVENT_DAYS` giorni dopo la data l'evento è scaduto (ruling R10).
    """
    return (
        e.next_ts is not None
        and e.days_to_next is not None
        and -STALE_EVENT_DAYS <= e.days_to_next <= s.event_window_days
    )


def _event_stale(e: EarningsInfo) -> bool:
    """Data della trimestrale passata da più di `STALE_EVENT_DAYS` giorni, utile non riportato."""
    return (
        e.next_ts is not None
        and e.days_to_next is not None
        and e.days_to_next < -STALE_EVENT_DAYS
    )


def _earnings_when(e: EarningsInfo) -> str:
    day = f"{e.next_ts.date():%d/%m/%Y}"
    if e.days_to_next > 1:
        return f"Trimestrale tra {e.days_to_next} giorni ({day})"
    if e.days_to_next == 1:
        return f"Trimestrale domani ({day})"
    if e.days_to_next == 0:
        return f"Trimestrale oggi ({day})"
    return f"Trimestrale {_art('del', day)}, risultati non ancora pubblicati"


def _moves_text(e: EarningsInfo) -> str:
    expected = (
        f"movimento atteso dalle opzioni ±{_pct(e.expected_move_pct)}"
        if e.expected_move_pct is not None
        else "movimento atteso dalle opzioni non disponibile"
    )
    past = (
        f"movimento medio dopo le ultime {len(e.past_moves_pct)} trimestrali ±{_pct(e.avg_abs_move_pct)}"
        if e.avg_abs_move_pct is not None
        else "movimento medio dopo le trimestrali passate non disponibile"
    )
    return f"{expected}; {past}"


def _wait_for_earnings(e: EarningsInfo, close: float, currency: str) -> Signal:
    """Regola 4: nessun acquisto prima dei risultati, piano a due scenari."""
    moves = _moves_text(e)
    plan = Plan(
        kind=PositionKind.event,
        entry_ref=close,
        currency=currency,
        scenarios=[
            (
                "Se i risultati battono le attese e il titolo regge: dopo la pubblicazione il motore "
                "rivaluta il titolo e può proporre un acquisto, sempre con stop e obiettivo scritti "
                f"prima di comprare ({moves})."
            ),
            (
                "Se deludono: nessun acquisto; il titolo può muoversi di una misura simile "
                f"({moves}) e conviene aspettare che il prezzo si stabilizzi."
            ),
        ],
        # Nessun livello da inserire: prima dei risultati non si compra.
        stop_order_on_tr=False,
    )
    expected = (
        f"Movimento atteso dalle opzioni: ±{_pct(e.expected_move_pct)} (misurato)."
        if e.expected_move_pct is not None
        else "Movimento atteso dalle opzioni: non disponibile."
    )
    past = (
        f"Movimento medio del titolo dopo le ultime {len(e.past_moves_pct)} trimestrali: "
        f"±{_pct(e.avg_abs_move_pct)} (misurato)."
        if e.avg_abs_move_pct is not None
        else "Movimenti storici dopo le trimestrali: non disponibili."
    )
    return Signal(
        action=Action.ATTENDI,
        kind=PositionKind.event,
        plan=plan,
        reasons=[
            (
                f"{_earnings_when(e)}: nessun acquisto prima dei risultati, "
                "perché una trimestrale può muovere il titolo molto in una notte."
            ),
            expected,
            past,
        ],
    )


# --- Posizioni aperte --------------------------------------------------------


def holding_entry(
    holding: Holding, listing_currency: str | None, eurusd: float
) -> tuple[float, list[str]]:
    """Prezzo di carico nella valuta del titolo (quella delle barre) e motivi aggiuntivi.

    Trade Republic mostra e addebita i titoli USA in euro, quindi una posizione in EUR
    su un titolo in USD è il caso normale (ruling R11): il prezzo medio si converte in
    dollari al cambio di oggi (`avg × eurusd`), un'approssimazione dichiarata nei motivi.
    Serve dove occorre un ingresso nella valuta del titolo (es. `build_plan` in T8).
    Altre combinazioni di valute -> ValueError.
    """
    if holding.currency == listing_currency:
        return holding.avg_price, []
    if holding.currency == "EUR" and listing_currency == "USD":
        return holding.avg_price * eurusd, [CONVERTED_ENTRY_REASON]
    raise ValueError(CURRENCY_MISMATCH_REASON)


def _currencies_compatible(holding: Holding, listing_currency: str | None) -> bool:
    return holding.currency == listing_currency or (
        holding.currency == "EUR" and listing_currency == "USD"
    )


def _evaluate_core_holding(
    close: float,
    close_eur: float,
    holding: Holding,
    plan: Plan | None,
    e: EarningsInfo,
    s: StocksSettings,
) -> Signal:
    """Regola 2: soglie dal prezzo medio di carico.

    Il confronto avviene nella valuta della posizione: se è in EUR su un titolo in
    USD (ruling R11) si confronta il prezzo in euro con il prezzo medio in euro.
    """
    avg = holding.avg_price
    cur = holding.currency
    price = close_eur if cur == "EUR" else close
    change = price / avg - 1
    exit_level = avg * (1 - s.core_exit_drawdown)
    review_level = avg * (1 - s.core_review_drawdown)
    where = f"Prezzo {_money(price, cur)}, {_pct(change, signed=True)} rispetto al tuo prezzo medio {_money(avg, cur)}"
    if _le(price, exit_level):
        action = Action.VENDI
        reasons = [
            f"{where}: raggiunta la soglia di uscita ({_pct(-s.core_exit_drawdown, 0)}). Vendi."
        ]
    elif _le(price, review_level):
        action = Action.RIVEDI
        reasons = [
            (
                f"{where}: raggiunta la soglia di revisione ({_pct(-s.core_review_drawdown, 0)}). "
                "Leggi le notizie e verifica se il motivo dell'investimento regge ancora."
            )
        ]
    else:
        action = Action.TIENI
        reasons = [f"{where}: nessuna soglia raggiunta."]
        if _earnings_soon(e, s):
            reasons.append(
                f"{_earnings_when(e)}: possibili movimenti più ampi del solito."
            )
    return Signal(action=action, kind=PositionKind.core, plan=plan, reasons=reasons)


def _evaluate_short_holding(
    close: float, currency: str, holding: Holding, plan: Plan | None, asof: date
) -> Signal:
    """Regola 3: occasione o evento aperto, uscita per stop, obiettivo o scadenza.

    Stop e obiettivo del piano sono nella valuta del titolo, come `close`.
    """
    cur = currency
    kind = holding.kind
    if plan is None:
        return Signal(
            action=Action.RIVEDI,
            kind=kind,
            reasons=[
                "Manca il piano di uscita per questa posizione: stop, obiettivo e scadenza vanno registrati."
            ],
        )
    if plan.stop is not None and _le(close, plan.stop):
        reason = (
            f"Stop raggiunto: prezzo {_money(close, cur)} pari o sotto lo stop {_money(plan.stop, cur)}. "
            "Vendi, se l'ordine stop-loss su Trade Republic non è già scattato."
        )
        return Signal(action=Action.VENDI, kind=kind, plan=plan, reasons=[reason])
    if plan.target is not None and _ge(close, plan.target):
        reason = (
            f"Obiettivo raggiunto: prezzo {_money(close, cur)} pari o sopra l'obiettivo "
            f"{_money(plan.target, cur)}. Vendi."
        )
        return Signal(action=Action.VENDI, kind=kind, plan=plan, reasons=[reason])
    if plan.deadline is not None and asof > plan.deadline:
        reason = f"Scadenza del piano superata ({plan.deadline:%d/%m/%Y}): vendi al prezzo attuale."
        return Signal(action=Action.VENDI, kind=kind, plan=plan, reasons=[reason])

    parts = [f"Prezzo {_money(close, cur)}"]
    if plan.stop is not None:
        parts.append(f"stop {_money(plan.stop, cur)}")
    if plan.target is not None:
        parts.append(f"obiettivo {_money(plan.target, cur)}")
    if plan.deadline is not None:
        parts.append(f"scadenza {plan.deadline:%d/%m/%Y}")
    reason = ", ".join(parts) + ": nessuna condizione di uscita raggiunta."
    return Signal(action=Action.TIENI, kind=kind, plan=plan, reasons=[reason])


# --- Nuovi ingressi ----------------------------------------------------------


def _reward_risk(
    close: float,
    close_eur: float,
    plan: Plan,
    ctx: SizingContext,
    s: StocksSettings,
) -> float:
    """Rapporto guadagno netto / perdita netta di un'occasione (spec §6.3, ruling R8).

    Quantità = importo che `max_order_eur` consente ora per un'occasione / prezzo in euro;
    se l'importo è 0 (acquisto comunque bloccato dopo) si usa l'ordine minimo.
    Tutto nella valuta del titolo: le commissioni in euro si convertono col rapporto
    close / close_eur. Guadagno = max(0, (obiettivo − close)·qty − commissioni) al netto
    delle tasse; perdita = (close − stop)·qty + commissioni (nessuna tassa sulle perdite).
    """
    amount, _ = max_order_eur(
        PositionKind.opportunity, ctx.holdings_eur, ctx.open_opps, s
    )
    qty = (amount if amount > 0 else s.min_order_eur) / close_eur
    fees = round_trip_fees(s) * close / close_eur
    reward = max(0.0, (plan.target - close) * qty - fees) * (1 - s.capital_gains_tax)
    risk = (close - plan.stop) * qty + fees
    return reward / risk if risk > 0 else float("-inf")


def _apply_sizing(sig: Signal, ctx: SizingContext, s: StocksSettings) -> Signal:
    """Dopo le regole: blocco del portafoglio e importo massimo per gli acquisti.

    Un acquisto bloccato diventa NESSUNA_AZIONE: restano tipo e motivi (perché
    sarebbe stato un acquisto), si tolgono piano e importo.
    """
    if sig.action not in _BUY_ACTIONS:
        return sig
    blocked = {"action": Action.NESSUNA_AZIONE, "plan": None, "amount_eur": None}
    if ctx.halted:
        reason = f"portafoglio in calo {_art('del', _pct(s.portfolio_halt_drawdown, 0))}: nuovi acquisti bloccati"
        return sig.model_copy(update={**blocked, "blocked_by": reason})
    amount, reason = max_order_eur(sig.kind, ctx.holdings_eur, ctx.open_opps, s)
    if amount <= 0:
        return sig.model_copy(update={**blocked, "blocked_by": reason})
    return sig.model_copy(update={"amount_eur": amount})


def _evaluate_entry(
    prices: pd.DataFrame,
    f: Fundamentals,
    div_years: int,
    asof: date,
    ctx: SizingContext,
    currency: str,
    close: float,
    close_eur: float,
    eurusd: float,
    s: StocksSettings,
) -> Signal:
    """Regole 5 (ingresso base), 6 (occasione) e 7 (nessuna azione)."""
    closes = prices["close"]
    sma200 = float(sma(closes, SMA_WINDOW).iloc[-1])
    dd = float(drawdown_from_high(closes, HIGH_WINDOW).iloc[-1])
    rsi14 = float(rsi(closes, RSI_WINDOW).iloc[-1])
    atr14 = float(atr(prices, ATR_WINDOW).iloc[-1])
    dd_text = (
        f"Il titolo è sceso {_art('del', _pct(dd))} dal massimo degli ultimi 3 mesi."
    )

    # Regola 5: ingresso base
    quality_ok, quality_reasons = quality_check(f, div_years, s)
    above_sma = not pd.isna(sma200) and close > sma200
    in_core_range = _ge(dd, s.core_pullback_min) and _le(dd, s.core_pullback_max)
    if quality_ok and above_sma and in_core_range:
        plan = build_plan(PositionKind.core, close, prices, asof, currency, eurusd, s)
        reasons = [
            *quality_reasons,
            (
                f"Prezzo {_money(close, currency)} sopra la media a 200 giorni "
                f"({_money(sma200, currency)}): tendenza di lungo periodo positiva."
            ),
            (
                f"{dd_text} È un ritracciamento tra {_art('il', _pct(s.core_pullback_min, 0))} e "
                f"{_art('il', _pct(s.core_pullback_max, 0))}."
            ),
            "Orizzonte indicativo 6–24 mesi, da rivalutare a ogni trimestrale.",
        ]
        return Signal(
            action=Action.COMPRA, kind=PositionKind.core, plan=plan, reasons=reasons
        )

    # Regola 6: occasione
    rejected = None
    if (
        _ge(dd, s.opp_pullback_min)
        and not pd.isna(rsi14)
        and _le(rsi14, s.opp_rsi_max)
        and not pd.isna(atr14)
    ):
        plan = build_plan(
            PositionKind.opportunity, close, prices, asof, currency, eurusd, s
        )
        ratio = _reward_risk(close, close_eur, plan, ctx, s)
        if _ge(ratio, s.opp_min_reward_risk):
            reasons = [
                dd_text,
                f"RSI a 14 giorni a {_num(rsi14, 0)}: vendite eccessive, possibile rimbalzo.",
                (
                    f"Rapporto obiettivo/rischio {_num(ratio)} (commissioni e tasse incluse): "
                    "è una stima, non una probabilità."
                ),
                f"Occasione di breve: durata massima {s.opp_max_days} giorni.",
            ]
            return Signal(
                action=Action.COMPRA,
                kind=PositionKind.opportunity,
                plan=plan,
                reasons=reasons,
            )
        rejected = (
            f"Occasione scartata: rapporto obiettivo/rischio {_num(ratio)} "
            "(commissioni e tasse incluse), "
            f"sotto il minimo di {_num(s.opp_min_reward_risk)}."
        )

    # Regola 7: motivo principale per cui non c'è un ingresso base
    if not quality_ok:
        main = quality_reasons
    elif pd.isna(sma200):
        main = [
            "Storia dei prezzi troppo corta per la media a 200 giorni: nessun ingresso base."
        ]
    elif not above_sma:
        main = [
            (
                f"Prezzo {_money(close, currency)} sotto la media a 200 giorni "
                f"({_money(sma200, currency)}): tendenza di lungo periodo non favorevole."
            )
        ]
    elif not _ge(dd, s.core_pullback_min):
        main = [
            (
                f"Il titolo è vicino al massimo degli ultimi 3 mesi (distanza {_pct(dd)}): "
                f"per un ingresso base serve un calo di almeno {_art('il', _pct(s.core_pullback_min, 0))}."
            )
        ]
    else:
        main = [
            f"{dd_text} Oltre {_art('il', _pct(s.core_pullback_max, 0))}: troppo per un ingresso base."
        ]
    reasons = [rejected, *main] if rejected else main
    return Signal(action=Action.NESSUNA_AZIONE, reasons=reasons)


# --- Punto di ingresso -------------------------------------------------------


def evaluate(
    prices: pd.DataFrame,
    f: Fundamentals,
    div_years: int,
    e: EarningsInfo,
    holding: Holding | None,
    plan: Plan | None,
    asof: date,
    ctx: SizingContext,
    eurusd: float,
    s: StocksSettings,
) -> Signal:
    """Azione per un titolo alla data `asof`: la prima regola che si applica vince.

    1. dati assenti o vecchi -> DATI_MANCANTI; 2. base posseduta: soglie dal prezzo medio;
    3. occasione/evento posseduto: stop, obiettivo, scadenza; 4. trimestrale imminente
    -> ATTENDI; 5. ingresso base; 6. occasione; 7. NESSUNA_AZIONE.
    Poi gli acquisti passano dal blocco del portafoglio e dai limiti di importo.
    `prices`: indice `datetime.date`, colonne open/high/low/close, nella valuta di
    quotazione `f.currency`. Una posizione può essere nella stessa valuta o in EUR su un
    titolo in USD (prezzo di Trade Republic); altrimenti DATI_MANCANTI.
    """
    prices = _upto(prices, asof)

    # Regola 1: dati mancanti o vecchi
    if prices.empty:
        return Signal(
            action=Action.DATI_MANCANTI,
            reasons=["Nessun prezzo disponibile per questo titolo."],
        )
    last = prices.index[-1]
    if (asof - last).days > MAX_STALE_DAYS:
        return Signal(
            action=Action.DATI_MANCANTI,
            reasons=[
                (
                    f"Ultimo prezzo {_art('del', f'{last:%d/%m/%Y}')}, più vecchio di {MAX_STALE_DAYS} giorni: "
                    "dati non aggiornati."
                )
            ],
        )
    # Le barre sono sempre nella valuta di quotazione del titolo (ruling R11).
    currency = f.currency
    if holding is not None and not _currencies_compatible(holding, currency):
        return Signal(action=Action.DATI_MANCANTI, reasons=[CURRENCY_MISMATCH_REASON])
    if currency is None:
        return Signal(
            action=Action.DATI_MANCANTI, reasons=["Valuta del titolo non disponibile."]
        )
    close = float(prices["close"].iloc[-1])
    if pd.isna(close):
        return Signal(
            action=Action.DATI_MANCANTI,
            reasons=["Prezzo di chiusura dell'ultima barra non disponibile."],
        )
    try:
        close_eur = to_eur(close, currency, eurusd)
    except ValueError:
        return Signal(
            action=Action.DATI_MANCANTI, reasons=[f"Valuta {currency} non supportata."]
        )

    # Regole 2 e 3: posizione aperta
    if holding is not None:
        if holding.kind == PositionKind.core:
            sig = _evaluate_core_holding(close, close_eur, holding, plan, e, s)
        else:
            sig = _evaluate_short_holding(close, currency, holding, plan, asof)
    # Regola 4: trimestrale imminente, nessun acquisto prima dei risultati
    elif _earnings_soon(e, s):
        return _wait_for_earnings(e, close, currency)
    # Regole 5-7, poi blocchi e importi
    else:
        sig = _evaluate_entry(
            prices, f, div_years, asof, ctx, currency, close, close_eur, eurusd, s
        )
        sig = _apply_sizing(sig, ctx, s)

    if _event_stale(e):
        sig = sig.model_copy(update={"reasons": [*sig.reasons, STALE_EVENT_REASON]})
    return sig
