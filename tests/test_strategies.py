"""Ogni strategia su un piccolo dataset sintetico con risultato noto (brief §5)."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from bot.indicators import donchian_lower, donchian_upper, rsi, sma
from bot.strategies import CANDIDATES, STRATEGIES, compute_targets, load_grid, load_params
from bot.strategies.donchian_breakout import DonchianParams
from bot.strategies.rsi_mean_reversion import RsiMeanReversionParams
from bot.strategies.sma_cross import SmaCrossParams


def frame(close, high=None, low=None) -> pd.DataFrame:
    close = pd.Series(close, dtype="float64")
    return pd.DataFrame({
        "open": close, "close": close,
        "high": close if high is None else pd.Series(high, dtype="float64"),
        "low": close if low is None else pd.Series(low, dtype="float64"),
        "volume": 1.0,
    })


def test_sma():
    assert sma(pd.Series([1.0, 2, 3, 4]), 2).tolist()[1:] == [1.5, 2.5, 3.5]


def test_rsi_matches_wilder_reference():
    # Esempio classico di Wilder/StockCharts: 15 close, RSI(14) = 70,46 sull'ultimo
    close = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
                       46.08, 45.89, 46.03, 45.61, 46.28, 46.28])
    values = rsi(close, 14)
    assert values.iloc[:14].isna().all()
    assert values.iat[14] == pytest.approx(70.46, abs=0.01)


def test_rsi_extremes():
    assert rsi(pd.Series(np.arange(1.0, 30.0)), 14).iat[-1] == 100.0
    assert rsi(pd.Series(np.arange(30.0, 1.0, -1)), 14).iat[-1] == 0.0


def test_donchian_excludes_current_candle():
    high = pd.Series([1.0, 3, 2, 5])
    assert donchian_upper(high, 2).tolist()[2:] == [3.0, 3.0]
    assert donchian_lower(high, 2).tolist()[2:] == [1.0, 2.0]


def test_sma_cross_known_result():
    close = [10.0] * 5 + [11, 12, 13] + [9, 8, 7]
    out = compute_targets(STRATEGIES["sma_cross"], frame(close), SmaCrossParams(fast=2, slow=4))
    # SMA2 > SMA4 dalla candela 5 alla 7; alla 8 (close 9): SMA2 = 11 < SMA4 = 11,25
    assert out.tolist() == [0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0]


def test_donchian_known_result():
    # Entrata alla candela 4 (12 > massimo delle 3 precedenti = 11; alla 3, 11 non supera 11);
    # uscita alla 7 (12 < minimo delle 2 precedenti = 13)
    close = [10, 11, 10, 11, 12, 13, 13, 12, 9, 10]
    out = compute_targets(STRATEGIES["donchian_breakout"], frame(close), DonchianParams(n_entry=3, n_exit=2))
    assert out.tolist() == [0, 0, 0, 0, 1, 1, 1, 0, 0, 0]


def test_rsi_mean_reversion_exits_on_high_or_after_max_hold():
    # Discesa che porta l'RSI(2) sotto 30, poi forte rialzo: esce quando supera 70
    close = [10, 10.5, 11, 10, 9, 9.5, 12, 13, 13.5]
    params = RsiMeanReversionParams(period=2, low=30, high=70, max_hold=10)
    values = rsi(pd.Series(close, dtype="float64"), 2).tolist()
    out = compute_targets(STRATEGIES["rsi_mean_reversion"], frame(close), params).tolist()
    entry = next(i for i in range(1, len(values)) if values[i] < 30 <= values[i - 1])
    exit_ = next(i for i in range(entry + 1, len(values)) if values[i] > 70)
    assert out == [0] * entry + [1] * (exit_ - entry) + [0] * (len(close) - exit_)

    # Con max_hold = 1 esce alla candela successiva all'entrata anche se l'RSI non sale
    short = RsiMeanReversionParams(period=2, low=30, high=99, max_hold=1)
    out_short = compute_targets(STRATEGIES["rsi_mean_reversion"], frame(close), short).tolist()
    assert sum(out_short) == 1 and out_short[entry] == 1


def test_trend_filter_zeroes_exposure_below_the_average():
    close = [10.0, 10, 10, 10, 20, 20, 20, 1, 1, 1]
    spec = STRATEGIES["sma_cross_trend_filter"]
    params = spec.params_model.model_validate({"fast": 1, "slow": 2, "sma_period": 3})
    base = compute_targets(STRATEGIES["sma_cross"], frame(close), SmaCrossParams(fast=1, slow=2))
    out = compute_targets(spec, frame(close), params)
    above = pd.Series(close) > sma(pd.Series(close), 3)
    assert out.tolist() == (base * above.astype(float)).tolist()
    assert out.iat[4] == 1 and out.iat[7] == 0


def test_invalid_params_are_rejected():
    with pytest.raises(ValidationError):
        SmaCrossParams(fast=50, slow=20)
    with pytest.raises(ValidationError):
        DonchianParams(n_entry=10, n_exit=20)
    with pytest.raises(ValidationError):
        RsiMeanReversionParams(period=14, low=70, high=30, max_hold=10)


@pytest.mark.parametrize("name", CANDIDATES)
def test_every_candidate_has_yaml_params_and_grid(name):
    spec = STRATEGIES[name]
    assert load_params(spec)
    grid = load_grid(spec)
    assert len(grid) >= 2 and len({p.model_dump_json() for p in grid}) == len(grid)
