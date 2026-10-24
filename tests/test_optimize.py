import math
from datetime import datetime

import pandas as pd
import pytest

from bot.backtest.engine import run_backtest
from bot.config.risk import load_risk_limits
from bot.optimize.core import (ComboResult, Evaluator, Fold, are_neighbors, select_robust, summarize_folds,
                               walk_forward, walk_forward_windows)
from bot.risk.manager import RiskManager
from bot.strategies import STRATEGIES, compute_targets
from bot.strategies.sma_cross import SmaCrossParams
from tests.backtest_helpers import LOOSE, config
from tests.conftest import candles as synthetic_candles

AXES = {"fast": [10, 20, 30], "slow": [50, 100, 150]}


def combo(fast, slow, sharpe, trades=50):
    return ComboResult(SmaCrossParams(fast=fast, slow=slow), sharpe, 0.1, 0.2, trades)


def test_walk_forward_windows_cover_training_without_leaving_it():
    w = walk_forward_windows(datetime(2020, 1, 1), datetime(2024, 1, 1), 12, 3, 3)
    assert len(w) == 12
    assert w[0] == (datetime(2020, 1, 1), datetime(2021, 1, 1), datetime(2021, 1, 1), datetime(2021, 4, 1))
    assert w[-1][3] == datetime(2024, 1, 1)
    assert all(is_end == oos_start for _, is_end, oos_start, _ in w)


def test_neighbors_differ_by_one_step_on_one_parameter():
    a = SmaCrossParams(fast=20, slow=100)
    assert are_neighbors(a, SmaCrossParams(fast=10, slow=100), AXES)
    assert are_neighbors(a, SmaCrossParams(fast=20, slow=150), AXES)
    assert not are_neighbors(a, SmaCrossParams(fast=10, slow=50), AXES)   # due parametri
    assert not are_neighbors(SmaCrossParams(fast=10, slow=50), SmaCrossParams(fast=30, slow=50), AXES)  # due passi


def test_isolated_peak_loses_to_a_plateau():
    results = [
        combo(10, 50, 0.0), combo(10, 100, 3.0), combo(10, 150, 0.0),   # picco isolato a (10, 100)
        combo(20, 50, 0.0), combo(20, 100, 0.0), combo(20, 150, 1.2),
        combo(30, 50, 0.0), combo(30, 100, 1.2), combo(30, 150, 1.4),   # altopiano intorno a (30, 150)
    ]
    selected, table, note = select_robust(results, AXES, min_trades=30)
    assert selected.params == SmaCrossParams(fast=30, slow=150)
    assert note is None
    assert table.loc[table["key"] == results[1].key, "sharpe"].iat[0] == 3.0  # il picco resta visibile nel report


def test_combinations_with_too_few_trades_are_not_eligible():
    results = [combo(10, 50, 2.0, trades=5), combo(10, 100, 0.5, trades=40), combo(20, 50, 0.4, trades=40)]
    selected, _, note = select_robust(results, AXES, min_trades=30)
    assert selected.params == SmaCrossParams(fast=10, slow=100)
    _, _, note = select_robust([combo(10, 50, 1.0, trades=3)], AXES, min_trades=30)
    assert "Nessuna combinazione" in note


def test_nan_sharpe_counts_as_zero():
    assert combo(10, 50, math.nan).score == 0.0


@pytest.fixture
def evaluator():
    df = synthetic_candles(datetime(2020, 1, 1), 24 * 120, seed=3)
    return Evaluator(STRATEGIES["sma_cross"], df, config(), LOOSE, load_risk_limits(), "1h")


def test_evaluator_matches_direct_backtest_on_the_same_slice(evaluator):
    params = SmaCrossParams(fast=10, slow=50)
    start, end = datetime(2020, 2, 1), datetime(2020, 3, 1)
    via_evaluator = evaluator.run(params, start, end)

    df = evaluator.history
    targets = compute_targets(STRATEGIES["sma_cross"], df, params)  # sulla storia intera: riscaldamento incluso
    mask = (df["open_time"] >= start) & (df["open_time"] < end)
    direct = run_backtest(df[mask], targets[mask], config(), LOOSE, RiskManager(load_risk_limits(), "off"))
    assert via_evaluator.final_equity == direct.final_equity


def test_walk_forward_chooses_on_in_sample_and_reports_out_of_sample(evaluator):
    combos = [SmaCrossParams(fast=10, slow=50), SmaCrossParams(fast=20, slow=100)]
    windows = walk_forward_windows(datetime(2020, 1, 1), datetime(2020, 4, 1), 1, 1, 1)
    folds = walk_forward(evaluator, combos, windows, min_trades=1)
    assert len(folds) == 2
    for f in folds:
        if f.params is None:
            continue
        best_is = max((evaluator.evaluate(p, f.is_start, f.is_end) for p in combos if
                       evaluator.evaluate(p, f.is_start, f.is_end).n_trades >= 1), key=lambda c: c.score)
        assert f.params == best_is.params.model_dump()
        oos = evaluator.run(SmaCrossParams(**f.params), f.oos_start, f.oos_end)
        assert f.oos_return == pytest.approx(oos.final_equity / oos.initial_capital - 1)


def test_summarize_folds_compounds_returns():
    t = datetime(2020, 1, 1)
    folds = [Fold(1, t, t, t, t, {"a": 1}, 1.0, 0.10, 0.05, 3, 0.1), Fold(2, t, t, t, t, None, math.nan, 0.0, -0.10, 0, 0)]
    s = summarize_folds(folds)
    assert s["oos_return"] == pytest.approx(0.10) and s["oos_bh_return"] == pytest.approx(1.05 * 0.9 - 1)
    assert s["folds_beating_bh"] == 2 and s["folds_without_choice"] == 1
