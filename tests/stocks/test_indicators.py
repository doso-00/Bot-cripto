"""Test degli indicatori tecnici."""

import pandas as pd
import pytest

from stocks.indicators import atr, drawdown_from_high, rsi, sma


def test_sma_matches_manual():
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(close, 3)
    assert out.iloc[:2].isna().all()
    assert out.iloc[2:].tolist() == pytest.approx([2.0, 3.0, 4.0])


def test_atr_wilder_known_values():
    # Serie a mano: TR = [2, 3, 3] con n=2.
    # ATR(2) alla seconda barra = media dei primi 2 TR = 2.5; poi Wilder: (2.5*1 + 3)/2 = 2.75
    df = pd.DataFrame(
        {
            "high": [10.0, 12.0, 13.0, 14.0],
            "low": [8.0, 9.0, 11.0, 11.0],
            "close": [9.0, 11.0, 12.0, 13.0],
        }
    )
    # TR: barra0 = 2 (high-low); barra1 = max(3, |12-9|, |9-9|) = 3;
    # barra2 = max(2, |13-11|, |11-11|) = 2; barra3 = max(3, |14-12|, |11-12|) = 3
    out = atr(df, 2)
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == pytest.approx(2.5)
    assert out.iloc[2] == pytest.approx((2.5 * 1 + 2) / 2)
    assert out.iloc[3] == pytest.approx((out.iloc[2] * 1 + 3) / 2)


def test_rsi_all_up_is_100():
    close = pd.Series(range(1, 40), dtype=float)
    out = rsi(close, 14)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_drawdown_zero_at_new_high():
    close = pd.Series([10.0, 12.0, 11.0, 9.0, 13.0])
    out = drawdown_from_high(close, window=3)
    assert out.iloc[-1] == pytest.approx(0.0)
    assert out.iloc[3] == pytest.approx(1 - 9 / 12)
    assert (out >= 0).all()
