"""Modificatore trend_filter: applica una strategia solo quando il close è sopra la propria
SMA di `sma_period` candele; sotto la media l'esposizione è 0.

Esempi di periodo: 200 candele 1h = circa 8 giorni; 4.800 candele 1h = 200 giorni
(la classica "SMA 200 giornaliera"). Durante il riscaldamento della media si resta fuori.
"""

from collections.abc import Callable

import pandas as pd
from pydantic import BaseModel, Field, create_model

from bot.indicators import sma


def filtered_params_model(base: type[BaseModel]) -> type[BaseModel]:
    """Modello dei parametri della strategia base più `sma_period`."""
    return create_model(f"{base.__name__}TrendFilter", __base__=base, sma_period=(int, Field(ge=2)))


def with_trend_filter(
    base_func: Callable[[pd.DataFrame, BaseModel], pd.Series], base_params: type[BaseModel]
) -> Callable[[pd.DataFrame, BaseModel], pd.Series]:
    base_fields = set(base_params.model_fields)

    def filtered(candles: pd.DataFrame, params: BaseModel) -> pd.Series:
        inner = base_params.model_validate({k: v for k, v in params.model_dump().items() if k in base_fields})
        trend = sma(candles["close"], params.sma_period)
        above = (candles["close"] > trend).astype("float64")  # NaN della media -> False -> fuori
        return base_func(candles, inner) * above

    return filtered
