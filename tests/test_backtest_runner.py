"""Backtest completo su un database sintetico: qualità dati, varianti, salvataggio, report."""

from datetime import date, datetime

import pytest

from bot.backtest.periods import get_period
from bot.backtest.runner import DataQualityError, run_full_backtest
from bot.config.risk import load_risk_limits
from bot.data.db import upsert_candles
from bot.data.klines import klines_to_frame
from bot.strategies.sma_cross import SmaCrossParams
from tests.conftest import aggregate_rows, raw_klines

NOW = datetime(2020, 5, 1)


@pytest.fixture
def market(con, settings):
    rows = raw_klines(datetime(2020, 1, 1), 121 * 24, "1h", seed=5)  # gennaio-aprile 2020
    for r in rows:  # prezzi nell'ordine di BTC
        r[1:5] = [f"{float(x) * 80:.2f}" for x in r[1:5]]
    upsert_candles(con, klines_to_frame(rows, "BTC/USDT", "1h", "test"), NOW)
    upsert_candles(con, klines_to_frame(aggregate_rows(rows, 4, "4h"), "BTC/USDT", "4h", "test"), NOW)
    con.execute("INSERT INTO exchange_filters VALUES ('BTC/USDT', '2026-10-24', 0.00001, 9000, 0.00001, 5, true, 0.01, '[]')")
    s = settings.model_copy(update={"train_end": date(2020, 3, 31), "validation_end": date(2020, 4, 15)})
    return con, s


@pytest.mark.parametrize("strategy, params", [("sma_cross", SmaCrossParams(fast=10, slow=40)), ("buy_and_hold", None)])
def test_full_backtest_saves_run_and_writes_report(market, tmp_path, strategy, params):
    con, s = market
    period = get_period(s, "train", NOW)
    run = run_full_backtest(con, s, load_risk_limits(), strategy, "BTC/USDT", "1h", period, NOW,
                            params=params, report_dir=tmp_path)

    assert abs(run.context.bh_check["diff"]) < 0.0001
    n_candles = con.execute("SELECT count(*) FROM candles WHERE timeframe = '1h' AND open_time < '2020-04-01'").fetchall()[0][0]
    saved = con.execute("SELECT count(*) FROM bt_equity WHERE run_id = ?", [run.context.run_id]).fetchall()[0][0]
    assert saved == n_candles
    assert con.execute("SELECT strategy, period_name FROM runs").fetchall() == [(strategy, "train")]

    html = run.report_path.read_text(encoding="utf-8")
    for section in ("Avvisi automatici", "Verifica del motore", "Metriche", "Risultati per regime",
                    "Sensibilità allo slippage", "Misurato e ipotesi", "Buy &amp; hold"):
        assert section in html, section
    assert html.count("plotly") > 0


def test_validation_period_uses_earlier_candles_only_for_warmup(market):
    con, s = market
    period = get_period(s, "validation", NOW)
    run = run_full_backtest(con, s, load_risk_limits(), "sma_cross", "BTC/USDT", "1h", period, NOW,
                            params=SmaCrossParams(fast=10, slow=40), save=False)
    eq = run.context.variants[0].result.equity
    assert eq["open_time"].iat[0] == datetime(2020, 4, 1) and eq["open_time"].iat[-1] < datetime(2020, 4, 16)
    # Grazie al riscaldamento la SMA lenta è già definita alla prima candela del periodo
    assert run.context.variants[1].result.equity["target_exposure"].notna().all()


def test_bad_data_blocks_backtest(market):
    con, s = market
    con.execute("DELETE FROM candles WHERE timeframe = '1h' AND open_time = '2020-02-10 05:00'")
    with pytest.raises(DataQualityError):
        run_full_backtest(con, s, load_risk_limits(), "buy_and_hold", "BTC/USDT", "1h",
                          get_period(s, "train", NOW), NOW, save=False)
    run = run_full_backtest(con, s, load_risk_limits(), "buy_and_hold", "BTC/USDT", "1h",
                            get_period(s, "train", NOW), NOW, save=False, allow_bad_data=True)
    assert "non affidabili" in run.context.data_quality
