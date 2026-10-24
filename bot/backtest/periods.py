"""Periodi fissi di training, validazione e test (brief §6.2 punto 1).

Il periodo di test si guarda una sola volta per strategia, alla fine: per usarlo serve
una conferma esplicita e ogni uso resta registrato nella tabella `runs`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.config import Settings

PERIOD_NAMES = ("train", "validation", "test")


@dataclass(frozen=True)
class Period:
    name: str
    start: datetime  # incluso
    end: datetime    # escluso


def get_period(settings: Settings, name: str, now: datetime) -> Period:
    day = timedelta(days=1)
    start_of = lambda d: datetime.combine(d, datetime.min.time())  # noqa: E731
    if name == "train":
        return Period(name, start_of(settings.history_start), start_of(settings.train_end) + day)
    if name == "validation":
        return Period(name, start_of(settings.train_end) + day, start_of(settings.validation_end) + day)
    if name == "test":
        return Period(name, start_of(settings.validation_end) + day, now)
    raise ValueError(f"periodo sconosciuto '{name}': usare {PERIOD_NAMES}")
