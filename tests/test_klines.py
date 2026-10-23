from datetime import datetime

import pandas as pd
import pytest

from bot.data.klines import CANDLE_COLUMNS, drop_unclosed, klines_to_frame
from tests.conftest import raw_klines


def test_microseconds_and_milliseconds_give_same_candles():
    # I file spot dell'archivio dal 2025 hanno timestamp in microsecondi
    start = datetime(2025, 1, 1)
    ms = klines_to_frame(raw_klines(start, 5, "1h"), "BTC/USDT", "1h", "bulk")
    us = klines_to_frame(raw_klines(start, 5, "1h", micro=True), "BTC/USDT", "1h", "bulk")
    pd.testing.assert_frame_equal(ms, us)
    assert ms["open_time"].iat[0] == pd.Timestamp("2025-01-01 00:00:00")
    assert ms["close_time"].iat[0] == pd.Timestamp("2025-01-01 00:59:59.999")


def test_frame_has_canonical_columns_and_types():
    frame = klines_to_frame(raw_klines(datetime(2020, 1, 1), 3, "4h"), "ETH/USDT", "4h", "api")
    assert list(frame.columns) == CANDLE_COLUMNS
    assert frame["open"].dtype == "float64"
    assert frame["trade_count"].dtype == "int64"
    assert (frame["symbol"] == "ETH/USDT").all() and (frame["source"] == "api").all()
    assert frame["is_closed"].all()


def test_wrong_column_count_is_rejected():
    with pytest.raises(ValueError, match="12 colonne"):
        klines_to_frame([[1, 2, 3]], "BTC/USDT", "1h", "api")


def test_drop_unclosed_removes_candle_in_progress():
    rows = raw_klines(datetime(2026, 10, 23, 10), 3, "1h")
    now_ms = rows[2][0] + 1_000  # un secondo dopo l'apertura dell'ultima candela
    kept = drop_unclosed(rows, now_ms)
    assert [r[0] for r in kept] == [rows[0][0], rows[1][0]]
