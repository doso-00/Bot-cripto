"""Funzioni pure per indicatori tecnici (SMA, EMA, RSI, ATR, Donchian).

Ogni indicatore al tempo t usa solo valori fino a t (finestre all'indietro): è la
condizione perché le strategie non abbiano look-ahead.
"""

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Media mobile semplice; NaN finché non ci sono `period` valori."""
    if period < 1:
        raise ValueError("period deve essere >= 1")
    return series.rolling(window=period, min_periods=period).mean()
