"""Esegue le query di docs/QUERY_VERIFICA.md su un database sintetico."""

import re
from datetime import datetime
from pathlib import Path

import pytest

from bot.data.db import upsert_candles
from bot.data.klines import klines_to_frame
from tests.conftest import aggregate_rows, raw_klines

DOC = Path(__file__).resolve().parent.parent / "docs" / "QUERY_VERIFICA.md"
QUERIES = {
    m.group(1): m.group(0)
    for m in re.finditer(r"-- (Q\d+)\n.*?;", DOC.read_text(encoding="utf-8"), re.DOTALL)
}
NOW = datetime(2020, 1, 10)


@pytest.fixture
def db(con):
    rows = raw_klines(datetime(2020, 1, 1), 96, "1h")
    h1 = klines_to_frame(rows, "BTC/USDT", "1h", "bulk").drop(index=[50, 51])  # buco di 2 ore
    h4 = klines_to_frame(aggregate_rows(rows, 4, "4h"), "BTC/USDT", "4h", "bulk")
    upsert_candles(con, h1, NOW)
    upsert_candles(con, h4, NOW)
    con.execute(
        "INSERT INTO exchange_filters VALUES ('BTC/USDT', '2026-01-01', 0.00001, 9000, 0.00001, 5, true, 0.01, '[]')"
    )
    return con


def test_all_queries_found():
    assert sorted(QUERIES, key=lambda q: int(q[1:])) == [f"Q{i}" for i in range(1, 10)]


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_query_runs(db, name):
    db.execute(QUERIES[name]).fetchall()


def test_query_results_match_expectations(db):
    q1 = {(r[0], r[1]): r for r in db.execute(QUERIES["Q1"]).fetchall()}
    assert q1["BTC/USDT", "1h"][2] == 94 and q1["BTC/USDT", "1h"][-1] == 2
    assert q1["BTC/USDT", "4h"][-1] == 0

    gaps = db.execute(QUERIES["Q3"]).fetchall()
    assert gaps == [("BTC/USDT", "1h", datetime(2020, 1, 3, 2), datetime(2020, 1, 3, 3), 2)]

    for clean in ("Q2", "Q4", "Q5", "Q6"):
        assert db.execute(QUERIES[clean]).fetchall() == [], clean

    db.execute("UPDATE candles SET close = close * 1.01, high = high * 1.01 WHERE timeframe = '4h' AND open_time = '2020-01-01 08:00'")
    assert len(db.execute(QUERIES["Q6"]).fetchall()) == 1
