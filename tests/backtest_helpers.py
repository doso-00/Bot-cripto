"""Strumenti comuni ai test del motore di backtest."""

from datetime import datetime
from decimal import Decimal

import pandas as pd

from bot.backtest.engine import BacktestConfig
from bot.exchange.rounding import SymbolFilters

# Filtri "larghi": non interferiscono con i calcoli quando il test non riguarda i filtri
LOOSE = SymbolFilters(
    min_qty=Decimal("0.00000001"), max_qty=Decimal("1000000000"), step_size=Decimal("0.00000001"),
    min_notional=Decimal("0"), apply_min_to_market=True, tick_size=Decimal("0.01"),
)
# Valori reali di BTC/USDT in ordine di grandezza (step 0.00001 BTC, notional minimo 5 USDT)
BTC_LIKE = SymbolFilters(
    min_qty=Decimal("0.00001"), max_qty=Decimal("9000"), step_size=Decimal("0.00001"),
    min_notional=Decimal("5"), apply_min_to_market=True, tick_size=Decimal("0.01"),
)


def config(fee=0.001, slippage=0.0005, capital=1000.0, min_delta=0.1) -> BacktestConfig:
    return BacktestConfig(initial_capital=capital, fee_rate=fee, slippage_rate=slippage, min_rebalance_delta=min_delta)


def price_candles(opens, closes=None, start=datetime(2020, 1, 1), freq="1h", volume=10.0) -> pd.DataFrame:
    """Candele con open/close scelti dal test; high/low coerenti."""
    closes = closes if closes is not None else opens
    df = pd.DataFrame({
        "open_time": pd.date_range(start, periods=len(opens), freq=freq),
        "open": [float(x) for x in opens],
        "close": [float(x) for x in closes],
        "volume": volume,
    })
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    return df


def targets(values) -> pd.Series:
    return pd.Series([float(v) for v in values])
