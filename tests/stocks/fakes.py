"""Oggetti finti di `yfinance` per i test (nessun accesso alla rete).

Imitano le forme verificate su dati reali: `history()` con indice DatetimeIndex
tz-aware nel fuso della borsa e colonne Open, High, Low, Close, Adj Close, Volume,
Dividends, Stock Splits; `info` dict; `calendar` dict; `dividends` Series tz-aware.
Task 5: `get_earnings_dates(limit)` (DataFrame indicizzato da "Earnings Date" tz-aware,
colonne "EPS Estimate", "Reported EPS", "Surprise(%)"), `options` (tuple di scadenze
'YYYY-MM-DD') e `option_chain(expiry)` (oggetto con `.calls` e `.puts`).
Task 8: `info_error` fa sollevare l'accesso a `info` (Yahoo che non risponde sui fondamentali).
Ondata finale: `info_sequence` restituisce un `info` diverso a ogni accesso (l'ultimo si
ripete), `info_calls` conta gli accessi.
"""

from types import SimpleNamespace

import pandas as pd


def make_history(
    dates: list[str],
    closes: list[float],
    tz: str = "America/New_York",
) -> pd.DataFrame:
    """Storico finto nella forma di `Ticker.history(auto_adjust=False)`."""
    index = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize(tz)
    return pd.DataFrame(
        {
            "Open": [c - 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Adj Close": [c * 0.99 for c in closes],
            "Volume": [1000 + i for i in range(len(closes))],
            "Dividends": [0.0] * len(closes),
            "Stock Splits": [0.0] * len(closes),
        },
        index=index,
    )


def make_dividends(
    by_date: dict[str, float], tz: str = "America/New_York"
) -> pd.Series:
    """Serie finta di dividendi per pagamento (indice tz-aware)."""
    index = pd.DatetimeIndex(pd.to_datetime(list(by_date))).tz_localize(tz)
    return pd.Series(list(by_date.values()), index=index, name="Dividends")


def make_earnings(
    rows: list[tuple[str, float | None, float | None, float | None]],
    tz: str = "America/New_York",
) -> pd.DataFrame:
    """Date utili finte: righe (timestamp locale, stima, utile reale, sorpresa %).

    Come Yahoo, l'ordine è dal più recente (evento futuro) al più vecchio.
    """
    index = pd.DatetimeIndex(pd.to_datetime([r[0] for r in rows])).tz_localize(tz)
    index.name = "Earnings Date"
    return pd.DataFrame(
        {
            "EPS Estimate": [r[1] for r in rows],
            "Reported EPS": [r[2] for r in rows],
            "Surprise(%)": [r[3] for r in rows],
        },
        index=index,
        dtype=float,
    )


def make_chain(
    calls: list[tuple[float, float, float, float]],
    puts: list[tuple[float, float, float, float]],
) -> SimpleNamespace:
    """Catena opzioni finta; le righe sono (strike, bid, ask, lastPrice)."""

    def frame(rows):
        return pd.DataFrame(
            rows, columns=["strike", "bid", "ask", "lastPrice"], dtype=float
        )

    return SimpleNamespace(calls=frame(calls), puts=frame(puts))


class FakeTicker:
    """Dati preconfezionati per un simbolo; `fail_times` simula errori transitori."""

    def __init__(
        self,
        history: pd.DataFrame | None = None,
        info: dict | None = None,
        calendar: dict | None = None,
        dividends: pd.Series | None = None,
        fail_times: int = 0,
        earnings: pd.DataFrame | None = None,
        options: tuple[str, ...] = (),
        chains: dict | None = None,
        earnings_error: Exception | None = None,
        info_error: Exception | None = None,
        info_sequence: list[dict] | None = None,
    ):
        self._history = history if history is not None else pd.DataFrame()
        self._info = info if info is not None else {}
        self._info_error = info_error
        self._info_sequence = list(info_sequence) if info_sequence else None
        self.info_calls = 0
        self.calendar = calendar if calendar is not None else {}
        self.dividends = dividends if dividends is not None else pd.Series(dtype=float)
        self.fail_times = fail_times
        self.history_calls: list[dict] = []
        self._earnings = earnings
        self.options = options
        self._chains = chains or {}
        self._earnings_error = earnings_error
        self.earnings_calls: list[int] = []

    @property
    def info(self) -> dict:
        self.info_calls += 1
        if self._info_error is not None:
            raise self._info_error
        if self._info_sequence:
            return self._info_sequence.pop(0) if len(self._info_sequence) > 1 else self._info_sequence[0]
        return self._info

    def history(self, **kwargs) -> pd.DataFrame:
        self.history_calls.append(kwargs)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("errore transitorio simulato")
        return self._history.copy()

    def get_earnings_dates(self, limit: int = 12) -> pd.DataFrame | None:
        self.earnings_calls.append(limit)
        if self._earnings_error is not None:
            raise self._earnings_error
        return None if self._earnings is None else self._earnings.copy()

    def option_chain(self, expiry: str) -> SimpleNamespace:
        return self._chains[expiry]


class FakeYF:
    """Sostituto del modulo `yf`: `client.Ticker(symbol)`. Simbolo ignoto = dati vuoti."""

    def __init__(self, tickers: dict[str, FakeTicker] | None = None):
        self.tickers = tickers or {}

    def Ticker(self, symbol: str) -> FakeTicker:
        return self.tickers.get(symbol, FakeTicker())
