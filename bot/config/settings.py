"""Modello tipizzato dei parametri di config/settings.yaml."""

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


class DataQualitySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_abs_return: dict[str, float]
    max_open_gap: float = Field(gt=0)
    resample_tolerance: float = Field(ge=0)


class RegimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_days: int = Field(gt=0)
    threshold: float = Field(gt=0, lt=1)


class OptimizationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_months: int = Field(gt=0)
    oos_months: int = Field(gt=0)
    step_months: int = Field(gt=0)
    min_trades_window: int = Field(ge=0)
    min_trades_full: int = Field(ge=0)
    confirm_symbol: str


class PathSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_data: Path
    database: Path
    reports: Path
    logs: Path
    known_events: Path

    def resolve(self, root: Path) -> "PathSettings":
        """Restituisce una copia con i percorsi relativi risolti rispetto a `root`."""
        return PathSettings(
            **{name: p if p.is_absolute() else root / p for name, p in self.__dict__.items()}
        )


class Settings(BaseModel):
    # extra="forbid": una chiave scritta male nello YAML deve far fallire il caricamento,
    # non essere ignorata in silenzio
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(min_length=1)
    timeframe: str
    comparison_timeframe: str
    history_start: date
    initial_capital_usdt: float = Field(gt=0)
    fee_rate: float = Field(ge=0, lt=0.01)
    slippage_rate: float = Field(ge=0, lt=0.01)
    train_end: date
    validation_end: date
    min_rebalance_delta: float = Field(ge=0, le=1)
    data_quality: DataQualitySettings
    regimes: RegimeSettings
    optimization: OptimizationSettings
    paths: PathSettings

    @field_validator("symbols")
    @classmethod
    def _symbols_are_unified(cls, symbols: list[str]) -> list[str]:
        for s in symbols:
            if s.count("/") != 1:
                raise ValueError(f"simbolo '{s}' non nel formato BASE/QUOTE (es. BTC/USDT)")
        return symbols

    @property
    def timeframes(self) -> list[str]:
        """Timeframe da scaricare: principale e di confronto."""
        return list(dict.fromkeys([self.timeframe, self.comparison_timeframe]))


def load_settings(path: Path | None = None, root: Path | None = None) -> Settings:
    """Carica e valida settings.yaml; i percorsi relativi diventano assoluti rispetto a `root`."""
    path = path or DEFAULT_SETTINGS_PATH
    root = root or PROJECT_ROOT
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    settings = Settings.model_validate(raw)
    if not settings.history_start < settings.train_end < settings.validation_end:
        raise ValueError("date incoerenti: serve history_start < train_end < validation_end")
    return settings.model_copy(update={"paths": settings.paths.resolve(root)})
