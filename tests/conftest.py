"""Dati sintetici condivisi dai test: nessun test usa la rete."""

import hashlib
import io
import zipfile
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from bot.config import load_settings
from bot.data.db import connect
from bot.data.klines import klines_to_frame, timeframe_ms


def raw_klines(start: datetime, n: int, timeframe: str, seed: int = 0, micro: bool = False) -> list[list]:
    """Righe grezze a 12 campi come quelle dell'API/archivio, con OHLC coerenti.

    Il prezzo segue una passeggiata casuale; ogni open coincide con il close precedente.
    `micro=True` produce timestamp in microsecondi (formato archivio spot dal 2025).
    """
    rng = np.random.default_rng(seed)
    step = timeframe_ms(timeframe)
    start_ms = int(pd.Timestamp(start).value // 10**6)
    scale = 1000 if micro else 1
    rows, price = [], 100.0
    for i in range(n):
        open_ = price
        close = round(open_ * (1 + rng.normal(0, 0.005)), 2)
        high = round(max(open_, close) * (1 + abs(rng.normal(0, 0.002))), 2)
        low = round(min(open_, close) * (1 - abs(rng.normal(0, 0.002))), 2)
        volume = round(float(rng.uniform(1, 10)), 4)
        t = start_ms + i * step
        rows.append([
            t * scale, f"{open_:.8f}", f"{high:.8f}", f"{low:.8f}", f"{close:.8f}", f"{volume:.8f}",
            (t + step) * scale - 1, f"{volume * close:.8f}", 10 + i, "0", "0", "0",
        ])
        price = close
    return rows


def aggregate_rows(rows: list[list], ratio: int, timeframe: str) -> list[list]:
    """Aggrega righe grezze consecutive (es. 4 x 1h -> 1 x 4h), come fa l'exchange."""
    step = timeframe_ms(timeframe)
    out = []
    for i in range(0, len(rows) - ratio + 1, ratio):
        chunk = rows[i : i + ratio]
        volume = sum(float(r[5]) for r in chunk)
        out.append([
            chunk[0][0], chunk[0][1], f"{max(float(r[2]) for r in chunk):.8f}",
            f"{min(float(r[3]) for r in chunk):.8f}", chunk[-1][4], f"{volume:.8f}",
            chunk[0][0] + step - 1, "0", sum(r[8] for r in chunk), "0", "0", "0",
        ])
    return out


def candles(start: datetime, n: int, timeframe: str = "1h", symbol: str = "BTC/USDT", seed: int = 0) -> pd.DataFrame:
    return klines_to_frame(raw_klines(start, n, timeframe, seed), symbol, timeframe, source="test")


def make_zip(rows: list[list], csv_name: str, header: bool = False) -> bytes:
    lines = [",".join(str(v) for v in r) for r in rows]
    if header:
        lines.insert(0, "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
                        "taker_buy_volume,taker_buy_quote_volume,ignore")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(csv_name, "\n".join(lines) + "\n")
    return buf.getvalue()


def checksum_for(content: bytes, name: str) -> bytes:
    return f"{hashlib.sha256(content).hexdigest()}  {name}\n".encode()


@pytest.fixture
def con():
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def settings(tmp_path):
    """Settings reali del progetto, con tutti i percorsi dentro una cartella temporanea."""
    return load_settings(root=tmp_path)
