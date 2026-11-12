"""Oggetti finti di `yfinance` per i test (nessun accesso alla rete).

Imitano le forme verificate su dati reali: `history()` con indice DatetimeIndex
tz-aware nel fuso della borsa e colonne Open, High, Low, Close, Adj Close, Volume,
Dividends, Stock Splits; `info` dict; `calendar` dict; `dividends` Series tz-aware.
Per estenderli (Task 5: earnings, opzioni) basta aggiungere campi a `FakeTicker`.
"""

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


class FakeTicker:
    """Dati preconfezionati per un simbolo; `fail_times` simula errori transitori."""

    def __init__(
        self,
        history: pd.DataFrame | None = None,
        info: dict | None = None,
        calendar: dict | None = None,
        dividends: pd.Series | None = None,
        fail_times: int = 0,
    ):
        self._history = history if history is not None else pd.DataFrame()
        self.info = info if info is not None else {}
        self.calendar = calendar if calendar is not None else {}
        self.dividends = dividends if dividends is not None else pd.Series(dtype=float)
        self.fail_times = fail_times
        self.history_calls: list[dict] = []

    def history(self, **kwargs) -> pd.DataFrame:
        self.history_calls.append(kwargs)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("errore transitorio simulato")
        return self._history.copy()


class FakeYF:
    """Sostituto del modulo `yf`: `client.Ticker(symbol)`. Simbolo ignoto = dati vuoti."""

    def __init__(self, tickers: dict[str, FakeTicker] | None = None):
        self.tickers = tickers or {}

    def Ticker(self, symbol: str) -> FakeTicker:
        return self.tickers.get(symbol, FakeTicker())
