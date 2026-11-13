"""Indicatori tecnici calcolati solo su barre giornaliere chiuse."""

import numpy as np
import pandas as pd


def sma(close: pd.Series, n: int) -> pd.Series:
    """Media mobile semplice su n barre (NaN finché non ci sono n valori)."""
    return close.rolling(n).mean()


def _wilder(values: np.ndarray, n: int, start: int) -> np.ndarray:
    """Media di Wilder: seme = media dei primi n valori da `start`, poi (prev*(n-1)+x)/n."""
    out = np.full(len(values), np.nan)
    end = start + n
    if len(values) < end:
        return out
    out[end - 1] = values[start:end].mean()
    for i in range(end, len(values)):
        out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range di Wilder (colonne high, low, close)."""
    prev_close = df["close"].shift(1)
    # La prima barra non ha chiusura precedente: il suo true range è high-low
    # (skipna in max ignora il NaN).
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return pd.Series(_wilder(tr.to_numpy(dtype=float), n, 0), index=df.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI di Wilder (0-100). Solo rialzi -> 100; serie piatta -> 50."""
    delta = close.diff().to_numpy(dtype=float)
    gain = np.where(np.isnan(delta), np.nan, np.maximum(delta, 0.0))
    loss = np.where(np.isnan(delta), np.nan, np.maximum(-delta, 0.0))
    # La prima variazione è alla barra 1: il seme parte da lì.
    avg_gain = _wilder(gain, n, 1)
    avg_loss = _wilder(loss, n, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - 100.0 / (1.0 + rs)
    out = np.where((avg_loss == 0) & (avg_gain > 0), 100.0, out)
    out = np.where((avg_loss == 0) & (avg_gain == 0), 50.0, out)
    return pd.Series(out, index=close.index)


def drawdown_from_high(close: pd.Series, window: int = 63) -> pd.Series:
    """Distanza dal massimo degli ultimi `window` giorni (>= 0; 0.12 = -12%)."""
    high = close.rolling(window, min_periods=1).max()
    return 1.0 - close / high


def trend_closes(prices: pd.DataFrame) -> pd.Series:
    """Chiusure per gli indicatori di tendenza (SMA200, massimo/ritracciamento, RSI).

    Usa `adj_close` (aggiustato per dividendi e frazionamenti, ruling R25) riportato alla
    scala del close grezzo dell'ultima barra: lo stacco di un dividendo non sembra un calo
    e la media resta confrontabile con il prezzo di oggi. Se `adj_close` manca o ha
    valori non utilizzabili si usa il close grezzo.
    """
    close = prices["close"]
    if "adj_close" not in prices.columns or close.empty:
        return close
    adj = prices["adj_close"]
    if adj.isna().any() or not (adj > 0).all():
        return close
    return adj * (float(close.iloc[-1]) / float(adj.iloc[-1]))
