"""Trimestrali: date utili da Yahoo, movimenti storici e movimento atteso dalle opzioni.

Convenzioni: i movimenti sono frazioni (0.09 = 9%); `surprise_pct` resta in
percentuale come la dà Yahoo. I timestamp delle trimestrali sono tz-aware nel
fuso della borsa USA ("America/New_York"); una pubblicazione alle 16:00 locali o
dopo è "dopo la chiusura" e la reazione del prezzo cade sul giorno di borsa dopo.
"""

import logging
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from stocks.data import with_retry
from stocks.models import EarningsInfo

logger = logging.getLogger(__name__)

EARNINGS_COLUMNS = ["ts", "eps_estimate", "eps_reported", "surprise_pct"]
EARNINGS_LIMIT = 12
MAX_PAST_MOVES = 8
# Ora locale dalla quale una pubblicazione conta come "dopo la chiusura"
AFTER_CLOSE_HOUR = 16
# Finestra in cui una pubblicazione è "uscita dall'ultimo run"
RECENT_REPORT_HOURS = 36
# Giorni dopo la data in cui un evento senza utile riportato resta "il prossimo"
# (ruling R20): copre la finestra di 3 giorni del motore regole (R10) e il giorno dopo,
# in cui il motore lo segnala come scaduto.
UNREPORTED_KEEP_DAYS = 4

_RENAME = {
    "EPS Estimate": "eps_estimate",
    "Reported EPS": "eps_reported",
    "Surprise(%)": "surprise_pct",
}


def _empty_earnings() -> pd.DataFrame:
    return pd.DataFrame(columns=EARNINGS_COLUMNS)


def fetch_earnings(ticker: str, client=yf) -> pd.DataFrame:
    """Date utili (passate e future) con colonne `ts`, `eps_estimate`, `eps_reported`, `surprise_pct`.

    Ordinate dalla più recente (di solito l'evento futuro). Vuoto se Yahoo non ha
    dati: titoli europei o ticker senza date utili non devono far fallire il run,
    quindi anche gli errori di rete, dopo i tentativi, danno un DataFrame vuoto.
    """
    try:
        raw = with_retry(
            lambda: client.Ticker(ticker).get_earnings_dates(limit=EARNINGS_LIMIT)
        )
    except Exception as exc:  # noqa: BLE001 - le trimestrali sono accessorie
        logger.warning("Trimestrali non disponibili per %s: %s", ticker, exc)
        return _empty_earnings()
    if raw is None or raw.empty:
        return _empty_earnings()
    df = raw.rename(columns=_RENAME)[list(_RENAME.values())].copy()
    df.insert(0, "ts", list(raw.index))
    return df.sort_values("ts", ascending=False).reset_index(drop=True)


def _event_move(ts: pd.Timestamp, prices: pd.DataFrame) -> float | None:
    """Movimento del close attorno alla pubblicazione, None se mancano i prezzi."""
    day = ts.date()
    closes = prices["close"]
    if ts.hour >= AFTER_CLOSE_HOUR:
        # dopo la chiusura: reazione sul giorno di borsa successivo
        if day not in closes.index:
            return None
        later = closes.index[closes.index > day]
        if len(later) == 0:
            return None
        return float(closes[later[0]] / closes[day] - 1)
    # prima dell'apertura: reazione sullo stesso giorno
    if day not in closes.index:
        return None
    earlier = closes.index[closes.index < day]
    if len(earlier) == 0:
        return None
    return float(closes[day] / closes[earlier[-1]] - 1)


def earnings_info(
    edf: pd.DataFrame, prices: pd.DataFrame, asof_now: datetime
) -> EarningsInfo:
    """Sintesi trimestrali: prossimo evento, ultima uscita e movimenti storici.

    Il prossimo evento è il primo (in ordine di data) senza `eps_reported` e con data
    al massimo `UNREPORTED_KEEP_DAYS` giorni fa (nel fuso dell'evento): un evento con
    data passata ma utile non ancora pubblicato resta il prossimo (la sera della
    trimestrale, prima che Yahoo aggiorni l'EPS reale, o se Yahoo tarda qualche giorno).
    `prices` ha indice `date` e colonna `close`.
    `expected_move_pct` resta None: lo imposta il chiamante (serve la rete).
    """
    if edf is None or edf.empty:
        return EarningsInfo()
    df = edf.sort_values("ts").reset_index(drop=True)
    reported = df[df["eps_reported"].notna()]
    recent_enough = [
        (ts.date() - asof_now.astimezone(ts.tzinfo).date()).days >= -UNREPORTED_KEEP_DAYS
        for ts in df["ts"]
    ]
    pending = df[df["eps_reported"].isna() & pd.Series(recent_enough, index=df.index)]

    next_ts = days_to_next = None
    if not pending.empty:
        next_ts = pending["ts"].iloc[0].to_pydatetime()
        days_to_next = (
            next_ts.date() - asof_now.astimezone(next_ts.tzinfo).date()
        ).days

    last_ts = last_surprise = last_after_close = None
    recent = False
    if not reported.empty:
        last = reported.iloc[-1]
        last_ts = last["ts"].to_pydatetime()
        last_after_close = last_ts.hour >= AFTER_CLOSE_HOUR
        last_surprise = (
            None if pd.isna(last["surprise_pct"]) else float(last["surprise_pct"])
        )
        recent = (
            timedelta(0) <= asof_now - last_ts <= timedelta(hours=RECENT_REPORT_HOURS)
        )

    moves: list[float] = []
    for ts in reported["ts"].iloc[::-1]:  # dal più recente
        move = _event_move(ts, prices)
        if move is not None:
            moves.append(move)
        if len(moves) == MAX_PAST_MOVES:
            break
    avg_abs = sum(abs(m) for m in moves) / len(moves) if moves else None

    return EarningsInfo(
        next_ts=next_ts,
        days_to_next=days_to_next,
        reported_since_last_run=recent,
        last_ts=last_ts,
        last_after_close=last_after_close,
        last_surprise_pct=last_surprise,
        past_moves_pct=moves,
        avg_abs_move_pct=avg_abs,
    )


def _mid(row: pd.Series) -> float | None:
    """Prezzo medio bid/ask; se bid o ask è 0 (mercato chiuso) usa l'ultimo prezzo."""
    bid, ask = row["bid"], row["ask"]
    if pd.notna(bid) and pd.notna(ask) and bid > 0 and ask > 0:
        return float((bid + ask) / 2)
    last = row["lastPrice"]
    if pd.notna(last) and last > 0:
        return float(last)
    return None


def _row_at_strike(side: pd.DataFrame, strike: float) -> pd.Series | None:
    rows = side[side["strike"] == strike]
    return None if rows.empty else rows.iloc[0]


def expected_move_pct(
    ticker: str, event_date: date, spot: float, client=yf
) -> float | None:
    """Movimento atteso dal prezzo dello straddle ATM sulla prima scadenza >= `event_date`.

    (mid call + mid put) / spot allo strike più vicino a `spot`. None se il titolo
    non ha opzioni (di solito i titoli europei) o i prezzi non sono utilizzabili.
    """
    try:
        t = client.Ticker(ticker)
        expiries = [
            e for e in with_retry(lambda: t.options) if e >= event_date.isoformat()
        ]
        if not expiries:
            return None
        chain = with_retry(lambda: t.option_chain(min(expiries)))
    except Exception as exc:  # noqa: BLE001 - il movimento atteso è accessorio
        logger.warning("Opzioni non disponibili per %s: %s", ticker, exc)
        return None
    calls, puts = chain.calls, chain.puts
    if calls.empty or puts.empty or spot <= 0:
        return None
    strike = float(calls.loc[(calls["strike"] - spot).abs().idxmin(), "strike"])
    call, put = _row_at_strike(calls, strike), _row_at_strike(puts, strike)
    if call is None or put is None:
        return None
    mids = _mid(call), _mid(put)
    if None in mids:
        return None
    return (mids[0] + mids[1]) / spot
