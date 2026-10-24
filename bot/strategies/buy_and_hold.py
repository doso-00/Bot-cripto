"""Benchmark obbligatorio: investito al 100% dalla prima candela alla fine."""

import pandas as pd
from pydantic import BaseModel, ConfigDict


class BuyAndHoldParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def buy_and_hold(candles: pd.DataFrame, params: BuyAndHoldParams) -> pd.Series:
    return pd.Series(1.0, index=candles.index)
