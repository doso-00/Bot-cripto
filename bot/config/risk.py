"""Modello tipizzato dei limiti di rischio di config/risk.yaml (brief §7)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from bot.config.settings import PROJECT_ROOT

DEFAULT_RISK_PATH = PROJECT_ROOT / "config" / "risk.yaml"


class RiskLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Tutti i limiti devono essere strettamente positivi: un limite a zero non è un limite
    max_exposure_per_symbol: float = Field(gt=0, le=1)
    max_order_fraction: float = Field(gt=0, le=1)
    max_daily_loss: float = Field(gt=0, lt=1)
    max_drawdown: float = Field(gt=0, lt=1)
    max_orders_per_hour: int = Field(gt=0)
    max_stale_intervals: int = Field(gt=0)
    max_price_divergence: float = Field(gt=0, lt=1)
    kill_switch_file: str


def load_risk_limits(path: Path | None = None) -> RiskLimits:
    raw = yaml.safe_load(Path(path or DEFAULT_RISK_PATH).read_text(encoding="utf-8"))
    return RiskLimits.model_validate(raw)
