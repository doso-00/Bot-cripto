from datetime import date, datetime

import pandas as pd
import yaml

from bot.data.db import upsert_candles
from bot.data.klines import klines_to_frame
from bot.data.quality import ERROR, WARNING, check_candles, check_resample, explain_gaps, run_quality
from tests.conftest import aggregate_rows, candles, raw_klines

START = datetime(2020, 1, 1)


def checks(df, history_start=date(2020, 1, 1)):
    issues, gaps = check_candles(df, "BTC/USDT", "1h", history_start, max_abs_return=0.15, max_open_gap=0.02)
    return {i.check: i for i in issues}, gaps


def test_clean_series_has_no_issues():
    found, gaps = checks(candles(START, 200))
    assert found == {} and gaps == []


def test_structural_errors_are_detected():
    df = candles(START, 50)
    df.loc[5, "high"] = df.loc[5, "low"] - 1          # high < low
    df.loc[10, "close"] = df.loc[10, "high"] * 1.01    # close fuori range
    df.loc[15, "open"] = 0                             # prezzo non positivo
    df.loc[20, "close_time"] += pd.Timedelta(seconds=1)
    df = pd.concat([df, df.iloc[[30]]])                # duplicato
    found, _ = checks(df)
    for name in ("high < low", "close fuori dal range [low, high]", "open <= 0",
                 "close_time diverso da open_time + intervallo - 1 ms", "open_time duplicato"):
        assert found[name].severity == ERROR, name
    assert found["open_time duplicato"].count == 2


def test_misaligned_open_time_is_an_error():
    df = candles(START, 10)
    df["open_time"] += pd.Timedelta(minutes=1)
    df["close_time"] += pd.Timedelta(minutes=1)
    found, _ = checks(df)
    assert found["open_time non allineato al timeframe"].count == 10


def test_history_starting_late_is_an_error():
    found, _ = checks(candles(datetime(2020, 1, 2), 10))
    assert "storico inizia dopo history_start" in found


def test_gaps_are_found_with_exact_bounds():
    df = candles(START, 100).drop(index=[10, 11, 12, 50])
    _, gaps = checks(df)
    assert [(g.first_missing, g.last_missing, g.n_missing) for g in gaps] == [
        (datetime(2020, 1, 1, 10), datetime(2020, 1, 1, 12), 3),
        (datetime(2020, 1, 3, 2), datetime(2020, 1, 3, 2), 1),
    ]


def test_warnings_for_jumps_open_gaps_and_zero_volume():
    df = candles(START, 50)
    df.loc[20, ["open", "high", "low", "close"]] = df.loc[19, "close"] * pd.Series([1.0, 1.3, 1.0, 1.25]).values
    df.loc[30, "open"] = df.loc[29, "close"] * 1.05
    df.loc[30, "high"] = max(df.loc[30, "high"], df.loc[30, "open"])
    df.loc[40, "volume"] = 0
    found, _ = checks(df)
    jump = found["variazione close-to-close oltre 15%"]
    assert jump.severity == WARNING and "2020-01-01 20:00:00" in jump.examples[0]
    assert found["open distante oltre 2% dal close precedente"].severity == WARNING
    assert found["candela a volume zero"].count == 1
    assert not [i for i in found.values() if i.severity == ERROR]


def test_jump_across_a_gap_is_not_flagged_as_open_gap():
    df = candles(START, 30).drop(index=[10])
    df.loc[11, "open"] = df.loc[9, "close"] * 1.10
    df.loc[11, "high"] = max(df.loc[11, "high"], df.loc[11, "open"])
    found, gaps = checks(df)
    assert "open distante oltre 2% dal close precedente" not in found and len(gaps) == 1


def test_resample_matches_when_coarse_is_aggregate_of_fine():
    rows = raw_klines(START, 96, "1h")
    fine = klines_to_frame(rows, "BTC/USDT", "1h", "test")
    coarse = klines_to_frame(aggregate_rows(rows, 4, "4h"), "BTC/USDT", "4h", "test")
    issues, note = check_resample(fine, coarse, "BTC/USDT", "1h", "4h", 1e-6)
    assert issues == [] and "24 candele confrontate, 0 diverse, 0 non confrontabili" in note


def test_resample_detects_mismatch_and_skips_incomplete_blocks():
    rows = raw_klines(START, 96, "1h")
    fine = klines_to_frame(rows, "BTC/USDT", "1h", "test").drop(index=[5])  # blocco 04:00 incompleto
    coarse = klines_to_frame(aggregate_rows(rows, 4, "4h"), "BTC/USDT", "4h", "test")
    coarse.loc[10, "close"] *= 1.001
    issues, note = check_resample(fine, coarse, "BTC/USDT", "1h", "4h", 1e-6)
    assert issues[0].severity == ERROR and issues[0].count == 1
    assert "23 candele confrontate, 1 diverse, 1 non confrontabili" in note


def test_gap_explanations_and_common_gap_hint():
    _, btc = checks(candles(START, 48).drop(index=[10, 11]))
    eth_issues, eth = check_candles(
        candles(START, 48, symbol="ETH/USDT").drop(index=[10, 11]), "ETH/USDT", "1h", None, 0.15, 0.02
    )
    gaps = btc + eth
    known = [{"symbol": "*", "timeframe": "1h", "start": datetime(2020, 1, 1, 10),
              "end": datetime(2020, 1, 1, 11), "reason": "manutenzione"}]
    explain_gaps(gaps, known, ["BTC/USDT", "ETH/USDT"])
    assert all(g.explanation == "manutenzione" and g.common_to_all_symbols for g in gaps)

    _, only_btc = checks(candles(START, 48).drop(index=[30]))
    explain_gaps(only_btc, known, ["BTC/USDT", "ETH/USDT"])
    assert only_btc[0].explanation is None and not only_btc[0].common_to_all_symbols


def test_run_quality_end_to_end(con, settings):
    now = datetime(2020, 1, 5)
    rows_1h = raw_klines(START, 96, "1h")
    upsert_candles(con, klines_to_frame(rows_1h, "BTC/USDT", "1h", "test").drop(index=[40]), now)
    upsert_candles(con, klines_to_frame(aggregate_rows(rows_1h, 4, "4h"), "BTC/USDT", "4h", "test"), now)

    report = run_quality(con, settings, symbols=["BTC/USDT"], now=now)
    assert not report.ok and len(report.unexplained_gaps) == 1 and report.errors == []

    settings.paths.known_gaps.parent.mkdir(parents=True, exist_ok=True)
    settings.paths.known_gaps.write_text(yaml.safe_dump({"known_gaps": [
        {"symbol": "BTC/USDT", "timeframe": "1h", "start": "2020-01-02T16:00:00",
         "end": "2020-01-02T16:00:00", "reason": "test"}
    ]}))
    assert run_quality(con, settings, symbols=["BTC/USDT"], now=now).ok
