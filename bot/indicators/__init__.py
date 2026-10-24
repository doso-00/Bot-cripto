"""Funzioni pure per indicatori tecnici (SMA, RSI, canali di Donchian).

Ogni indicatore al tempo t usa solo valori fino a t (finestre all'indietro): è la
condizione perché le strategie non abbiano look-ahead.
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Media mobile semplice; NaN finché non ci sono `period` valori."""
    if period < 1:
        raise ValueError("period deve essere >= 1")
    return series.rolling(window=period, min_periods=period).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI di Wilder (la definizione originale, usata da quasi tutte le piattaforme).

    Prima media: media semplice dei primi `period` guadagni e perdite; poi
    media_t = (media_{t-1} * (period - 1) + valore_t) / period. NaN finché non ci sono
    `period` variazioni.
    """
    if period < 2:
        raise ValueError("period deve essere >= 2")
    delta = close.diff().to_numpy()
    gains, losses = np.clip(delta, 0, None), np.clip(-delta, 0, None)
    out = np.full(len(close), np.nan)
    if len(close) <= period:
        return pd.Series(out, index=close.index)
    avg_gain, avg_loss = gains[1 : period + 1].mean(), losses[1 : period + 1].mean()
    for i in range(period, len(close)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            out[i] = 100 - 100 / (1 + avg_gain / avg_loss)
    return pd.Series(out, index=close.index)


def donchian_upper(high: pd.Series, period: int) -> pd.Series:
    """Massimo dei `period` massimi PRECEDENTI (esclusa la candela corrente).

    Si esclude la candela corrente perché la "rottura" si misura confrontando il close
    di oggi con i massimi già noti prima di oggi.
    """
    return high.shift(1).rolling(window=period, min_periods=period).max()


def donchian_lower(low: pd.Series, period: int) -> pd.Series:
    """Minimo dei `period` minimi precedenti (esclusa la candela corrente)."""
    return low.shift(1).rolling(window=period, min_periods=period).min()
