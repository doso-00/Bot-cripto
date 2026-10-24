"""Calcolo manuale del buy & hold, indipendente dal motore (criterio della Fase 2).

Riproduce a mano le stesse ipotesi del motore: acquisto all'apertura della seconda
candela (il segnale della prima si esegue alla successiva) o della prima successiva con
volume, quantità arrotondata per difetto allo step size, commissione e slippage sia in
entrata sia in uscita, uscita al close dell'ultima candela.
"""

from decimal import ROUND_DOWN, Decimal

import pandas as pd

from bot.backtest.engine import BacktestConfig
from bot.exchange.rounding import SymbolFilters


def manual_buy_and_hold(candles: pd.DataFrame, config: BacktestConfig, filters: SymbolFilters) -> float:
    """Equity finale del buy & hold calcolata con la formula, senza simulazione."""
    tradable = candles.iloc[1:]
    first = tradable[tradable["volume"] > 0].iloc[0]
    buy_price = first["open"] * (1 + config.slippage_rate)
    sell_price = candles["close"].iat[-1] * (1 - config.slippage_rate)

    raw_qty = Decimal(str(config.initial_capital / (1 + config.fee_rate) / buy_price))
    qty = float((raw_qty / filters.step_size).to_integral_value(rounding=ROUND_DOWN) * filters.step_size)
    cash_left = config.initial_capital - qty * buy_price * (1 + config.fee_rate)
    return cash_left + qty * sell_price * (1 - config.fee_rate)


def formula_buy_and_hold(candles: pd.DataFrame, config: BacktestConfig) -> float:
    """Versione "da manuale" senza arrotondamenti: capitale x prezzo finale / iniziale, meno i costi."""
    tradable = candles.iloc[1:]
    first_open = tradable.loc[tradable["volume"] > 0, "open"].iat[0]
    gross = candles["close"].iat[-1] / first_open
    costs = (1 - config.slippage_rate) * (1 - config.fee_rate) / ((1 + config.slippage_rate) * (1 + config.fee_rate))
    return config.initial_capital * gross * costs
