from datetime import datetime

import pandas as pd
import pytest

from bot.data.db import last_open_time, load_candles, upsert_candles
from tests.conftest import candles

NOW = datetime(2026, 10, 23, 12)


def test_upsert_is_idempotent(con):
    frame = candles(datetime(2020, 1, 1), 48)
    first = upsert_candles(con, frame, NOW)
    second = upsert_candles(con, frame, NOW)
    assert (first.inserted, first.changed, first.unchanged) == (48, 0, 0)
    assert (second.inserted, second.changed, second.unchanged) == (0, 0, 48)
    assert con.execute("SELECT count(*) FROM candles").fetchall()[0][0] == 48


def test_changed_values_are_updated_and_counted(con):
    frame = candles(datetime(2020, 1, 1), 10)
    upsert_candles(con, frame, NOW)
    fixed = frame.copy()
    fixed.loc[3, "volume"] = 999.0
    stats = upsert_candles(con, fixed, NOW)
    assert (stats.inserted, stats.changed, stats.unchanged) == (0, 1, 9)
    assert con.execute("SELECT max(volume) FROM candles").fetchall()[0][0] == 999.0


def test_duplicates_in_batch_are_rejected(con):
    frame = candles(datetime(2020, 1, 1), 5)
    with pytest.raises(ValueError, match="duplicate"):
        upsert_candles(con, pd.concat([frame, frame.iloc[[0]]]), NOW)


def test_last_open_time_and_range_load(con):
    upsert_candles(con, candles(datetime(2020, 1, 1), 24), NOW)
    assert last_open_time(con, "BTC/USDT", "1h") == datetime(2020, 1, 1, 23)
    assert last_open_time(con, "ETH/USDT", "1h") is None
    part = load_candles(con, "BTC/USDT", "1h", start=datetime(2020, 1, 1, 6), end=datetime(2020, 1, 1, 12))
    assert len(part) == 6 and part["open_time"].is_monotonic_increasing
