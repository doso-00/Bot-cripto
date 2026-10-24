"""Mean reversion: entra quando l'RSI scende sotto `low`, esce quando supera `high` o
dopo `max_hold` candele.

Per evitare di rientrare subito dopo un'uscita a tempo con l'RSI ancora basso, l'entrata
richiede che l'RSI *scenda* sotto `low` (era >= low alla candela precedente).
"""

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.indicators import rsi


class RsiMeanReversionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: int = Field(ge=2)
    low: float = Field(gt=0, lt=100)
    high: float = Field(gt=0, lt=100)
    max_hold: int = Field(ge=1)

    @model_validator(mode="after")
    def _low_below_high(self) -> "RsiMeanReversionParams":
        if self.low >= self.high:
            raise ValueError("low deve essere minore di high")
        return self


def rsi_mean_reversion(candles: pd.DataFrame, params: RsiMeanReversionParams) -> pd.Series:
    values = rsi(candles["close"], params.period).to_numpy()
    out = np.zeros(len(values))
    held = None  # candele trascorse dall'entrata; None = fuori dal mercato
    for i in range(1, len(values)):
        if np.isnan(values[i]):
            continue
        if held is None:
            crossed_down = not np.isnan(values[i - 1]) and values[i - 1] >= params.low and values[i] < params.low
            if crossed_down:
                held = 0
        else:
            held += 1
            if values[i] > params.high or held >= params.max_hold:
                held = None
        out[i] = 0.0 if held is None else 1.0
    return pd.Series(out, index=candles.index)
