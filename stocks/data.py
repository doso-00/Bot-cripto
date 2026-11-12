"""Livello dati yfinance: storico, cambio EUR/USD, fondamentali, dividendi.

Il client (`yf` per default) è iniettabile: i test usano oggetti finti e non
toccano la rete. Le funzioni che leggono da Yahoo ritentano con attese crescenti;
se dopo i tentativi Yahoo continua a fallire l'eccezione risale al chiamante
(Task 8), che segna quel titolo `data_ok = False` e prosegue con gli altri.
"""

import logging
import time
from collections.abc import Callable
from datetime import date, datetime
from datetime import time as dtime
from typing import TypeVar
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from stocks.models import Fundamentals

logger = logging.getLogger(__name__)

T = TypeVar("T")

HISTORY_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}

# Margine dopo la chiusura prima di fidarsi della barra del giorno (Yahoo ritarda
# l'aggiornamento della barra finale).
CLOSE_MARGIN_MINUTES = 20

_US = ("America/New_York", dtime(16, 0))
_ROME = ("Europe/Rome", dtime(17, 30))
_BERLIN = ("Europe/Berlin", dtime(17, 30))
_PARIS = ("Europe/Paris", dtime(17, 30))
# Amsterdam non è nel brief come fuso a parte: usa l'ora di Parigi (stesso fuso e orario)
_SUFFIX_EXCHANGES = {".MI": _ROME, ".DE": _BERLIN, ".PA": _PARIS, ".AS": _PARIS}


def with_retry(
    fn: Callable[[], T],
    attempts: int = 4,
    base_delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Esegue `fn` con tentativi e attese raddoppiate (2, 4, 8 s); rilancia l'ultimo errore."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "Tentativo %d/%d fallito (%s); riprovo tra %.0f s",
                attempt + 1,
                attempts,
                exc,
                delay,
            )
            sleep(delay)
    raise AssertionError("irraggiungibile")  # attempts <= 0


def exchange_for_ticker(ticker: str) -> tuple[str, dtime]:
    """Fuso e orario di chiusura della borsa dal suffisso del ticker (nessun suffisso = USA)."""
    if "." not in ticker:
        return _US
    suffix = "." + ticker.rsplit(".", 1)[1].upper()
    if suffix in _SUFFIX_EXCHANGES:
        return _SUFFIX_EXCHANGES[suffix]
    logger.warning(
        "Suffisso borsa sconosciuto per %s: trattato come titolo USA", ticker
    )
    return _US


def drop_incomplete_bar(
    df: pd.DataFrame, now: datetime, exchange_tz: str, close_time: dtime
) -> pd.DataFrame:
    """Scarta l'ultima barra se è di oggi (nel fuso della borsa) e il mercato non è chiuso.

    Una barra è considerata definitiva solo dopo `close_time` + 20 minuti. `df` ha
    come indice le date di borsa (`datetime.date`); `now` deve essere tz-aware.
    """
    if df.empty:
        return df
    local_now = now.astimezone(ZoneInfo(exchange_tz))
    close_dt = datetime.combine(local_now.date(), close_time, tzinfo=local_now.tzinfo)
    market_closed = local_now >= close_dt + pd.Timedelta(minutes=CLOSE_MARGIN_MINUTES)
    if df.index[-1] >= local_now.date() and not market_closed:
        return df.iloc[:-1]
    return df


def _normalize_history(raw: pd.DataFrame) -> pd.DataFrame:
    """Colonne in minuscolo e indice `date` (giorno di borsa) al posto del timestamp tz-aware."""
    if raw is None or raw.empty:
        return pd.DataFrame(
            columns=HISTORY_COLUMNS, index=pd.Index([], name="date"), dtype=float
        )
    df = raw.rename(columns=_RENAME)[HISTORY_COLUMNS].copy()
    # il tz dell'indice è già quello della borsa: .date dà il giorno di borsa
    df.index = pd.Index([ts.date() for ts in raw.index], name="date")
    # Yahoo a volte restituisce righe con prezzi NaN (es. barra del giorno non ancora valorizzata)
    return df.dropna(subset=["close"])


def fetch_history(ticker: str, years: int, now: datetime, client=yf) -> pd.DataFrame:
    """Storico giornaliero non aggiustato; la barra di oggi è scartata se il mercato è aperto.

    DataFrame vuoto (stesse colonne) se Yahoo non restituisce dati.
    """
    raw = with_retry(
        lambda: client.Ticker(ticker).history(
            period=f"{years}y", interval="1d", auto_adjust=False
        )
    )
    df = _normalize_history(raw)
    tz, close_time = exchange_for_ticker(ticker)
    return drop_incomplete_bar(df, now, tz, close_time)


def fetch_fx_eurusd(now: datetime, client=yf) -> float:
    """Ultimo close chiuso di EURUSD=X: dollari per 1 euro."""
    raw = with_retry(
        lambda: client.Ticker("EURUSD=X").history(
            period="1mo", interval="1d", auto_adjust=False
        )
    )
    df = _normalize_history(raw)
    # il cambio non ha una chiusura di borsa: la barra di oggi è sempre parziale
    df = drop_incomplete_bar(df, now, "UTC", dtime(23, 59))
    if df.empty:
        raise ValueError("Nessun dato per EURUSD=X da Yahoo")
    return float(df["close"].iloc[-1])


def _num(value) -> float | None:
    """Numero finito oppure None (campi assenti o non numerici di `info`)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if pd.isna(value) or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def _to_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def fetch_fundamentals(ticker: str, client=yf) -> Fundamentals:
    """Fondamentali da `Ticker.info` e data di stacco dividendo da `Ticker.calendar`.

    Unità di yfinance mantenute: `dividendYield` già in percentuale (0.58 = 0,58%),
    `payoutRatio` e `returnOnEquity` frazioni, `debtToEquity` in percento.
    Campi mancanti -> None.
    """
    t = client.Ticker(ticker)
    info = with_retry(lambda: t.info) or {}
    try:
        calendar = with_retry(lambda: t.calendar) or {}
    except Exception as exc:  # noqa: BLE001 - il calendario è accessorio, non deve far fallire i fondamentali
        logger.warning("Calendario non disponibile per %s: %s", ticker, exc)
        calendar = {}
    ex_div = (
        _to_date(calendar.get("Ex-Dividend Date"))
        if isinstance(calendar, dict)
        else None
    )
    return Fundamentals(
        name=info.get("longName") or info.get("shortName"),
        currency=info.get("currency"),
        trailing_pe=_num(info.get("trailingPE")),
        forward_pe=_num(info.get("forwardPE")),
        dividend_yield_pct=_num(info.get("dividendYield")),
        payout_ratio=_num(info.get("payoutRatio")),
        debt_to_equity=_num(info.get("debtToEquity")),
        roe=_num(info.get("returnOnEquity")),
        market_cap=_num(info.get("marketCap")),
        ex_dividend_date=ex_div,
    )


def fetch_dividends(ticker: str, client=yf) -> pd.Series:
    """Dividendi per singolo pagamento (Series con indice temporale; vuota se assenti)."""
    divs = with_retry(lambda: client.Ticker(ticker).dividends)
    return divs if divs is not None else pd.Series(dtype=float)


def dividend_years_without_cut(divs: pd.Series, asof: date) -> int:
    """Anni solari completi consecutivi, a ritroso da quello prima di `asof`, senza taglio.

    Un anno è "senza taglio" se la somma dei dividendi è >= a quella dell'anno
    precedente. L'anno in corso è incompleto e non conta; un anno senza dividendi
    interrompe la serie.
    """
    if divs is None or divs.empty:
        return 0
    annual = divs.groupby(divs.index.year).sum()
    year = asof.year - 1
    count = 0
    while year - 1 in annual.index and year in annual.index:
        if annual[year] + 1e-9 < annual[year - 1]:
            break
        count += 1
        year -= 1
    return count


def to_eur(amount: float, currency: str, eurusd: float) -> float:
    """Converte in euro: EUR invariato, USD diviso per `eurusd`; altre valute non supportate."""
    if currency == "EUR":
        return amount
    if currency == "USD":
        return amount / eurusd
    raise ValueError(f"Valuta non supportata: {currency}")
