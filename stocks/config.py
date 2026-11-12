"""Configurazione tipizzata della dashboard azioni (config/stocks/*.yaml)."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "stocks" / "settings.yaml"
DEFAULT_WATCHLIST_PATH = PROJECT_ROOT / "config" / "stocks" / "watchlist.yaml"


class StocksSettings(BaseModel):
    # extra="forbid": una chiave scritta male nello YAML deve far fallire il caricamento
    model_config = ConfigDict(extra="forbid")

    # Budget e costi
    budget_eur: float = Field(default=1000, gt=0)
    min_order_eur: float = Field(default=100, gt=0)
    fee_per_order_eur: float = Field(default=1.0, ge=0)
    capital_gains_tax: float = Field(default=0.26, ge=0, lt=1)

    # Posizioni base (core)
    core_share: float = Field(default=0.80, gt=0, le=1)
    core_min_positions: int = Field(default=3, gt=0)
    core_max_positions: int = Field(default=5, gt=0)
    core_max_weight: float = Field(default=0.30, gt=0, le=1)

    # Occasioni ed eventi
    opp_max_total_share: float = Field(default=0.20, ge=0, le=1)
    opp_max_open: int = Field(default=2, ge=0)
    event_max_eur: float = Field(default=100, ge=0)
    opp_max_stop_pct: float = Field(default=0.08, gt=0, lt=1)
    opp_atr_stop_mult: float = Field(default=2.0, gt=0)
    opp_atr_target_mult: float = Field(default=4.0, gt=0)
    opp_min_reward_risk: float = Field(default=2.0, gt=0)
    opp_max_days: int = Field(default=30, gt=0)

    # Soglie di drawdown
    core_review_drawdown: float = Field(default=0.15, gt=0, lt=1)
    core_exit_drawdown: float = Field(default=0.20, gt=0, lt=1)
    portfolio_halt_drawdown: float = Field(default=0.15, gt=0, lt=1)

    # Dati
    event_window_days: int = Field(default=5, gt=0)
    history_years: int = Field(default=5, gt=0)

    # Soglie di qualità e di ingresso
    quality_min_roe: float = Field(default=0.08)
    quality_max_debt_to_equity: float = Field(default=150, gt=0)
    quality_max_payout: float = Field(default=0.80, gt=0)
    core_pullback_min: float = Field(default=0.05, ge=0, lt=1)
    core_pullback_max: float = Field(default=0.15, gt=0, lt=1)
    opp_pullback_min: float = Field(default=0.10, ge=0, lt=1)
    opp_rsi_max: float = Field(default=35, gt=0, lt=100)


class WatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(min_length=1)
    note: str = ""
    proposed_by: Literal["riccardo", "claude"]


class Watchlist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WatchItem]
    # area -> ticker dell'indice di riferimento (es. {"US": "^GSPC"})
    benchmarks: dict[str, str]


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> StocksSettings:
    """Carica e valida settings.yaml della dashboard azioni."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return StocksSettings.model_validate(raw)


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> Watchlist:
    """Carica e valida watchlist.yaml (titoli seguiti e benchmark)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Watchlist.model_validate(raw)
