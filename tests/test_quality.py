from datetime import date, datetime

import pandas as pd
import yaml

from bot.data.db import upsert_candles
from bot.data.klines import klines_to_frame
from bot.config import PROJECT_ROOT
from bot.data.quality import (
    ERROR,
    EXPLAINED,
    WARNING,
    KnownEvent,
    check_candles,
    check_resample,
    load_known_events,
    mark_common_gaps,
    run_quality,
)
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
    assert issues == []
    assert "24 candele confrontate, 0 con prezzi diversi, 0 solo con volume diverso, 0 non confrontabili" in note


def test_resample_price_mismatch_is_error_and_incomplete_blocks_are_skipped():
    rows = raw_klines(START, 96, "1h")
    fine = klines_to_frame(rows, "BTC/USDT", "1h", "test").drop(index=[5])  # blocco 04:00 incompleto
    coarse = klines_to_frame(aggregate_rows(rows, 4, "4h"), "BTC/USDT", "4h", "test")
    coarse.loc[10, "close"] *= 1.001
    issues, note = check_resample(fine, coarse, "BTC/USDT", "1h", "4h", 1e-6)
    assert [(i.severity, i.count) for i in issues] == [(ERROR, 1)]
    assert "23 candele confrontate, 1 con prezzi diversi, 0 solo con volume diverso, 1 non confrontabili" in note


def test_resample_volume_only_mismatch_is_warning():
    # Caso reale (BTC/USDT 4h 2021-01-21 08:00): prezzi identici, volume 4h diverso dalla somma 1h
    rows = raw_klines(START, 96, "1h")
    fine = klines_to_frame(rows, "BTC/USDT", "1h", "test")
    coarse = klines_to_frame(aggregate_rows(rows, 4, "4h"), "BTC/USDT", "4h", "test")
    coarse.loc[2, "volume"] *= 0.9
    issues, _ = check_resample(fine, coarse, "BTC/USDT", "1h", "4h", 1e-6)
    assert [(i.severity, i.count) for i in issues] == [(WARNING, 1)]


def outage_series(timeframe_rows, first_missing, n_missing, truncated_at):
    """Serie con un fermo come quello reale del 2020-02-19: la candela prima del fermo si
    chiude in anticipo (close_time a metà candela) e le successive mancano."""
    df = klines_to_frame(timeframe_rows, "BTC/USDT", "1h", "test")
    before = df.index[df["open_time"] == first_missing - pd.Timedelta(hours=1)][0]
    df.loc[before, "close_time"] = truncated_at
    return df.drop(index=range(before + 1, before + 1 + n_missing))


EVENT = KnownEvent("*", "*", datetime(2020, 1, 2, 11), datetime(2020, 1, 2, 17), "fermo di prova")


def test_known_event_explains_gap_and_truncated_candle():
    df = outage_series(raw_klines(START, 72, "1h"), pd.Timestamp("2020-01-02 12:00"), 5, pd.Timestamp("2020-01-02 11:35:32"))

    found, gaps = checks(df)
    assert "close_time diverso da open_time + intervallo - 1 ms" in found
    assert gaps[0].explanation is None

    issues, gaps = check_candles(df, "BTC/USDT", "1h", date(2020, 1, 1), 0.15, 0.02, [EVENT])
    assert {i.check: i.severity for i in issues} == {"close_time anomalo durante un evento noto": EXPLAINED}
    assert [(g.n_missing, g.explanation) for g in gaps] == [(5, "fermo di prova")]


def test_known_event_widens_to_coarse_candle_boundaries():
    # Il fermo 11:00-17:00 tocca le candele 4h delle 08:00 (troncata) e delle 12:00 (mancante)
    df = klines_to_frame(raw_klines(START, 18, "4h"), "BTC/USDT", "4h", "test")
    df.loc[8, "close_time"] = pd.Timestamp("2020-01-02 11:35:32")
    df = df.drop(index=[9])
    issues, gaps = check_candles(df, "BTC/USDT", "4h", date(2020, 1, 1), 0.2, 0.02, [EVENT])
    assert [i.severity for i in issues] == [EXPLAINED]
    assert gaps[0].explanation == "fermo di prova"


def test_event_does_not_explain_what_is_outside_it():
    df = outage_series(raw_klines(START, 72, "1h"), pd.Timestamp("2020-01-02 12:00"), 7, pd.Timestamp("2020-01-02 11:35:32"))
    _, gaps = check_candles(df, "BTC/USDT", "1h", None, 0.15, 0.02, [EVENT])
    assert gaps[0].explanation is None  # il buco dura fino alle 19:00, oltre la fine dell'evento
    eth_event = KnownEvent("ETH/USDT", "*", EVENT.start, EVENT.end, "solo ETH")
    _, gaps = check_candles(df, "BTC/USDT", "1h", None, 0.15, 0.02, [eth_event])
    assert gaps[0].explanation is None


def test_zero_volume_inside_event_is_explained_outside_is_warning():
    df = candles(START, 72)
    df.loc[[35, 50], "volume"] = 0  # 2020-01-02 11:00 (nell'evento) e 2020-01-03 02:00 (fuori)
    issues, _ = check_candles(df, "BTC/USDT", "1h", None, 0.15, 0.02, [EVENT])
    assert {i.check: (i.severity, i.count) for i in issues} == {
        "candela a volume zero": (WARNING, 1),
        "candela a volume zero durante un evento noto": (EXPLAINED, 1),
    }


def test_common_gap_hint():
    _, btc = checks(candles(START, 48).drop(index=[10, 11]))
    _, eth = check_candles(candles(START, 48, symbol="ETH/USDT").drop(index=[10, 11]), "ETH/USDT", "1h", None, 0.15, 0.02)
    _, only_btc = checks(candles(START, 48).drop(index=[30]))
    gaps = btc + eth + only_btc
    mark_common_gaps(gaps, ["BTC/USDT", "ETH/USDT"])
    assert [g.common_to_all_symbols for g in gaps] == [True, True, False]


def test_project_known_events_file_is_valid():
    events = load_known_events(PROJECT_ROOT / "config" / "known_events.yaml")
    assert events and all(e.start < e.end for e in events)


def test_run_quality_end_to_end(con, settings):
    now = datetime(2020, 1, 5)
    rows_1h = raw_klines(START, 96, "1h")
    upsert_candles(con, klines_to_frame(rows_1h, "BTC/USDT", "1h", "test").drop(index=[40]), now)
    upsert_candles(con, klines_to_frame(aggregate_rows(rows_1h, 4, "4h"), "BTC/USDT", "4h", "test"), now)

    report = run_quality(con, settings, symbols=["BTC/USDT"], now=now)
    assert not report.ok and len(report.unexplained_gaps) == 1 and report.errors == []

    settings.paths.known_events.parent.mkdir(parents=True, exist_ok=True)
    settings.paths.known_events.write_text(yaml.safe_dump({"known_events": [
        {"symbol": "BTC/USDT", "timeframe": "1h", "start": "2020-01-02T16:00:00",
         "end": "2020-01-02T17:00:00", "reason": "test"}
    ]}))
    assert run_quality(con, settings, symbols=["BTC/USDT"], now=now).ok
