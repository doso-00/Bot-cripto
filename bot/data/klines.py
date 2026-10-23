"""Conversione delle klines Binance (archivio CSV o API) nel formato della tabella `candles`.

Archivio e API hanno le stesse 12 colonne (documentazione binance-public-data e
binance-spot-api-docs). Differenza nota: nei file spot dell'archivio dal 2025-01-01 i
timestamp sono in microsecondi invece che in millisecondi; qui vengono riportati in ms.
"""

from collections.abc import Sequence

import pandas as pd

TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}

RAW_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]

CANDLE_COLUMNS = [
    "symbol",
    "timeframe",
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "is_closed",
    "source",
]

# Un timestamp in ms del periodo 2017-2100 è < 1e13; in microsecondi è > 1e15.
# Qualunque valore sopra 1e14 è quindi in microsecondi.
_MICROSECOND_THRESHOLD = 10**14


def timeframe_ms(timeframe: str) -> int:
    try:
        return TIMEFRAME_MS[timeframe]
    except KeyError:
        raise ValueError(f"timeframe non supportato: {timeframe}") from None


def to_binance_symbol(symbol: str) -> str:
    """'BTC/USDT' (formato ccxt, usato nel database) -> 'BTCUSDT' (formato Binance)."""
    return symbol.replace("/", "")


def epoch_ms(value: int | str) -> int:
    """Singolo timestamp (ms o microsecondi) -> millisecondi."""
    value = int(value)
    return value if value < _MICROSECOND_THRESHOLD else value // 1000


def normalize_epoch_ms(values: pd.Series) -> pd.Series:
    """Riporta in millisecondi i timestamp espressi in microsecondi."""
    values = values.astype("int64")
    return values.where(values < _MICROSECOND_THRESHOLD, values // 1000)


def klines_to_frame(
    rows: pd.DataFrame | Sequence[Sequence], symbol: str, timeframe: str, source: str
) -> pd.DataFrame:
    """Costruisce il DataFrame canonico delle candele da righe grezze a 12 colonne.

    Tutte le righe passate devono essere candele chiuse: il filtro sulle candele ancora
    aperte va fatto prima (vedi `drop_unclosed`).
    """
    raw = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if raw.shape[1] != len(RAW_COLUMNS):
        raise ValueError(f"attese {len(RAW_COLUMNS)} colonne di klines, trovate {raw.shape[1]}")
    raw.columns = RAW_COLUMNS

    frame = pd.DataFrame(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "open_time": pd.to_datetime(normalize_epoch_ms(raw["open_time"]), unit="ms"),
            "open": pd.to_numeric(raw["open"]).astype("float64"),
            "high": pd.to_numeric(raw["high"]).astype("float64"),
            "low": pd.to_numeric(raw["low"]).astype("float64"),
            "close": pd.to_numeric(raw["close"]).astype("float64"),
            "volume": pd.to_numeric(raw["volume"]).astype("float64"),
            "close_time": pd.to_datetime(normalize_epoch_ms(raw["close_time"]), unit="ms"),
            "quote_volume": pd.to_numeric(raw["quote_volume"]).astype("float64"),
            "trade_count": pd.to_numeric(raw["trade_count"]).astype("int64"),
            "is_closed": True,
            "source": source,
        }
    )
    return frame[CANDLE_COLUMNS]


def drop_unclosed(rows: Sequence[Sequence], now_ms: int) -> list[Sequence]:
    """Scarta le candele non ancora chiuse (close_time nel futuro rispetto a `now_ms`).

    L'API restituisce anche la candela in corso: non va mai salvata come chiusa.
    """
    return [r for r in rows if epoch_ms(r[6]) < now_ms]
