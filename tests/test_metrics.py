from datetime import datetime

import math

import pandas as pd
import pytest

from bot.backtest.engine import BacktestResult, run_backtest
from bot.metrics import alpha_vs_benchmark, compute_metrics, max_drawdown_duration, periods_per_year, warnings_for
from bot.metrics.regimes import BEAR, BULL, UNKNOWN, label_days, regime_performance
from tests.backtest_helpers import LOOSE, config, price_candles, targets


def test_periods_per_year():
    assert periods_per_year("1h") == 8760
    assert periods_per_year("4h") == 2190


def test_drawdown_and_duration():
    equity = pd.DataFrame({
        "open_time": pd.date_range("2020-01-01", periods=6, freq="1h"),
        "equity": [100, 120, 90, 110, 125, 100],
    })
    assert max_drawdown_duration(equity) == pd.Timedelta(hours=2)  # dal picco 120 al nuovo picco 125


def test_metrics_on_known_round_trips():
    candles = price_candles([100, 100, 110, 110, 100, 90, 90])
    result = run_backtest(candles, targets([1, 0, 1, 0, 0, 0, 0]), config(fee=0.0, slippage=0.0, min_delta=0.1), LOOSE)
    m = compute_metrics(result, "1h")
    assert m["n_trades"] == 2
    assert m["win_rate"] == pytest.approx(0.5)
    # primo trade +10% su 1000, secondo -10%/11 circa: profit factor = 100 / 100
    assert m["profit_factor"] == pytest.approx(100 / (1100 * (1 - 100 / 110)), rel=1e-9)
    assert m["total_return"] == pytest.approx(result.final_equity / 1000 - 1)
    assert m["fees"] == 0 and m["slippage_cost"] == 0


def test_warnings_flag_suspicious_results():
    m = {"sharpe": 3.5, "n_trades": 5, "max_drawdown": 0.005, "costs_pct_of_gross": 0.7,
         "stopped_at": None, "n_rejected_orders": 0}
    text = " ".join(warnings_for(m))
    assert "Sharpe sospettosamente alto" in text and "meno di 30" in text
    assert "Drawdown quasi zero" in text and "costi" in text


def test_alpha_vs_benchmark():
    a = alpha_vs_benchmark({"total_return": 0.5, "max_drawdown": 0.2}, {"total_return": 0.6, "max_drawdown": 0.4})
    assert a["return_diff"] == pytest.approx(-0.1) and a["max_drawdown_reduction"] == pytest.approx(0.5)


def test_regime_labels_use_only_past_days():
    days = pd.date_range("2020-01-01", periods=200, freq="1D")
    close = [100.0] * 100 + [100.0 * 1.01 ** i for i in range(1, 101)]  # piatto, poi rialzo
    candles = pd.DataFrame({"open_time": days, "close": close})
    labels = label_days(candles, lookback_days=90, threshold=0.15)
    assert labels.iloc[:91].eq(UNKNOWN).all()
    assert labels.iloc[-1] == BULL
    # Il primo giorno rialzista è quello DOPO il primo close sopra la soglia
    first_cross = next(i for i in range(90, 200) if close[i] / close[i - 90] - 1 > 0.15)
    assert labels.iloc[first_cross] != BULL and labels.iloc[first_cross + 1] == BULL

    falling = candles.assign(close=[100.0 * 0.99 ** i for i in range(200)])
    assert label_days(falling, 90, 0.15).iloc[-1] == BEAR


def test_regime_performance_compounds_returns_inside_each_regime():
    times = pd.date_range("2020-01-01", periods=4, freq="1D")
    eq = pd.DataFrame({"open_time": times, "equity": [100.0, 110.0, 99.0, 99.0]})
    labels = pd.Series([BULL, BULL, BEAR, BEAR], index=times)
    perf = regime_performance({"strategia": eq}, labels).set_index("regime")
    assert perf.loc[BULL, "strategia"] == pytest.approx(0.10)
    assert perf.loc[BEAR, "strategia"] == pytest.approx(99 / 110 - 1)
