"""Strategie come funzioni pure: candele chiuse + parametri -> esposizione target.

Contratto di ogni strategia:
- input: DataFrame di candele CHIUSE ordinato per open_time (colonne open, high, low,
  close, volume, ...) e un oggetto parametri validato;
- output: Series allineata alle righe del DataFrame, con l'esposizione target in [0, 1]
  decisa alla chiusura di ciascuna candela. Il valore alla riga t usa solo le righe <= t.
Il motore esegue il target della riga t all'apertura della candela t+1. Backtest, paper e
live usano la stessa funzione: in paper/live si prende l'ultimo valore della Series.

Parametri e griglia di ottimizzazione di ogni strategia: config/strategies/<nome>.yaml.
"""

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel, ValidationError

from bot.config.settings import PROJECT_ROOT
from bot.strategies.buy_and_hold import BuyAndHoldParams, buy_and_hold
from bot.strategies.donchian_breakout import DonchianParams, donchian_breakout
from bot.strategies.rsi_mean_reversion import RsiMeanReversionParams, rsi_mean_reversion
from bot.strategies.sma_cross import SmaCrossParams, sma_cross
from bot.strategies.trend_filter import filtered_params_model, with_trend_filter

STRATEGY_CONFIG_DIR = PROJECT_ROOT / "config" / "strategies"


@dataclass(frozen=True)
class StrategySpec:
    name: str
    func: Callable[[pd.DataFrame, BaseModel], pd.Series]
    params_model: type[BaseModel]


_BASE = {
    "sma_cross": (sma_cross, SmaCrossParams),
    "donchian_breakout": (donchian_breakout, DonchianParams),
    "rsi_mean_reversion": (rsi_mean_reversion, RsiMeanReversionParams),
}

STRATEGIES: dict[str, StrategySpec] = {
    "buy_and_hold": StrategySpec("buy_and_hold", buy_and_hold, BuyAndHoldParams),
    **{name: StrategySpec(name, func, model) for name, (func, model) in _BASE.items()},
    **{
        f"{name}_trend_filter": StrategySpec(
            f"{name}_trend_filter", with_trend_filter(func, model), filtered_params_model(model)
        )
        for name, (func, model) in _BASE.items()
    },
}

# Strategie confrontate nella Fase 3 (il buy & hold è il benchmark, non un candidato)
CANDIDATES = [name for name in STRATEGIES if name != "buy_and_hold"]


def get_strategy(name: str) -> StrategySpec:
    try:
        return STRATEGIES[name]
    except KeyError:
        raise ValueError(f"strategia sconosciuta '{name}'; disponibili: {sorted(STRATEGIES)}") from None


def _config(spec: StrategySpec, config_dir: Path) -> dict:
    path = config_dir / f"{spec.name}.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def load_params(spec: StrategySpec, config_dir: Path = STRATEGY_CONFIG_DIR) -> BaseModel:
    """Parametri di default (chiave `params` di config/strategies/<nome>.yaml)."""
    return spec.params_model.model_validate(_config(spec, config_dir).get("params") or {})


def load_grid(spec: StrategySpec, config_dir: Path = STRATEGY_CONFIG_DIR) -> list[BaseModel]:
    """Tutte le combinazioni VALIDE della griglia (chiave `grid`), in ordine deterministico.

    Le combinazioni che violano i vincoli della strategia (es. fast >= slow) sono scartate:
    il numero restituito è quello delle combinazioni davvero provate.
    """
    grid = _config(spec, config_dir).get("grid") or {}
    if not grid:
        return [load_params(spec, config_dir)]
    names = list(grid)
    combos = []
    for values in itertools.product(*(grid[n] for n in names)):
        try:
            combos.append(spec.params_model.model_validate(dict(zip(names, values))))
        except ValidationError:
            continue
    return combos


def compute_targets(spec: StrategySpec, candles: pd.DataFrame, params: BaseModel) -> pd.Series:
    """Esegue la strategia e verifica il contratto sull'output."""
    targets = spec.func(candles, params)
    if len(targets) != len(candles) or not targets.index.equals(candles.index):
        raise ValueError(f"{spec.name}: l'output deve essere allineato alle candele")
    if targets.isna().any() or (targets < 0).any() or (targets > 1).any():
        raise ValueError(f"{spec.name}: esposizione target fuori da [0, 1] o NaN")
    return targets.astype("float64")
