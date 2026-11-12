from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stocks.earnings import earnings_info, expected_move_pct, fetch_earnings
from tests.stocks.fakes import FakeTicker, FakeYF, make_chain, make_earnings

ROME = ZoneInfo("Europe/Rome")
NY = ZoneInfo("America/New_York")


def edf(rows):
    """Passa dal formato Yahoo al formato di `fetch_earnings` (usa l'adattatore reale)."""
    return fetch_earnings(
        "X", client=FakeYF({"X": FakeTicker(earnings=make_earnings(rows))})
    )


def prices(closes_by_date: dict[str, float]) -> pd.DataFrame:
    idx = pd.Index([date.fromisoformat(d) for d in closes_by_date], name="date")
    return pd.DataFrame({"close": list(closes_by_date.values())}, index=idx)


def test_fetch_earnings_columns_and_empty():
    df = edf(
        [("2026-11-17 16:00", 1.0, None, None), ("2026-08-27 16:00", 0.9, 1.0, 11.0)]
    )
    assert list(df.columns) == ["ts", "eps_estimate", "eps_reported", "surprise_pct"]
    assert df["ts"].iloc[0].tzinfo is not None
    for client in (
        FakeYF(),  # simbolo ignoto -> None da get_earnings_dates
        FakeYF({"X": FakeTicker(earnings=make_earnings([]))}),
    ):
        empty = fetch_earnings("X", client=client)
        assert empty.empty
        assert list(empty.columns) == [
            "ts",
            "eps_estimate",
            "eps_reported",
            "surprise_pct",
        ]


def test_next_event_future():
    df = edf(
        [("2026-11-17 16:00", 1.0, None, None), ("2026-08-27 16:00", 0.9, 1.0, 11.0)]
    )
    now = datetime(2026, 11, 12, 9, 0, tzinfo=ROME)
    info = earnings_info(df, prices({"2026-08-27": 100.0}), now)
    assert info.next_ts.date() == date(2026, 11, 17)
    assert info.days_to_next == 5
    assert info.reported_since_last_run is False


def test_past_date_without_eps_is_still_next():
    df = edf(
        [("2026-11-17 16:00", 1.0, None, None), ("2026-08-27 16:00", 0.9, 1.0, 11.0)]
    )
    now = datetime(2026, 11, 17, 22, 0, tzinfo=ROME)
    info = earnings_info(df, prices({}), now)
    assert info.next_ts.date() == date(2026, 11, 17)
    assert info.reported_since_last_run is False


def test_reported_since_last_run_true_after_release():
    df = edf(
        [("2026-11-17 16:00", 1.0, 1.1, 8.5), ("2026-08-27 16:00", 0.9, 1.0, 11.0)]
    )
    now = datetime(2026, 11, 18, 7, 0, tzinfo=ROME)
    info = earnings_info(df, prices({}), now)
    assert info.reported_since_last_run is True
    assert info.last_ts.date() == date(2026, 11, 17)
    assert info.last_surprise_pct == pytest.approx(8.5)
    assert info.next_ts is None
    # oltre le 36 ore non è più "appena uscita"
    later = datetime(2026, 11, 19, 12, 0, tzinfo=ROME)
    assert earnings_info(df, prices({}), later).reported_since_last_run is False


def test_after_close_move_uses_next_day():
    df = edf([("2026-08-27 16:00", 0.9, 1.0, 11.0)])
    p = prices({"2026-08-26": 90.0, "2026-08-27": 100.0, "2026-08-28": 110.0})
    info = earnings_info(df, p, datetime(2026, 9, 1, 9, 0, tzinfo=ROME))
    assert info.past_moves_pct == [pytest.approx(0.10)]
    assert info.avg_abs_move_pct == pytest.approx(0.10)


def test_pre_open_move_uses_same_day():
    df = edf([("2026-08-27 08:00", 0.9, 1.0, 11.0)])
    p = prices({"2026-08-26": 100.0, "2026-08-27": 95.0, "2026-08-28": 120.0})
    info = earnings_info(df, p, datetime(2026, 9, 1, 9, 0, tzinfo=ROME))
    assert info.past_moves_pct == [pytest.approx(-0.05)]
    assert info.avg_abs_move_pct == pytest.approx(0.05)


def test_past_moves_max_eight():
    rows, closes = [], {}
    for i in range(10):  # 10 trimestrali passate, una al mese
        d = date(2026, 1 + i, 10) if i < 9 else date(2026, 10, 10)
        rows.append((f"{d} 16:00", 1.0, 1.0, 1.0))
        closes[str(d)] = 100.0
        closes[str(date(d.year, d.month, 11))] = 101.0
    rows.sort(reverse=True)
    info = earnings_info(
        edf(rows),
        prices(dict(sorted(closes.items()))),
        datetime(2026, 11, 12, 9, 0, tzinfo=ROME),
    )
    assert len(info.past_moves_pct) == 8
    assert all(m == pytest.approx(0.01) for m in info.past_moves_pct)


def test_events_without_prices_are_skipped():
    df = edf([("2026-08-27 16:00", 0.9, 1.0, 1.0), ("2026-05-28 16:00", 0.9, 1.0, 1.0)])
    p = prices({"2026-05-28": 100.0, "2026-05-29": 103.0})  # manca agosto
    info = earnings_info(df, p, datetime(2026, 9, 1, 9, 0, tzinfo=ROME))
    assert info.past_moves_pct == [pytest.approx(0.03)]


def test_empty_earnings():
    info = earnings_info(
        fetch_earnings("X", client=FakeYF()),
        prices({}),
        datetime(2026, 11, 12, tzinfo=ROME),
    )
    assert info.next_ts is None and info.days_to_next is None
    assert info.past_moves_pct == [] and info.avg_abs_move_pct is None


def chain_client(expiries, chains):
    return FakeYF({"X": FakeTicker(options=expiries, chains=chains)})


def test_expected_move_straddle():
    chain = make_chain(
        calls=[(95, 9.0, 9.4, 9.2), (100, 4.8, 5.2, 5.0), (105, 2.0, 2.2, 2.1)],
        puts=[(95, 1.0, 1.2, 1.1), (100, 3.8, 4.2, 4.0), (105, 7.0, 7.4, 7.2)],
    )
    client = chain_client(
        ("2026-11-13", "2026-11-20", "2026-12-18"), {"2026-11-20": chain}
    )
    # prima scadenza >= 17/11 è il 20/11; spot 100.4 -> strike 100
    assert expected_move_pct(
        "X", date(2026, 11, 17), 100.0, client=client
    ) == pytest.approx(0.09)


def test_expected_move_zero_bid_ask_uses_last_price():
    chain = make_chain(calls=[(100, 0.0, 0.0, 5.0)], puts=[(100, 3.8, 0.0, 4.0)])
    client = chain_client(("2026-11-20",), {"2026-11-20": chain})
    assert expected_move_pct(
        "X", date(2026, 11, 17), 100.0, client=client
    ) == pytest.approx(0.09)


def test_expected_move_none_without_options():
    assert expected_move_pct("X", date(2026, 11, 17), 100.0, client=FakeYF()) is None
    # nessuna scadenza dopo l'evento
    client = chain_client(("2026-11-13",), {})
    assert expected_move_pct("X", date(2026, 11, 17), 100.0, client=client) is None


def test_fetch_earnings_failure_returns_empty(monkeypatch):
    monkeypatch.setattr("stocks.earnings.with_retry", lambda fn: fn())
    client = FakeYF({"X": FakeTicker(earnings_error=ValueError("no earnings"))})
    assert fetch_earnings("X", client=client).empty
