"""Test del risk manager: test_risk_daily_loss e test_risk_max_drawdown (brief §10) e
limiti per ordine. Le regole legate all'ambiente reale (kill switch, dati vecchi)
hanno i loro test in Fase 4."""

from datetime import datetime, timedelta

import pytest

from bot.backtest.engine import run_backtest
from bot.config.risk import RiskLimits, load_risk_limits
from bot.risk.manager import RiskManager
from tests.backtest_helpers import LOOSE, config, price_candles, targets

T0 = datetime(2026, 1, 1, 0, 0)


def limits(**overrides) -> RiskLimits:
    return load_risk_limits().model_copy(update=overrides)


def test_project_risk_limits_match_brief():
    lim = load_risk_limits()
    assert (lim.max_order_fraction, lim.max_daily_loss, lim.max_drawdown, lim.max_orders_per_hour) == (0.25, 0.03, 0.15, 6)


def test_zero_limits_are_rejected():
    with pytest.raises(ValueError):
        RiskLimits.model_validate({**load_risk_limits().model_dump(), "max_drawdown": 0})


def test_risk_daily_loss():
    rm = RiskManager(limits())
    rm.on_candle_open(T0, 1000)
    rm.on_candle_close(T0, 975)                      # -2,5%: sotto il limite
    assert rm.review_order(T0, "buy", 100, 975, 0) == 100
    rm.on_candle_close(T0 + timedelta(hours=1), 969)  # -3,1%: limite superato
    assert rm.buys_blocked_today
    assert rm.review_order(T0 + timedelta(hours=2), "buy", 100, 969, 0) == 0
    assert rm.review_order(T0 + timedelta(hours=2), "sell", 100, 969, 500) == 100  # vendere resta possibile
    rm.on_candle_open(T0 + timedelta(days=1), 969)   # mezzanotte UTC: si riparte
    assert not rm.buys_blocked_today
    assert rm.review_order(T0 + timedelta(days=1), "buy", 100, 969, 0) == 100
    assert [e.rule for e in rm.events].count("max_daily_loss") >= 2


def test_risk_max_drawdown_closes_and_stops_in_backtest():
    # Sale a 120 (picco), poi scende a 100: -16,7% dal picco con posizione piena
    opens = [100, 100, 110, 120, 110, 100, 100, 130, 140]
    candles = price_candles(opens, start=datetime(2026, 1, 1), freq="1D")
    lim = limits(max_order_fraction=1.0, max_daily_loss=0.5)
    result = run_backtest(candles, targets([1] * len(opens)), config(fee=0.0, slippage=0.0), LOOSE, RiskManager(lim))

    assert result.stopped_at == candles["open_time"].iat[5]
    assert result.trades["exit_reason"].iat[0] == "risk manager"
    assert result.trades["exit_time"].iat[0] == candles["open_time"].iat[6]  # chiude all'apertura successiva
    # Dopo lo stop resta fuori dal mercato anche se il prezzo risale e il segnale è 1
    assert (result.equity["position_qty"].iloc[6:] == 0).all()
    assert len(result.trades) == 1


def test_buy_orders_are_capped_and_entry_takes_four_candles():
    candles = price_candles([100] * 8)
    result = run_backtest(candles, targets([1] * 8), config(fee=0.0, slippage=0.0), LOOSE, RiskManager(limits()))
    buys = result.orders[result.orders["side"] == "buy"]
    assert len(buys) == 4
    assert buys["qty"].iat[0] * 100 == pytest.approx(250)  # 25% del capitale
    assert result.equity["exposure"].iat[4] == pytest.approx(1.0)


def test_sells_are_not_capped():
    candles = price_candles([100] * 8)
    sig = targets([1, 1, 1, 1, 0, 0, 0, 0])
    result = run_backtest(candles, sig, config(fee=0.0, slippage=0.0), LOOSE, RiskManager(limits()))
    sells = result.orders[result.orders["side"] == "sell"]
    assert len(sells) == 1 and result.equity["position_qty"].iat[-1] == 0


def test_max_orders_per_hour():
    rm = RiskManager(limits(max_orders_per_hour=2))
    for minute in (0, 10):
        rm.record_order(T0 + timedelta(minutes=minute))
    assert rm.review_order(T0 + timedelta(minutes=20), "sell", 10, 1000, 10) == 0
    assert rm.review_order(T0 + timedelta(minutes=61), "sell", 10, 1000, 10) == 10


def test_drawdown_policy_restart_resumes_after_cooldown():
    # Picco 120, crollo a 100 (stop), poi dopo 7 giorni il bot riparte con il picco azzerato
    opens = [100, 100, 110, 120, 110, 100, 100] + [100] * 8 + [130, 140]
    candles = price_candles(opens, start=datetime(2026, 1, 1), freq="1D")
    lim = limits(max_order_fraction=1.0, max_daily_loss=0.5)
    rm = RiskManager(lim, drawdown_policy="restart")
    result = run_backtest(candles, targets([1] * len(opens)), config(fee=0.0, slippage=0.0), LOOSE, rm)

    assert result.stopped_at == candles["open_time"].iat[5] and result.n_stops == 1
    restart = [e for e in rm.events if e.action_taken.startswith("operatività ripresa")]
    assert restart and restart[0].ts == candles["open_time"].iat[12]  # 7 giorni dopo lo stop
    assert result.equity["position_qty"].iat[-1] > 0  # è tornato nel mercato
    assert len(result.trades) == 2


def test_drawdown_policy_off_never_stops():
    opens = [100, 100, 110, 120, 60, 50, 100]
    candles = price_candles(opens, start=datetime(2026, 1, 1), freq="1D")
    lim = limits(max_order_fraction=1.0, max_daily_loss=0.9)
    result = run_backtest(candles, targets([1] * len(opens)), config(fee=0.0, slippage=0.0), LOOSE,
                          RiskManager(lim, drawdown_policy="off"))
    assert result.stopped_at is None and result.n_stops == 0
    assert (result.equity["position_qty"].iloc[2:] > 0).all()
