"""Strategie come funzioni pure: candele chiuse + parametri -> esposizione target.

Contratto di ogni strategia:
- input: DataFrame di candele CHIUSE ordinato per open_time (colonne open, high, low,
  close, volume, ...) e un oggetto parametri validato;
- output: Series allineata alle righe del DataFrame, con l'esposizione target in [0, 1]
  decisa alla chiusura di ciascuna candela. Il valore alla riga t usa solo le righe <= t.
Il motore esegue il target della riga t all'apertura della candela t+1. Backtest, paper e
live usano la stessa funzione: in paper/live si prende l'ultimo valore della Series.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel

from bot.config.settings import PROJECT_ROOT
from bot.strategies.buy_and_hold import BuyAndHoldParams, buy_and_hold
from bot.strategies.sma_cross import SmaCrossParams, sma_cross

STRATEGY_CONFIG_DIR = PROJECT_ROOT / "config" / "strategies"


@dataclass(frozen=True)
class StrategySpec:
    name: str
    func: Callable[[pd.DataFrame, BaseModel], pd.Series]
    params_model: type[BaseModel]


STRATEGIES: dict[str, StrategySpec] = {
    "buy_and_hold": StrategySpec("buy_and_hold", buy_and_hold, BuyAndHoldParams),
    "sma_cross": StrategySpec("sma_cross", sma_cross, SmaCrossParams),
}


def get_strategy(name: str) -> StrategySpec:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(f"strategia sconosciuta '{name}'; disponibili: {sorted(STRATEGIES)}") from None


def load_params(spec: StrategySpec, config_dir: Path = STRATEGY_CONFIG_DIR) -> BaseModel:
    """Parametri da config/strategies/<nome>.yaml (vuoto se il file non esiste)."""
    path = config_dir / f"{spec.name}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return spec.params_model.model_validate(raw or {})


def compute_targets(spec: StrategySpec, candles: pd.DataFrame, params: BaseModel) -> pd.Series:
    """Esegue la strategia e verifica il contratto sull'output."""
    targets = spec.func(candles, params)
    if len(targets) != len(candles) or not targets.index.equals(candles.index):
        raise ValueError(f"{spec.name}: l'output deve essere allineato alle candele")
    if targets.isna().any() or (targets < 0).any() or (targets > 1).any():
        raise ValueError(f"{spec.name}: esposizione target fuori da [0, 1] o NaN")
    return targets.astype("float64")
