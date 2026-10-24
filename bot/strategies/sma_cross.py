"""Trend following di base: investito quando la SMA veloce è sopra la SMA lenta."""

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.indicators import sma


class SmaCrossParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fast: int = Field(ge=1)
    slow: int = Field(ge=2)

    @model_validator(mode="after")
    def _fast_below_slow(self) -> "SmaCrossParams":
        if self.fast >= self.slow:
            raise ValueError("fast deve essere minore di slow")
        return self


def sma_cross(candles: pd.DataFrame, params: SmaCrossParams) -> pd.Series:
    fast = sma(candles["close"], params.fast)
    slow = sma(candles["close"], params.slow)
    # Durante il riscaldamento (medie non ancora definite) si resta fuori dal mercato
    return (fast > slow).astype("float64")
