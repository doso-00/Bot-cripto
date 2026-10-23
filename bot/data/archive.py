"""Download dei file mensili di klines dall'archivio pubblico data.binance.vision.

Riferimento: https://github.com/binance/binance-public-data (README, sezione SPOT/Klines).
- URL: /data/spot/monthly/klines/<SIMBOLO>/<TF>/<SIMBOLO>-<TF>-<AAAA>-<MM>.zip
- Ogni zip ha accanto un file .CHECKSUM (SHA256) da verificare.
- I file mensili vengono pubblicati il primo lunedì del mese successivo: per il mese
  appena concluso il file può non esistere ancora (HTTP 404), e il buco lo copre l'API.
"""

import hashlib
import io
import logging
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from bot.data.klines import klines_to_frame, to_binance_symbol

log = logging.getLogger(__name__)

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
RETRY_DELAYS_S = (2, 4, 8, 16)


class NotPublishedError(Exception):
    """Il file richiesto non esiste (ancora) nell'archivio."""


class ChecksumError(Exception):
    """Lo SHA256 del file scaricato non coincide con quello pubblicato."""


Fetcher = Callable[[str], bytes]


def http_get(url: str, timeout: float = 30.0) -> bytes:
    """GET con retry e backoff esponenziale sugli errori di rete; 404 -> NotPublishedError."""
    for attempt, delay in enumerate((*RETRY_DELAYS_S, None), start=1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotPublishedError(url) from None
            if delay is None or (exc.code < 500 and exc.code != 429):
                raise
            log.warning("HTTP %s su %s (tentativo %d), riprovo tra %ss", exc.code, url, attempt, delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            if delay is None:
                raise
            log.warning("errore di rete su %s (tentativo %d): %s; riprovo tra %ss", url, attempt, exc, delay)
        time.sleep(delay)
    raise AssertionError("non raggiungibile")


def monthly_url(symbol: str, timeframe: str, year: int, month: int) -> str:
    s = to_binance_symbol(symbol)
    return f"{BASE_URL}/{s}/{timeframe}/{s}-{timeframe}-{year:04d}-{month:02d}.zip"


def iter_months(start: date, end: date) -> Iterator[tuple[int, int]]:
    """Mesi (anno, mese) da quello di `start` a quello di `end`, estremi inclusi."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def last_complete_month(today: date) -> date:
    """Primo giorno del mese concluso più recente."""
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def verify_checksum(content: bytes, checksum_file: bytes, filename: str) -> None:
    """Il file .CHECKSUM ha il formato di sha256sum: '<hash>  <nome file>'."""
    expected = checksum_file.decode().split()[0].strip().lower()
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ChecksumError(f"{filename}: sha256 {actual} diverso da quello pubblicato {expected}")


def download_month(
    symbol: str, timeframe: str, year: int, month: int, raw_dir: Path, fetch: Fetcher = http_get
) -> Path:
    """Scarica (se non già presente) e verifica il file mensile; restituisce il percorso locale.

    Il file viene scritto su disco solo dopo la verifica del checksum, quindi un file
    presente in locale è sempre un file verificato.
    """
    url = monthly_url(symbol, timeframe, year, month)
    target = raw_dir / "spot" / "monthly" / "klines" / to_binance_symbol(symbol) / timeframe / url.rsplit("/", 1)[1]
    if target.exists():
        return target

    content = fetch(url)
    verify_checksum(content, fetch(url + ".CHECKSUM"), target.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(content)
    tmp.replace(target)
    log.info("scaricato %s", target.name)
    return target


def read_month(path: Path, symbol: str, timeframe: str) -> pd.DataFrame:
    """Legge lo zip mensile e restituisce le candele nel formato canonico."""
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"{path.name}: atteso un solo CSV, trovati {names}")
        text = zf.read(names[0]).decode()

    raw = pd.read_csv(io.StringIO(text), header=None, dtype=str)
    # Alcuni file dell'archivio hanno una riga di intestazione: si riconosce perché
    # il primo campo non è numerico
    if not raw.empty and not str(raw.iat[0, 0]).strip().isdigit():
        raw = raw.iloc[1:].reset_index(drop=True)
    return klines_to_frame(raw, symbol, timeframe, source="bulk")
