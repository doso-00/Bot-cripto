"""Fase 3 completa su un database sintetico piccolo: ottimizzazione, validazione, test finale."""

from datetime import date, datetime

import pytest

import bot.optimize.phase3 as phase3_module
from bot.backtest.periods import get_period
from bot.config.risk import load_risk_limits
from bot.data.db import load_candles as real_load_candles
from bot.data.db import upsert_candles
from bot.data.klines import klines_to_frame
from bot.evaluate.final import choose_selections, run_final_test
from bot.optimize.phase3 import run_phase3
from bot.report.final_html import render_final_report
from bot.report.phase3_html import render_phase3_report
from tests.conftest import aggregate_rows, raw_klines

NOW = datetime(2021, 1, 1)
STRATEGIES = ["sma_cross", "rsi_mean_reversion"]


@pytest.fixture
def market(con, settings):
    for sym, scale, seed in (("BTC/USDT", 80, 21), ("ETH/USDT", 3, 22)):
        rows = raw_klines(datetime(2020, 1, 1), 366 * 24, "1h", seed=seed)
        for r in rows:
            r[1:5] = [f"{float(x) * scale:.2f}" for x in r[1:5]]
        upsert_candles(con, klines_to_frame(rows, sym, "1h", "test"), NOW)
        upsert_candles(con, klines_to_frame(aggregate_rows(rows, 4, "4h"), sym, "4h", "test"), NOW)
        con.execute(f"INSERT INTO exchange_filters VALUES ('{sym}', '2026-10-24', 0.0001, 9000, 0.0001, 5, true, 0.01, '[]')")
    s = settings.model_copy(update={
        "train_end": date(2020, 8, 31), "validation_end": date(2020, 10, 31),
        "optimization": settings.optimization.model_copy(update={
            "is_months": 2, "oos_months": 1, "step_months": 1, "min_trades_window": 1, "min_trades_full": 5}),
    })
    return con, s


def test_phase3_never_reads_the_test_period(market, monkeypatch):
    con, s = market
    ends = []
    monkeypatch.setattr(phase3_module, "load_candles", lambda c, sym, tf, start=None, end=None: (
        ends.append(end) or real_load_candles(c, sym, tf, start, end)))
    run_phase3(con, s, load_risk_limits(), ["sma_cross"], NOW, save=False)
    assert ends and all(e <= datetime(2020, 11, 1) for e in ends)


def test_phase3_saves_selections_and_renders_report(market):
    con, s = market
    res = run_phase3(con, s, load_risk_limits(), STRATEGIES, NOW)
    assert [r.name for r in res.strategies] == STRATEGIES
    assert res.total_combinations == 15 + 16
    for r in res.strategies:
        assert r.wf_summary["n_folds"] == 6  # finestre 2+1 mesi da gennaio ad agosto, passo 1
        assert len(r.criteria) == 4
        assert set(r.robustness["timeframe"]) == {"1h", "4h"} and set(r.robustness["symbol"]) == {"BTC/USDT", "ETH/USDT"}
    saved = con.execute("SELECT strategy, grid_size FROM param_selections ORDER BY strategy").fetchall()
    assert saved == [("rsi_mean_reversion", 16), ("sma_cross", 15)]
    assert con.execute("SELECT count(*) FROM grid_results").fetchall()[0][0] == 31

    html = render_phase3_report(res, s.optimization.model_dump())
    for text in ("Esito", "Riepilogo", "31 combinazioni provate", "Walk-forward sul training", "Robustezza"):
        assert text in html, text


def test_final_test_runs_once_on_frozen_params(market):
    con, s = market
    run_phase3(con, s, load_risk_limits(), STRATEGIES, NOW)
    con.execute("UPDATE param_selections SET passed_validation = (strategy = 'sma_cross')")

    chosen, messages = choose_selections(con, None, repeat=False)
    assert [c.strategy for c in chosen] == ["sma_cross"]  # solo le promosse dalla validazione

    period = get_period(s, "test", NOW)
    tests = run_final_test(con, s, load_risk_limits(), chosen, period, NOW)
    assert tests[0].selection.params == chosen[0].params and len(tests[0].criteria) == 4
    assert tests[0].metrics["valutazione"]["n_trades"] >= 0
    assert "test finale" in render_final_report(tests, period, messages, NOW, "ETH/USDT")

    again, messages = choose_selections(con, None, repeat=False)
    assert again == [] and "già testata" in messages[0]
    repeated, _ = choose_selections(con, None, repeat=True)
    assert [c.strategy for c in repeated] == ["sma_cross"]

    explicit, messages = choose_selections(con, ["rsi_mean_reversion"], repeat=False)
    assert [c.strategy for c in explicit] == ["rsi_mean_reversion"] and "non essendo promossa" in messages[0]
