"""Configurazione del logging: console + file giornaliero in logs/."""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(log_dir: Path, name: str, level: int = logging.INFO) -> Path:
    """Inizializza il logger root. Restituisce il percorso del file di log."""
    log_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    log_file = log_dir / f"{name}_{day}.log"

    formatter = logging.Formatter(FORMAT)
    formatter.converter = time.gmtime  # orari nei log in UTC, come nel database

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in (logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    return log_file
