"""Trend following alternativo: entra quando il close rompe il massimo delle ultime
n_entry candele, esce quando rompe il minimo delle ultime n_exit candele."""

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bot.indicators import donchian_lower, donchian_upper


class DonchianParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_entry: int = Field(ge=2)
    n_exit: int = Field(ge=2)

    @model_validator(mode="after")
    def _exit_not_longer_than_entry(self) -> "DonchianParams":
        if self.n_exit > self.n_entry:
            raise ValueError("n_exit non può superare n_entry")
        return self


def donchian_breakout(candles: pd.DataFrame, params: DonchianParams) -> pd.Series:
    close = candles["close"].to_numpy()
    upper = donchian_upper(candles["high"], params.n_entry).to_numpy()
    lower = donchian_lower(candles["low"], params.n_exit).to_numpy()
    out = np.zeros(len(close))
    in_position = False
    # La posizione dipende dalla storia (entrata e uscita usano soglie diverse): ciclo esplicito
    for i in range(len(close)):
        if not in_position and not np.isnan(upper[i]) and close[i] > upper[i]:
            in_position = True
        elif in_position and not np.isnan(lower[i]) and close[i] < lower[i]:
            in_position = False
        out[i] = 1.0 if in_position else 0.0
    return pd.Series(out, index=candles.index)
